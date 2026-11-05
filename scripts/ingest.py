"""Ingest documents into per-file chunk stores.

Layout:
  index/docs/<pid>.jsonl      chunks of one document
  index/manifest.json         path -> {mtime, size, pid, kind, chunks, chars}
  index/ocr/pages/<pid>/*.txt OCR output for scanned PDFs

Sectioning strategy: use the PDF outline (TOC) when it exists, otherwise keep
the document flat.  Font-size heuristics were tried and rejected -- physics PDFs
render formulae larger than headings, so auto-detected titles were mostly noise.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time

import pymupdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index")
DOCS = os.path.join(INDEX, "docs")
OCR_PAGES = os.path.join(INDEX, "ocr", "pages")
PENDING = os.path.join(INDEX, "ocr", "pending.json")
MANIFEST = os.path.join(INDEX, "manifest.json")

SKIP_DIRS = {"deepseek-harness", "AppData", "node_modules", ".git", "__pycache__",
             "battery-audit", "models", "index", ".venv", "venv", "site-packages"}
TEXT_EXT = {".txt", ".md", ".csv"}

CHUNK_CHARS = 450
MIN_CHUNK = 20


def pid_for(path):
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]


def ocr_page_count(pid):
    """How many pages OCR has produced for this document so far."""
    folder = os.path.join(OCR_PAGES, pid[:10])
    if not os.path.isdir(folder):
        return 0
    return sum(1 for f in os.listdir(folder) if f.endswith(".txt"))


def write_pending(items):
    """Hand the daemon a work list: scanned PDFs whose OCR is still incomplete."""
    os.makedirs(os.path.dirname(PENDING), exist_ok=True)
    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pages_missing": sum(max(0, i["pages"] - i["ocr_pages"]) for i in items),
        "items": items,
    }
    tmp = PENDING + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, PENDING)


def clean(text):
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def toc_sections(doc):
    try:
        toc = doc.get_toc(simple=True)
    except Exception:
        return []
    if not toc:
        return []
    secs = []
    for i, (level, title, page) in enumerate(toc):
        start = max(0, page - 1)
        end = (toc[i + 1][2] - 1) if i + 1 < len(toc) else doc.page_count
        end = max(end, start + 1)
        secs.append({"level": level, "title": clean(title),
                     "start": start, "end": min(end, doc.page_count)})
    return secs


def split_paragraphs(text):
    parts = re.split(r"\n\s*\n|\n(?=\s*[（(]?\d+[.、)])|\n(?=第[一二三四五六七八九十\d]+[章节])", text)
    return [clean(p) for p in parts if clean(p)]


def pack(paragraphs, size=CHUNK_CHARS):
    chunks, buf = [], ""
    for p in paragraphs:
        while len(p) > size * 1.6:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(p[:size])
            p = p[size:]
        if len(buf) + len(p) <= size:
            buf = (buf + "\n" + p).strip()
        else:
            if buf:
                chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    return [c for c in chunks if len(c) >= MIN_CHUNK]


def page_labels(doc):
    """Per-page printed labels (falls back to the physical page number)."""
    out = []
    for i in range(doc.page_count):
        try:
            out.append(doc[i].get_label() or str(i + 1))
        except Exception:
            out.append(str(i + 1))
    return out


def ocr_chunks(path, pid, labels=None):
    folder = os.path.join(OCR_PAGES, pid[:10])
    if not os.path.isdir(folder):
        return []
    chunks = []
    for name in sorted(f for f in os.listdir(folder) if f.endswith(".txt")):
        page_no = int(os.path.splitext(name)[0])
        with open(os.path.join(folder, name), encoding="utf-8") as fh:
            text = clean(fh.read())
        if len(text) < MIN_CHUNK or text.startswith("[ocr-error]"):
            continue
        label = labels[page_no] if labels and page_no < len(labels) else str(page_no + 1)
        for c in pack(split_paragraphs(text)):
            chunks.append({"page": page_no + 1, "label": label, "section": "",
                           "text": c, "ocr": True})
    return chunks


def pdf_chunks(path, pid):
    """Return (chunks, page_count, text_layer_chars)."""
    doc = pymupdf.open(path)
    pages_text = [p.get_text() or "" for p in doc]
    pages = doc.page_count
    labels = page_labels(doc)
    chars = sum(len(t) for t in pages_text)
    if chars < 50:  # scanned: use whatever OCR pages exist so far (may be none)
        doc.close()
        return ocr_chunks(path, pid, labels), pages, chars

    chunks = []
    secs = toc_sections(doc)
    if len(secs) >= 3:
        for sec in secs:
            parts = []
            for pno in range(sec["start"], sec["end"]):
                parts.extend(split_paragraphs(pages_text[pno]))
            for c in pack(parts):
                chunks.append({"page": sec["start"] + 1, "label": labels[sec["start"]],
                               "section": sec["title"], "text": c})
    else:
        for pno, text in enumerate(pages_text):
            for c in pack(split_paragraphs(text)):
                chunks.append({"page": pno + 1, "label": labels[pno], "section": "", "text": c})
    doc.close()
    return chunks, pages, chars


def read_text(path):
    """Decode a text file without silently losing it.

    Windows users have a mix of UTF-8 (often with BOM) and GBK .txt/.csv files;
    decoding everything as UTF-8 with errors="ignore" turns GBK files into an
    empty string, so they disappear from the index entirely.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # mixed / partly corrupt file: keep whichever encoder loses least
    best, best_bad = "", None
    for enc in ("utf-8", "gb18030"):
        txt = raw.decode(enc, "replace")
        bad = txt.count("\ufffd")
        if best_bad is None or bad < best_bad:
            best, best_bad = txt, bad
    return best


