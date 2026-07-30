#!/usr/bin/env python3
"""Step 5: Dismiss Privacy screen. Usage: python _step5_privacy.py <phone_id>"""
import sys, time, xml.etree.ElementTree as ET
from pathlib import Path

PHONE = sys.argv[1]

sys.path.insert(0, str(Path(r'C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer')))
from core.geelark_client import _post

def sh(cmd):
    r = _post('/open/v1/shell/execute', {'id': PHONE, 'cmd': cmd})
    return r.get('output','') or ''

def tap(x, y):
    _post('/open/v1/shell/execute', {'id': PHONE, 'cmd': f'input tap {x} {y}'})

def find_and_tap(xml_str, query, label):
    q = query.lower()
    best = None; best_score = 999
    if not xml_str.startswith('<'): return False
    root = ET.fromstring(xml_str)
    for node in root.iter('node'):
        t = node.get('text','').strip().lower()
        cd = node.get('content-desc','').strip().lower()
        if t == q or cd == q: score = 0
        elif t.startswith(q) or cd.startswith(q): score = 1
        elif q in t or q in cd: score = 2
        else: continue
        if score < best_score:
            best_score = score
            bounds = node.get('bounds','')
            try:
                x1,y1,x2,y2 = map(int, bounds.replace('[','').replace(']',',').rstrip(',').split(','))
                best = ((x1+x2)//2, (y1+y2)//2)
            except: pass
    if best:
        print(f'  Tapped "{query}" at {best}')
        tap(best[0], best[1])
        return True
    else:
        print(f'  "{query}" NOT FOUND')
        vis = []
        for node in ET.fromstring(xml_str).iter('node'):
            t = node.get('text','').strip()
            if t: vis.append(t)
            cd = node.get('content-desc','').strip()
            if cd and cd != t: vis.append(cd)
        print(f'    Visible: {vis[:25]}')
        return False

print('STEP 5: Dismiss Privacy screen')
xml = sh('uiautomator dump /sdcard/privacy.xml && cat /sdcard/privacy.xml')
if not find_and_tap(xml, 'ACCEPT', 'Privacy-ACCEPT'):
    find_and_tap(xml, 'Accept', 'Privacy-Accept')
time.sleep(3)
print('[OK] Step 5 complete')
