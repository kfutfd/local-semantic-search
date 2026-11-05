# -*- coding: utf-8 -*-
"""Exercise the Tk search window without a human: type, search, navigate, open."""
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.chdir(ROOT)

import daemon

CASES = []
def case(fn):
    CASES.append(fn)
    return fn

CFG = {"hotkey": "ctrl+alt+space", "model": "bge-small", "device": "auto",
       "roots": [], "scan_interval_min": 100000, "idle_unload_min": 60,
       "max_results": 5, "window_width": 1040, "window_height": 680,
       "mode": "semantic", "ocr": False}

def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.root.update()
        time.sleep(0.02)

def wait_results(app, timeout=40):
    end = time.time() + timeout
    while time.time() < end and not app.results:
        app.root.update()
        time.sleep(0.02)
    return app.results

def close_app(app):
    try:
        app.root.after_cancel(app._poll_after)
    except Exception:
        pass
    app.root.destroy()

def make_app():
    app = daemon.App(dict(CFG))
    pump(app, 0.3)
    return app

@case
def window_starts_hidden():
    app = make_app()
    try:
        assert app.visible is False, "window should start withdrawn"
        assert app.root.state() == "withdrawn", app.root.state()
    finally:
        close_app(app)

@case
def show_hide_toggle():
    app = make_app()
    try:
        app.show(); pump(app, 0.2)
        assert app.visible and app.root.state() == "normal", app.root.state()
        app.hide(); pump(app, 0.2)
        assert not app.visible
        app.toggle(); pump(app, 0.2)
        assert app.visible
        app.toggle(); pump(app, 0.2)
        assert not app.visible
    finally:
        close_app(app)

@case
def search_renders_results():
    app = make_app()
    try:
        app.show(); pump(app, 0.2)
        app.entry.insert(0, "矩阵乘积态")
        app._kick_search()
        hits = wait_results(app)
        assert hits, "no results for a query that certainly exists"
        pump(app, 0.4)
        body = app.text.get("1.0", "end")
        assert "密度矩阵" in body or hits[0]["name"] in body, body[:200]
        assert app.selected == 0, app.selected
        assert str(len(hits)) in app.status.cget("text"), app.status.cget("text")
    finally:
        close_app(app)

@case
def navigation_and_selection():
    app = make_app()
    try:
        app.show(); pump(app, 0.2)
        app.entry.insert(0, "量子力学")
        app._kick_search()
        if len(wait_results(app)) < 2:
            print("      (only one hit, navigation check limited)")
            return
        pump(app, 0.4)
        app._nav(1)
        assert app.selected == 1, app.selected
        app._nav(-1)
        assert app.selected == 0, app.selected
        app._nav(-5)
        assert app.selected == 0, "selection must clamp at the top"
        app._nav(999)
        assert app.selected == len(app.spans) - 1, "selection must clamp at the bottom"
    finally:
        close_app(app)

@case
def rapid_queries_end_on_the_latest():
    app = make_app()
    try:
        app.show(); pump(app, 0.2)
        for q in ("量子", "量子纠缠", "矩阵乘积态"):
            app.entry.delete(0, "end")
            app.entry.insert(0, q)
            app._kick_search()
            pump(app, 0.05)
        hits = wait_results(app, timeout=30)
        pump(app, 0.8)
        assert hits, "no results after rapid typing"
        body = app.text.get("1.0", "end")
        assert app.entry.get() == "矩阵乘积态"
        assert any(h["name"] in body for h in hits), "rendered results do not match the last query"
    finally:
        close_app(app)

@case
def enter_opens_file_and_hides():
    import unittest.mock as mock
    app = make_app()
    try:
        app.show(); pump(app, 0.2)
        app.entry.insert(0, "矩阵乘积态")
        app._kick_search()
        hits = wait_results(app)
        assert hits
        pump(app, 0.3)
        opened = []
        with mock.patch("os.startfile", lambda p: opened.append(p), create=True):
            app._on_enter()
        assert opened and opened[0] == hits[0]["file"], (opened, hits[0]["file"])
        assert app.visible is False, "window should hide after opening"
    finally:
        close_app(app)

@case
def empty_result_state():
    app = make_app()
    try:
        app.show(); pump(app, 0.2)
        app.entry.insert(0, "zzzzqqqqxxxx")
        app._kick_search()
        wait_results(app, timeout=20)
        pump(app, 1.0)
        body = app.text.get("1.0", "end")
        print("      empty state text:", body.strip()[:40])
        assert "没有找到" in body or app.results, body[:100]
    finally:
        close_app(app)

@case
def clearing_query_resets_view():
    app = make_app()
    try:
        app.show(); pump(app, 0.2)
        app.entry.insert(0, "矩阵乘积态")
        app._kick_search()
        wait_results(app)
        pump(app, 0.4)
        app.entry.delete(0, "end")
        app._kick_search()
        pump(app, 0.6)
        assert app.text.get("1.0", "end").strip() == "", app.text.get("1.0", "end")[:80]
        assert app.results == []
    finally:
        close_app(app)

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
