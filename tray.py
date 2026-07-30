"""
Account Warmer — System Tray Launcher
======================================
Run this file to get a tray icon that starts / stops the dashboard server
and manages the daily mobile warm-up batch runner.

  python tray.py

Double-click the tray icon  → open dashboard in browser
Right-click → Start Server  → starts api/main.py (uvicorn, port from SERVER_PORT env or 8000)
Right-click → Stop Server   → kills the server process
Right-click → Start Daily Mobile Batch  → launches daily_mobile_batch_runner.py --daemon
Right-click → Stop Daily Mobile Batch   → terminates the batch runner
Right-click → Exit          → stops server + batch (if running) and quits the tray app

Icon colour:
  Green  — server is running
  Grey   — server is stopped / not yet started
  Amber  — daily mobile batch is running

Requirements (install once):
  pip install pystray pillow
"""

import os
import subprocess
import sys
import time
import threading
import webbrowser
import logging
from pathlib import Path

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    print("Missing dependencies. Run:  pip install pystray pillow")
    sys.exit(1)

# ── Logging ────────────────────────────────────────────────────────────────────

LOG_PATH = Path(__file__).parent.parent / "WarmingData" / "logs" / "tray.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("tray")

# ── Single-instance guard ──────────────────────────────────────────────────────

try:
    import ctypes
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "AccountWarmerTrayMutex")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(0,
            "Account Warmer is already running.\n\nCheck the system tray for the existing icon.",
            "Account Warmer", 0x40)  # MB_ICONINFORMATION
        sys.exit(0)
except Exception:
    pass  # If the mutex check fails for any reason, allow startup

# ── Load warmer.env ────────────────────────────────────────────────────────────

_env_file = Path(__file__).parent / "warmer.env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# ── Paths ──────────────────────────────────────────────────────────────────────

ROOT            = Path(__file__).parent
MAIN_PY         = ROOT / "api" / "main.py"
BATCH_RUNNER    = ROOT / "daily_mobile_batch_runner.py"
_port           = os.environ.get("SERVER_PORT", "8000")
DASHBOARD       = f"http://localhost:{_port}"

# ── State ──────────────────────────────────────────────────────────────────────

_proc: subprocess.Popen | None = None       # dashboard server process
_batch_proc: subprocess.Popen | None = None  # daily mobile batch process
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


# ── Port helpers ────────────────────────────────────────────────────────────────

SERVER_PORT = os.environ.get("SERVER_PORT", "8000")

def _kill_server_port() -> str:
    """Kill ANY process holding the configured port and return what was done."""
    try:
        r = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=10,
        )
        for line in r.stdout.splitlines():
            if f":{SERVER_PORT}" in line and "LISTENING" in line:
                parts = line.strip().split()
                pid = parts[-1]
                log.info("Found PID %s listening on port %s — killing", pid, SERVER_PORT)
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", pid],
                    capture_output=True, timeout=10,
                )
                return f"Killed PID {pid} on port {SERVER_PORT}"
        return f"No process found on port {SERVER_PORT}"
    except Exception as e:
        log.warning("Port %s scan failed: %s", SERVER_PORT, e)
        return f"Port scan error: {e}"


# ── Server control ─────────────────────────────────────────────────────────────

def _is_running() -> bool:
    with _lock:
        return _proc is not None and _proc.poll() is None


def _start(icon: pystray.Icon, *, from_watchdog: bool = False) -> None:
    global _proc, _user_stopped
    if _is_running():
        log.info("_start: already running, skipping")
        return

    # Only clear _user_stopped if the USER explicitly clicked Start.
    # The watchdog calling _start does NOT override a manual stop.
    if not from_watchdog:
        _user_stopped = False
        log.info("_start: user-initiated, _user_stopped=False")
    else:
        log.info("_start: watchdog-initiated (crash restart)")

    icon.icon  = ICON_AMBER
    icon.title = "Account Warmer — Starting…"

    with _lock:
        _proc = subprocess.Popen(
            [sys.executable, str(MAIN_PY)],
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        log.info("_start: spawned PID %d", _proc.pid)

    # Wait up to 12 s for the server to accept connections, then mark green
    def _wait_ready():
        import urllib.request
        for i in range(24):
            time.sleep(0.5)
            try:
                urllib.request.urlopen(DASHBOARD, timeout=1)
                log.info("_wait_ready: server accepted connection after %.1fs", (i + 1) * 0.5)
                break
            except Exception:
                pass
        _refresh(icon)

    threading.Thread(target=_wait_ready, daemon=True).start()


def _stop(icon: pystray.Icon) -> None:
    global _proc, _user_stopped
    _user_stopped = True   # tell watchdog this was intentional
    log.info("_stop: user-initiated, _user_stopped=True")

    killed_pid = None
    with _lock:
        if _proc is not None:
            if _proc.poll() is None:
                pid = _proc.pid
                log.info("_stop: killing process tree PID %d", pid)
                # ── Phase 1: kill the known process tree ──────────────────────
                try:
                    r = subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        capture_output=True, text=True, timeout=10,
                    )
                    log.info("_stop: taskkill result: %s", r.stdout.strip() or r.stderr.strip())
                except Exception as e:
                    log.warning("_stop: taskkill error: %s", e)
                # Wait for it to actually die
                try:
                    _proc.wait(timeout=3)
                    log.info("_stop: process exited cleanly")
                except subprocess.TimeoutExpired:
                    log.warning("_stop: process did not exit, calling _proc.kill()")
                    _proc.kill()
                killed_pid = pid
            else:
                # Process was already dead but _proc was never cleared
                log.info("_stop: _proc exists but already exited (rc=%s)", _proc.poll())
        else:
            log.info("_stop: _proc is None, nothing to kill")

        _proc = None

    # ── Phase 2: belt-and-braces — kill anything still on the server port ────
    result = _kill_server_port()
    log.info("_stop: port %s cleanup: %s", SERVER_PORT, result)

    _refresh(icon)
    log.info("_stop: complete, icon refreshed")


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
            log.warning("_watchdog: crash detected (PID %s exited rc=%s)",
                        getattr(_proc, 'pid', '?'),
                        _proc.poll() if _proc else '?')
            icon.icon  = ICON_AMBER
            icon.title = "Account Warmer — Crashed, restarting…"
            time.sleep(5)   # brief pause so rapid crash loops don't hammer the system
            _start(icon, from_watchdog=True)


