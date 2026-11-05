"""Embed per-document chunks and store vectors alongside the chunk store."""
import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
os.chdir(ROOT)

from embed_lib import BGEEmbedder

INDEX = "index"
DOCS = os.path.join(INDEX, "docs")
VECTORS = os.path.join(INDEX, "vectors")
VECTORS_SMALL = os.path.join(INDEX, "vectors-small")
MANIFEST = os.path.join(INDEX, "manifest.json")

MODELS = {
    "bge-m3": {"dir": "models/bge-m3-fp16", "tok": "models/bge-m3-src/tokenizer.json", "pad": 1, "dim": 1024},
    "bge-small": {"dir": "models/bge-small-zh-fp16", "tok": "models/bge-small-zh-src/tokenizer.json", "pad": 0, "dim": 512},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="bge-m3", choices=list(MODELS))
    ap.add_argument("--device", default="GPU")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--seq-len", type=int, default=512)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--allow-battery", action="store_true")
    args = ap.parse_args()

    from power_state import require_ac
    if not require_ac("embedding", args.allow_battery):
        sys.exit(2)

    if args.device == "auto":
        import openvino as _ov
        args.device = "GPU" if "GPU" in _ov.Core().available_devices else "CPU"
    cfg = MODELS[args.model]
    default_dir = VECTORS_SMALL if args.model == "bge-small" else VECTORS
    vectors_dir = args.out_dir or default_dir
    os.makedirs(vectors_dir, exist_ok=True)
    try:
        manifest = json.load(open(MANIFEST, encoding="utf-8-sig"))
    except Exception as exc:
        raise SystemExit(f"index/manifest.json is unreadable ({exc}); run ingest.py again")

    todo = []
    for path, meta in manifest.items():
        if meta["chunks"] <= 0:
            continue
        dst = os.path.join(vectors_dir, meta["pid"] + ".npy")
        src = os.path.join(DOCS, meta["pid"] + ".jsonl")
        if not args.force and os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
            continue
        todo.append((path, meta, dst, src))
    if args.limit:
        todo = todo[: args.limit]
    print(f"[plan] {len(todo)} documents to embed with {args.model} on {args.device}", flush=True)
    if not todo:
        print("[done] vectors up to date")
        return

    t0 = time.time()
    emb = BGEEmbedder(cfg["dir"], cfg["tok"], device=args.device, pad_id=cfg["pad"],
                      static_batch=None, static_len=args.seq_len)
    print(f"[load] {time.time() - t0:.1f}s", flush=True)

    done_chunks = 0
    t = time.time()
    for path, meta, dst, src in todo:
        rows = [json.loads(line) for line in open(src, encoding="utf-8")]
        texts = [r["text"] for r in rows]
        vecs = []
        step = 256
        for i in range(0, len(texts), step):
            vecs.append(emb.embed(texts[i:i + step], batch_size=args.batch))
        mat = np.vstack(vecs).astype(np.float32) if vecs else np.zeros((0, cfg["dim"]), dtype=np.float32)
        np.save(dst, mat)
        done_chunks += len(texts)
        el = time.time() - t
        print(f"[ok] {os.path.basename(path)[:50]:50s} {len(texts):4d} chunks  "
              f"{done_chunks / el:5.1f} chunks/s", flush=True)

    el = time.time() - t
    print(json.dumps({"model": args.model, "device": args.device, "documents": len(todo),
                      "chunks": done_chunks, "seconds": round(el, 1),
                      "chunks_per_s": round(done_chunks / el, 1)}, ensure_ascii=False))


if __name__ == "__main__":
    main()


