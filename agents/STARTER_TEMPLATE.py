#!/usr/bin/env python3
"""
Autonomous social account creator for GeelarK cloud phones.
Platform: [PLATFORM]
Agent folder: agents/[PLATFORM]/
"""
import sys, time, re, json, random
from pathlib import Path
from datetime import datetime

PLATFORM = "[PLATFORM]"
AGENT_DIR = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\agents") / PLATFORM
LOG_DIR = AGENT_DIR / "logs"
DUMP_DIR = AGENT_DIR / "dumps"
SHOT_DIR = AGENT_DIR / "screenshots"
STATE_DIR = AGENT_DIR / "state"
RECOVERY_DIR = AGENT_DIR / "recovery_codes"

for d in [LOG_DIR, DUMP_DIR, SHOT_DIR, STATE_DIR, RECOVERY_DIR]:
    d.mkdir(parents=True, exist_ok=True)

PHONE_ID = "[FILL_IN_PHONE_ID]"

PROJECT_ROOT = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
sys.path.insert(0, str(PROJECT_ROOT))
from core.geelark_client import GeelarKClient, _post
from core.sms_pool_client import order_sms, wait_for_sms, check_sms

client = GeelarKClient()

# ── SHELL HELPERS ──────────────────────────────────────────────────────────
def sh(cmd):
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})
    return (r.get("output", "") or "")

def tap(x, y):
    return sh("input tap %d %d" % (x, y))

def swipe(x1, y1, x2, y2, duration=300):
    return sh("input swipe %d %d %d %d %d" % (x1, y1, x2, y2, duration))

def back():
    return sh("input keyevent KEYCODE_BACK")

def home():
    return sh("input keyevent KEYCODE_HOME")

