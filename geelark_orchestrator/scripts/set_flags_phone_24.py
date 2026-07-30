import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from runs_store import RunsStore

DB_PATH = r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\runs.sqlite"
SCHEMA_PATH = r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\schema.sql"

store = RunsStore(DB_PATH, SCHEMA_PATH)

PHONE_PROFILE_KEY = "serial_24"
PHONE_GEE_ID = "614216822245294147"

print(f"--- Setting flags for {PHONE_PROFILE_KEY} ---")

# Try to update existing profile
try:
    store.conn.execute(
        "UPDATE profiles SET provisioned=1, maps_verified=1 WHERE profile_key=?",
        (PHONE_PROFILE_KEY,),
    )
    if store.conn.total_changes == 0:
        print("No existing profile found. Inserting new row...")
        store.conn.execute(
            """INSERT INTO profiles
               (profile_key, geelark_profile_id, business_name, home_lat, home_lng,
                business_lat, business_lng, nearby_points, nearby_points_backup,
                onsite_points, branded_keywords, search_keywords, business_goal,
                profile_verdict, provisioned, maps_verified)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (PHONE_PROFILE_KEY, PHONE_GEE_ID, "Unknown", 0.0, 0.0, 0.0, 0.0,
             "", "", "", "", "", "review", "pending", 1, 1),
        )
    store.conn.commit()
    print("Flags set: provisioned=1, maps_verified=1")
except Exception as e:
    print(f"ERROR: {e}")

# Verify
row = store.conn.execute(
    "SELECT profile_key, geelark_profile_id, provisioned, maps_verified FROM profiles WHERE profile_key=?",
    (PHONE_PROFILE_KEY,),
).fetchone()
if row:
    print(f"Confirmed: {dict(row)}")
else:
    print("Row not found after update!")

store.close()
