"""Merge per-document chunks/vectors into mmap-friendly files + BM25 index."""
import argparse
import json
import os
import shutil
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
os.chdir(ROOT)

import bm25  # noqa: E402

MODELS = {
    "bge-small": {"dim": 512, "dir": "index/vectors-small"},
    "bge-m3": {"dim": 1024, "dir": "index/vectors"},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="bge-small", choices=list(MODELS))
    ap.add_argument("--vectors", default="")
    ap.add_argument("--out", default="index/all")
    args = ap.parse_args()

    dim = MODELS[args.model]["dim"]
    vec_dir = args.vectors or MODELS[args.model]["dir"]
    try:
        manifest = json.load(open("index/manifest.json", encoding="utf-8-sig"))
    except Exception as exc:
        raise SystemExit(f"index/manifest.json is unreadable ({exc}); run ingest.py again")
    # build into a sibling directory, then swap: a failed run can never leave
    # the live index half-written (the daemon reloads this while running)
    out_dir = args.out.rstrip("\\/")
    tmp_dir = out_dir + ".tmp"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)

    texts, meta_rows, mats = [], [], []
    skipped = 0
    for path in sorted(manifest):
        meta = manifest[path]
        if meta.get("chunks", 0) <= 0:
            continue
        pid = meta["pid"]
        doc_file = os.path.join("index", "docs", pid + ".jsonl")
        vec_file = os.path.join(vec_dir, pid + ".npy")
        if not (os.path.exists(doc_file) and os.path.exists(vec_file)):
            skipped += 1
            continue
        rows = [json.loads(line) for line in open(doc_file, encoding="utf-8")]
        vec = np.load(vec_file)
        if vec.ndim != 2 or vec.shape[0] != len(rows) or vec.shape[1] != dim:
            skipped += 1
            continue
        for r in rows:
            texts.append(r["text"])
            meta_rows.append({"file": r["file"], "page": r.get("page", 1),
                              "label": r.get("label") or str(r.get("page", 1)),
                              "section": r.get("section", ""), "ocr": bool(r.get("ocr")),
                              "pid": pid})
        mats.append(vec)

    if not mats:
        raise SystemExit("no documents with vectors; run embed_docs first")

    np.save(os.path.join(tmp_dir, "vectors.npy"), np.vstack(mats).astype(np.float32))

    with open(os.path.join(tmp_dir, "rows.jsonl"), "w", encoding="utf-8") as fh:
        for r in meta_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    # text blob + offsets so snippets can be read straight from a mmap
    ptr = np.zeros(len(texts) + 1, dtype=np.int64)
    with open(os.path.join(tmp_dir, "texts.bin"), "wb") as fh:
        off = 0
        for i, t in enumerate(texts):
            b = t.encode("utf-8")
            fh.write(b)
            ptr[i] = off
            off += len(b)
        ptr[len(texts)] = off
    np.save(os.path.join(tmp_dir, "text_ptr.npy"), ptr)

    t = time.time()
    bm_meta = bm25.build(texts, os.path.join(tmp_dir, "bm25"))

    out_meta = {"model": args.model, "dim": dim, "rows": len(meta_rows),
                "documents": len(mats), "skipped": skipped,
                "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "bm25": bm_meta}
    # meta.json must already be inside the new directory before it goes live,
    # otherwise a crash right after the swap leaves an index nobody can open
    with open(os.path.join(tmp_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(out_meta, fh, ensure_ascii=False, indent=1)

    old_dir = out_dir + ".old"
    shutil.rmtree(old_dir, ignore_errors=True)
    try:
        if os.path.isdir(out_dir):
            os.replace(out_dir, old_dir)
        try:
            os.replace(tmp_dir, out_dir)
        except Exception:
            # put the previous index back rather than leaving none at all
            if os.path.isdir(old_dir) and not os.path.isdir(out_dir):
                os.replace(old_dir, out_dir)
            raise
    except PermissionError as exc:
        print(f"[error] cannot swap in the new index: {exc}", flush=True)
        print("[error] the index is still open -- run stop-daemon.cmd, then retry", flush=True)
        raise SystemExit(3)
    shutil.rmtree(old_dir, ignore_errors=True)
    print(json.dumps({**out_meta, "bm25_seconds": round(time.time() - t, 1)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
