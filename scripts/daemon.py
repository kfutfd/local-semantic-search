"""Everything-style background daemon: global hotkey + instant search window.

Runs with pythonw (no console).  Idles at ~0 CPU, loads the embedding model in
the background at start, unloads it after 15 minutes of no searches, and scans
for new files only while plugged in.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
os.chdir(ROOT)

from search_engine import Engine  # noqa: E402

SETTINGS = os.path.join(ROOT, "settings.json")
LOG = os.path.join(ROOT, "index", "daemon.log")

DEFAULTS = {
    "hotkey": "ctrl+alt+space",
    "model": "bge-small",
    "device": "GPU",
    "roots": [os.path.join(os.path.expanduser("~"), "Desktop"),
              os.path.join(os.path.expanduser("~"), "Downloads")],
    "scan_interval_min": 10,
    "idle_unload_min": 15,
    "max_results": 10,
    "window_width": 1040,
    "window_height": 680,
    "ocr": True,
    "ocr_pages_per_run": 120,
    "ocr_workers": 1,
    "ocr_max_worker_mb": 1100,
}

MOD_MAP = {"ctrl": 0x0002, "control": 0x0002, "alt": 0x0001,
           "shift": 0x0004, "win": 0x0008}
VK_MAP = {"space": 0x20, "`": 0xC0, "/": 0xBF, "\"": 0xDE}
# every letter, digit and F-key is usable in settings.json
VK_MAP.update({chr(ord("a") + i): 0x41 + i for i in range(26)})
VK_MAP.update({str(d): 0x30 + d for d in range(10)})
VK_MAP.update({f"f{i}": 0x6F + i for i in range(1, 13)})
WM_HOTKEY = 0x0312
SHOW_EVENT = "LocalSearch_ShowEvent"


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > 5 * 1024 * 1024:
            os.replace(LOG, LOG + ".1")   # one old generation is enough
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:
        pass


def load_settings():
    cfg = dict(DEFAULTS)
    if os.path.exists(SETTINGS):
        try:
            with open(SETTINGS, encoding="utf-8-sig") as fh:
                cfg.update(json.load(fh))
        except Exception as exc:
            log(f"settings.json unreadable ({exc}); falling back to defaults")
    return cfg


def on_ac():
    class _PS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

    st = _PS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
        return True
    return st.ACLineStatus == 1


_K32 = ctypes.WinDLL("kernel32", use_last_error=True)
_MUTEX = []  # keep the handle alive for the process lifetime
MUTEX_NAME = os.environ.get("LOCAL_SEARCH_MUTEX", "LocalSearchDaemon_v1")


def single_instance():
    """Return a mutex handle or None when another daemon is already running."""
    handle = _K32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return None
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return None
    _MUTEX.append(handle)
    return handle


def no_window_flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class App:
    def __init__(self, cfg, show_now=False):
        self.cfg = cfg
        self.engine = Engine(model=cfg["model"], device=cfg["device"],
                             idle_unload=int(cfg["idle_unload_min"] * 60),
                             mode=cfg.get("mode", "semantic"))
        self.results = []
        self.q = queue.Queue()
        self.scanning = False
        self.last_scan = time.time()
        self.visible = False
        self.pending = 0
        self._rebuild_pending = False
        self._dense_warned = False
        self._build_ui()
        if show_now:
            self.show()
        self.start_housekeeping()
        if cfg.get("mode", "semantic") != "lite":
            threading.Thread(target=self.engine.warm, daemon=True).start()
        log(f"daemon started: {len(self.engine.rows)} rows, mode={cfg.get('mode', 'semantic')}, hotkey={cfg['hotkey']}")

    # ---------- UI ----------
    def _build_ui(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        bg, panel, fg, muted, accent = "#0f1115", "#171a21", "#e6e9ef", "#9aa4b2", "#2b4a7a"
        self.colors = dict(bg=bg, panel=panel, fg=fg, muted=muted, accent=accent)

        outer = tk.Frame(self.root, bg=accent, bd=0)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=bg, bd=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        self.entry = tk.Entry(inner, font=("Microsoft YaHei UI", 15), bg=panel, fg=fg,
                              insertbackground=fg, relief="flat", bd=11)
        self.entry.pack(fill="x", padx=12, pady=(12, 6))
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<Escape>", lambda e: self.hide())
        self.entry.bind("<Down>", lambda e: self._nav(1))
        self.entry.bind("<KeyRelease>", self._on_key)

        wrap = tk.Frame(inner, bg=bg)
        wrap.pack(fill="both", expand=True, padx=12, pady=(2, 0))

        self.text = tk.Text(wrap, font=("Microsoft YaHei UI", 10), bg=bg, fg=fg, bd=0,
                            relief="flat", highlightthickness=0, wrap="char", cursor="arrow",
                            width=1, height=1, padx=4, pady=2,
                            spacing1=6, spacing2=2, spacing3=2,
                            selectbackground=bg, insertwidth=0)
        self.text.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(wrap, command=self.text.yview, bd=0, relief="flat", width=10,
                          bg=bg, troughcolor=bg, activebackground=accent, highlightthickness=0)
        sb.pack(side="right", fill="y")
        self.text.config(yscrollcommand=sb.set, state="disabled")

        self.text.tag_configure("name", foreground=fg, font=("Microsoft YaHei UI", 10, "bold"))
        self.text.tag_configure("meta", foreground="#8b95a5", font=("Microsoft YaHei UI", 9))
        self.text.tag_configure("snip", foreground="#c8d0dd", lmargin1=14, lmargin2=14)
        self.text.tag_configure("sel", background="#22344f")
        self.text.tag_configure("hl", foreground="#ffd76e", font=("Microsoft YaHei UI", 10, "bold"))
        self.text.tag_configure("bar", foreground="#4a7dd6", font=("Microsoft YaHei UI", 10, "bold"))
        self.text.tag_configure("empty", foreground=muted, font=("Microsoft YaHei UI", 10))

        self.text.bind("<Button-1>", self._on_click)
        self.text.bind("<Double-Button-1>", self._on_enter)
        self.text.bind("<Up>", lambda e: self._nav(-1))
        self.text.bind("<Down>", lambda e: self._nav(1))
        self.text.bind("<Return>", self._on_enter)
        self.text.bind("<Escape>", lambda e: self.hide())

        self.hint = tk.Label(inner, text="↑↓ 选择 · Enter 打开 · Esc 隐藏",
                             bg=bg, fg=muted, font=("Microsoft YaHei UI", 9), anchor="w")
        self.hint.pack(fill="x", padx=14, pady=(6, 0))
        self.status = tk.Label(inner, text="", bg=bg, fg=muted,
                               font=("Microsoft YaHei UI", 9), anchor="w")
        self.status.pack(fill="x", padx=14, pady=(0, 10))

        self.root.bind("<Escape>", lambda e: self.hide())
        self.spans = []          # [(start_index, end_index)] per result
        self.selected = -1

    def place_window(self):
        """Request a physical size that fits the screen (Tk units are DPI-scaled)."""
        u32 = ctypes.windll.user32
        try:
            dpi = u32.GetDpiForSystem()
        except Exception:
            dpi = 96
        scale = (dpi / 96.0) if dpi else 1.0
        sw_phys, sh_phys = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
        w_phys = min(int(self.cfg.get("window_width", 1040)), sw_phys - 40)
        h_phys = min(int(self.cfg.get("window_height", 680)), sh_phys - 80)
        w, h = int(w_phys / scale), int(h_phys / scale)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w, h = min(w, sw), min(h, sh)
        x = max(0, (sw - w) // 2)
        y = max(10, (sh - h) // 3)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def show(self):
        self.place_window()
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.entry.focus_force()
        self.entry.select_range(0, "end")
        self.visible = True
        if self.entry.get().strip() == "":
            self._clear_results()

    def hide(self):
        self.root.withdraw()
        self.visible = False

    def toggle(self):
        if self.visible:
            self.hide()
        else:
            self.show()

    # ---------- events ----------
    def _on_key(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape"):
            return
        self.root.after(120, self._kick_search)

    def _kick_search(self):
        text = self.entry.get().strip()
        if not text:
            self._clear_results()
            self.status.config(text="")
            return
        self.pending += 1
        threading.Thread(target=self._search_worker, args=(text,), daemon=True).start()

    def _search_worker(self, text):
        try:
            res = self.engine.search(text, k=int(self.cfg.get("max_results", 10)))
            err = getattr(self.engine, "dense_error", "")
            if err and not self._dense_warned:
                self._dense_warned = True
                log(f"dense search disabled, keyword-only mode: {err}")
            self.q.put(("results", text, res, self.engine.elapsed_ms))
        except Exception as exc:  # keep the daemon alive no matter what
            log(f"search failed: {exc}")
            self.q.put(("error", text, str(exc), 0))

    def _poll(self):
        try:
            while True:
                kind, text, payload, ms = self.q.get_nowait()
                self.pending = max(0, self.pending - 1)
                if kind == "error":
                    self.status.config(text=f"搜索出错：{payload}")
                    continue
                if text != self.entry.get().strip():
                    continue
                try:
                    self._render(payload, ms)
                except Exception as exc:
                    log(f"render failed: {exc}")
        except queue.Empty:
            pass
        self._poll_after = self.root.after(60 if self.pending else 350, self._poll)

    def _clear_results(self):
        self.results = []
        self.spans = []
        self.selected = -1
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.config(state="disabled")

    def _render(self, results, ms):
        self.results = results
        self.spans = []
        self.selected = -1
        self.card_tags = []
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        if not results:
            self.text.insert("end", "没有找到相关内容\n", "empty")
        for i, r in enumerate(results):
            card = f"card{i}"
            shade = "#24405f"
            self.text.tag_configure(card, background=shade, lmargin1=12, lmargin2=12,
                                    rmargin=10, spacing1=9, spacing3=9)
            self.card_tags.append((card, shade))
            start = self.text.index("end-1c")
            tag = "[OCR] " if r.get("ocr") else ""
            label = r.get("label") or str(r["page"])
            where = f"p.{label}"
            if str(label) != str(r["page"]):
                where += f"（PDF {r['page']}）"
            self.text.insert("end", "▌ ", (card, "bar"))
            self.text.insert("end", tag + r["name"], (card, "name"))
            self.text.insert("end", "   " + where + "\n", (card, "meta"))
            pos = self.text.index("end-1c")
            self.text.insert("end", r.get("snippet", ""), (card, "snip"))
            for term in self._terms(self.entry.get().strip()):
                idx = pos
                while True:
                    found = self.text.search(term, idx, stopindex="end", nocase=True)
                    if not found:
                        break
                    self.text.tag_add("hl", found, f"{found}+{len(term)}c")
                    idx = f"{found}+{len(term)}c"
            self.text.insert("end", "\n", (card,))
            end = self.text.index("end-1c")
            self.spans.append((start, end))
            self.text.insert("end", "\n")
        self.text.tag_raise("sel")
        self.text.tag_raise("hl")
        self.text.config(state="disabled")
        if results:
            self._select(0)
            self.status.config(text=f"{len(results)} 条 · {ms} ms")
            log(f"render {len(results)} results in {ms}ms")
        else:
            self.status.config(text="没有找到相关内容")

    @staticmethod
    def _terms(query):
        return [t for t in re.split(r"\s+", query) if len(t) >= 2][:6]

    def _select(self, i):
        if not self.spans:
            return
        i = max(0, min(i, len(self.spans) - 1))
        for j, (tag, shade) in enumerate(getattr(self, "card_tags", [])):
            self.text.tag_configure(tag, background="#356093" if j == i else shade)
        start, end = self.spans[i]
        self.selected = i
        self.text.see(start)

    def _nav(self, delta):
        if not self.spans:
            return "break"
        self._select(self.selected + delta if self.selected >= 0 else 0)
        self.text.focus_set()
        return "break"

    def _on_click(self, event):
        idx = self.text.index(f"@{event.x},{event.y}")
        for i, (start, end) in enumerate(self.spans):
            if self.text.compare(idx, ">=", start) and self.text.compare(idx, "<", end):
                self._select(i)
                self.text.focus_set()
                break

    def _on_select(self, event=None):
        pass

    def _on_enter(self, event=None):
        if not self.results:
            return "break"
        i = self.selected if self.selected >= 0 else 0
        r = self.results[i]
        try:
            os.startfile(r["file"])
            log(f"open: {r['file']} p.{r['page']}")
        except Exception as exc:
            log(f"open failed: {exc}")
        self.hide()
        return "break"

    # ---------- housekeeping ----------
    def start_housekeeping(self):
        self._poll_after = self.root.after(100, self._poll)
        threading.Thread(target=self._housekeeping, daemon=True).start()

    def _housekeeping(self):
        interval = max(1, int(self.cfg["scan_interval_min"])) * 60
        allow_battery = bool(self.cfg.get("scan_on_battery", False))
        while True:
            time.sleep(30)
            try:
                self.engine.maybe_unload()
                if self.scanning:
                    continue
                if not (on_ac() or allow_battery):
                    continue
                if time.time() - self.last_scan < interval:
                    continue
                self.scanning = True
                self.last_scan = time.time()
                threading.Thread(target=self._scan, daemon=True).start()
            except Exception as exc:
                log(f"housekeeping: {exc}")

    def _run_step(self, script, *args):
        """Run one pipeline step; return (returncode, parsed JSON stats)."""
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        out = subprocess.run([sys.executable, os.path.join(HERE, script), *args],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", env=env,
                             creationflags=no_window_flags(), cwd=ROOT)
        lines = (out.stdout or "").strip().splitlines()
        stats = {}
        if lines and lines[-1].startswith("{"):
            try:
                stats = json.loads(lines[-1])
            except Exception:
                log(f"{script}: bad stats line: {lines[-1][:120]}")
        if out.returncode != 0:
            log(f"{script} rc={out.returncode}: {(out.stderr or '').strip()[-300:]}")
        return out.returncode, stats

    def _scan(self):
        try:
            rc, stats = self._run_step("ingest.py", "--roots", *self.cfg["roots"])
            changed = (stats.get("new", 0) + stats.get("updated", 0)
                       + stats.get("removed", 0))
            log(f"scan: ingest rc={rc} changed={changed} "
                f"pending_ocr={stats.get('ocr_pending', 0)} "
                f"pages_missing={stats.get('ocr_pages_missing', 0)}")

            if self.cfg.get("ocr", True) and stats.get("ocr_pages_missing", 0) > 0:
                if on_ac() or self.cfg.get("scan_on_battery", False):
                    args = ["--max-pages", str(int(self.cfg.get("ocr_pages_per_run", 120))),
                            "--workers", str(int(self.cfg.get("ocr_workers", 1))),
                            "--max-worker-mb", str(int(self.cfg.get("ocr_max_worker_mb", 1100))),
                            "--from-daemon"]
                    if self.cfg.get("scan_on_battery", False):
                        args.append("--allow-battery")
                    log("scan: ocr start")
                    _rc, ocr = self._run_step("ocr_auto.py", *args)
                    if ocr.get("error"):
                        log(f"scan: ocr unavailable: {ocr['error']}")
                    elif ocr.get("skipped"):
                        log(f"scan: ocr skipped ({ocr['skipped']}, "
                            f"free={ocr.get('available_mb')}MB)")
                    log(f"scan: ocr {int(ocr.get('pages_done', 0))} pages, "
                        f"{ocr.get('pages_left', '?')} left, "
                        f"peak={ocr.get('peak_mb', '?')}MB"
                        + (f" ({ocr['stopped']})" if ocr.get("stopped") else ""))
                    if int(ocr.get("pages_done", 0)) > 0:
                        _rc, stats = self._run_step("ingest.py", "--roots", *self.cfg["roots"])
                        changed += (stats.get("new", 0) + stats.get("updated", 0)
                                    + stats.get("removed", 0))
                        log(f"scan: ingest after ocr changed={changed}")

            if changed <= 0 and not self._rebuild_pending:
                return
            if not (on_ac() or self.cfg.get("scan_on_battery", False)):
                log("scan: power lost mid-cycle; leaving the rebuild for the next AC run")
                return
            log("scan: embed")
            # Windows keeps the mmap'd index locked: let go for the rebuild only
            self.engine.close()
            try:
                embed_args = ["--model", self.cfg["model"],
                              "--device", self.cfg.get("device", "auto")]
                if self.cfg.get("scan_on_battery", False):
                    # the scan was allowed on battery, so embedding must be too;
                    # otherwise OCR results never reach the index
                    embed_args.append("--allow-battery")
                rc, _ = self._run_step("embed_docs.py", *embed_args)
                if rc != 0:
                    # publishing now would silently drop the documents we could
                    # not embed; keep the old index and retry next cycle
                    self._rebuild_pending = True
                    log("scan: embed failed; keeping the current index, will retry")
                    return
                log("scan: build_all")
                rc, _ = self._run_step("build_all.py", "--model", self.cfg["model"])
                if rc != 0:
                    self._rebuild_pending = True
                    log(f"scan: build_all failed rc={rc}; keeping the current index, will retry")
                    return
                self._rebuild_pending = False
            finally:
                self.engine.reload()
            log(f"scan: reloaded rows={len(self.engine.rows)}")
            self.root.after(0, self._kick_search)
        except Exception as exc:
            log(f"scan failed: {exc}")
        finally:
            self.scanning = False


def start_show_event(app):
    """Let other programs (shortcuts, scripts) pop the window."""
    k32 = ctypes.windll.kernel32
    ev = k32.CreateEventW(None, False, False, SHOW_EVENT)
    if not ev:
        return

    def loop():
        while True:
            if k32.WaitForSingleObject(ev, 0xFFFFFFFF) == 0:
                log('show event received')
                app.root.after(0, app.show)

    threading.Thread(target=loop, daemon=True).start()


def start_hotkey(app, spec):
    """Register and pump the hotkey in the SAME thread (WM_HOTKEY is thread-queued)."""
    user32 = ctypes.windll.user32
    mods = 0
    key = None
    for part in [p.strip().lower() for p in spec.split("+")]:
        if part in MOD_MAP:
            mods |= MOD_MAP[part]
        elif part in VK_MAP:
            key = VK_MAP[part]
    if key is None:
        log(f"bad hotkey: {spec}")
        return False

    ready = threading.Event()
    ok = {"value": False}

    def loop():
        if not user32.RegisterHotKey(None, 1, mods, key):
            log(f"RegisterHotKey failed for {spec} (already used by another app?)")
            ready.set()
            return
        ok["value"] = True
        ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                log("hotkey pressed")
                app.root.after(0, app.toggle)

    threading.Thread(target=loop, daemon=True).start()
    ready.wait(3)
    return ok["value"]


def main():
    """Entry point: never die silently -- pythonw has no console to complain in."""
    try:
        run_daemon()
    except Exception:
        import traceback
        detail = traceback.format_exc().strip()
        log("fatal: " + " | ".join(detail.splitlines()))
        try:
            if sys.stdout is None:  # pythonw: there is no console to explain it
                ctypes.windll.user32.MessageBoxW(
                    None,
                    "本地语义搜索启动失败。\n\n" + detail.splitlines()[-1][:300] +
                    "\n\n详细信息见 index\\daemon.log",
                    "本地语义搜索", 0x10)
        except Exception:
            pass
        raise


def run_daemon():
    cfg = load_settings()
    show_now = "--show" in sys.argv
    no_hotkey = "--no-hotkey" in sys.argv
    selftest = "--selftest" in sys.argv
    demo = ""
    if "--demo" in sys.argv:
        i = sys.argv.index("--demo")
        demo = sys.argv[i + 1] if i + 1 < len(sys.argv) else "量子场论"
    if not single_instance():
        log("another instance is running; exiting")
        return
    if not os.path.exists(os.path.join(ROOT, "index", "all", "meta.json")):
        log("no index found in index/all -- run rebuild-index.cmd; trying to start anyway")
    app = App(cfg, show_now=show_now and not selftest)
    if selftest:
        for q in ["模电 负反馈", "矩阵乘积态"]:
            res = app.engine.search(q, k=3)
            print(f"[selftest] {q}: {len(res)} hits, {app.engine.elapsed_ms} ms")
            for r in res[:2]:
                print("   ", r["name"], "p.", r["page"])
        app.root.destroy()
        return
    if demo:
        app.show()
        app.entry.insert(0, demo)
        app._kick_search()
    start_show_event(app)
    if not no_hotkey:
        if not start_hotkey(app, cfg["hotkey"]):
            log("hotkey unavailable; window only opens via manual start")
    app.root.mainloop()


if __name__ == "__main__":
    main()