def _refresh(icon: pystray.Icon) -> None:
    if _is_running():
        icon.icon  = ICON_GREEN
        icon.title = f"Account Warmer — Running  ●  http://localhost:{SERVER_PORT}"
    elif _is_batch_running():
        icon.icon  = ICON_AMBER
        icon.title = "Account Warmer — Daily Mobile Batch Running"
    else:
        icon.icon  = ICON_GREY
        icon.title = "Account Warmer — Stopped"
    icon.update_menu()


# ── Daily mobile batch control ──────────────────────────────────────────────────

def _is_batch_running() -> bool:
    with _lock:
        return _batch_proc is not None and _batch_proc.poll() is None


def _start_batch(icon: pystray.Icon) -> None:
    global _batch_proc
    if _is_batch_running():
        log.info("_start_batch: already running, skipping")
        return
    if not BATCH_RUNNER.exists():
        log.error("_start_batch: %s not found", BATCH_RUNNER)
        return

    log.info("_start_batch: launching daily_mobile_batch_runner.py --daemon")
    with _lock:
        _batch_proc = subprocess.Popen(
            [sys.executable, str(BATCH_RUNNER), "--daemon"],
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        log.info("_start_batch: spawned PID %d", _batch_proc.pid)

    _refresh(icon)


def _stop_batch(icon: pystray.Icon) -> None:
    global _batch_proc
    log.info("_stop_batch: terminating daily mobile batch runner")
    with _lock:
        if _batch_proc is not None:
            pid = _batch_proc.pid
            if _batch_proc.poll() is None:
                log.info("_stop_batch: killing process tree PID %d", pid)
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        capture_output=True, text=True, timeout=10,
                    )
                except Exception as e:
                    log.warning("_stop_batch: taskkill error: %s", e)
                try:
                    _batch_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    _batch_proc.kill()
            else:
                log.info("_stop_batch: process already exited (rc=%s)", _batch_proc.poll())
        _batch_proc = None

    _refresh(icon)


# ── Menu ───────────────────────────────────────────────────────────────────────

def _menu(icon: pystray.Icon) -> pystray.Menu:
    running = _is_running()
    batch_running = _is_batch_running()
    log.info("_menu: built, running=%s, batch_running=%s", running, batch_running)
    return pystray.Menu(
        pystray.MenuItem(
            "● Running" if running else ("● Batch Running" if batch_running else "○ Stopped"),
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
            lambda icon, item: threading.Thread(
                target=_start, args=(icon,),
                kwargs={"from_watchdog": False}, daemon=True
            ).start(),
            enabled=not running,
        ),
        pystray.MenuItem(
            "Stop Server",
            lambda icon, item: threading.Thread(
                target=_stop, args=(icon,), daemon=True
            ).start(),
            enabled=running,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Start Daily Mobile Batch",
            lambda icon, item: threading.Thread(
                target=_start_batch, args=(icon,), daemon=True
            ).start(),
            enabled=not batch_running,
        ),
        pystray.MenuItem(
            "Stop Daily Mobile Batch",
            lambda icon, item: threading.Thread(
                target=_stop_batch, args=(icon,), daemon=True
            ).start(),
            enabled=batch_running,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Safe to create social accounts manually —",
            None,
            enabled=False,
        ),
        pystray.MenuItem(
            "    occupied phones are skipped automatically.",
            None,
            enabled=False,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Exit",
            # Run _stop + _stop_batch in threads so pystray's event loop doesn't block
            lambda icon, item: (
                threading.Thread(target=_stop, args=(icon,), daemon=True).start(),
                threading.Thread(target=_stop_batch, args=(icon,), daemon=True).start(),
                # Give them a moment, then stop the icon
                threading.Thread(target=lambda: (time.sleep(2), icon.stop()), daemon=True).start(),
            ),
        ),
    )


def _exit(icon: pystray.Icon, item=None) -> None:
    _stop(icon)
    _stop_batch(icon)
    icon.stop()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=== tray.py starting ===")

    icon = pystray.Icon(
        name="account-warmer",
        icon=ICON_GREY,
        title="Account Warmer — Stopped",
        menu=pystray.Menu(lambda: _menu(icon)),
    )

    # Auto-start the server as soon as the tray icon appears
    threading.Thread(
        target=lambda: (time.sleep(0.5), _start(icon, from_watchdog=False)),
        daemon=True,
    ).start()

    # Watchdog — restarts the server automatically if it crashes
    threading.Thread(target=_watchdog_loop, args=(icon,), daemon=True).start()

    log.info("tray.py entering pystray event loop")
    icon.run()
    log.info("tray.py exiting")
