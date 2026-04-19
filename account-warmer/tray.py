"""
Account Warmer — System Tray Launcher
======================================
Run this file to get a tray icon that starts / stops the dashboard server.

  python tray.py

Double-click the tray icon  → open dashboard in browser
Right-click → Start Server  → starts api/main.py (uvicorn on port 8000)
Right-click → Stop Server   → kills the server process
Right-click → Exit          → stops server (if running) and quits the tray app

Icon colour:
  Green  — server is running
  Grey   — server is stopped / not yet started
  Amber  — server is starting up

Requirements (install once):
  pip install pystray pillow
"""

import subprocess
import sys
import time
import threading
import webbrowser
from pathlib import Path

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    print("Missing dependencies. Run:  pip install pystray pillow")
    sys.exit(1)

# ── Paths ──────────────────────────────────────────────────────────────────────

ROOT       = Path(__file__).parent
MAIN_PY    = ROOT / "api" / "main.py"
DASHBOARD  = "http://localhost:8000"

# ── State ──────────────────────────────────────────────────────────────────────

_proc: subprocess.Popen | None = None
_lock = threading.Lock()
_user_stopped = False   # True only when the user deliberately stopped the server
                        # Prevents the watchdog from restarting after a manual stop


# ── Icon drawing ───────────────────────────────────────────────────────────────

def _make_icon(colour: str) -> Image.Image:
    """Draw a 64×64 circle in the given colour."""
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 6
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=colour,
        outline="#ffffff",
        width=2,
    )
    return img


ICON_GREY  = _make_icon("#484f58")   # stopped
ICON_GREEN = _make_icon("#3fb950")   # running
ICON_AMBER = _make_icon("#d29922")   # starting


# ── Server control ─────────────────────────────────────────────────────────────

def _is_running() -> bool:
    with _lock:
        return _proc is not None and _proc.poll() is None


def _start(icon: pystray.Icon) -> None:
    global _proc, _user_stopped
    if _is_running():
        return

    _user_stopped = False
    icon.icon  = ICON_AMBER
    icon.title = "Account Warmer — Starting…"

    with _lock:
        _proc = subprocess.Popen(
            [sys.executable, str(MAIN_PY)],
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )

    # Wait up to 12 s for the server to accept connections, then mark green
    # (startup now includes a GeelarK phone cleanup call so takes a few seconds longer)
    def _wait_ready():
        import urllib.request
        for _ in range(24):
            time.sleep(0.5)
            try:
                urllib.request.urlopen(DASHBOARD, timeout=1)
                break
            except Exception:
                pass
        _refresh(icon)

    threading.Thread(target=_wait_ready, daemon=True).start()


def _stop(icon: pystray.Icon) -> None:
    global _proc, _user_stopped
    _user_stopped = True   # tell watchdog this was intentional
    with _lock:
        if _proc and _proc.poll() is None:
            _proc.send_signal(__import__("signal").CTRL_BREAK_EVENT)
            try:
                _proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _proc.kill()
        _proc = None
    _refresh(icon)


def _watchdog_loop(icon: pystray.Icon) -> None:
    """
    Background thread — monitors the server process and restarts it if it crashes.

    Only restarts when the process exits unexpectedly (i.e. _user_stopped is False).
    After a crash the server startup cleanup will stop any orphaned GeelarK phones,
    and the scheduler will resume any pending desktop warming sessions automatically.
    """
    global _proc
    while True:
        time.sleep(10)
        if _user_stopped:
            continue
        # If we had a process but it has now exited → crash detected
        with _lock:
            crashed = _proc is not None and _proc.poll() is not None
        if crashed:
            icon.icon  = ICON_AMBER
            icon.title = "Account Warmer — Crashed, restarting…"
            time.sleep(5)   # brief pause so rapid crash loops don't hammer the system
            _start(icon)


def _refresh(icon: pystray.Icon) -> None:
    if _is_running():
        icon.icon  = ICON_GREEN
        icon.title = "Account Warmer — Running  ●  http://localhost:8000"
    else:
        icon.icon  = ICON_GREY
        icon.title = "Account Warmer — Stopped"
    icon.update_menu()


# ── Menu ───────────────────────────────────────────────────────────────────────

def _menu(icon: pystray.Icon) -> pystray.Menu:
    running = _is_running()
    return pystray.Menu(
        pystray.MenuItem(
            "● Running" if running else "○ Stopped",
            None,
            enabled=False,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Open Dashboard",
            lambda icon, item: webbrowser.open(DASHBOARD),
            enabled=running,
            default=True,        # double-click triggers this
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Start Server",
            lambda icon, item: threading.Thread(target=_start, args=(icon,), daemon=True).start(),
            enabled=not running,
        ),
        pystray.MenuItem(
            "Stop Server",
            lambda icon, item: threading.Thread(target=_stop, args=(icon,), daemon=True).start(),
            enabled=running,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Exit",
            _exit,
        ),
    )


def _exit(icon: pystray.Icon, item=None) -> None:
    _stop(icon)
    icon.stop()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    icon = pystray.Icon(
        name="account-warmer",
        icon=ICON_GREY,
        title="Account Warmer — Stopped",
        menu=pystray.Menu(lambda: _menu(icon)),
    )

    # Auto-start the server as soon as the tray icon appears
    threading.Thread(
        target=lambda: (time.sleep(0.5), _start(icon)),
        daemon=True,
    ).start()

    # Watchdog — restarts the server automatically if it crashes
    threading.Thread(target=_watchdog_loop, args=(icon,), daemon=True).start()

    icon.run()
