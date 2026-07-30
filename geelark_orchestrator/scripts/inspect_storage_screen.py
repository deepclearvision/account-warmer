#!/usr/bin/env python3
"""
Inspect Fake GPS App Info -> Storage screen to determine exact "Clear" button text.
Uses shell commands + uiautomator dump (text-based, no screenshots into model).
"""
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

# Windows console: force UTF-8 so phone glyphs (e.g. ‑) don't crash prints
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_repo_root = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
sys.path.insert(0, str(_repo_root / "geelark_orchestrator" / "src"))
sys.path.insert(0, str(_repo_root / "geelark_orchestrator"))
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import _post

PHONE_ID = "614216822245294147"
PACKAGE = "com.theappninjas.fakegpsjoystick"
DUMP_PATH = "/sdcard/storage_inspect.xml"


def shell_exec(cmd: str) -> dict:
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})


def dump_screen() -> str:
    r = shell_exec(f"uiautomator dump {DUMP_PATH}")
    out = r.get("output", "")
    if "dumped" not in out.lower() and "hierarchy" not in out.lower():
        print(f"  dump warning: {out}")
    r2 = shell_exec(f"cat {DUMP_PATH}")
    return r2.get("output", "")


def find_bounds(xml_data: str, text_query: str) -> tuple[int, int] | None:
    """Find the center point of an element matching text_query."""
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        print(f"  XML parse error: {e}")
        return None

    best = None
    best_score = 999

    for node in root.iter("node"):
        t = node.get("text", "").strip().lower()
        cd = node.get("content-desc", "").strip().lower()
        q = text_query.lower()

        # Score: exact match > starts with > contains
        if t == q or cd == q:
            score = 0
        elif t.startswith(q) or cd.startswith(q):
            score = 1
        elif q in t or q in cd:
            score = 2
        else:
            continue

        if score < best_score:
            best_score = score
            bounds = node.get("bounds", "")
            # Parse "[x1,y1][x2,y2]"
            try:
                coords = bounds.replace("[", "").replace("]", ",").rstrip(",").split(",")
                x1, y1, x2, y2 = map(int, coords)
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                best = (cx, cy)
            except Exception:
                pass

    return best


def inspect_storage_screen() -> None:
    print("=== Opening App Info for Fake GPS ===")
    r = shell_exec(f"am start -a android.settings.APPLICATION_DETAILS_SETTINGS -d package:{PACKAGE}")
    print(f"  open result: {r.get('output', '')[:100]}")
    time.sleep(3)

    # Dump App Info screen
    xml1 = dump_screen()
    texts1 = set()
    for node in ET.fromstring(xml1).iter("node"):
        t = node.get("text", "").strip()
        if t:
            texts1.add(t)
        cd = node.get("content-desc", "").strip()
        if cd:
            texts1.add(cd)

    print(f"\nApp Info screen texts ({len(texts1)} unique):")
    for t in sorted(texts1):
        print(f"  {t}")

    # Find and click Storage
    storage_pos = find_bounds(xml1, "Storage")
    if storage_pos:
        print(f"\n  Found Storage button at {storage_pos}")
        r = shell_exec(f"input tap {storage_pos[0]} {storage_pos[1]}")
        print(f"  tap result: {r.get('output', '')[:100]}")
        time.sleep(3)
    else:
        print("\n  WARNING: Storage button not found on App Info screen")
        # Try scrolling down to find it
        shell_exec("input swipe 260 1200 260 600 500")
        time.sleep(2)
        xml1b = dump_screen()
        storage_pos = find_bounds(xml1b, "Storage")
        if storage_pos:
            print(f"  Found Storage after scroll at {storage_pos}")
            shell_exec(f"input tap {storage_pos[0]} {storage_pos[1]}")
            time.sleep(3)
        else:
            print("  Still not found. Aborting inspection.")
            return

    # Dump Storage screen
    xml2 = dump_screen()
    texts2 = set()
    clear_buttons = []
    for node in ET.fromstring(xml2).iter("node"):
        t = node.get("text", "").strip()
        cd = node.get("content-desc", "").strip()
        if t:
            texts2.add(t)
        if cd:
            texts2.add(cd)
        lower = f"{t} {cd}".lower()
        if "clear" in lower and ("storage" in lower or "data" in lower or "cache" in lower):
            bounds = node.get("bounds", "")
            clear_buttons.append({"text": t or cd, "bounds": bounds})

    print(f"\nStorage screen texts ({len(texts2)} unique):")
    for t in sorted(texts2):
        print(f"  {t}")

    print(f"\nClear buttons found: {len(clear_buttons)}")
    for btn in clear_buttons:
        print(f"  '{btn['text']}' bounds={btn['bounds']}")

    # 3. Tap CLEAR STORAGE and inspect confirmation dialog
    clear_storage_pos = find_bounds(xml2, "CLEAR STORAGE")
    if clear_storage_pos:
        print(f"\n  Tapping CLEAR STORAGE at {clear_storage_pos}")
        r = shell_exec(f"input tap {clear_storage_pos[0]} {clear_storage_pos[1]}")
        print(f"  tap result: {r.get('output', '')[:100]}")
        time.sleep(2)

        xml3 = dump_screen()
        texts3 = set()
        ok_buttons = []
        for node in ET.fromstring(xml3).iter("node"):
            t = node.get("text", "").strip()
            cd = node.get("content-desc", "").strip()
            if t:
                texts3.add(t)
            if cd:
                texts3.add(cd)
            lower = f"{t} {cd}".lower()
            if any(k in lower for k in ("ok", "clear", "delete", "confirm")):
                bounds = node.get("bounds", "")
                ok_buttons.append({"text": t or cd, "bounds": bounds})

        print(f"\nConfirmation dialog texts ({len(texts3)} unique):")
        for t in sorted(texts3):
            print(f"  {t}")
        print(f"\nOK/Clear buttons found: {len(ok_buttons)}")
        for btn in ok_buttons:
            print(f"  '{btn['text']}' bounds={btn['bounds']}")

        out_path2 = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\ad_inspect\clear_dialog_dump.xml")
        out_path2.write_text(xml3, encoding="utf-8")
        print(f"\nDialog XML saved to: {out_path2}")
    else:
        print("\n  WARNING: CLEAR STORAGE button not found")

    # Also save raw XML for reference
    out_path = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\ad_inspect\storage_screen_dump.xml")
    out_path.write_text(xml2, encoding="utf-8")
    print(f"\nRaw XML saved to: {out_path}")


if __name__ == "__main__":
    inspect_storage_screen()
