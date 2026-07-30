"""Check alternative delete flow endpoints."""
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient, _post

endpoints = [
    "/open/v1/task/flow/remove",
    "/open/v1/task/flow/del",
    "/open/v1/task/flow/batchDelete",
    "/open/v1/task/flow/batchDel",
    "/open/v1/task/flow/batchRemove",
    "/open/v1/task/flow/delete",
]

for ep in endpoints:
    try:
        result = _post(ep, {"ids": ["test-nonexistent-id"]})
        print(f"{ep}: {result}")
    except Exception as e:
        msg = str(e)
        if "404" in msg:
            print(f"{ep}: 404 NOT FOUND")
        elif "400" in msg or "430" in msg:
            # Endpoint exists but rejected the test ID — that's a hit!
            print(f"{ep}: EXISTS (rejected test ID: {msg[:100]})")
        else:
            print(f"{ep}: {msg[:100]}")
