"""
setup_login_flow.py — Configure the GeelarK custom RPA flow for Google login.

Usage:
    python setup_login_flow.py --list                 List all saved RPA flows
    python setup_login_flow.py --create               Build & import the Google login flow
    python setup_login_flow.py --create --with-method-select  Variant with 2FA method screen
    python setup_login_flow.py --create --update <flow_id>    Update an existing flow in-place
    python setup_login_flow.py --set <flow_id>        Set flow_id on all accounts
    python setup_login_flow.py --set <flow_id> --account acc_001
    python setup_login_flow.py --export <flow_id>     Export flow JSON to file
    python setup_login_flow.py --check                Show current flow_id on each account

QUICKSTART (no visual editor needed):
  1. Run:  python setup_login_flow.py --create
     This builds the complete Google login + TOTP flow and imports it via the API.
     The flow ID is printed at the end.
  2. Run:  python setup_login_flow.py --set <flow_id>
     to write geelark_login_flow_id to all accounts in accounts.yaml.
  3. Done — login_check_run.py will now use the custom flow automatically.

MANUAL SETUP (if you prefer the visual editor):
  1. Log into rpa.geelark.com with your GeelarK credentials
  2. Build a Google login flow that includes:
       - Type email  (use parameter: email)
       - Type password  (use parameter: password)
       - Wait for 2FA screen
       - Get data → Authenticator code  (Key parameter: totp_secret)
       - Type the TOTP code variable into the verification code field
       - Submit and wait for success
  3. Save the flow and note its ID (shown in the URL or flow list)
  4. Run:  python setup_login_flow.py --set <flow_id>
"""

import argparse
import json
import sys
from pathlib import Path

# ── Bootstrap env ─────────────────────────────────────────────────────────────
_env = Path(__file__).parent / "warmer.env"
if _env.exists():
    import os
    for _l in _env.read_text(encoding="utf-8").splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            k, _, v = _l.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import flows as _flow_templates


def _load_accounts() -> tuple[Path, list]:
    """Load geelark_accounts.yaml from WarmingData (falls back to config/)."""
    import yaml
    data_dir = Path(
        __import__("os").environ.get("WARMER_DATA_DIR", r"C:\WarmingData")
    )
    # GeelarK accounts live in geelark_accounts.yaml
    p = data_dir / "geelark_accounts.yaml"
    if not p.exists():
        # fallback: config/accounts.yaml for older layouts
        p = Path(__file__).parent / "config" / "accounts.yaml"
    if not p.exists():
        print(f"ERROR: geelark_accounts.yaml not found at {p}")
        sys.exit(1)
    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    # File may be a bare list or a dict with an 'accounts' key
    accounts = raw.get("accounts", raw) if isinstance(raw, dict) else raw
    return p, accounts


def _save_accounts(path: Path, accounts: list) -> None:
    import yaml
    # Preserve the 'accounts:' wrapper if the file uses it
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        raw["accounts"] = accounts
        data = raw
    else:
        data = accounts
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False,
                  default_flow_style=False)


def cmd_list():
    client = GeelarKClient()
    print("Fetching saved RPA flows from GeelarK …\n")
    flows = client.list_rpa_flows()
    if not flows:
        print("No flows found. Build one at rpa.geelark.com.")
        print("\nThe flow needs these steps:")
        print("  1. Open Google sign-in")
        print("  2. Input email  (param: email)")
        print("  3. Input password  (param: password)")
        print("  4. Wait for 2FA screen")
        print("  5. Get data → Authenticator code  (param: totp_secret)")
        print("  6. Input the TOTP code variable into the verification field")
        print("  7. Submit")
        return
    print(f"{'ID':<24}  {'Title':<40}  {'Params'}")
    print("-" * 80)
    for f in flows:
        fid    = str(f.get("id", "?"))
        title  = str(f.get("title", "—"))[:40]
        params = ", ".join(f.get("params") or [])
        print(f"{fid:<24}  {title:<40}  {params}")


