"""One-command installer for Gesture Mouse.

    python install.py             install dependencies, start with Windows, launch it now
    python install.py --no-start  same, but do not launch it immediately
    python install.py --remove    remove the "start with Windows" entry

What it does:
  * creates a local .venv (if missing) so nothing is installed system-wide,
  * installs requirements.txt into that .venv,
  * drops a tiny silent launcher in the Windows Startup folder, so the app is
    already running at every login,
  * starts the app now (unless --no-start) - look for the tray icon.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
LAUNCHER_NAME = "Gesture Mouse.vbs"
APP = ROOT / "gesture_mouse.py"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise SystemExit("APPDATA is not set, so the Startup folder cannot be found.")
    return Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup"


def ensure_venv() -> Path:
    python = venv_python()
    if not python.exists():
        print(f"Creating {VENV.name} ...")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    return python


def install_requirements(python: Path) -> None:
    print("Installing dependencies (the first run downloads ~150 MB) ...")
    subprocess.check_call([str(python), "-m", "pip", "install", "--upgrade", "pip", "--quiet"])
    subprocess.check_call([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])


def write_launcher() -> Path:
    """Startup entry: pythonw.exe has no console, so nothing flashes at login."""
    pythonw = venv_python().with_name("pythonw.exe")
    launcher = startup_dir() / LAUNCHER_NAME
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        'Set shell = CreateObject("WScript.Shell")\n'
        f'shell.Run """{pythonw}"" ""{APP}""", 0, False\n',
        encoding="utf-8",
    )
    return launcher


def remove_launcher() -> bool:
    launcher = startup_dir() / LAUNCHER_NAME
    if launcher.exists():
        launcher.unlink()
        print(f"Removed autostart entry: {launcher}")
        return True
    print("No autostart entry was installed.")
    return False


def launch_now() -> None:
    pythonw = venv_python().with_name("pythonw.exe")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen([str(pythonw), str(APP)], cwd=str(ROOT), creationflags=flags, close_fds=True)
    print("Started Gesture Mouse - look for the tray icon.")


def main() -> int:
    if "--remove" in sys.argv:
        remove_launcher()
        return 0

    if os.name != "nt":
        print("This installer targets Windows. On macOS/Linux run the app directly:")
        print(f"  {venv_python()} {APP}")
        return 1

    if sys.version_info < (3, 9) or sys.version_info >= (3, 13):
        print(f"You are running Python {sys.version.split()[0]}; MediaPipe needs 3.9 - 3.12.")
        print("Install a compatible Python and run: py -3.12 install.py")
        return 1

    if not APP.exists():
        print(f"gesture_mouse.py is missing next to install.py ({APP}).")
        return 1

    python = ensure_venv()
    install_requirements(python)
    launcher = write_launcher()
    print(f"Autostart installed: {launcher}")

    if "--no-start" not in sys.argv:
        launch_now()

    print()
    print("Gestures: point to move, pinch to click, pinch and hold to drag,")
    print("thumb+middle pinch to right click, two fingers to scroll, fist to pause.")
    print("Right-click the tray icon to show the preview or quit.")
    print("Remove autostart any time with: python install.py --remove")
    return 0


if __name__ == "__main__":
    sys.exit(main())