def get_screen():
    out = sh("wm size")
    m = re.search(r"(\d+)x(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else (1080, 2412)

def read_remote_file(remote_path, chunk_size=1800):
    chunks = []
    offset = 0
    while True:
        cmd = "dd if=%s bs=1 skip=%d count=%d 2>/dev/null" % (remote_path, offset, chunk_size)
        chunk = sh(cmd)
        if not chunk:
            break
        chunks.append(chunk.replace("\r\n", "\n"))
        if len(chunk) < chunk_size:
            break
        offset += chunk_size
    return "".join(chunks)

def dump_ui(step):
    tag = "%s_%s_%s" % (PLATFORM, step, datetime.now().strftime("%H%M%S"))
    path = "/sdcard/%s.xml" % tag
    sh("uiautomator dump %s" % path)
    time.sleep(0.8)
    xml = read_remote_file(path)
    local_path = DUMP_DIR / ("%s.xml" % tag)
    local_path.write_text(xml, encoding="utf-8")
    return xml

def find_and_tap(xml, queries, label):
    if isinstance(queries, str):
        queries = [queries]
    x = xml.find("<?xml") if xml else -1
    if x < 0:
        return False
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml[x:])
    except Exception:
        return False
    best = None
    best_score = 999
    best_q = None
    for q in queries:
        ql = q.lower()
        for node in root.iter("node"):
            for attr in ["text", "content-desc", "resource-id"]:
                v = (node.get(attr, "") or "").strip().lower()
                if not v:
                    continue
                score = None
                if v == ql:
                    score = 0
                elif v.startswith(ql) or ql in v:
                    score = 1 if v.startswith(ql) else 2
                if score is not None and score < best_score:
                    best_score = score
                    bounds = node.get("bounds", "")
                    try:
                        x1, y1, x2, y2 = map(int, bounds.replace("[","").replace("]",",").rstrip(",").split(","))
                        best = ((x1 + x2) // 2, (y1 + y2) // 2)
                        best_q = q
                    except Exception:
                        pass
    if best:
        print("  [%s] Tapped %r at %s" % (label, best_q, best))
        tap(best[0], best[1])
        return True
    print("  [%s] NOT FOUND: %s" % (label, queries[:3]))
    return False

# ── IP ROTATION ─────────────────────────────────────────────────────────────
def maybe_rotate_ip():
    state_file = STATE_DIR / "state.json"
    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    last_rot = state.get("last_rotation", 0)
    should_rotate = False
    if (time.time() - last_rot) > 1200:
        should_rotate = True
    elif random.random() < 0.30:
        should_rotate = True
    if not should_rotate:
        print("IP rotation skipped")
        return
    print("Rotating proxy IP...")
    try:
        import requests
        resp = requests.post(
            "https://mobile-proxy-140-166.streamvia.io/index.php",
            auth=("19866_1", "Wealther3211!!"),
            data={"action": "changeip"}, timeout=15
        )
        print("  Rotation: %s %s" % (resp.status_code, resp.text.strip()))
    except Exception as e:
        print("  Rotation failed: %s" % e)
    time.sleep(30)
    ip = sh("curl -s --max-time 10 https://ifconfig.me").strip()
    print("  New IP: %s" % ip)
    state["last_ip"] = ip
    state["last_rotation"] = time.time()
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

# ── REPORTING ───────────────────────────────────────────────────────────────
def save_report(step, success, what_happened, what_failed, next_action, creds_saved=False):
    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "platform": PLATFORM,
        "phone_id": PHONE_ID,
        "step": step,
        "success": success,
        "what_happened": what_happened,
        "what_failed": what_failed,
        "next_action": next_action,
        "credentials_saved": creds_saved,
    }
    path = LOG_DIR / ("report_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

def save_credentials(account_id, email, password, username=None, backup_codes=None, phone=None):
    creds = {
        "platform": PLATFORM,
        "account_id": account_id,
        "email": email,
        "username": username,
        "password": password,
        "phone": phone,
        "backup_codes": backup_codes or [],
        "created_at": datetime.utcnow().isoformat(),
    }
    cred_path = STATE_DIR / "credentials.json"
    cred_path.write_text(json.dumps(creds, indent=2), encoding="utf-8")
    if backup_codes:
        recovery_path = RECOVERY_DIR / ("%s_%s_%s.txt" % (PLATFORM, account_id, datetime.now().strftime("%Y%m%d_%H%M%S")))
        recovery_path.write_text("\n".join(backup_codes), encoding="utf-8")

# ── SMS VERIFICATION ────────────────────────────────────────────────────────
def do_sms_verification():
    print("Ordering SMS number for %s..." % PLATFORM)
    try:
        order = order_sms(PLATFORM)
        print("  Order: %s" % order)
        order_id = order.get("order_id") or order.get("id")
        phone_number = order.get("phonenumber") or order.get("number")
        if not order_id:
            raise RuntimeError("No order_id in SMS Pool response: %s" % order)
        print("  Waiting for SMS on %s ..." % phone_number)
        sms_code = wait_for_sms(order_id, timeout=180, interval=5)
        print("  SMS code received: %s" % sms_code)
        return phone_number, sms_code
    except Exception as e:
        print("  SMS verification failed: %s" % e)
        return None, None

# ── MAIN FLOW (stub — expand per platform) ──────────────────────────────────
def main():
    maybe_rotate_ip()
    w, h = get_screen()
    print("Screen: %dx%d" % (w, h))

    pkg = "[PACKAGE_NAME]"
    installed = pkg in sh("pm list packages")

    if not installed:
        print("Installing %s via Play Store..." % pkg)
        sh("am start -a android.intent.action.VIEW -d 'market://details?id=%s'" % pkg)
        for _ in range(30):
            time.sleep(10)
            if pkg in sh("pm list packages"):
                print("Installed")
                break
        else:
            save_report("install", False, "App did not install", "Package not found after 5 min", "Check Play Store availability or use APK")
            return

    sh("am force-stop %s" % pkg)
    time.sleep(1)
    sh("monkey -p %s -c android.intent.category.LAUNCHER 1" % pkg)
    time.sleep(8)

    xml = dump_ui("app_opened")
    save_report("open_app", True, "App opened", None, "Implement signup flow")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        save_report("crash", False, "Script crashed", str(e), "Fix exception and rerun")
        raise