def cmd_check():
    _, accounts = _load_accounts()
    print(f"{'Account':<12}  {'Email':<36}  {'flow_id'}")
    print("-" * 80)
    for a in accounts:
        fid = a.get("geelark_login_flow_id", "—")
        print(f"{a.get('id',''):<12}  {a.get('email',''):<36}  {fid}")


def cmd_set(flow_id: str, account_id: str = None):
    path, accounts = _load_accounts()
    updated = 0
    for a in accounts:
        if account_id and a.get("id") != account_id:
            continue
        if not a.get("geelark_phone_id"):
            continue  # skip accounts with no phone
        a["geelark_login_flow_id"] = flow_id
        updated += 1
        print(f"  Set {a.get('id')} ({a.get('email')}) -> flow_id={flow_id}")
    if updated == 0:
        print("No matching accounts found.")
        return
    _save_accounts(path, accounts)
    print(f"\nSaved. {updated} account(s) updated.")
    print("\nNext: run the mobile login to test:")
    if account_id:
        print(f"  python login_check_run.py --account {account_id}")
    else:
        print("  python login_check_run.py --all")


def cmd_create(with_method_select: bool = False, update_id: str = None):
    """
    Programmatically build and import the Google login + TOTP flow via the API.
    No need to use the rpa.geelark.com visual editor.
    """
    variant = "with method select" if with_method_select else "standard"
    action  = f"Updating flow {update_id}" if update_id else "Creating new flow"
    print(f"{action} — Google Login + TOTP ({variant}) …\n")

    if with_method_select:
        fid = _flow_templates.google_login_totp_with_method_select(flow_id=update_id)
    else:
        fid = _flow_templates.google_login_totp(flow_id=update_id)

    print(f"Flow {'updated' if update_id else 'created'} successfully.")
    print(f"  Flow ID: {fid}\n")
    print("Next steps:")
    print(f"  1. Set this flow on your accounts:")
    print(f"       python setup_login_flow.py --set {fid}")
    print(f"  2. Test a login:")
    print(f"       python login_check_run.py --all")
    print(f"\nTo update this flow in future (without changing the ID):")
    print(f"  python setup_login_flow.py --create --update {fid}")


def cmd_export(flow_id: str):
    client = GeelarKClient()
    print(f"Exporting flow {flow_id} …")
    gal_str = client.export_rpa_flow(flow_id)
    if not gal_str:
        print("ERROR: No data returned. Check the flow ID is correct.")
        return
    # Pretty-print the nested GAL JSON
    try:
        gal_obj = json.loads(gal_str)
        pretty  = json.dumps(gal_obj, indent=2, ensure_ascii=False)
    except Exception:
        pretty = gal_str
    out = Path(__file__).parent.parent / "research" / f"geelark_flow_{flow_id}.json"
    out.write_text(pretty, encoding="utf-8")
    print(f"Saved to {out}")


def main():
    parser = argparse.ArgumentParser(description="Configure GeelarK login flow")
    parser.add_argument("--list",               action="store_true",
                        help="List saved flows")
    parser.add_argument("--check",              action="store_true",
                        help="Show flow_id on each account")
    parser.add_argument("--create",             action="store_true",
                        help="Build and import the Google login + TOTP flow via the API")
    parser.add_argument("--with-method-select", action="store_true",
                        help="Use the variant that handles the 2FA method selection screen")
    parser.add_argument("--update",             metavar="FLOW_ID",
                        help="Update an existing flow in-place (use with --create)")
    parser.add_argument("--set",                metavar="FLOW_ID",
                        help="Set flow_id on accounts")
    parser.add_argument("--account",            metavar="ACC_ID",
                        help="Limit --set to one account")
    parser.add_argument("--export",             metavar="FLOW_ID",
                        help="Export flow JSON to file")
    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.check:
        cmd_check()
    elif args.create:
        cmd_create(
            with_method_select=args.with_method_select,
            update_id=args.update,
        )
    elif args.set:
        cmd_set(args.set, args.account)
    elif args.export:
        cmd_export(args.export)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
