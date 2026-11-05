# -*- coding: utf-8 -*-
"""Unit-level checks: tokenizer, chunking, snippets, hotkey specs, decoding."""
import os, sys, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
os.chdir(ROOT)

import bm25
import ingest
from search_engine import Engine, TOKEN_RE

CASES = []
def case(fn):
    CASES.append(fn)
    return fn

@case
def tokenize_cjk_bigrams():
    got = bm25.tokenize("模电负反馈")
    assert got == ["模电", "电负", "负反", "反馈"], got

@case
def tokenize_single_cjk_char():
    assert bm25.tokenize("矩阵") == ["矩阵"]
    assert bm25.tokenize("好") == ["好"]

@case
def tokenize_mixed_script():
    got = bm25.tokenize("矩阵 A1 model")
    print("      mixed ->", got)
    assert "矩阵" in got and "model" in got

@case
def tokenize_empty_and_punct():
    print("      '' ->", bm25.tokenize(""))
    print("      '!!!' ->", bm25.tokenize("!!!"))
    print("      'a b' ->", bm25.tokenize("a b"))
    assert bm25.tokenize("") == []

@case
def clean_text():
    assert ingest.clean("a\x00b") == "a b"
    assert ingest.clean("a   b") == "a b"
    assert ingest.clean("a\n\n\n\nb") == "a\n\nb"

@case
def pack_respects_limits():
    paras = ["段落%d " % i + "字" * 200 for i in range(20)]
    chunks = ingest.pack(paras)
    assert chunks, "no chunks"
    too_long = [c for c in chunks if len(c) > ingest.CHUNK_CHARS * 1.6]
    too_short = [c for c in chunks if len(c) < ingest.MIN_CHUNK]
    print("      chunks=%d max=%d min=%d" % (len(chunks), max(map(len, chunks)), min(map(len, chunks))))
    assert not too_long, "chunk too long: %d" % len(too_long[0])
    assert not too_short, "chunk too short: %r" % too_short[:1]

@case
def pack_keeps_all_long_text():
    text = "甲" * 5000
    chunks = ingest.pack([text])
    total = sum(len(c) for c in chunks)
    print("      long text -> %d chunks, %d chars of %d" % (len(chunks), total, len(text)))
    assert total >= 4000, "lost too much text"

@case
def pid_for_is_stable():
    a = ingest.pid_for(r"C:\x\y.pdf")
    b = ingest.pid_for(r"C:\x\y.pdf")
    assert a == b and len(a) == 12 and all(c in "0123456789abcdef" for c in a)

@case
def snippet_chinese_phrase():
    text = "前言部分。" + "中间讲的是矩阵乘积态的基本定义。" + "后记。"
    snip = Engine._snippet(text, "矩阵乘积态")
    print("      snippet ->", snip)
    assert "矩阵乘积态" in snip

@case
def snippet_missing_term_falls_back_to_head():
    snip = Engine._snippet("完全不相关的内容在这里", "量子场论")
    assert snip, "empty snippet"
    print("      fallback ->", snip)

@case
def snippet_empty_text():
    assert Engine._snippet("", "abc") == ""
    assert Engine._snippet("   ", "abc") == ""

@case
def token_re_extracts_terms():
    got = TOKEN_RE.findall("矩阵乘积态 MPS 2024")
    print("      TOKEN_RE ->", got)
    assert "矩阵乘积态" in got

@case
def decode_gbk_text_file():
    """Windows users have GBK .txt files; they must not turn into mojibake."""
    import tempfile
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "gbk.txt")
    body = "量子力学导论 第一章 波函数与薛定谔方程。" * 4
    with open(p, "wb") as fh:
        fh.write(body.encode("gbk"))
    chunks = ingest.text_chunks(p)
    text = "".join(c["text"] for c in chunks)
    print("      decoded ->", repr(text[:40]))
    assert "量子力学导论" in text, "GBK text was decoded as garbage: %r" % text[:40]

