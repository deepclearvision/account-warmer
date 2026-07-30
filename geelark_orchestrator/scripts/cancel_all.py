"""Cancel all running tasks on acc_049."""
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient

client = GeelarKClient()
task_ids = [
    '620909492093013908',  # idle test
    '620909597283848629',  # click immediate
]

for task_id in task_ids:
    try:
        client.cancel_task(task_id)
        print(f"Cancelled {task_id}")
    except Exception as e:
        print(f"Failed to cancel {task_id}: {e}")
