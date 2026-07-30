from core.geelark_client import GeelarKClient

c = GeelarKClient()
ids = ['614216911852404803', '614216903581237315', '614216899286270322', '614216895242960963', '614216890796998723', '614216886971793475']
phones = c.list_phones()
for p in phones:
    if p.get('id') in ids:
        equip = p.get('equipmentInfo', {})
        print(f"{p.get('id')} | {p.get('serialName')} | model={equip.get('deviceModel')} | android={equip.get('osVersion')}")
