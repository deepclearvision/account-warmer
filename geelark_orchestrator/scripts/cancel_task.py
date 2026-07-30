"""Cancel a specific task."""
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient

client = GeelarKClient()
task_id = "620910008665636981"

try:
    client.cancel_task(task_id)
    print(f"Cancelled {task_id}")
except Exception as e:
    print(f"Failed: {e}")
