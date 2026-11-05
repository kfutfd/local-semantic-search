# -*- coding: utf-8 -*-
"""End-to-end pipeline test in a throw-away copy of the project."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
FAILS = []

def ok(name, cond, detail=""):
    if cond:
        print("PASS", name)
    else:
        FAILS.append(name)
        print("FAIL", name, "->", str(detail)[:600])

def run(sandbox, args, timeout=900):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    p = subprocess.run([PY] + args, cwd=sandbox, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")

def last_json(out):
    for line in reversed(out.strip().splitlines()):
        if line.strip().startswith("{"):
            return json.loads(line)
    return {}

def build_corpus(corpus):
    import pymupdf
    os.makedirs(os.path.join(corpus, "nested", "deep"), exist_ok=True)
    os.makedirs(os.path.join(corpus, "models"), exist_ok=True)
    os.makedirs(os.path.join(corpus, ".venv", "lib"), exist_ok=True)

    open(os.path.join(corpus, "a.txt"), "w", encoding="utf-8").write(
        "量子纠缠是量子力学中最反直觉的现象。" * 8)
    open(os.path.join(corpus, "b_gbk.txt"), "wb").write(
        ("热力学第二定律指出孤立系统的熵不会减少。" * 8).encode("gbk"))
    open(os.path.join(corpus, "c.md"), "w", encoding="utf-8").write(
        "# 笔记\n\n" + "密度矩阵重正化群可以用来研究强关联体系。" * 6)
    open(os.path.join(corpus, "nested", "deep", "e.txt"), "w", encoding="utf-8").write(
        "嵌套目录里的文档，讲的是傅里叶光学与衍射。" * 6)
    open(os.path.join(corpus, "模型 样例 (1).txt"), "w", encoding="utf-8").write(
        "文件名里有空格和括号，也应当能被正常索引与搜索。" * 6)
    open(os.path.join(corpus, "models", "should_skip.txt"), "w", encoding="utf-8").write(
        "这个目录应该被跳过，绝不能进入索引。" * 6)
    open(os.path.join(corpus, ".venv", "lib", "should_skip.txt"), "w", encoding="utf-8").write(
        "虚拟环境里的文件也应该被跳过。" * 6)
    open(os.path.join(corpus, "empty.txt"), "w").close()
    open(os.path.join(corpus, "big.txt"), "w", encoding="utf-8").write(
        ("大数据量文档的一行内容，用来测试分块性能。" * 40 + "\n") * 120)

    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 100), "PDF TEXT LAYER PAGE %d SCHWINGER MODEL" % (i + 1), fontsize=14)
    doc.save(os.path.join(corpus, "text.pdf"))
    doc.close()

    # PDF with a real outline (exercises the TOC sectioning path)
    doc = pymupdf.open()
    for i in range(6):
        page = doc.new_page()
        page.insert_text((72, 100), "SECTION %d CONTENT ABOUT GAUGE FIELDS" % (i + 1), fontsize=13)
    doc.set_toc([[1, "第一章 规范场", 1], [1, "第二章 重整化", 3], [1, "第三章 反常", 5]])
    doc.save(os.path.join(corpus, "toc.pdf"))
    doc.close()

    # scanned PDF: image only, two pages
    from PIL import Image, ImageDraw, ImageFont
    # mixed case: the OCR model keeps word spacing here, all-caps at this size
    # gets glued into one token ("SCANNEDPAGEALPHA") -- an engine limitation
    lines = ["Scanned page alpha", "Lattice gauge theory notes", "Entropy and free energy"]
    img = Image.new("L", (1240, 500), 255)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 44)
    except Exception:
        font = ImageFont.load_default()
    for n, line in enumerate(lines):
        draw.text((60, 60 + n * 90), line, fill=0, font=font)
    png = os.path.join(corpus, "scan.png")
    img.save(png)
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page()
        page.insert_image(page.rect, filename=png)
    doc.save(os.path.join(corpus, "scan.pdf"))
    doc.close()
    os.remove(png)

    # empty-password encrypted PDF and a corrupt PDF
    doc = pymupdf.open()
    doc.insert_pdf(pymupdf.open(os.path.join(corpus, "text.pdf")))
    doc.save(os.path.join(corpus, "locked.pdf"),
             encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="")
    doc.close()
    open(os.path.join(corpus, "corrupt.pdf"), "wb").write(b"%PDF-1.4 definitely not a real pdf")

def main():
    sandbox = tempfile.mkdtemp(prefix="lsq-selftest-")
    corpus = os.path.join(sandbox, "corpus")
    os.makedirs(corpus)
    shutil.copytree(os.path.join(PROJ, "scripts"), os.path.join(sandbox, "scripts"))
    for m in ("bge-small-zh-fp16", "bge-small-zh-src"):
        shutil.copytree(os.path.join(PROJ, "models", m), os.path.join(sandbox, "models", m))
    build_corpus(corpus)
    json.dump({"roots": [corpus], "model": "bge-small", "device": "auto", "mode": "semantic",
               "scan_on_battery": True, "ocr": True, "ocr_pages_per_run": 50, "ocr_workers": 2},
              open(os.path.join(sandbox, "settings.json"), "w", encoding="utf-8"))
    print("sandbox:", sandbox)

    rc, out = run(sandbox, ["scripts/ingest.py", "--from-settings"])
    st = last_json(out)
    print("  ingest ->", st)
    ok("ingest rc=0", rc == 0, out[-800:])
    man = json.load(open(os.path.join(sandbox, "index", "manifest.json"), encoding="utf-8"))
    names = {os.path.basename(p) for p in man}
    ok("indexed 10 docs", st.get("files") == 10, st)
    ok("scan.pdf waits for OCR", st.get("ocr_pending") == 1, st)
    ok("corrupt.pdf does not abort the run", "corrupt.pdf" not in names, names)
    ok("skipped models/ dir", "should_skip.txt" not in names, names)
    ok("indexed filename with spaces/parens", "模型 样例 (1).txt" in names, names)
    ok("gbk file has text", man.get(os.path.join(corpus, "b_gbk.txt"), {}).get("chars", 0) > 50)
    ok("empty file handled", man.get(os.path.join(corpus, "empty.txt"), {}).get("chunks") == 0)
    ok("big file chunked", man.get(os.path.join(corpus, "big.txt"), {}).get("chunks", 0) > 100,
       man.get(os.path.join(corpus, "big.txt")))
    print("  locked.pdf ->", man.get(os.path.join(corpus, "locked.pdf")))

    rc, out = run(sandbox, ["scripts/ingest.py", "--from-settings"])
    st2 = last_json(out)
    ok("second ingest is a no-op", st2.get("new") == 0 and st2.get("updated") == 0, st2)
    ok("pending survives the no-op run", st2.get("ocr_pending") == 1, st2)

    # a root that is temporarily unavailable must not delete what is indexed
    rc, out = run(sandbox, ["scripts/ingest.py", "--roots", os.path.join(corpus, "gone-drive")])
    st = last_json(out)
    ok("missing root removes nothing", st.get("removed") == 0 and st.get("files") == 10, st)
    ok("missing root keeps the OCR queue", st.get("ocr_pending") == 1, st)

    rc, out = run(sandbox, ["scripts/embed_docs.py", "--model", "bge-small", "--device", "auto",
                            "--allow-battery"])
    ok("embed rc=0", rc == 0, out[-500:])
    rc, out = run(sandbox, ["scripts/build_all.py", "--model", "bge-small"])
    ok("build rc=0", rc == 0, out[-500:])
    meta = json.load(open(os.path.join(sandbox, "index", "all", "meta.json"), encoding="utf-8"))
    ok("build indexed 9 docs", meta["documents"] == 9, meta)
    ok("build left no .tmp/.old", not [d for d in os.listdir(os.path.join(sandbox, "index"))
                                       if d.endswith((".tmp", ".old"))],
       os.listdir(os.path.join(sandbox, "index")))

    sys.path.insert(0, os.path.join(sandbox, "scripts"))
    os.chdir(sandbox)
    import search_engine
    eng = search_engine.Engine(device="auto")
    ok("engine row count matches meta", len(eng.rows) == meta["rows"], (len(eng.rows), meta["rows"]))
    for q, expect in [("量子纠缠", "a.txt"), ("热力学第二定律 熵", "b_gbk.txt"),
                      ("密度矩阵重正化群", "c.md"), ("傅里叶光学 衍射", "e.txt"),
                      ("SCHWINGER", "text.pdf"), ("重整化 GAUGE", "toc.pdf")]:
        hits = eng.search(q, k=5)
        ok("search hits %s" % expect, expect in [h["name"] for h in hits],
           [h["name"] for h in hits])
    ok("empty query returns []", eng.search("") == [])
    ok("whitespace query returns []", eng.search("   \t ") == [])
    ok("k=0 returns nothing", eng.search("量子纠缠", k=0) == [], eng.search("量子纠缠", k=0))
    ok("punctuation-only query", isinstance(eng.search("!!!???~~~"), list))
    ok("very long query", isinstance(eng.search("量子" * 2000), list))
    ok("emoji query", isinstance(eng.search("量子 🚀 纠缠"), list))
    ok("newline query", isinstance(eng.search("量子\n纠缠\t"), list))
    ok("snippets present", all(h["snippet"] for h in eng.search("量子纠缠", k=3)))
    ok("files exist", all(os.path.exists(h["file"]) for h in eng.search("量子纠缠", k=3)))
    lite = search_engine.Engine(device="auto", mode="lite")
    ok("lite mode searches without vectors", bool(lite.search("量子纠缠", k=3)))
    lite.close()

    # ---- concurrent search during reload ---------------------------------
    import threading
    errors = []
    stop = threading.Event()
    def hammer():
        while not stop.is_set():
            try:
                eng.search("量子纠缠", k=3)
                eng.text(0)
            except Exception as exc:
                errors.append(repr(exc))
    th = threading.Thread(target=hammer, daemon=True)
    th.start()
    for _ in range(5):
        eng.reload()
        time.sleep(0.05)
    stop.set()
    th.join(timeout=5)
    ok("search during reload does not crash", not errors, errors[:3])

    # ---- delete + update --------------------------------------------------
    os.remove(os.path.join(corpus, "c.md"))
    open(os.path.join(corpus, "a.txt"), "w", encoding="utf-8").write(
        "完全改写过的内容，现在讲的是黑洞信息悖论。" * 8)
    rc, out = run(sandbox, ["scripts/ingest.py", "--from-settings"])
    st = last_json(out)
    ok("removed 1, updated 1", st.get("removed") == 1 and st.get("updated") == 1, st)
    ok("manifest dropped the doc", st.get("files") == 9, st)

    eng.close()

    # ---- rebuild while another process holds the index open --------------
    eng2 = search_engine.Engine(device="auto")
    rc, out = run(sandbox, ["scripts/build_all.py", "--model", "bge-small"])
    ok("build refuses politely when index is in use", rc == 3 and "stop-daemon.cmd" in out,
       (rc, out[-400:]))
    meta_after = json.load(open(os.path.join(sandbox, "index", "all", "meta.json"), encoding="utf-8"))
    ok("index untouched after refused build", meta_after == meta)
    eng2.close()

    # ---- kill builds at several points: live index must never be torn -----
    for delay in (0.05, 0.2, 0.4):
        proc = subprocess.Popen([PY, "scripts/build_all.py", "--model", "bge-small"], cwd=sandbox,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        time.sleep(delay)
        proc.kill()
        proc.wait()
        detail = ""
        try:
            probe = search_engine.Engine(device="auto")  # load() validates consistency
            hits = probe.search("量子纠缠", k=3)
            ok("index consistent after kill at %.2fs" % delay, isinstance(hits, list))
            probe.close()
        except Exception as exc:
            ok("index consistent after kill at %.2fs" % delay, False, repr(exc))

    rc, out = run(sandbox, ["scripts/build_all.py", "--model", "bge-small"])
    ok("rebuild afterwards succeeds", rc == 0, out[-400:])
    eng3 = search_engine.Engine(device="auto")
    hits = eng3.search("黑洞信息悖论", k=3)
    ok("updated content searchable", bool(hits) and hits[0]["name"] == "a.txt",
       [h["name"] for h in hits])
    ok("deleted doc gone", "c.md" not in [h["name"] for h in eng3.search("密度矩阵重正化群", k=5)])
    eng3.close()

    # ---- OCR round trip ---------------------------------------------------
    rc, out = run(sandbox, ["scripts/ocr_auto.py", "--max-pages", "2", "--workers", "1",
                            "--threads", "2", "--allow-battery"])
    print("  ocr ->", out.strip().splitlines()[-1] if out.strip() else out)
    ok("ocr rc=0", rc == 0, out[-500:])
    pages = []
    for dp, _dn, fn in os.walk(os.path.join(sandbox, "index", "ocr", "pages")):
        pages += [os.path.join(dp, f) for f in fn if f.endswith(".txt")]
    ok("ocr produced 2 pages", len(pages) == 2, pages)
    joined = " ".join(open(p, encoding="utf-8").read().upper() for p in pages)
    ok("ocr text readable", "SCANNED" in joined and "GAUGE" in joined, joined[:100])
    ok("ocr kept word spacing", "SCANNED PAGE" in joined, joined[:120])

    rc, out = run(sandbox, ["scripts/ingest.py", "--from-settings"])
    st = last_json(out)
    ok("ocr made scan.pdf indexable", st.get("new") == 1 and st.get("ocr_pending") == 0, st)
    rc, out = run(sandbox, ["scripts/embed_docs.py", "--model", "bge-small", "--device", "auto", "--allow-battery"])
    rc, out = run(sandbox, ["scripts/build_all.py", "--model", "bge-small"])
    ok("build after ocr", rc == 0, out[-300:])
    eng4 = search_engine.Engine(device="auto")
    hits = eng4.search("SCANNED PAGE ALPHA", k=6)
    ok("ocr content searchable + flagged",
       any(h["name"] == "scan.pdf" and h["ocr"] for h in hits), [(h["name"], h["ocr"]) for h in hits])
    eng4.close()

    # ---- --limit must not touch the manifest removal or the OCR queue ------
    before_manifest = open(os.path.join(sandbox, "index", "manifest.json"), encoding="utf-8").read()
    rc, out = run(sandbox, ["scripts/ingest.py", "--from-settings", "--limit", "2"])
    st = last_json(out)
    after_manifest = open(os.path.join(sandbox, "index", "manifest.json"), encoding="utf-8").read()
    ok("--limit leaves the manifest alone", st.get("files") == 10 and before_manifest == after_manifest,
       st)

    # ---- a settings.json saved with a BOM (Notepad default) must still work --
    settings_path = os.path.join(sandbox, "settings.json")
    cfg_text = open(settings_path, encoding="utf-8").read()
    with open(settings_path, "wb") as fh:
        fh.write(b"\xef\xbb\xbf" + cfg_text.encode("utf-8"))
    rc, out = run(sandbox, ["scripts/ingest.py", "--from-settings"])
    st = last_json(out)
    ok("BOM settings.json is accepted", rc == 0 and st.get("files") == 10, (rc, st, out[-300:]))

    # ---- a torn manifest must self-heal rather than brick every scan ------
    man_path = os.path.join(sandbox, "index", "manifest.json")
    good = open(man_path, encoding="utf-8").read()
    open(man_path, "w", encoding="utf-8").write(good[: max(10, len(good) // 2)])
    rc, out = run(sandbox, ["scripts/ingest.py", "--from-settings"])
    st = last_json(out)
    ok("torn manifest self-heals", rc == 0 and "[warn]" in out and st.get("files") == 10,
       (rc, st, out[-300:]))

    print()
    if FAILS:
        print("FAILED:", len(FAILS))
        for f in FAILS:
            print("  -", f)
        print("sandbox kept:", sandbox)
        return 1
    print("ALL PIPELINE TESTS PASSED")
    shutil.rmtree(sandbox, ignore_errors=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
