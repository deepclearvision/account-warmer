"""
Read the Settings UI XML dump saved by diagnose_settings_ui.py
Run from: account-warmer/
"""
import re, sys

xml_path = r'C:\WarmingData\screenshots\settings_main_ui.xml'

with open(xml_path, 'r', encoding='utf-8') as f:
    content = f.read()

texts = re.findall(r'text="([^"]*)"', content)
resource_ids = re.findall(r'resource-id="([^"]*)"', content)

print('=== ALL TEXT ELEMENTS in Settings main ===')
seen = set()
for t in texts:
    if t.strip() and t not in seen:
        seen.add(t)
        print(' ', t.encode('ascii', 'replace').decode())

print()
print('=== RESOURCE IDs (non-empty) ===')
seen_ids = set()
for rid in resource_ids:
    if rid and rid not in seen_ids:
        seen_ids.add(rid)
        print(' ', rid)
