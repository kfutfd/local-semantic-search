"""OCR every page of the scanned PDFs into per-page text files.

Resumable: a page is done when its .txt exists.  Safe to kill and re-run.
"""
import argparse
import gc
import glob
import hashlib
import json
import multiprocessing as mp
import os
import sys
import time

import numpy as np
import pymupdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES_DIR = os.path.join(ROOT, "index", "ocr", "pages")
MAX_DIM = 2000
_DETECTED = None


def page_id(pdf):
    return hashlib.sha1(pdf.encode("utf-8")).hexdigest()[:10]


def out_path(pdf, page_no):
    return os.path.join(PAGES_DIR, page_id(pdf), f"{page_no:05d}.txt")


def init_worker(threads, dpi):
    global _OCR, _DPI
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["RAPIDOCR_THREADS"] = str(threads)
    os.environ.setdefault("RAPIDOCR_DEVICE", "CPU")
    os.environ.setdefault("RAPIDOCR_CACHE", os.path.join(ROOT, "models", "ocr-ov-cache"))
    import ocr_compat
    ocr_compat.prepare()
    from rapidocr_openvino import RapidOCR

    _DPI = dpi
    _OCR = RapidOCR()


def ocr_one(task):
    pdf, page_no = task
    dst = out_path(pdf, page_no)
    if os.path.exists(dst):
        return page_no, 0.0, 0
    try:
        doc = pymupdf.open(pdf)
        page = doc[page_no]
        rect = page.rect
        zoom = min(_DPI / 72.0, MAX_DIM / max(rect.width, rect.height, 1))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()
        doc.close()
        t = time.time()
        result, _ = _OCR(img)
        dt = time.time() - t
        del img
        # keep line order (top to bottom)
        lines = [x[1] for x in (result or [])]
        text = "\n".join(lines)
        del result, lines
    except Exception as exc:  # pragma: no cover - defensive
        text = f"[ocr-error] {exc}"
        dt = 0.0
    # RapidOCR/OpenVINO keep large buffers per page; collect now instead of
    # waiting for the cyclic GC.  Native buffers still grow, so the pool's
    # maxtasksperchild/memory watchdog remains the real safety net.
    gc.collect()
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, dst)
    return page_no, dt, len(text)


def scanned_pdfs(roots):
    # keep one skip list for the whole project (models/, index/, .venv/ ... )
    try:
        from ingest import SKIP_DIRS as skip_dirs
    except Exception:
        skip_dirs = {"deepseek-harness", "__pycache__", ".git"}
    found = []
    for root in roots:
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d not in skip_dirs]
            for name in fn:
                if not name.lower().endswith(".pdf"):
                    continue
                path = os.path.join(dp, name)
                try:
                    doc = pymupdf.open(path)
                    if sum(len(p.get_text() or "") for p in doc) < 50:
                        found.append((path, doc.page_count))
                    doc.close()
                except Exception:
                    continue
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", nargs="+", default=[
        os.path.join(os.path.expanduser("~"), "Desktop"),
        os.path.join(os.path.expanduser("~"), "Downloads"),
    ])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--limit", type=int, default=0, help="only OCR this many pages (debug)")
    ap.add_argument("--only", default="", help="only PDFs whose name contains this")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--allow-battery", action="store_true")
    ap.add_argument("--retry-errors", action="store_true", help="redo pages that stored an [ocr-error] marker")
    args = ap.parse_args()

    from power_state import require_ac
    if not require_ac("OCR", args.allow_battery):
        sys.exit(2)

    pdfs = scanned_pdfs(args.roots)
    if args.only:
        pdfs = [p for p in pdfs if args.only.lower() in p[0].lower()]
    if args.list:
        for p, n in pdfs:
            print(f"{n:5d} pages  {os.path.basename(p)}")
        print(f"total {len(pdfs)} pdfs, {sum(n for _, n in pdfs)} pages")
        return

    if args.retry_errors:
        bad = 0
        for pdf, pages in pdfs:
            for i in range(pages):
                p = out_path(pdf, i)
                if os.path.exists(p):
                    try:
                        if open(p, encoding="utf-8").read().startswith("[ocr-error]"):
                            os.remove(p)
                            bad += 1
                    except OSError:
                        pass
        print(f"[retry] cleared {bad} failed pages", flush=True)

    tasks = []
    for pdf, pages in pdfs:
        for i in range(pages):
            if not os.path.exists(out_path(pdf, i)):
                tasks.append((pdf, i))
    print(f"[plan] {len(pdfs)} pdfs, {sum(n for _, n in pdfs)} pages, {len(tasks)} to OCR", flush=True)
    if args.limit:
        tasks = tasks[: args.limit]
    if not tasks:
        print("[done] nothing to do")
        return

    t0 = time.time()
    done_pages = 0
    done_chars = 0
    with mp.Pool(args.workers, initializer=init_worker, initargs=(args.threads, args.dpi)) as pool:
        for page_no, dt, nchars in pool.imap_unordered(ocr_one, tasks, chunksize=1):
            done_pages += 1
            done_chars += nchars
            if done_pages % 25 == 0:
                el = time.time() - t0
                rate = done_pages / el
                eta = (len(tasks) - done_pages) / rate if rate else 0
                print(f"[ocr] {done_pages}/{len(tasks)}  {rate:.2f} pages/s  "
                      f"eta {eta/60:.1f} min  chars/page {done_chars/done_pages:.0f}", flush=True)

    el = time.time() - t0
    print(json.dumps({"pages": done_pages, "seconds": round(el, 1),
                      "pages_per_s": round(done_pages / el, 2),
                      "chars_per_page": int(done_chars / max(done_pages, 1))}, ensure_ascii=False))


if __name__ == "__main__":
    main()



