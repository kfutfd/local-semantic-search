"""Lightweight search engine over the merged mmap index (no sklearn)."""
import json
import mmap
import os
import re
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import bm25 as bm25_mod  # noqa: E402

MODELS = {
    "bge-small": {"dir": "models/bge-small-zh-fp16", "tok": "models/bge-small-zh-src/tokenizer.json",
                  "pad": 0, "dim": 512},
    "bge-m3": {"dir": "models/bge-m3-fp16", "tok": "models/bge-m3-src/tokenizer.json",
               "pad": 1, "dim": 1024},
}

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]*|\d+(?:\.\d+)?|[\u4e00-\u9fff]{2,}")


class Engine:
    def __init__(self, index_dir="index/all", model="bge-small", device="GPU",
                 idle_unload=900, mode="semantic"):
        if device == "auto":
            try:
                import openvino as _ov
                device = "GPU" if "GPU" in _ov.Core().available_devices else "CPU"
            except Exception:
                device = "CPU"
        self.index_dir = index_dir if os.path.isabs(index_dir) else os.path.join(ROOT, index_dir)
        self.model = model
        self.device = device
        self.idle_unload = idle_unload
        self.mode = mode
        self.use_dense = mode != "lite"
        # RLock + condition: searches must never touch an mmap while a rebuild
        # is swapping the index out from under them.
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self.dense_error = ""
        self.embedder = None
        self.last_used = 0.0
        self.elapsed_ms = 0
        self.load()

    def load(self):
        t0 = time.time()
        with open(os.path.join(self.index_dir, "meta.json"), encoding="utf-8-sig") as fh:
            self.meta = json.load(fh)
        self.rows = [json.loads(line) for line in
                     open(os.path.join(self.index_dir, "rows.jsonl"), encoding="utf-8-sig")]
        self.vectors = (np.load(os.path.join(self.index_dir, "vectors.npy"), mmap_mode="r")
                        if self.use_dense else None)
        self.bm25 = bm25_mod.BM25(os.path.join(self.index_dir, "bm25"))
        self.text_ptr = np.load(os.path.join(self.index_dir, "text_ptr.npy"), mmap_mode="r")
        self._text_fh = open(os.path.join(self.index_dir, "texts.bin"), "rb")
        self._text_mm = mmap.mmap(self._text_fh.fileno(), 0, access=mmap.ACCESS_READ)
        if len(self.text_ptr) != len(self.rows) + 1:
            raise ValueError(
                f"index is inconsistent: rows={len(self.rows)} "
                f"text_ptr={len(self.text_ptr) - 1} -- run rebuild-index.cmd")
        self.loaded_in = time.time() - t0

    def reload(self):
        with self._lock:
            try:
                if self._text_mm is not None:
                    self._text_mm.close()
                if self._text_fh is not None:
                    self._text_fh.close()
            except Exception:
                pass
            try:
                self.load()
            finally:
                # wake searches even if the new index turned out to be unusable,
                # so they can report an error instead of hanging
                self._cv.notify_all()

    @staticmethod
    def _release(arr):
        """Drop a numpy memmap and its underlying file mapping."""
        mm = getattr(arr, "_mmap", None)
        if mm is not None:
            try:
                mm.close()
            except Exception:
                pass

    def close(self):
        """Release every mmap so a rebuild can overwrite the files (Windows locks them).

        numpy's memmap keeps the file open until its _mmap is closed; merely
        dropping the reference is not enough before build_all rewrites the index.
        """
        with self._lock:
            try:
                if self._text_mm is not None:
                    self._text_mm.close()
            except Exception:
                pass
            try:
                if self._text_fh is not None:
                    self._text_fh.close()
            except Exception:
                pass
            for arr in (self.text_ptr, self.vectors):
                self._release(arr)
            if self.bm25 is not None:
                for name in ("term_ptr", "post_docs", "post_tfs", "doc_len"):
                    self._release(getattr(self.bm25, name, None))
            self._text_mm = None
            self._text_fh = None
            self.text_ptr = None
            self.vectors = None
            self.bm25 = None
            self.rows = []

    def text(self, i):
        if self._text_mm is None:
            return ""
        a, b = int(self.text_ptr[i]), int(self.text_ptr[i + 1])
        return self._text_mm[a:b].decode("utf-8", "ignore")

    # ---------- model ----------
    def _embedder(self):
        if self.embedder is None:
            import sys
            sys.path.insert(0, HERE)
            from embed_lib import BGEEmbedder
            cfg = MODELS[self.model]
            self.embedder = BGEEmbedder(cfg["dir"], cfg["tok"], device=self.device,
                                        pad_id=cfg["pad"], static_batch=1, static_len=128)
        return self.embedder

    def warm(self):
        try:
            with self._lock:
                self._embedder().embed(["warmup"])
                self.last_used = time.time()
        except Exception:
            self.embedder = None

    def maybe_unload(self):
        if self.embedder is not None and self.idle_unload > 0:
            if time.time() - self.last_used > self.idle_unload:
                with self._lock:
                    self.embedder = None

    # ---------- search ----------
    def search(self, query, k=8):
        query = query.strip()
        if not query or k <= 0:
            return []
        with self._lock:
            # a background rebuild closes the index for a few seconds; wait for
            # it (the condition releases the lock) instead of hitting a dead mmap
            deadline = time.time() + 30.0
            while self.bm25 is None and time.time() < deadline:
                self._cv.wait(0.1)
            if self.bm25 is None:
                raise RuntimeError("index is being rebuilt; try again in a moment")
            return self._search_locked(query, k)

    def _search_locked(self, query, k):
        t0 = time.time()
        lex = self.bm25.scores(query)
        dense = None
        if self.use_dense:
            try:
                with self._lock:
                    emb = self._embedder()
                    qv = emb.embed([query])[0].astype(np.float32)
                    self.last_used = time.time()
                dense = self.vectors @ qv
            except Exception as exc:
                # a broken/missing model must not make the whole app useless:
                # fall back to keyword-only search for the rest of the session
                self.use_dense = False
                self.dense_error = f"{type(exc).__name__}: {exc}"

        pool = 300
        l_order = np.argsort(-lex)[:pool]
        d_order = np.argsort(-dense)[:pool] if dense is not None else []
        K = 60.0
        fused = {}
        for rank, i in enumerate(d_order):
            fused[int(i)] = fused.get(int(i), 0.0) + 1.0 / (K + rank + 1)
        for rank, i in enumerate(l_order):
            fused[int(i)] = fused.get(int(i), 0.0) + 1.0 / (K + rank + 1)

        out, seen_pages, seen_snip = [], set(), set()
        for i, score in sorted(fused.items(), key=lambda kv: -kv[1]):
            if i >= len(self.rows):  # index shrank under us during a rebuild
                continue
            row = self.rows[i]
            key = (row["file"], row["page"])
            if key in seen_pages:
                continue
            snip = self._snippet(self.text(i), query)
            if not snip or snip in seen_snip:
                continue
            seen_pages.add(key)
            seen_snip.add(snip)
            out.append({
                "file": row["file"],
                "name": os.path.basename(row["file"]),
                "page": row.get("page", 1),
                "label": row.get("label") or str(row.get("page", 1)),
                "section": row.get("section", ""),
                "ocr": row.get("ocr", False),
                "snippet": snip,
                "dense": float(dense[i]) if dense is not None else 0.0,
                "lexical": float(lex[i]),
                "score": float(score),
            })
            if len(out) >= k:
                break
        self.elapsed_ms = round((time.time() - t0) * 1000)
        return out

    @staticmethod
    def _snippet(text, query, width=180):
        flat = " ".join(text.split())
        if not flat:
            return ""
        terms = sorted({t.lower() for t in TOKEN_RE.findall(query) if len(t) >= 2},
                       key=len, reverse=True)
        pos = -1
        for t in terms:
            p = flat.lower().find(t)
            if p >= 0 and (pos < 0 or p < pos):
                pos = p
        start = max(0, (pos if pos > 0 else 0) - 40)
        snip = flat[start:start + width]
        return ("…" if start else "") + snip + ("…" if start + width < len(flat) else "")
