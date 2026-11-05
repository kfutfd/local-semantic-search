"""Repair missing / failed OCR pages with a hard per-page timeout."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
os.chdir(ROOT)

import ocr_run as core  # noqa: E402

CHILD = r"""
import sys, os, numpy as np, pymupdf
pdf, page_no, dpi, dst = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
import ocr_compat
ocr_compat.prepare()
from rapidocr_openvino import RapidOCR
ocr = RapidOCR()
doc = pymupdf.open(pdf)
pix = doc[page_no].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
res, _ = ocr(img)
text = "\n".join(x[1] for x in (res or []))
os.makedirs(os.path.dirname(dst), exist_ok=True)
open(dst, "w", encoding="utf-8").write(text)
doc.close()
"""


def run_page(pdf, page_no, dst, dpi, timeout):
    env = dict(os.environ)
    env["RAPIDOCR_THREADS"] = "4"
    env.setdefault("RAPIDOCR_DEVICE", "CPU")
    env["PYTHONPATH"] = os.path.join(ROOT, "scripts")
    env.setdefault("RAPIDOCR_CACHE", os.path.join(ROOT, "models", "ocr-ov-cache"))
    try:
        subprocess.run([sys.executable, "-c", CHILD, pdf, str(page_no), str(dpi), dst],
                       timeout=timeout, env=env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def main():
    roots = [os.path.join(os.path.expanduser("~"), "Desktop"),
             os.path.join(os.path.expanduser("~"), "Downloads")]
    pdfs = core.scanned_pdfs(roots)
    targets = []
    for pdf, pages in pdfs:
        for i in range(pages):
            dst = core.out_path(pdf, i)
            bad = False
            if os.path.exists(dst):
                try:
                    bad = open(dst, encoding="utf-8").read().startswith("[ocr-error]")
                except OSError:
                    bad = True
            if not os.path.exists(dst) or bad:
                targets.append((pdf, i, dst))
    print(f"[fix] {len(targets)} pages to repair", flush=True)

    ok = failed = 0
    for n, (pdf, page, dst) in enumerate(targets, 1):
        for dpi in (110, 70):
            if run_page(pdf, page, dst, dpi, timeout=120):
                ok += 1
                break
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            open(dst, "w", encoding="utf-8").write("[ocr-error] timeout")
            failed += 1
        if n % 10 == 0:
            print(f"[fix] {n}/{len(targets)} ok={ok} failed={failed}", flush=True)
    print(f"[fix] done ok={ok} failed={failed}", flush=True)


if __name__ == "__main__":
    main()
