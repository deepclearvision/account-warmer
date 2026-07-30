import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from run_one_phone import _get_phone_ip
from proxy_control import check_proxy_ip

expected = check_proxy_ip()
print(f"Proxy: {expected}")
detected = _get_phone_ip("614216822245294147")
ok = detected == expected
status = "CLEAN" if ok else "LEAK"
print(f"serial_24: {status} | {detected}")
