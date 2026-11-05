# -*- coding: utf-8 -*-
"""Live daemon test: does the background loop scan, OCR, rebuild and serve?"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
PYW = os.path.join(os.path.dirname(PY), "pythonw.exe")
CREATE_NO_WINDOW = 0x08000000
FAILS = []

def ok(name, cond, detail=""):
    if cond:
        print("PASS", name)
    else:
        FAILS.append(name)
        print("FAIL", name, "->", str(detail)[:700])

def run(sandbox, args, timeout=300):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    p = subprocess.run([PY] + args, cwd=sandbox, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")

def last_json(out):
    for line in reversed(out.strip().splitlines()):
        if line.strip().startswith("{"):
            return json.loads(line)
    return {}

def make_corpus(corpus):
    import pymupdf
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(corpus)
    open(os.path.join(corpus, "notes.txt"), "w", encoding="utf-8").write(
        "超导体的迈斯纳效应说明磁场会被排除出材料内部。" * 10)
    open(os.path.join(corpus, "more.txt"), "w", encoding="utf-8").write(
        "声子谱的计算依赖晶格动力学矩阵。" * 10)

    img = Image.new("L", (1400, 420), 255)
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype("arial.ttf", 52)
    d.text((60, 120), "Superconductivity measurements", fill=0, font=font)
    png = os.path.join(corpus, "src.png")
    img.save(png)
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page()
        page.insert_image(page.rect, filename=png)
    doc.save(os.path.join(corpus, "scan.pdf"))
    doc.close()
    os.remove(png)

def main():
    # the running production daemon owns the global mutex: stand down first
    subprocess.run([PY, os.path.join(PROJ, "scripts", "stop_daemon.py")],
                   capture_output=True, timeout=120)
    time.sleep(2)

    sandbox = tempfile.mkdtemp(prefix="lsq-daemon-")
    corpus = os.path.join(sandbox, "corpus")
    shutil.copytree(os.path.join(PROJ, "scripts"), os.path.join(sandbox, "scripts"))
    for m in ("bge-small-zh-fp16", "bge-small-zh-src"):
        shutil.copytree(os.path.join(PROJ, "models", m), os.path.join(sandbox, "models", m))
    make_corpus(corpus)
    json.dump({"roots": [corpus], "model": "bge-small", "device": "auto", "mode": "semantic",
               "scan_interval_min": 1, "scan_on_battery": True,
               "ocr": True, "ocr_pages_per_run": 5, "ocr_workers": 1,
               "hotkey": "ctrl+alt+space", "max_results": 5},
              open(os.path.join(sandbox, "settings.json"), "w", encoding="utf-8"))
    print("sandbox:", sandbox)

    run(sandbox, ["scripts/ingest.py", "--from-settings"])
    run(sandbox, ["scripts/embed_docs.py", "--model", "bge-small", "--device", "auto", "--allow-battery"])
    rc, out = run(sandbox, ["scripts/build_all.py", "--model", "bge-small"])
    ok("initial build", rc == 0, out[-300:])
    meta0 = json.load(open(os.path.join(sandbox, "index", "all", "meta.json"), encoding="utf-8"))

    log_path = os.path.join(sandbox, "index", "daemon.log")
    exe = PYW if os.path.exists(PYW) else PY
    proc = subprocess.Popen([exe, os.path.join(sandbox, "scripts", "daemon.py")],
                            cwd=sandbox, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW if exe == PY else 0)
    try:
        deadline = time.time() + 300
        text = ""
        while time.time() < deadline:
            time.sleep(5)
            text = open(log_path, encoding="utf-8").read() if os.path.exists(log_path) else ""
            if "reloaded rows=" in text:
                break
        print("---- daemon log ----")
        print(text.strip())
        print("--------------------")
        ok("daemon started", "daemon started" in text, text[-300:])
        ok("daemon scanned", "scan: ingest rc=0" in text, text[-400:])
        ok("daemon ran OCR", "scan: ocr" in text, text[-400:])
        ok("daemon rebuilt and reloaded", "reloaded rows=" in text, text[-400:])
        ok("no failures logged", "scan failed" not in text and "fatal:" not in text
           and " rc=1" not in text, text[-600:])

        meta1 = json.load(open(os.path.join(sandbox, "index", "all", "meta.json"), encoding="utf-8"))
        ok("index grew after OCR", meta1["rows"] > meta0["rows"],
           (meta0["rows"], meta1["rows"]))
    finally:
        rc, out = run(sandbox, ["scripts/stop_daemon.py"])
        time.sleep(3)
        alive = proc.poll() is None
        ok("stop_daemon stops this project's daemon", not alive, (rc, out, proc.pid))
        if alive:
            proc.kill()

    rc, out = run(sandbox, ["scripts/daemon.py", "--selftest"])
    ok("daemon --selftest works after the shutdown",
       rc == 0 and "[selftest]" in out, (rc, out[-300:]))

    sys.path.insert(0, os.path.join(sandbox, "scripts"))
    os.chdir(sandbox)
    import search_engine
    eng = search_engine.Engine(device="auto")
    hits = eng.search("Superconductivity measurements", k=5)
    ok("OCR text is searchable afterwards",
       any(h["name"] == "scan.pdf" and h["ocr"] for h in hits),
       [(h["name"], h["ocr"]) for h in hits])
    ok("no leftover daemon log errors", "search failed" not in
       open(log_path, encoding="utf-8").read())
    eng.close()

    print()
    if FAILS:
        print("FAILED:", len(FAILS))
        for f in FAILS:
            print("  -", f)
        print("sandbox kept:", sandbox)
        return 1
    print("ALL DAEMON TESTS PASSED")
    shutil.rmtree(sandbox, ignore_errors=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
