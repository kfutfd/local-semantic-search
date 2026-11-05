"""Tiny Win32 power-state helper (no external deps)."""
import ctypes
import sys


class _PowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def status():
    st = _PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
        return {"ac": None, "percent": None}
    return {"ac": st.ACLineStatus == 1,
            "percent": st.BatteryLifePercent if st.BatteryLifePercent != 255 else None}


def on_ac():
    return status()["ac"]


def require_ac(task, allow_battery=False):
    """Heavy jobs should run while plugged in; refuse otherwise unless allowed."""
    st = status()
    if st["ac"] is None:
        print("[power] could not read power state, continuing", flush=True)
        return True
    if st["ac"]:
        print(f"[power] on AC (battery {st['percent']}%)", flush=True)
        return True
    if allow_battery:
        print(f"[power] WARNING running on battery ({st['percent']}%)", flush=True)
        return True
    print(f"[power] refusing to run '{task}' on battery ({st['percent']}%). "
          f"Plug in the charger, or pass --allow-battery.", flush=True)
    return False


if __name__ == "__main__":
    print(status())
    sys.exit(0 if on_ac() else 1)