def text_chunks(path):
    text = clean(read_text(path))
    return [{"page": 1, "label": "1", "section": "", "text": c}
            for c in pack(split_paragraphs(text))]


def scan(roots):
    files = []
    for root in roots:
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            for name in fn:
                if os.path.splitext(name)[1].lower() in TEXT_EXT or name.lower().endswith(".pdf"):
                    files.append(os.path.join(dp, name))
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", nargs="+", default=[
        os.path.join(os.path.expanduser("~"), "Desktop"),
        os.path.join(os.path.expanduser("~"), "Downloads"),
    ])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--from-settings", action="store_true")
    args = ap.parse_args()
    if args.from_settings:
        cfg_path = os.path.join(ROOT, "settings.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, encoding="utf-8-sig") as fh:
                    args.roots = json.load(fh).get("roots", args.roots)
            except Exception as exc:
                print(f"[warn] settings.json unreadable ({exc}); "
                      f"scanning the default folders", flush=True)

    os.makedirs(DOCS, exist_ok=True)
    manifest = {}
    if os.path.exists(MANIFEST) and not args.force:
        try:
            with open(MANIFEST, encoding="utf-8-sig") as fh:
                manifest = json.load(fh)
        except Exception as exc:
            # a torn manifest must not brick every future scan: rebuild it
            print(f"[warn] manifest unreadable ({exc}); rebuilding the index from scratch",
                  flush=True)
            manifest = {}

    files = scan(args.roots)
    if args.limit:
        files = files[: args.limit]
    print(f"[scan] {len(files)} files", flush=True)

    new = updated = skipped = waiting = 0
    pending = []
    # unchanged files are skipped, but their OCR may still be incomplete: carry
    # the previous work list forward instead of dropping them from pending.json
    carry = {}
    if os.path.exists(PENDING) and not args.force:
        try:
            with open(PENDING, encoding="utf-8-sig") as fh:
                carry = {i["file"]: i for i in json.load(fh).get("items", [])}
        except Exception:
            carry = {}
    for path in files:
        try:
            st = os.stat(path)
        except OSError:
            continue
        pid = pid_for(path)
        prev = manifest.get(path)
        is_pdf = path.lower().endswith(".pdf")
        ocr_n = ocr_page_count(pid) if is_pdf else 0
        # a PDF whose OCR grew must be re-read even when the file itself is unchanged
        if (prev and not args.force and prev.get("mtime") == st.st_mtime
                and prev.get("size") == st.st_size and prev.get("ocr_pages", 0) == ocr_n):
            skipped += 1
            old = carry.get(path)
            if old and ocr_n < int(old.get("pages", 0)):
                pending.append(dict(old, ocr_pages=ocr_n))
            continue
        try:
            if is_pdf:
                chunks, pages, chars = pdf_chunks(path, pid)
                kind = "pdf"
                scanned = chars < 50
                if scanned and ocr_n < pages:
                    pending.append({"file": path, "pid": pid, "pages": pages,
                                    "ocr_pages": ocr_n, "chars": chars})
                if not chunks:
                    waiting += 1
                    why = "OCR done, no usable text" if ocr_n >= pages else "OCR unfinished"
                    print(f"[wait] {os.path.basename(path)[:60]} ({why})", flush=True)
                    continue
            else:
                chunks = text_chunks(path)
                kind = "text"
        except Exception as exc:
            print(f"[err] {os.path.basename(path)}: {exc}", flush=True)
            continue

        with open(os.path.join(DOCS, pid + ".jsonl"), "w", encoding="utf-8") as fh:
            for i, c in enumerate(chunks):
                c.update({"file": path, "pid": pid, "idx": i})
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")

        manifest[path] = {
            "mtime": st.st_mtime, "size": st.st_size, "pid": pid, "kind": kind,
            "chunks": len(chunks), "chars": sum(len(c["text"]) for c in chunks),
            "ocr": any(c.get("ocr") for c in chunks), "ocr_pages": ocr_n,
        }
        if prev:
            updated += 1
        else:
            new += 1
        print(f"[ok] {os.path.basename(path)[:58]:58s} {len(chunks):4d}", flush=True)

    # drop entries whose files disappeared (only inside the scanned roots)
    removed = 0
    if not args.limit:
        # A root that is temporarily gone (unplugged drive, offline share) must
        # not be read as "every file in it was deleted"; ignore missing roots.
        roots_ok = [os.path.normcase(os.path.abspath(r)) for r in args.roots
                    if os.path.isdir(r)]
        for path in list(manifest):
            real = os.path.normcase(os.path.abspath(path))
            inside = any(real == r or real.startswith(r + os.sep) for r in roots_ok)
            if inside and not os.path.exists(path):
                pid = manifest[path].get("pid")
                if not pid:
                    del manifest[path]
                    removed += 1
                    continue
                for p in (os.path.join(DOCS, pid + ".jsonl"),
                          os.path.join("index", "vectors", pid + ".npy"),
                          os.path.join("index", "vectors-small", pid + ".npy")):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                shutil.rmtree(os.path.join(OCR_PAGES, pid[:10]), ignore_errors=True)
                del manifest[path]
                removed += 1

    tmp_manifest = MANIFEST + ".tmp"
    with open(tmp_manifest, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    os.replace(tmp_manifest, MANIFEST)
    if not args.limit:
        # this run may have covered only some roots: keep queued work for the rest
        seen = set(files)
        queued = {p["file"] for p in pending}
        for path, old in carry.items():
            if path not in seen and path not in queued and os.path.exists(path):
                pending.append(old)
        write_pending(pending)

    print(json.dumps({"files": len(manifest), "new": new, "updated": updated,
                      "skipped": skipped, "waiting_ocr": waiting, "removed": removed,
                      "ocr_pending": len(pending),
                      "ocr_pages_missing": sum(max(0, i["pages"] - i["ocr_pages"]) for i in pending),
                      "chunks": sum(v["chunks"] for v in manifest.values())}, ensure_ascii=False))


if __name__ == "__main__":
    main()

