"""Stop only our daemon (never other pythonw processes such as CapsWriter)."""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# match on the project path as well as the script name: another project's
# daemon.py must never be killed by this script
# the daemon itself, plus the OCR worker it spawns (marked with --from-daemon);
# a manual ocr-scan.cmd run has no marker and is left alone
PS = (
    "$root = '" + ROOT.replace("'", "''") + "'; "
    "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
    "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($root) -and "
    "($_.CommandLine.Contains('daemon.py') -or $_.CommandLine.Contains('--from-daemon')) } | "
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
)

if __name__ == "__main__":
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-Command", PS], capture_output=True)
    print("daemon stopped (if it was running)")
