"""
Debug: Take screenshots during the 2FA flow to see exactly what's on screen.
Run from: account-warmer/
"""
import os, yaml, time, pyotp
from pathlib import Path

_env = Path('warmer.env')
for line in _env.read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip())

from activities.google_login_mobile import _shell, _get_window_focus, GOOGLE_AUTH_ACTIVITY
from core.geelark_client import GeelarKClient

client = GeelarKClient()
with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)
acc = data['accounts'][0]
phone_id = acc['geelark_phone_id']
secret = acc.get('totp_secret','').replace(' ','').upper()

shot_dir = Path(r'C:\WarmingData\screenshots\totp_debug')
shot_dir.mkdir(parents=True, exist_ok=True)

print(f'Account: {acc["email"]}')
print(f'Phone:   {phone_id}')
print(f'Secret:  {secret}')
print(f'Code now: {pyotp.TOTP(secret).now()}')
print()

# Start phone
print('Starting phone...')
viewer_url = client.start_phone(phone_id)
for i in range(24):
    time.sleep(5)
    ok, out = _shell(phone_id, 'wm size')
    if ok and out.strip():
        print(f'Phone ready after {(i+1)*5}s')
        break
time.sleep(3)

def shot(label):
    s = client.take_screenshot(phone_id)
    if s:
        p = shot_dir / f'{label}.png'
        p.write_bytes(s)
        print(f'  Screenshot: {label}.png')
    return s

def dump_ui(label):
    ok, xml = _shell(phone_id, 'uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml')
    if xml:
        (shot_dir / f'{label}.xml').write_text(xml, encoding='utf-8', errors='replace')
    return xml or ''

# Launch Add Account
print()
print('Opening Add Account intent...')
_shell(phone_id, 'am start -a android.settings.ADD_ACCOUNT_SETTINGS --es account_types com.google')
time.sleep(4)
shot('01_initial')
focus = _get_window_focus(phone_id)
print(f'Focus: {focus[-60:]}')

# If on 2FA method select, take screenshot BEFORE tapping
if GOOGLE_AUTH_ACTIVITY in focus:
    print()
    print('On MinuteMaidActivity — taking screenshot of 2FA method selection screen')
    shot('02_2fa_method_selection')
    xml = dump_ui('02_method_xml')
    print(f'  EditText count: {xml.count("android.widget.EditText")}')
    print(f'  WebView count:  {xml.count("android.webkit.WebView")}')

    # Show what's visible at our tap coordinates
    print()
    print('Our tap coordinate: (360, 829) on 720x1440 screen')
    print('This should be "Get a code from Google Authenticator"')
    print()

    # Tap and immediately screenshot
    print('Tapping (360, 829)...')
    _shell(phone_id, 'input tap 360 829')
    time.sleep(3)
    shot('03_after_first_tap')
    focus2 = _get_window_focus(phone_id)
    print(f'Focus after tap: {focus2[-60:]}')

    time.sleep(2)
    shot('04_after_wait')

    # Check what screen type we're on now
    ok2, xml2 = _shell(phone_id, 'uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml')
    dump_ui('04_after_tap_xml')
    if xml2:
        has_edit = xml2.count('android.widget.EditText')
        has_web = xml2.count('android.webkit.WebView')
        print(f'After tap — EditText={has_edit}  WebView={has_web}')
        if has_edit:
            print('EditText found — this is the code entry screen')
            print()

            # Now enter the TOTP code
            secs = 30 - (int(time.time()) % 30)
            if secs < 5:
                print(f'Waiting {secs+1}s for fresh TOTP window...')
                time.sleep(secs + 1)
            code = pyotp.TOTP(secret).now()
            secs_left = 30 - (int(time.time()) % 30)
            print(f'Entering TOTP code: {code}  ({secs_left}s remaining)')

            # Tap the field
            import re
            m = re.search(r'class="android.widget.EditText"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml2)
            if m:
                x = (int(m.group(1)) + int(m.group(3))) // 2
                y = (int(m.group(2)) + int(m.group(4))) // 2
                print(f'EditText at ({x},{y})')
                _shell(phone_id, f'input tap {x} {y}')
            else:
                print('EditText not found by coords — tapping (360,631)')
                _shell(phone_id, 'input tap 360 631')
            time.sleep(0.5)

            # Clear field
            _shell(phone_id, 'input keyevent KEYCODE_CTRL_A')
            _shell(phone_id, 'input keyevent KEYCODE_DEL')
            time.sleep(0.3)

            # Enter code DIGIT BY DIGIT for reliability
            print(f'Typing digits: {code}')
            for digit in code:
                _shell(phone_id, f'input keyevent KEYCODE_{digit}')
                time.sleep(0.15)

            shot('05_code_entered')
            print('Code entered. Waiting 2s before submitting...')
            time.sleep(2)

            # Submit
            _shell(phone_id, 'input keyevent 66')
            print('Submitted. Waiting 8s for Google response...')
            time.sleep(8)

            shot('06_after_submit')
            focus3 = _get_window_focus(phone_id)
            print(f'Focus after submit: {focus3[-80:]}')
            if GOOGLE_AUTH_ACTIVITY in focus3:
                print()
                print('STILL on MinuteMaidActivity - TOTP code was REJECTED')
                print()
                print('DIAGNOSIS: The TOTP secret in the YAML is WRONG for this account.')
                print('The stored secret generates valid codes, but they do not match')
                print('what Google expects. The secret must be re-exported from the')
                print('authenticator app or Google account settings.')
            else:
                print()
                print('SUCCESS - Left MinuteMaidActivity! TOTP code accepted.')
                dump_ui('06_success')

client.stop_phone(phone_id)
print()
print(f'Screenshots saved to: {shot_dir}')
