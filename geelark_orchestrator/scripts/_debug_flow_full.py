#!/usr/bin/env python3
"""Full deep export of new-maps1 — every node, every nested child."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import GeelarKClient

c = GeelarKClient()
gal = c.export_rpa_flow('623992235725160754')
flow = json.loads(gal)

def walk(nodes, depth=0):
    """Recursively dump every node."""
    if isinstance(nodes, dict):
        nodes = [nodes]
    for item in nodes:
        if not isinstance(item, dict):
            continue
        prefix = "  " * depth
        n = item.get('name', '?')
        t = item.get('type', '?')
        en = item.get('enabled', '?')
        cid = item.get('id', '?')[-8:]
        print(f"{prefix}[{cid}] {n} ({t}) enabled={en}")

        cfg = item.get('config', {})
        if t == 'scrollPage':
            print(f"{prefix}  CONFIG: pos={cfg.get('position')} dir={cfg.get('direction')} distMin={cfg.get('distanceMin')} distMax={cfg.get('distanceMax')} _a={cfg.get('_a')} _b={cfg.get('_b')}")
        elif t == 'inputContent':
            print(f"{prefix}  CONFIG: content={cfg.get('content')} clear={cfg.get('clear')} simulate={cfg.get('simulate')}")
        elif t == 'keyOption':
            print(f"{prefix}  CONFIG: keyType={cfg.get('keyType')}")
        elif t == 'openApp':
            print(f"{prefix}  CONFIG: pkg={cfg.get('packgename')}")
        elif t == 'waitTime':
            print(f"{prefix}  CONFIG: min={cfg.get('timeoutMin')} max={cfg.get('timeoutMax')}")
        elif t == 'waitEle':
            fc = cfg.get('filterCollection', [[]])
            f0 = fc[0][0] if fc and fc[0] else {}
            print(f"{prefix}  CONFIG: var={cfg.get('variable')} filter='{f0.get('content','?')[:80]}' filterType={f0.get('filterType','?')}")
        elif t == 'forTimes':
            print(f"{prefix}  CONFIG: times={cfg.get('times')}")
            children = cfg.get('children', [])
            if children:
                print(f"{prefix}  CHILDREN ({len(children)}):")
                walk(children, depth + 2)
        elif t == 'ifElse':
            cond = cfg.get('conditionV3', [])
            print(f"{prefix}  CONFIG: condition={json.dumps(cond)[:200]}")
            children = cfg.get('children', [])
            if children:
                print(f"{prefix}  CHILDREN ({len(children)}):")
                walk(children, depth + 2)
        elif t == 'click':
            print(f"{prefix}  CONFIG: useOffset={cfg.get('useOffset')} saveItemName={cfg.get('saveItemName')}")
        elif t == 'breakLoop':
            pass

print("=" * 70)
print("FULL FLOW TREE — new-maps1 (ID: 623992235725160754)")
print("=" * 70)
walk(flow['content'].get('contents', flow['content'].get('children', [])))

# Also verify: is run_custom_flow calling the right ID?
print()
print("=" * 70)
print("VERIFY: The _rerun_maps.py script calls flow_id='623992235725160754'")
print("If steps differ from what you see in builder, the API may be returning")
print("a stale/cached version or the builder writes to a different flow ID.")
