"""OCR the pages ingest reported as missing, a bounded batch at a time.

ingest.py writes index/ocr/pending.json with every scanned PDF and how many of
its pages still lack OCR text.  The daemon calls this after each scan, so OCR
makes progress in the background without a manual step.  Pages are written
atomically and the run is resumable: re-running simply picks up what is left.
"""
import argparse
import json
import os
import sys
import time

try:
    import psutil
except Exception:  # psutil is optional; the guards simply stay off without it
    psutil = None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
os.chdir(ROOT)

PENDING = os.path.join(ROOT, "index", "ocr", "pending.json")


def available_mb():
    if psutil is None:
        return None
    return psutil.virtual_memory().available // (1024 * 1024)


def max_worker_rss_mb(pool):
    """Largest resident memory of one pool worker, in MB.

    The budget is per worker: with N workers the total memory is naturally N
    times larger, so summing would abort every parallel batch as soon as two
    workers hold their models.
    """
    if psutil is None:
        return 0
    try:
        me = psutil.Process()
        rss = [p.memory_info().rss for p in me.children(recursive=True)
               if p.is_running()]
        return (max(rss) if rss else 0) // (1024 * 1024)
    except Exception:
        return 0


def load_pending():
    try:
        with open(PENDING, encoding="utf-8-sig") as fh:
            return json.load(fh).get("items", [])
    except Exception:
        return []


def clean_stale_tmp(max_age=3600.0):
    """A killed worker can leave a half-written page behind; sweep old ones."""
    now = time.time()
    pages = os.path.join(ROOT, "index", "ocr", "pages")
    removed = 0
    for dp, _dn, files in os.walk(pages):
        for name in files:
            if not name.endswith(".tmp"):
                continue
            path = os.path.join(dp, name)
            try:
                if now - os.path.getmtime(path) > max_age:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
    return removed


def stop_reason(max_worker_mb, allow_battery, rss_mb, on_ac_now, elapsed, max_seconds):
    """Why the current batch must stop ("" means keep going)."""
    if rss_mb > max_worker_mb:
        return f"worker memory {rss_mb} MB > {max_worker_mb} MB"
    if not allow_battery and not on_ac_now:
        return "unplugged, back on battery"
    if elapsed > max_seconds:
        return "time budget reached"
    return ""


def build_tasks(items):
    from ocr_run import out_path
    tasks = []
    for it in items:
        pdf = it.get("file", "")
        if not os.path.exists(pdf):
            continue
        for i in range(int(it.get("pages", 0))):
            if not os.path.exists(out_path(pdf, i)):
                tasks.append((pdf, i))
    return tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=120)
    ap.add_argument("--max-seconds", type=float, default=240.0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--threads", type=int, default=2)
    # rapidocr/openvino retain ~100 MB per page: recycle workers often and stop
    # the whole batch if a worker grows past the budget
    ap.add_argument("--max-tasks-per-child", type=int, default=3)
    ap.add_argument("--max-worker-mb", type=int, default=1100)
    ap.add_argument("--min-free-mb", type=int, default=1200)
    ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--allow-battery", action="store_true")
    ap.add_argument("--from-daemon", action="store_true",
                    help="set by the daemon so stop_daemon.py can find this process")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    items = load_pending()
    clean_stale_tmp()
    tasks = build_tasks(items)
    if args.list:
        print(json.dumps({"documents": len(items), "pages_left": len(tasks)}))
        return
    if not tasks:
        print(json.dumps({"documents": 0, "pages_done": 0, "pages_left": 0}))
        return

    try:
        import ocr_compat  # noqa: F401  openvino.runtime shim for rapidocr
        import rapidocr_openvino  # noqa: F401
    except Exception as exc:
        print(json.dumps({"error": f"rapidocr unavailable: {exc}", "pages_done": 0}))
        return

    free = available_mb()
    if free is not None and free < args.min_free_mb:
        print(json.dumps({"pages_done": 0, "skipped": "low memory",
                          "available_mb": free, "need_mb": args.min_free_mb}))
        return

    from power_state import on_ac, require_ac
    if not require_ac("OCR", args.allow_battery):
        sys.exit(2)

    import multiprocessing as mp
    from ocr_run import init_worker, ocr_one

    batch = tasks[: max(1, args.max_pages)]
    t0 = time.time()
    done = chars = 0
    peak = 0
    stopped = ""
    with mp.Pool(args.workers, initializer=init_worker,
                 initargs=(args.threads, args.dpi),
                 maxtasksperchild=max(1, args.max_tasks_per_child)) as pool:
        for _page, _dt, nchars in pool.imap_unordered(ocr_one, batch, chunksize=1):
            done += 1
            chars += nchars
            if done % 2 == 0:
                rss = max_worker_rss_mb(pool)
                peak = max(peak, rss)
                # unplugging must actually stop the work, not just block new
                # scans: a batch already running would otherwise keep going
                why = stop_reason(args.max_worker_mb, args.allow_battery, rss,
                                  on_ac(), time.time() - t0, args.max_seconds)
                if why:
                    stopped = why
                    pool.terminate()
                    break

    el = max(time.time() - t0, 1e-6)
    out = {"documents": len(items), "pages_done": done,
           "pages_left": len(tasks) - done, "chars": chars,
           "workers": args.workers,
           "seconds": round(el, 1),
           "pages_per_s": round(done / el, 2), "peak_mb": peak}
    if stopped:
        out["stopped"] = stopped
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
