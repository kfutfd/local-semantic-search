"""Poke the running daemon to show its window; start it first if needed."""
import ctypes
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENT = "LocalSearch_ShowEvent"
EVENT_MODIFY_STATE = 0x0002
CREATE_NO_WINDOW = 0x08000000
k32 = ctypes.windll.kernel32


def find_event():
    return k32.OpenEventW(EVENT_MODIFY_STATE, False, EVENT)


def start_daemon():
    pyw = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
    script = os.path.join(ROOT, "scripts", "daemon.py")
    subprocess.Popen([pyw, script], cwd=ROOT, creationflags=CREATE_NO_WINDOW,
                     close_fds=True)


def main():
    handle = find_event()
    if not handle:
        start_daemon()
        for _ in range(60):
            time.sleep(0.25)
            handle = find_event()
            if handle:
                break
    if not handle:
        print("daemon did not start; see index/daemon.log")
        return 1
    k32.SetEvent(handle)
    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
