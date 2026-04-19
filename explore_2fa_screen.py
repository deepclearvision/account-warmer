import os, yaml, time, pathlib
from pathlib import Path
_env = Path("warmer.env")
for line in _env.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

from activities.google_login_mobile import _shell
from core.geelark_client import GeelarKClient

client = GeelarKClient()
with open(r"C:\WarmingData\geelark_accounts.yaml", encoding="utf-8") as f:
    data = yaml.safe_load(f)
acc = next(a for a in data["accounts"] if a.get("id") == "acc_032")
phone_id = acc["geelark_phone_id"]

out_dir = pathlib.Path(r"C:\WarmingData\screenshots\2fa_explore")
out_dir.mkdir(parents=True, exist_ok=True)

def shot(label):
    s = client.take_screenshot(phone_id)
    if s:
        p = out_dir / f"{label}.png"
        p.write_bytes(s)
        print(f"  Screenshot: {label}.png ({len(s)} bytes)")

def dump_xml(label):
    ok, xml = _shell(phone_id, "uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml")
    if xml:
        p = out_dir / f"{label}.xml"
        p.write_text(xml, encoding="utf-8", errors="replace")
        print(f"  XML: {label}.xml  EditText={xml.count('android.widget.EditText')}  WebView={xml.count('android.webkit.WebView')}")
    return xml or ""

print("=== Current screen ===")
shot("01_current")
dump_xml("01_current")

# Clear the field first
print("\n=== Clearing field ===")
ok, xml = _shell(phone_id, "uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml")
import re
m = re.search(r'class="android\.widget\.EditText"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
if m:
    x = (int(m.group(1)) + int(m.group(3))) // 2
    y = (int(m.group(2)) + int(m.group(4))) // 2
    print(f"  EditText at ({x},{y})")
    _shell(phone_id, f"input tap {x} {y}")
    time.sleep(0.5)
    # Use end+shift+home to select all, then delete
    _shell(phone_id, "input keyevent KEYCODE_MOVE_END")
    time.sleep(0.2)
    _shell(phone_id, "input keyevent --longpress KEYCODE_MOVE_HOME")
    time.sleep(0.2)
    _shell(phone_id, "input keyevent KEYCODE_DEL")
    time.sleep(0.3)
    shot("02_after_clear")

# Dismiss keyboard
print("\n=== Dismissing keyboard (Back) ===")
_shell(phone_id, "input keyevent KEYCODE_BACK")
time.sleep(2)
shot("03_keyboard_dismissed")
dump_xml("03_keyboard_dismissed")

# Look for 'Try another way' coordinates
print("\n=== Checking for 'Try another way' by scrolling ===")
# Scroll up a bit to see if there's something above
_shell(phone_id, "input swipe 360 400 360 700")
time.sleep(1)
shot("04_scrolled_up")

# Scroll back
_shell(phone_id, "input swipe 360 700 360 400")
time.sleep(1)

# Try pressing Back to go to method selection
print("\n=== Pressing Back to try method selection ===")
_shell(phone_id, "input keyevent KEYCODE_BACK")
time.sleep(3)
shot("05_after_back")
dump_xml("05_after_back")
