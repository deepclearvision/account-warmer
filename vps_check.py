import yaml
with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)
accounts = data.get('accounts', [])
has_phone = [a for a in accounts if a.get('geelark_phone_id')]
no_phone  = [a for a in accounts if not a.get('geelark_phone_id')]
has_flow  = [a for a in accounts if a.get('geelark_login_flow_id')]
print('Total: %d, Has phone: %d, No phone: %d, Has flow: %d' % (len(accounts), len(has_phone), len(no_phone), len(has_flow)))
print()
for a in has_phone:
    fid = a.get('geelark_login_flow_id', 'NONE')
    pid = str(a.get('geelark_phone_id', ''))
    print('%s | %s | phone=%s | flow=%s' % (a['id'], a.get('email',''), pid, fid))
