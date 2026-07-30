import sys, time
sys.path.insert(0, r'C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src')
sys.path.insert(0, r'C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator')
sys.path.insert(0, r'C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer')

from run_one_phone import _get_phone_ip
from proxy_control import check_proxy_ip

expected_ip = check_proxy_ip()
print(f"Using current proxy IP: {expected_ip}")

phones = [
    ('614216911852404803', 'tannerchambers9987', 'Pixel 7', 'Android 13'),
    ('614216903581237315', 'oakleighoneal9987', 'FIND X', 'Android 10'),
    ('614216899286270322', 'huxleymcmillan9912', 'A72', 'Android 10'),
    ('614216895242960963', 'gracelynvillanueva9987', 'X70', 'Android 13'),
    ('614216890796998723', 'gustavostanley92327', 'X27', 'Android 10'),
    ('614216886971793475', 'luisamathis93437', 'Y31', 'Android 13'),
]

for pid, name, model, android in phones:
    print(f"\n=== {pid} | {name} | {model} | {android} ===")
    detected = _get_phone_ip(pid)
    ok = detected == expected_ip
    status = "CLEAN" if ok else "LEAK"
    print(f"  Result: {status} | detected_ip={detected}")
