# -*- coding: utf-8 -*-
"""Daemon-level checks that do not need a live UI."""
import json
import os
import subprocess
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.chdir(ROOT)

import daemon

CASES = []
def case(fn):
    CASES.append(fn)
    return fn

@case
def single_instance_mutex():
    # run in a child: holding the mutex here would block the next test's daemon
    code = ("import sys; sys.path.insert(0, 'scripts'); import daemon; "
            "print(bool(daemon.single_instance()), bool(daemon.single_instance()))")
    env = dict(os.environ, LOCAL_SEARCH_MUTEX="LocalSearchDaemon_test_mutex")
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert p.stdout.strip() == "True False", (p.stdout, p.stderr)

@case
def bad_hotkey_is_rejected():
    ok = daemon.start_hotkey(None, "ctrl+alt+notakey")
    assert ok is False, "a hotkey with no key code must not report success"

@case
def hotkey_spec_parses_every_documented_key():
    combos = ["ctrl+alt+space", "ctrl+alt+f12", "win+shift+a", "ctrl+alt+z", "ctrl+alt+0"]
    for spec in combos:
        mods = key = None
        mods, key = 0, None
        for part in [p.strip().lower() for p in spec.split("+")]:
            if part in daemon.MOD_MAP:
                mods |= daemon.MOD_MAP[part]
            elif part in daemon.VK_MAP:
                key = daemon.VK_MAP[part]
        assert key is not None, spec
        assert mods, spec

@case
def hotkey_conflict_is_detected():
    """If another app owns the combo, registration must fail, not lie."""
    import ctypes
    user32 = ctypes.windll.user32
    MOD_ALT, MOD_CONTROL, VK_F9 = 0x1, 0x2, 0x78
    if not user32.RegisterHotKey(None, 99, MOD_CONTROL | MOD_ALT, VK_F9):
        print("      (could not grab ctrl+alt+f9 for the test; skipped)")
        return
    try:
        ok = daemon.start_hotkey(None, "ctrl+alt+f9")
        assert ok is False, "a taken hotkey must report failure"
    finally:
        user32.UnregisterHotKey(None, 99)

@case
def run_step_parses_json_and_reports_failure():
    tmp = tempfile.mkdtemp()
    ok_script = os.path.join(tmp, "ok_step.py")
    with open(ok_script, "w", encoding="utf-8") as fh:
        fh.write("import json; print('noise'); print(json.dumps({'new': 3}))\n")
    bad_script = os.path.join(tmp, "bad_step.py")
    with open(bad_script, "w", encoding="utf-8") as fh:
        fh.write("import sys; print('boom', file=sys.stderr); sys.exit(7)\n")

    obj = daemon.App.__new__(daemon.App)          # no Tk needed for this method
    daemon.HERE = tmp                              # keep the scripts under tmp
    rc, stats = obj._run_step("ok_step.py")
    assert rc == 0 and stats == {"new": 3}, (rc, stats)
    rc, stats = obj._run_step("bad_step.py")
    assert rc == 7 and stats == {}, (rc, stats)

@case
def daemon_without_index_logs_a_fatal_error():
    tmp = tempfile.mkdtemp(prefix="lsq-noidx-")
    sandbox = os.path.join(tmp, "proj")
    import shutil
    shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(sandbox, "scripts"))
    log_path = os.path.join(sandbox, "index", "daemon.log")
    env = dict(os.environ, LOCAL_SEARCH_MUTEX="LocalSearchDaemon_test_noidx")
    p = subprocess.run([sys.executable, "scripts/daemon.py"], cwd=sandbox, env=env,
                       capture_output=True, text=True, timeout=120)
    text = open(log_path, encoding="utf-8").read() if os.path.exists(log_path) else ""
    print("      exit=%s log=%r" % (p.returncode, text.strip()[:160]))
    assert "no index found" in text, "missing-index hint not logged"
    assert "fatal:" in text, "crash was not logged"
    shutil.rmtree(tmp, ignore_errors=True)

@case
def dense_failure_falls_back_to_keywords():
    """A broken embedding model must degrade to BM25, not break search."""
    import search_engine
    eng = search_engine.Engine(device="cpu")
    saved = dict(search_engine.MODELS["bge-small"])
    search_engine.MODELS["bge-small"] = {"dir": "models/does-not-exist",
                                         "tok": "models/does-not-exist/t.json",
                                         "pad": 0, "dim": 512}
    try:
        eng.embedder = None
        hits = eng.search("矩阵乘积态", k=3)
        assert hits, "keyword fallback returned nothing"
        assert eng.use_dense is False
        assert "dense_error" in dir(eng) and eng.dense_error
        print("      fallback hits:", [h["name"][:30] for h in hits])
    finally:
        search_engine.MODELS["bge-small"] = saved

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
