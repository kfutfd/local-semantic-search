"""Create settings.json with this machine's Desktop/Downloads paths."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(ROOT, "settings.json")

DEFAULTS = {
    "hotkey": "ctrl+alt+space",
    "model": "bge-small",
    "device": "auto",
    "roots": [os.path.join(os.path.expanduser("~"), "Desktop"),
              os.path.join(os.path.expanduser("~"), "Downloads")],
    "scan_interval_min": 10,
    "idle_unload_min": 10,
    "mode": "semantic",
    "scan_on_battery": False,
    "ocr": True,
    "ocr_pages_per_run": 120,
    "ocr_workers": 1,
    "ocr_max_worker_mb": 1100,
    "max_results": 10,
    "window_width": 1040,
    "window_height": 680,
}


def main():
    force = "--force" in sys.argv
    if os.path.exists(SETTINGS) and not force:
        print("settings.json already exists")
        return
    with open(SETTINGS, "w", encoding="utf-8") as fh:
        json.dump(DEFAULTS, fh, ensure_ascii=False, indent=1)
    print("wrote", SETTINGS)
    for r in DEFAULTS["roots"]:
        print("  root:", r, "exists" if os.path.isdir(r) else "MISSING")


if __name__ == "__main__":
    main()
