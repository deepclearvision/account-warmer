import csv, shutil, os

platforms = {
    "facebook": {"package": "com.facebook.katana", "fallback": "com.facebook.lite"},
    "twitter":  {"package": "com.twitter.android", "fallback": None},
    "instagram_lite": {"package": "com.instagram.lite", "fallback": "com.instagram.android"},
    "pinterest": {"package": "com.pinterest", "fallback": None},
    "reddit":    {"package": "com.reddit.frontpage", "fallback": None},
    "tiktok":    {"package": "com.zhiliaoapp.musically", "fallback": "com.ss.android.ugc.trill"},
}

assignments = {}
with open(r"agents/agent_phone_assignments.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        assignments[row["platform"]] = row["phone_id"]

with open(r"agents/STARTER_TEMPLATE.py", "r", encoding="utf-8") as f:
    starter = f.read()

for p, info in platforms.items():
    phone = assignments[p]
    out_path = os.path.join("agents", p, "create_%s.py" % p)
    script = starter.replace("[PLATFORM]", p).replace("[FILL_IN_PHONE_ID]", phone).replace("[PACKAGE_NAME]", info["package"])

    if info["fallback"]:
        repl = """# Fallback package if primary not available
fallback_pkg = "%s"

# Example: ensure app installed""" % info["fallback"]
        script = script.replace("# Example: ensure app installed", repl)
        repl2 = """    installed = pkg in sh("pm list packages")
    if not installed and fallback_pkg:
        pkg = fallback_pkg
        installed = fallback_pkg in sh("pm list packages")"""
        script = script.replace("    installed = pkg in sh(\"pm list packages\")", repl2)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(script)

    print("Created %s for %s phone %s package %s" % (out_path, p, phone, info["package"]))
