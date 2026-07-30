#!/usr/bin/env python3
"""Export new-maps1 flow and show all steps with enabled/disabled status."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import GeelarKClient

c = GeelarKClient()
gal = c.export_rpa_flow('623992235725160754')
flow = json.loads(gal)

print("=== STEPS IN API (what actually runs) ===\n")
for item in flow['content']['contents']:
    n = item.get('name', '?')
    t = item.get('type', '?')
    en = item.get('enabled', '?')
    cfg = item.get('config', {})

    if t == 'inputContent':
        print(f"  {n} ({t}) enabled={en} content={cfg.get('content', [])}")
    elif t == 'scrollPage':
        print(f"  {n} ({t}) enabled={en} pos={cfg.get('position')} dir={cfg.get('direction')} dist={cfg.get('distanceMin')}-{cfg.get('distanceMax')} _a={cfg.get('_a')} _b={cfg.get('_b')}")
    elif t == 'waitEle':
        fc = cfg.get('filterCollection', [[]])
        f0 = fc[0][0] if fc and fc[0] else {}
        print(f"  {n} ({t}) enabled={en} var={cfg.get('variable')} filter={f0.get('content', '?')[:60]} type={f0.get('filterType', '?')}")
    elif t == 'forTimes':
        chs = cfg.get('children', [])
        print(f"  {n} ({t}) enabled={en} times={cfg.get('times')} children:")
        for ch in chs:
            cn = ch.get('name', '?')
            ct = ch.get('type', '?')
            ce = ch.get('enabled', '?')
            if ct == 'scrollPage':
                ccfg = ch.get('config', {})
                print(f"    - {cn} ({ct}) enabled={ce} pos={ccfg.get('position')} dir={ccfg.get('direction')} dist={ccfg.get('distanceMin')}-{ccfg.get('distanceMax')}")
            else:
                print(f"    - {cn} ({ct}) enabled={ce}")
    else:
        print(f"  {n} ({t}) enabled={en}")

print()
print("=== ALL MAPS FLOWS ===")
for f in c.list_rpa_flows(1, 100):
    title = f.get('title', '') or ''
    if 'maps' in title.lower():
        print(f"  ID={f.get('id')} title={title}")