@case
def decode_bom_utf8_text_file():
    import tempfile
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "bom.txt")
    body = "薛定谔方程与波函数" + "测试" * 20
    with open(p, "wb") as fh:
        fh.write(b"\xef\xbb\xbf" + body.encode("utf-8"))
    text = "".join(c["text"] for c in ingest.text_chunks(p))
    assert "\ufeff" not in text, "BOM leaked into the indexed text"
    assert "薛定谔方程" in text, text[:40]

@case
def decode_invalid_utf8_bytes():
    import tempfile
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "junk.txt")
    body = "混合编码的内容" * 5
    with open(p, "wb") as fh:
        fh.write(body.encode("gbk") + b"\xff\xfe\x00\x01")
    text = "".join(c["text"] for c in ingest.text_chunks(p))
    assert "混合编码" in text, "mixed junk broke decoding: %r" % text[:50]

@case
def ocr_error_pages_are_ignored():
    import tempfile
    tmp = tempfile.mkdtemp()
    saved = ingest.OCR_PAGES
    ingest.OCR_PAGES = tmp
    try:
        pid = "abcdef123456"
        folder = os.path.join(tmp, pid[:10])
        os.makedirs(folder)
        with open(os.path.join(folder, "00000.txt"), "w", encoding="utf-8") as fh:
            fh.write("[ocr-error] page failed to render")
        with open(os.path.join(folder, "00001.txt"), "w", encoding="utf-8") as fh:
            fh.write("正常运行的一页内容，足够长足够长足够长。")
        chunks = ingest.ocr_chunks("x.pdf", pid, ["1", "2"])
        print("      chunks:", [c["text"][:20] for c in chunks])
        assert len(chunks) == 1, "failed OCR page leaked into the index"
        assert "正常运行" in chunks[0]["text"]
    finally:
        ingest.OCR_PAGES = saved

@case
def ocr_stop_conditions():
    import ocr_auto
    ok = lambda **kw: ocr_auto.stop_reason(**kw)
    assert ok(max_worker_mb=1100, allow_battery=False, rss_mb=500, on_ac_now=True,
              elapsed=10, max_seconds=240) == ""
    assert "memory" in ok(max_worker_mb=1100, allow_battery=False, rss_mb=1200,
                          on_ac_now=True, elapsed=10, max_seconds=240)
    assert "battery" in ok(max_worker_mb=1100, allow_battery=False, rss_mb=500,
                           on_ac_now=False, elapsed=10, max_seconds=240)
    assert ok(max_worker_mb=1100, allow_battery=True, rss_mb=500, on_ac_now=False,
              elapsed=10, max_seconds=240) == "", "explicit --allow-battery must run"
    assert "time" in ok(max_worker_mb=1100, allow_battery=False, rss_mb=500,
                        on_ac_now=True, elapsed=300, max_seconds=240)

@case
def hotkey_spec_parsing():
    import daemon
    def parse(spec):
        mods, key = 0, None
        for part in [p.strip().lower() for p in spec.split("+")]:
            if part in daemon.MOD_MAP:
                mods |= daemon.MOD_MAP[part]
            elif part in daemon.VK_MAP:
                key = daemon.VK_MAP[part]
        return mods, key
    cases = ["ctrl+alt+space", "win+shift+f5", "ctrl+alt+q", "ctrl+alt+m", "ctrl+alt+1"]
    for spec in cases:
        mods, key = parse(spec)
        print("      %-18s mods=%#x key=%s" % (spec, mods, key))
        assert mods != 0, spec
        if spec not in ("ctrl+alt+m", "ctrl+alt+1"):
            assert key is not None, "hotkey %r has no key mapping" % spec

def main():
    fails = 0
    for fn in CASES:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as exc:
            fails += 1
            print("FAIL", fn.__name__, "->", exc)
            traceback.print_exc()
    print()
    print("%d/%d passed" % (len(CASES) - fails, len(CASES)))
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
