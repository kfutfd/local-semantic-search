"""Download a fixed list of files from a HF repo through the local proxy."""
import json
import os
import sys
import urllib.request

os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:7897")
os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:7897")

REPO = sys.argv[1]
DEST = sys.argv[2]
FILES = sys.argv[3:]

os.makedirs(DEST, exist_ok=True)
for rel in FILES:
    dst = os.path.join(DEST, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    url = f"https://huggingface.co/{REPO}/resolve/main/{rel}"
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        print(f"[skip] {rel} {os.path.getsize(dst)}", flush=True)
        continue
    tmp = dst + ".part"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as fh:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
            os.replace(tmp, dst)
            print(f"[ ok ] {rel} {os.path.getsize(dst) / (1 << 20):.1f} MB", flush=True)
            break
        except Exception as exc:
            print(f"[retry {attempt + 1}] {rel}: {exc}", flush=True)
    else:
        raise SystemExit(f"failed: {rel}")
