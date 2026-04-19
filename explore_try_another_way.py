"""
Navigate through email+password to the security code screen,
then tap 'Try another way' and take screenshots of the resulting screen.
"""
import os, yaml, time, pathlib, pyotp
from pathlib import Path
_env = Path("warmer.env")
for line in _env.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

from activities.google_login_mobile import (
    _shell, _find_text_field_and_tap, _get_window_focus,
    GOOGLE_AUTH_ACTIVITY
)
from core.geelark_client import GeelarKClient

client = GeelarKClient()
with open(r"C:\WarmingData\geelark_accounts.yaml", encoding="utf-8") as f:
    data = yaml.safe_load(f)
acc = next(a for a in data["accounts"] if a.get("id") == "acc_032")
phone_id = acc["geelark_phone_id"]
email = acc["email"]
password = acc["password"]
totp_secret = acc.get("totp_secret", "").replace(" ", "").upper()

out_dir = pathlib.Path(r"C:\WarmingData\screenshots\try_another_way")
out_dir.mkdir(parents=True, exist_ok=True)

def shot(label):
    s = client.take_screenshot(phone_id)
    if s:
        p = out_dir / f"{label}.png"
        p.write_bytes(s)
        print(f"  Screenshot: {label}.png")
    return s

def clear_and_type(text):
    """Tap field, clear it, type text."""
    field = _find_text_field_and_tap(phone_id)
    time.sleep(0.5)
    _shell(phone_id, "input keyevent KEYCODE_CTRL_A")
    _shell(phone_id, "input keyevent KEYCODE_DEL")
    for _ in range(50):
        _shell(phone_id, "input keyevent KEYCODE_DEL")
    time.sleep(0.3)
    _shell(phone_id, f"input text {text}")
    time.sleep(0.5)
    return field

print(f"Account: {email}")
print(f"TOTP secret: {totp_secret}")
print(f"Current TOTP code: {pyotp.TOTP(totp_secret).now()}")
print()

# Launch Add Account
print("Launching Add Account intent...")
_shell(phone_id, "am start -a android.settings.ADD_ACCOUNT_SETTINGS --es account_types com.google")
time.sleep(5)

focus = _get_window_focus(phone_id)
print(f"Focus: {focus[-60:]}")
shot("01_initial")

# Enter email
print("\nEntering email...")
clear_and_type(email)
_shell(phone_id, "input keyevent 66")
print("Email submitted. Waiting 8s...")
time.sleep(8)
shot("02_after_email")

# Enter password
print("\nEntering password...")
clear_and_type(password)
_shell(phone_id, "input keyevent 66")
print("Password submitted. Waiting 8s...")
time.sleep(8)
shot("03_after_password")

# Check what screen we're on
ok, xml = _shell(phone_id, "uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml")
print(f"After password: EditText={xml.count('EditText')}  WebView={xml.count('WebView')}")
focus = _get_window_focus(phone_id)
print(f"Focus: {focus[-60:]}")

# Wait a bit more for 2FA to load
time.sleep(5)
shot("04_2fa_screen")

# Dismiss keyboard if present
_shell(phone_id, "input keyevent KEYCODE_BACK")
time.sleep(1)
shot("05_keyboard_dismissed")

# Now tap "Try another way" at approximately (127, 1291) on 720x1440
print("\nTapping 'Try another way' at (127, 1291)...")
_shell(phone_id, "input tap 127 1291")
time.sleep(4)
shot("06_after_try_another_way")

ok, xml = _shell(phone_id, "uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml")
print(f"After Try another way: EditText={xml.count('EditText')}  WebView={xml.count('WebView')}")
(out_dir / "06_after_try_another_way.xml").write_text(xml, encoding="utf-8", errors="replace")

focus = _get_window_focus(phone_id)
print(f"Focus: {focus[-60:]}")

# Wait and screenshot to see the method list
time.sleep(2)
shot("07_method_list")
ok, xml = _shell(phone_id, "uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml")
print(f"Method list: EditText={xml.count('EditText')}  WebView={xml.count('WebView')}")
(out_dir / "07_method_list.xml").write_text(xml, encoding="utf-8", errors="replace")

print()
print(f"Screenshots saved to: {out_dir}")
