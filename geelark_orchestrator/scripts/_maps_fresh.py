#!/usr/bin/env python3
"""
Run Maps search on a GeelarK phone by creating a FRESH workflow each time.
Avoids stale-cache / edit-in-place issues with the GeelarK builder.

Usage: python _maps_fresh.py <phone_id> <business_name> <keyword> <label>
Example: python _maps_fresh.py 614216810182475843 "Rush Green Electrical And Heating Services" "Emergency Plumber Near Me" acc_020
"""
import sys, time, json
from pathlib import Path

PHONE = sys.argv[1]
BUSINESS = sys.argv[2]
KEYWORD = sys.argv[3] if len(sys.argv) > 3 else "Emergency Plumber Near Me"
LABEL = sys.argv[4] if len(sys.argv) > 4 else "unknown"

sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import GeelarKClient

c = GeelarKClient()

# Build a fresh GAL flow with business name baked in
# Based on the working "new-maps1" structure but with all placeholders replaced
flow = {
    "title": f"Maps fresh — {LABEL}",
    "desc": f"Auto-generated Maps search for {BUSINESS}",
    "content": {
        "desc": f"Auto-generated Maps search for {BUSINESS}",
        "name": f"maps-fresh-{LABEL}",
        "isDebug": False,
        "errorType": "skip",
        "startParamMap": [],
        "contents": [
            {
                "id": "step-open",
                "name": "Open App",
                "type": "openApp",
                "enabled": True,
                "config": {"packgename": "com.google.android.apps.maps"}
            },
            {
                "id": "step-wait1",
                "name": "Time",
                "type": "waitTime",
                "enabled": True,
                "config": {"timeoutMax": 18000, "timeoutMin": 16000, "timeoutType": "randomInterval"}
            },
            {
                "id": "step-input",
                "name": "Input",
                "type": "inputContent",
                "enabled": True,
                "config": {
                    "clear": True,
                    "serial": 1,
                    "content": [KEYWORD],
                    "simulate": True,
                    "waitTime": 300,
                    "inputType": "taskOrder",
                    "searchTime": 3000,
                    "serialType": "fixedValue",
                    "hiddenChildren": False,
                    "filterCollection": [
                        [
                            {"type": "class", "content": "android.widget.TextView", "filterType": "equal"},
                            {"type": "text", "content": "Search here", "filterType": "equal"}
                        ]
                    ]
                }
            },
            {
                "id": "step-wait2",
                "name": "Time",
                "type": "waitTime",
                "enabled": True,
                "config": {"timeoutMax": 4000, "timeoutMin": 2000, "timeoutType": "randomInterval"}
            },
            {
                "id": "step-enter",
                "name": "Keys",
                "type": "keyOption",
                "enabled": True,
                "config": {"keyType": "enter"}
            },
            {
                "id": "step-wait3",
                "name": "Time",
                "type": "waitTime",
                "enabled": True,
                "config": {"timeoutMax": 6000, "timeoutMin": 4000, "timeoutType": "randomInterval"}
            },
            {
                "id": "step-loop",
                "name": "For Loop Times",
                "type": "forTimes",
                "enabled": True,
                "config": {
                    "times": "15",
                    "serialType": "fixedValue",
                    "children": [
                        {
                            "id": "step-waitele",
                            "name": "Element appears",
                            "type": "waitEle",
                            "enabled": True,
                            "config": {
                                "serial": 1,
                                "variable": BUSINESS,
                                "searchTime": 3000,
                                "serialType": "fixedValue",
                                "hiddenChildren": False,
                                "filterCollection": [
                                    [{"type": "text", "content": BUSINESS, "filterType": "contains"}]
                                ]
                            }
                        },
                        {
                            "id": "step-if",
                            "name": "Statement if",
                            "type": "ifElse",
                            "enabled": True,
                            "config": {
                                "conditionV3": [[{"relation": "exist", "probability": 50, "useVariable": BUSINESS}]],
                                "children": [
                                    {
                                        "id": "step-click",
                                        "name": "Click",
                                        "type": "click",
                                        "enabled": True,
                                        "config": {
                                            "useOffset": False,
                                            "doubleClick": False,
                                            "saveItemName": BUSINESS,
                                            "randomDistance": 0
                                        }
                                    },
                                    {
                                        "id": "step-break",
                                        "name": "Exit Loop",
                                        "type": "breakLoop",
                                        "enabled": True,
                                        "config": {}
                                    }
                                ]
                            }
                        },
                        {
                            "id": "step-scroll",
                            "name": "Scroll",
                            "type": "scrollPage",
                            "enabled": True,
                            "config": {
                                "position": ["360", "600"],
                                "direction": "top",
                                "distanceMax": "500",
                                "distanceMin": "300",
                                "randomDistance": 10,
                                "randomWheelSleepTime": [600, 800],
                                "_a": 400,
                                "_b": 700
                            }
                        },
                        {
                            "id": "step-wait4",
                            "name": "Time",
                            "type": "waitTime",
                            "enabled": True,
                            "config": {"timeoutMax": 3000, "timeoutMin": 1000, "timeoutType": "randomInterval"}
                        }
                    ]
                }
            }
        ]
    }
}

# Import as NEW flow (no flow_id = create)
print(f"Creating fresh flow for {LABEL}:")
print(f"  Business: {BUSINESS}")
print(f"  Keyword:  {KEYWORD}")

gal_str = json.dumps(flow)
new_id = c.import_rpa_flow(gal_str)  # No flow_id → creates new
print(f"  New flow ID: {new_id}")

# Run it
task_id = c.run_custom_flow(
    flow_id=new_id,
    phone_id=PHONE,
    param_map={},
    task_name=f"Maps fresh — {LABEL}"
)
print(f"  Task ID: {task_id}")

# Poll to completion
for i in range(18):
    time.sleep(10)
    tasks = c.query_tasks([task_id])
    for t in tasks:
        sm = {1: 'Waiting', 2: 'InProgress', 3: 'Completed', 4: 'Failed', 7: 'Cancelled'}
        st = sm.get(t.get('status'), str(t.get('status')))
        # Get message if any
        msg = t.get('msg', '') or ''
        if msg:
            msg = f' | msg={msg[:120]}'
        print(f"  t+{(i+1)*10}s: {st}{msg}")
        if st in ('Completed', 'Failed', 'Cancelled'):
            print(f"\nDone — flow ID {new_id} left on account (delete manually in builder if needed)")
            sys.exit(0)
