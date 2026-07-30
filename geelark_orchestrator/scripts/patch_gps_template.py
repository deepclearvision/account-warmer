#!/usr/bin/env python3
"""Modify .gps_flow_template.json to add ad-dismissal loops at strategic points."""
import json
import uuid
from pathlib import Path

TEMPLATE_PATH = Path("C:/Users/Administrator/Desktop/AccountWarmer-Deploy-Enhanced/geelark_orchestrator/src/.gps_flow_template.json")

def _uuid() -> str:
    return str(uuid.uuid4())

def make_ad_dismiss_loop(prefix: str) -> dict:
    """Return a Geelark forTimes loop that searches for 'Continue to app' and clicks it."""
    return {
        "id": f"{prefix}_loop",
        "name": "For Loop Times",
        "type": "forTimes",
        "config": {
            "times": "2",
            "remark": "Dismiss Google Mobile Ads interstitial if present",
            "children": [
                {
                    "id": f"{prefix}_wait",
                    "name": "Element appears",
                    "type": "waitEle",
                    "config": {
                        "serial": 1,
                        "variable": "adDismissBtn",
                        "searchTime": 5000,
                        "serialType": "fixedValue",
                        "hiddenChildren": False,
                        "filterCollection": [
                            [{"type": "text", "content": "Continue to app", "filterType": "contain"}],
                            [{"type": "text", "content": "CONTINUE TO APP", "filterType": "contain"}],
                            [{"type": "desc", "content": "Continue to app", "filterType": "contain"}],
                        ],
                    },
                },
                {
                    "id": f"{prefix}_if",
                    "name": "Statement if",
                    "type": "ifElse",
                    "config": {
                        "other": [],
                        "children": [
                            {
                                "id": f"{prefix}_click",
                                "name": "Click",
                                "type": "click",
                                "config": {
                                    "useOffset": False,
                                    "doubleClick": False,
                                    "saveItemName": "adDismissBtn",
                                    "randomDistance": 0,
                                },
                            },
                            {
                                "id": f"{prefix}_wait_after",
                                "name": "Time",
                                "type": "waitTime",
                                "config": {
                                    "timeout": 2000,
                                    "timeoutType": "fixedValue",
                                },
                            },
                            {
                                "id": f"{prefix}_break",
                                "name": "Exit Loop",
                                "type": "breakLoop",
                                "config": {},
                            },
                        ],
                        "conditionV3": [
                            [{"relation": "exist", "useVariable": "adDismissBtn"}]
                        ],
                    },
                },
            ],
            "serialType": "fixedValue",
        },
    }


def main() -> int:
    data = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    contents = data["content"]["contents"]

    # Find key indices
    start_loop_idx = None
    start_using_idx = None
    for i, step in enumerate(contents):
        rid = step.get("id", "")
        remark = step.get("config", {}).get("remark", "")
        if step.get("type") == "forTimes" and "Click START" in remark:
            start_loop_idx = i
        if "Start Using GPS JoyStick" in remark:
            start_using_idx = i

    print(f"Found START loop at index {start_loop_idx}")
    print(f"Found 'Start Using' click at index {start_using_idx}")

    # We'll insert 3 ad-dismissal loops:
    # 1. After initial wait (index 1)
    # 2. After the onboarding 'Start Using' step + its following waits
    #    We insert after index start_using_idx + 2 (after the two waitTime steps that follow)
    # 3. Right before START loop

    insertions = []

    # Loop 1: after initial wait (index 1)
    insertions.append((2, make_ad_dismiss_loop("ad_dismiss_early")))

    # Loop 2: after Start Using + the two waitTime steps that follow it
    # Looking at the template, after Start Using click (ca8d705c...) there are:
    #   edb7aeba: Time (wait 3-5s)
    #   4d2bf42f: Time (wait 4-6s)
    #   99678022: What's New loop
    # We insert after the What's New loop (index ~30) to catch ads that appear during onboarding
    whats_new_idx = None
    for i, step in enumerate(contents):
        if step.get("id") == "99678022-418c-4e61-8600-e7cf1c1bb1cc":
            whats_new_idx = i
            break
    if whats_new_idx is not None:
        insertions.append((whats_new_idx + 1, make_ad_dismiss_loop("ad_dismiss_mid")))
        print(f"Inserting mid-flow loop after What's New at index {whats_new_idx + 1}")
    else:
        print("WARNING: could not find What's New loop, skipping mid insertion")

    # Loop 3: right before START loop
    if start_loop_idx is not None:
        insertions.append((start_loop_idx, make_ad_dismiss_loop("ad_dismiss_pre_start")))
        print(f"Inserting pre-START loop at index {start_loop_idx}")
    else:
        print("WARNING: could not find START loop, skipping pre-START insertion")

    # Sort insertions by index descending so earlier indices aren't shifted
    insertions.sort(key=lambda x: x[0], reverse=True)

    for idx, loop in insertions:
        contents.insert(idx, loop)
        print(f"Inserted ad-dismissal loop at index {idx}")

    # Backup original
    backup = TEMPLATE_PATH.with_suffix(".json.bak")
    if not backup.exists():
        TEMPLATE_PATH.rename(backup)
        print(f"Backed up original template to {backup}")

    TEMPLATE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote updated template to {TEMPLATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
