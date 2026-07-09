import json, datetime

p = "C:/WarmingData/logs/state/scheduler.json"
d = json.load(open(p))
d["max_concurrent"] = 6
now = datetime.datetime.now().isoformat()

for a in d["accounts"].values():
    a["next_eligible_after"] = now
    a.pop("last_run_at", None)

json.dump(d, open(p, "w"), indent=2)
print(f"Done — {len(d['accounts'])} accounts eligible, 6 concurrent")
