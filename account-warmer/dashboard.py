"""
Account Warmer Dashboard
========================
A CustomTkinter desktop GUI for managing Google account warming sessions.

Run from the account-warmer/ directory:
    python dashboard.py

Requires:
    pip install customtkinter
    (all other dependencies already in requirements.txt)
"""

import json
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path

import customtkinter as ctk
import tkinter as tk
from tkinter import ttk
import yaml

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
CONFIG_DIR      = BASE_DIR / "config"
LOGS_DIR        = BASE_DIR / "logs"
STATE_DIR       = LOGS_DIR / "state"
ACCOUNTS_FILE   = CONFIG_DIR / "accounts.yaml"
BUSINESSES_FILE = CONFIG_DIR / "businesses.yaml"
AGER_CONFIG     = CONFIG_DIR / "profile_ager.yaml"
EMU_STATE_FILE  = STATE_DIR / "emulator_users.json"
ML_TOKEN_FILE   = STATE_DIR / "ml_token.json"

# ── Appearance ─────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

FONT_NORMAL = ("Segoe UI", 11)
FONT_BOLD   = ("Segoe UI", 11, "bold")
FONT_MONO   = ("Consolas", 11)
COLOUR_OK   = "#44cc44"
COLOUR_WARN = "#ccaa44"
COLOUR_ERR  = "#cc4444"
COLOUR_DIM  = "#888888"

# ── Data helpers ───────────────────────────────────────────────────────────────

def _load_yaml(path: Path):
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_accounts() -> list:
    data = _load_yaml(ACCOUNTS_FILE)
    return data.get("accounts", []) if isinstance(data, dict) else []


def load_businesses() -> list:
    data = _load_yaml(BUSINESSES_FILE)
    return data.get("businesses", []) if isinstance(data, dict) else []


def load_ager_config() -> tuple:
    data = _load_yaml(AGER_CONFIG)
    if not isinstance(data, dict):
        return {"target_age_days": 7, "min_sessions": 5}, []
    return data.get("settings", {"target_age_days": 7, "min_sessions": 5}), \
           data.get("profiles", [])


def biz_signal_state(acc_id: str, biz_id: str) -> dict:
    return _load_json(STATE_DIR / f"{acc_id}_biz_{biz_id}.json")


def weeks_elapsed(account: dict) -> int:
    start_str = account.get("warmup_start_date", str(date.today()))
    start = date.fromisoformat(str(start_str))
    return max(0, (date.today() - start).days // 7)


def week_label(account: dict) -> str:
    w = weeks_elapsed(account)
    if w < 2:   return "Wk 1-2"
    if w < 4:   return "Wk 3-4"
    if w < 6:   return "Wk 5-6"
    return f"Wk {w + 1}+"


def emulator_stage(account: dict) -> str:
    w = weeks_elapsed(account)
    if w < 2:   return "Home area (wks 1-2)"
    if w < 4:   return "Local places (wks 2-4)"
    return "Target business (wks 4+)"


def ran_today(account: dict) -> bool:
    log_file = LOGS_DIR / f"{account['id']}.log"
    if not log_file.exists():
        return False
    today = date.today().isoformat()
    try:
        with open(log_file, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if today in line and "Session" in line:
                    return True
    except Exception:
        pass
    return False


def ml_status() -> tuple:
    """Returns (is_ok: bool, status_text: str)."""
    data = _load_json(ML_TOKEN_FILE)
    if not data or not data.get("token"):
        return False, "Not signed in"
    age_hours = (time.time() - data.get("fetched_at", 0)) / 3600
    if age_hours > 23:
        return False, "Token expired — run a script to refresh"
    remaining = int(23 - age_hours)
    return True, f"Signed in  (token valid ~{remaining}h)"


def get_alerts(accounts: list, businesses: list) -> list:
    """Returns list of (level, message) tuples."""
    alerts = []
    biz_map  = {b["id"]: b for b in businesses}
    now_hour = datetime.now().hour

    for acc in accounts:
        active = acc.get("active_hours", [7, 23])
        if not ran_today(acc) and active[0] <= now_hour <= active[1]:
            alerts.append(("warn", f"{acc['id']}  —  has not run today (within active hours {active[0]}:00–{active[1]}:00)"))

        for biz_id in acc.get("target_businesses", []):
            biz   = biz_map.get(biz_id)
            if not biz:
                continue
            state = biz_signal_state(acc["id"], biz_id)
            name  = biz.get("name", biz_id)

            if not state:
                alerts.append(("info", f"{acc['id']} / {name}  —  Phase 1 not started yet"))
            elif state.get("phase_1_complete") and not state.get("phase_2_complete"):
                p1_date = state.get("phase_1_date", "")
                if p1_date and p1_date != str(date.today()):
                    alerts.append(("info", f"{acc['id']} / {name}  —  Phase 2 ready to run"))
            elif state.get("phase_2_complete") and not state.get("phase_3_complete"):
                appt = state.get("appointment_date")
                if appt and date.today() >= date.fromisoformat(appt):
                    p2_date = state.get("phase_2_date", "")
                    if p2_date and p2_date != str(date.today()):
                        alerts.append(("info", f"{acc['id']} / {name}  —  Phase 3 ready to run"))

    return alerts


# ── Treeview style ─────────────────────────────────────────────────────────────

def _apply_treeview_style():
    style = ttk.Style()
    style.theme_use("clam")
    style.configure(
        "Warmer.Treeview",
        background="#2b2b2b",
        foreground="white",
        fieldbackground="#2b2b2b",
        rowheight=28,
        font=FONT_NORMAL,
        borderwidth=0,
    )
    style.configure(
        "Warmer.Treeview.Heading",
        background="#1a1a2e",
        foreground="#aaaaaa",
        font=FONT_BOLD,
        relief="flat",
    )
    style.map(
        "Warmer.Treeview",
        background=[("selected", "#1f538d")],
        foreground=[("selected", "white")],
    )


def _make_treeview(parent, columns: list, height: int = 10, selectmode: str = "browse"):
    """
    columns: list of (col_id, heading_text, width_px)
    Returns (outer_frame, treeview_widget)
    """
    frame   = tk.Frame(parent, bg="#2b2b2b")
    col_ids = [c[0] for c in columns]
    tree    = ttk.Treeview(
        frame, columns=col_ids, show="headings",
        style="Warmer.Treeview", selectmode=selectmode, height=height,
    )
    for col_id, heading, width in columns:
        tree.heading(col_id, text=heading)
        tree.column(col_id, width=width, anchor="w", stretch=False)
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")
    return frame, tree


# ── Main Application ───────────────────────────────────────────────────────────

class WarmerDashboard(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("Account Warmer Dashboard")
        self.geometry("1160x740")
        self.minsize(980, 640)
        self._process = None
        self._stop_requested = False
        _apply_treeview_style()
        self._build_ui()
        self.refresh_all()
        self._schedule_auto_refresh()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # Top status bar
        bar = ctk.CTkFrame(self, height=40, corner_radius=0, fg_color="#1a1a2e")
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        self.lbl_ml = ctk.CTkLabel(
            bar, text="Multilogin: checking...", anchor="w",
            font=ctk.CTkFont(size=12),
        )
        self.lbl_ml.pack(side="left", padx=16, pady=10)

        self.lbl_counts = ctk.CTkLabel(
            bar, text="", anchor="w",
            font=ctk.CTkFont(size=12), text_color=COLOUR_DIM,
        )
        self.lbl_counts.pack(side="left", padx=16)

        ctk.CTkButton(
            bar, text="Refresh", width=80, height=28,
            command=self.refresh_all,
        ).pack(side="right", padx=12, pady=6)

        self.lbl_clock = ctk.CTkLabel(
            bar, text="", anchor="e",
            font=ctk.CTkFont(size=12), text_color=COLOUR_DIM,
        )
        self.lbl_clock.pack(side="right", padx=4)

        # Tabs
        self.tabs = ctk.CTkTabview(self, anchor="nw")
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        for name in ("Overview", "Run", "Businesses", "Emulator", "Pipeline", "Proxies", "Logs"):
            self.tabs.add(name)

        self._build_overview()
        self._build_run()
        self._build_businesses()
        self._build_emulator()
        self._build_pipeline()
        self._build_proxies()
        self._build_logs()

        self._tick_clock()

    def _tick_clock(self):
        self.lbl_clock.configure(text=datetime.now().strftime("%Y-%m-%d   %H:%M:%S"))
        self.after(1000, self._tick_clock)

    # ── Overview tab ──────────────────────────────────────────────────────────

    def _build_overview(self):
        tab = self.tabs.tab("Overview")

        ctk.CTkLabel(
            tab, text="Accounts",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=12, pady=(10, 4))

        cols = [
            ("id",       "ID",       90),
            ("email",    "Email",    250),
            ("location", "Location", 130),
            ("strategy", "Strategy", 105),
            ("week",     "Week",     70),
            ("status",   "Today",    80),
        ]
        tree_frame, self.acc_tree = _make_treeview(tab, cols, height=7, selectmode="extended")
        tree_frame.pack(fill="x", padx=12, pady=(0, 4))
        self.acc_tree.tag_configure("done",    foreground=COLOUR_OK)
        self.acc_tree.tag_configure("pending", foreground=COLOUR_WARN)
        self.acc_tree.bind("<Double-1>", self._on_account_double_click)
        self.acc_tree.bind("<<TreeviewSelect>>", self._on_acc_selection_changed)

        sel_row = ctk.CTkFrame(tab, fg_color="transparent")
        sel_row.pack(fill="x", padx=12, pady=(0, 6))
        self.lbl_sel_count = ctk.CTkLabel(
            sel_row, text="0 selected", text_color=COLOUR_DIM,
            font=ctk.CTkFont(size=11),
        )
        self.lbl_sel_count.pack(side="left")
        ctk.CTkButton(
            sel_row, text="Select all", width=90, height=24, fg_color="#444",
            font=ctk.CTkFont(size=11),
            command=self._select_all_accounts,
        ).pack(side="left", padx=(10, 4))
        ctk.CTkButton(
            sel_row, text="Select none", width=90, height=24, fg_color="#444",
            font=ctk.CTkFont(size=11),
            command=lambda: self.acc_tree.selection_set([]),
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            sel_row, text="Select pending", width=110, height=24, fg_color="#444",
            font=ctk.CTkFont(size=11),
            command=self._select_pending_accounts,
        ).pack(side="left", padx=(0, 4))

        ctk.CTkLabel(
            tab, text="Alerts",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=12, pady=(4, 2))

        self.alerts_box = ctk.CTkTextbox(
            tab, height=100, state="disabled",
            font=ctk.CTkFont(size=12),
        )
        self.alerts_box.pack(fill="x", padx=12, pady=(0, 10))

        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkButton(
            btn_row, text="Run all now", width=140,
            command=lambda: self._run_command(["python", "run.py", "--all"]),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Dry run all", width=130, fg_color="#444",
            command=lambda: self._run_command(["python", "run.py", "--all", "--dry-run"]),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Run emulator (all)", width=160, fg_color="#444",
            command=lambda: self._run_command(["python", "emulator_sessions.py", "--all"]),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Run selected", width=130, fg_color="#1f6aa5",
            command=self._run_selected_accounts,
        ).pack(side="left")

    # ── Run tab ───────────────────────────────────────────────────────────────

    def _build_run(self):
        tab = self.tabs.tab("Run")

        ctrl = ctk.CTkFrame(tab)
        ctrl.pack(fill="x", padx=10, pady=(10, 6))

        # Row 1 — account + script selection
        r1 = ctk.CTkFrame(ctrl, fg_color="transparent")
        r1.pack(fill="x", padx=12, pady=(12, 4))

        ctk.CTkLabel(r1, text="Account:", width=110, anchor="w").pack(side="left")
        self.run_acc_var  = ctk.StringVar(value="-- All --")
        self.run_acc_menu = ctk.CTkOptionMenu(
            r1, variable=self.run_acc_var, values=["-- All --"], width=230,
        )
        self.run_acc_menu.pack(side="left", padx=(0, 24))

        ctk.CTkLabel(r1, text="Script:", width=60, anchor="w").pack(side="left")
        self.run_script_var = ctk.StringVar(value="run.py")
        ctk.CTkOptionMenu(
            r1, variable=self.run_script_var, width=210,
            values=[
                "run.py", "emulator_sessions.py", "profile_ager.py",
                "pin_warmer.py", "add_business.py", "warmer.py",
            ],
        ).pack(side="left")

        # Row 2 — overrides
        r2 = ctk.CTkFrame(ctrl, fg_color="transparent")
        r2.pack(fill="x", padx=12, pady=4)

        ctk.CTkLabel(r2, text="Force week:", width=110, anchor="w").pack(side="left")
        self.force_week_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            r2, textvariable=self.force_week_var,
            width=64, placeholder_text="auto",
        ).pack(side="left", padx=(0, 24))

        ctk.CTkLabel(r2, text="Force activity:", width=110, anchor="w").pack(side="left")
        self.force_act_var = ctk.StringVar(value="")
        ctk.CTkOptionMenu(
            r2, variable=self.force_act_var, width=210,
            values=[
                "", "search", "email_read", "email_send", "browse",
                "maps_browse", "maps_review", "calendar_setup",
                "calendar_browse", "calendar_add", "drive_setup",
                "drive_browse", "drive_edit", "drive_create",
                "newsletter_signup",
            ],
        ).pack(side="left")

        # Row 3 — dry run toggle + run/stop buttons
        r3 = ctk.CTkFrame(ctrl, fg_color="transparent")
        r3.pack(fill="x", padx=12, pady=(4, 12))

        self.dry_run_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            r3, text="Dry run (preview only — no browser opens)",
            variable=self.dry_run_var,
        ).pack(side="left")

        self.btn_stop = ctk.CTkButton(
            r3, text="Stop", width=84,
            fg_color=COLOUR_ERR, hover_color="#991111",
            command=self._stop_process, state="disabled",
        )
        self.btn_stop.pack(side="right", padx=(8, 0))
        ctk.CTkButton(r3, text="RUN", width=100, command=self._run_from_tab).pack(side="right")

        ctk.CTkLabel(
            tab, text="Output",
            font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
        ).pack(anchor="w", padx=14, pady=(2, 2))

        self.run_output = ctk.CTkTextbox(
            tab, font=ctk.CTkFont(family="Consolas", size=11),
            state="disabled", wrap="none",
        )
        self.run_output.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ── Businesses tab ────────────────────────────────────────────────────────

    def _build_businesses(self):
        tab = self.tabs.tab("Businesses")

        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 6))
        ctk.CTkLabel(
            top, text="Target Businesses",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")
        ctk.CTkButton(
            top, text="+ Add business", width=130,
            command=lambda: self._run_command(["python", "add_business.py"]),
        ).pack(side="right")

        self.biz_scroll = ctk.CTkScrollableFrame(tab)
        self.biz_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ── Emulator tab ──────────────────────────────────────────────────────────

    def _build_emulator(self):
        tab = self.tabs.tab("Emulator")

        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 6))

        self.lbl_adb  = ctk.CTkLabel(top, text="ADB: checking...", anchor="w",
                                     font=ctk.CTkFont(size=12))
        self.lbl_adb.pack(side="left", padx=(4, 0))

        self.lbl_imei = ctk.CTkLabel(top, text="", anchor="w",
                                     font=ctk.CTkFont(size=12), text_color=COLOUR_DIM)
        self.lbl_imei.pack(side="left", padx=20)

        cols = [
            ("id",        "Account",       105),
            ("user",      "Android User",  115),
            ("biz_visit", "Business Visit",130),
            ("emu_done",  "Emulator Done", 115),
            ("stage",     "Current GPS Stage", 210),
        ]
        tree_frame, self.emu_tree = _make_treeview(tab, cols, height=8)
        tree_frame.pack(fill="x", padx=12, pady=(0, 6))

        ctk.CTkLabel(
            tab,
            text="GPS schedule:   Wks 1-2: home area   |   Wks 2-4: local places   |   Wks 4+: target business",
            text_color=COLOUR_DIM, font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=16, pady=(0, 8))

        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(
            btn_row, text="Run all sessions", width=160,
            command=lambda: self._run_command(["python", "emulator_sessions.py", "--all"]),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Dry run", width=100, fg_color="#444",
            command=lambda: self._run_command(["python", "emulator_sessions.py", "--all", "--dry-run"]),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Setup (first time)", width=155, fg_color="#444",
            command=lambda: self._run_command(["python", "emulator_sessions.py", "--setup"]),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Show config", width=120, fg_color="#444",
            command=lambda: self._run_command(["python", "emulator_sessions.py", "--show-config"]),
        ).pack(side="left")

    # ── Pipeline tab ──────────────────────────────────────────────────────────

    def _build_pipeline(self):
        tab = self.tabs.tab("Pipeline")

        ctk.CTkLabel(
            tab, text="Profile Aging Pipeline",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(12, 2))
        ctk.CTkLabel(
            tab,
            text="Age Multilogin profiles for 7 days before creating Google accounts in them.",
            text_color=COLOUR_DIM, font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=14, pady=(0, 8))

        cols = [
            ("pid",      "Profile ID (Multilogin UUID)", 300),
            ("location", "Location",  130),
            ("days",     "Days aged",  90),
            ("sessions", "Sessions",   80),
            ("status",   "Status",    110),
        ]
        tree_frame, self.pipeline_tree = _make_treeview(tab, cols, height=6)
        tree_frame.pack(fill="x", padx=12, pady=(0, 8))
        self.pipeline_tree.tag_configure("ready", foreground=COLOUR_OK)
        self.pipeline_tree.tag_configure("used",  foreground=COLOUR_DIM)
        self.pipeline_tree.tag_configure("aging", foreground=COLOUR_WARN)

        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(
            btn_row, text="Run profile ager", width=155,
            command=lambda: self._run_command(["python", "profile_ager.py", "--all"]),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Check readiness", width=145, fg_color="#444",
            command=lambda: self._run_command(["python", "profile_ager.py", "--check"]),
        ).pack(side="left")

        # Creation checklist card
        cl = ctk.CTkFrame(tab)
        cl.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkLabel(
            cl,
            text="Account creation checklist — follow when a profile shows READY",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(10, 6))
        steps = [
            "Open the profile in Multilogin",
            "Go to  accounts.google.com/signup  using mobile data  (NOT home WiFi)",
            "Use a fresh SMS-Activate number for verification — never reuse a number",
            "Add the account to  config/accounts.yaml",
            "Run:   python profile_ager.py --mark-done <profile_id>",
        ]
        for i, step in enumerate(steps, 1):
            ctk.CTkLabel(
                cl, text=f"  {i}.   {step}",
                anchor="w", font=ctk.CTkFont(size=11),
            ).pack(anchor="w", padx=14)
        ctk.CTkLabel(cl, text="").pack(pady=4)

    # ── Proxies tab ───────────────────────────────────────────────────────────

    def _build_proxies(self):
        tab = self.tabs.tab("Proxies")

        ctk.CTkLabel(
            tab, text="Proxy Configuration",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(12, 2))
        ctk.CTkLabel(
            tab,
            text="Shows the proxy set in accounts.yaml for each account. Use the buttons to push changes to Multilogin.",
            text_color=COLOUR_DIM, font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=14, pady=(0, 8))

        cols = [
            ("id",       "Account",  90),
            ("email",    "Email",   210),
            ("type",     "Type",     70),
            ("host",     "Host",    200),
            ("port",     "Port",     60),
            ("username", "Username",260),
        ]
        tree_frame, self.proxy_tree = _make_treeview(tab, cols, height=8)
        tree_frame.pack(fill="x", padx=12, pady=(0, 8))

        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkButton(
            btn_row, text="Push all to Multilogin", width=185,
            command=lambda: self._run_command(["python", "set_proxies.py", "--all"]),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Push selected", width=130, fg_color="#444",
            command=self._push_selected_proxy,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Check vs Multilogin", width=160, fg_color="#444",
            command=lambda: self._run_command(["python", "set_proxies.py", "--check"]),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Dry run", width=100, fg_color="#444",
            command=lambda: self._run_command(["python", "set_proxies.py", "--all", "--dry-run"]),
        ).pack(side="left")

        ctk.CTkLabel(
            tab,
            text="To update a proxy: edit the proxy: field in config/accounts.yaml, then click Push.",
            text_color=COLOUR_DIM, font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=14, pady=(4, 0))

    # ── Logs tab ──────────────────────────────────────────────────────────────

    def _build_logs(self):
        tab = self.tabs.tab("Logs")

        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 4))

        ctk.CTkLabel(top, text="Account:").pack(side="left", padx=(4, 6))
        self.log_acc_var  = ctk.StringVar(value="combined")
        self.log_acc_menu = ctk.CTkOptionMenu(
            top, variable=self.log_acc_var,
            values=["combined"], width=190,
            command=self._load_logs,
        )
        self.log_acc_menu.pack(side="left", padx=(0, 12))
        ctk.CTkButton(top, text="Refresh", width=90,
                      command=self._load_logs).pack(side="left")

        self.lbl_log_info = ctk.CTkLabel(
            top, text="", text_color=COLOUR_DIM, font=ctk.CTkFont(size=11),
        )
        self.lbl_log_info.pack(side="right", padx=8)

        self.log_box = ctk.CTkTextbox(
            tab, font=ctk.CTkFont(family="Consolas", size=11),
            state="disabled", wrap="none",
        )
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ── Refresh methods ───────────────────────────────────────────────────────

    def refresh_all(self):
        accounts   = load_accounts()
        businesses = load_businesses()

        # Status bar
        ok, ml_text = ml_status()
        self.lbl_ml.configure(
            text=f"Multilogin:  {ml_text}",
            text_color=COLOUR_OK if ok else COLOUR_WARN,
        )
        ran  = sum(1 for a in accounts if ran_today(a))
        pend = len(accounts) - ran
        self.lbl_counts.configure(
            text=f"{len(accounts)} accounts   |   {ran} ran today   |   {pend} pending"
        )

        self._refresh_overview(accounts, businesses)
        self._refresh_run_menu(accounts)
        self._refresh_businesses(accounts, businesses)
        self._refresh_emulator(accounts)
        self._refresh_pipeline()
        self._refresh_proxies(accounts)
        self._refresh_log_menu(accounts)

    def _refresh_overview(self, accounts: list, businesses: list):
        for row in self.acc_tree.get_children():
            self.acc_tree.delete(row)

        for acc in accounts:
            done = ran_today(acc)
            self.acc_tree.insert(
                "", "end", iid=acc["id"],
                tags=("done" if done else "pending",),
                values=(
                    acc["id"],
                    acc.get("email", ""),
                    acc.get("location", ""),
                    acc.get("strategy", "standard"),
                    week_label(acc),
                    "DONE" if done else "PENDING",
                ),
            )

        alerts = get_alerts(accounts, businesses)
        self.alerts_box.configure(state="normal")
        self.alerts_box.delete("1.0", "end")
        if not alerts:
            self.alerts_box.insert("end", "  No alerts.\n")
        else:
            for level, msg in alerts:
                icon = "!" if level == "warn" else "i"
                self.alerts_box.insert("end", f"  [{icon}]  {msg}\n")
        self.alerts_box.configure(state="disabled")

    def _refresh_run_menu(self, accounts: list):
        opts = ["-- All --"] + [a["id"] for a in accounts]
        self.run_acc_menu.configure(values=opts)

    def _refresh_businesses(self, accounts: list, businesses: list):
        for widget in self.biz_scroll.winfo_children():
            widget.destroy()

        if not businesses:
            ctk.CTkLabel(
                self.biz_scroll,
                text="No businesses configured yet.   Click '+ Add business' to add one.",
                text_color=COLOUR_DIM,
            ).pack(pady=20)
            return

        for biz in businesses:
            card = ctk.CTkFrame(self.biz_scroll, corner_radius=8)
            card.pack(fill="x", padx=4, pady=4)

            # Business header
            hdr = ctk.CTkFrame(card, fg_color="transparent")
            hdr.pack(fill="x", padx=12, pady=(8, 4))
            ctk.CTkLabel(
                hdr, text=biz.get("name", biz["id"]),
                font=ctk.CTkFont(size=13, weight="bold"),
            ).pack(side="left")
            ctk.CTkLabel(
                hdr,
                text=f"   {biz.get('type', '')}   |   {biz.get('address', '')}",
                text_color=COLOUR_DIM, font=ctk.CTkFont(size=11),
            ).pack(side="left")

            assigned = [a for a in accounts if biz["id"] in a.get("target_businesses", [])]
            if not assigned:
                ctk.CTkLabel(
                    card, text="  No accounts assigned.",
                    text_color=COLOUR_DIM, font=ctk.CTkFont(size=11),
                ).pack(anchor="w", padx=12, pady=(0, 8))
                continue

            for acc in assigned:
                state    = biz_signal_state(acc["id"], biz["id"])
                p1       = state.get("phase_1_complete", False)
                p2       = state.get("phase_2_complete", False)
                p3       = state.get("phase_3_complete", False)
                emu_done = state.get("emulator_visit_done", False)
                review   = state.get("review_eligible", False)

                def _phase_label(done, prev_done, prev_date_key, appt_key=None):
                    if done:
                        return "DONE", COLOUR_OK
                    if not prev_done:
                        return "LOCKED", COLOUR_DIM
                    if appt_key:
                        appt = state.get(appt_key)
                        if appt and date.today() < date.fromisoformat(appt):
                            return "WAIT", COLOUR_DIM
                    prev_date = state.get(prev_date_key, "")
                    if prev_date and prev_date == str(date.today()):
                        return "WAIT", COLOUR_DIM
                    return "READY", COLOUR_WARN

                p1_lbl, p1_col = ("DONE", COLOUR_OK) if p1 else ("PENDING", COLOUR_WARN)
                p2_lbl, p2_col = _phase_label(p2, p1, "phase_1_date")
                p3_lbl, p3_col = _phase_label(p3, p2, "phase_2_date", "appointment_date")

                row = ctk.CTkFrame(card, fg_color="#1e1e1e", corner_radius=6)
                row.pack(fill="x", padx=12, pady=(0, 4))

                ctk.CTkLabel(
                    row, text=acc["id"], width=80, anchor="w",
                    font=ctk.CTkFont(size=12, weight="bold"),
                ).pack(side="left", padx=(10, 8), pady=6)

                for num, label, lbl, col in [
                    ("1", "Discovery",  p1_lbl, p1_col),
                    ("2", "Intent",     p2_lbl, p2_col),
                    ("3", "Post-visit", p3_lbl, p3_col),
                ]:
                    ctk.CTkLabel(
                        row, text=f"Ph{num} {label}:",
                        text_color=COLOUR_DIM, font=ctk.CTkFont(size=11),
                    ).pack(side="left", padx=(8, 2))
                    ctk.CTkLabel(
                        row, text=lbl, text_color=col,
                        font=ctk.CTkFont(size=11, weight="bold"),
                    ).pack(side="left")

                ctk.CTkLabel(
                    row, text="  GPS visit:",
                    text_color=COLOUR_DIM, font=ctk.CTkFont(size=11),
                ).pack(side="left", padx=(16, 2))
                ctk.CTkLabel(
                    row,
                    text="DONE" if emu_done else "NO",
                    text_color=COLOUR_OK if emu_done else COLOUR_DIM,
                    font=ctk.CTkFont(size=11, weight="bold"),
                ).pack(side="left")

                ctk.CTkLabel(
                    row, text="  Review eligible:",
                    text_color=COLOUR_DIM, font=ctk.CTkFont(size=11),
                ).pack(side="left", padx=(16, 2))
                ctk.CTkLabel(
                    row,
                    text="YES" if review else "NO",
                    text_color=COLOUR_OK if review else COLOUR_DIM,
                    font=ctk.CTkFont(size=11, weight="bold"),
                ).pack(side="left", padx=(0, 10))

            ctk.CTkLabel(card, text="").pack(pady=2)

    def _refresh_emulator(self, accounts: list):
        for row in self.emu_tree.get_children():
            self.emu_tree.delete(row)

        emu_state = _load_json(EMU_STATE_FILE)
        user_map  = emu_state.get("user_map", {})
        imei      = emu_state.get("device_imei", "")
        mfr       = emu_state.get("device_manufacturer", "")
        model     = emu_state.get("device_model", "")

        if imei:
            self.lbl_imei.configure(text=f"IMEI: {imei}  ({mfr} {model})")
        else:
            self.lbl_imei.configure(text="IMEI: not assigned — run Setup first")

        for acc in accounts:
            uid       = user_map.get(acc["id"], "Not set up — run Setup")
            biz_visit = "—"
            emu_done  = "—"
            for biz_id in acc.get("target_businesses", []):
                s = biz_signal_state(acc["id"], biz_id)
                if s.get("emulator_visit_date"):
                    biz_visit = s["emulator_visit_date"]
                emu_done = "YES" if s.get("emulator_visit_done") else "NO"

            self.emu_tree.insert("", "end", values=(
                acc["id"],
                uid,
                biz_visit,
                emu_done,
                emulator_stage(acc),
            ))

        # ADB check runs in background thread so UI never hangs
        threading.Thread(target=self._check_adb, daemon=True).start()

    def _check_adb(self):
        adb_candidates = [
            r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
            r"C:\LDPlayer\LDPlayer9\adb.exe",
            str(Path.home() / "AppData" / "Local" / "Android" / "Sdk" /
                "platform-tools" / "adb.exe"),
            "adb",
        ]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        for adb_exe in adb_candidates:
            try:
                result = subprocess.run(
                    [adb_exe, "devices"],
                    capture_output=True, text=True, timeout=4,
                    creationflags=flags,
                )
                if result.returncode == 0:
                    device_lines = [
                        l for l in result.stdout.strip().splitlines()
                        if l.strip() and "List of devices" not in l
                    ]
                    count = len(device_lines)
                    if count > 0:
                        text, col = f"ADB: CONNECTED  ({count} device{'s' if count > 1 else ''})", COLOUR_OK
                    else:
                        text, col = "ADB: No devices detected", COLOUR_WARN
                    self.after(0, lambda t=text, c=col: self.lbl_adb.configure(text=t, text_color=c))
                    return
            except Exception:
                continue
        self.after(0, lambda: self.lbl_adb.configure(
            text="ADB: Not found  (install Bluestacks or Android Studio SDK)",
            text_color=COLOUR_ERR,
        ))

    def _refresh_pipeline(self):
        for row in self.pipeline_tree.get_children():
            self.pipeline_tree.delete(row)

        settings, profiles = load_ager_config()
        target_days  = settings.get("target_age_days", 7)
        min_sessions = settings.get("min_sessions", 5)

        # Filter out placeholder profiles that haven't been filled in
        real_profiles = [
            p for p in profiles
            if not p.get("multilogin_profile_id", "").startswith("paste")
        ]
        if not real_profiles:
            self.pipeline_tree.insert("", "end", values=(
                "No profiles configured — edit config/profile_ager.yaml", "", "", "", "",
            ))
            return

        for p in real_profiles:
            pid      = p.get("id", "")
            state    = _load_json(STATE_DIR / f"ager_{pid}.json")
            days     = state.get("days_aged", 0)
            sessions = state.get("sessions_completed", 0)
            done     = state.get("account_created", False)

            if done:
                status, tag = "USED", "used"
            elif days >= target_days and sessions >= min_sessions:
                status, tag = "READY", "ready"
            else:
                status = f"{days}/{target_days}d  {sessions}/{min_sessions}s"
                tag    = "aging"

            self.pipeline_tree.insert("", "end", tags=(tag,), values=(
                p.get("multilogin_profile_id", pid),
                p.get("location", ""),
                days,
                sessions,
                status,
            ))

    def _refresh_log_menu(self, accounts: list):
        opts = ["combined", "runner", "warmer"] + [a["id"] for a in accounts]
        self.log_acc_menu.configure(values=opts)
        self._load_logs()

    def _load_logs(self, *_):
        target   = self.log_acc_var.get()
        log_file = LOGS_DIR / f"{target}.log"

        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")

        if not log_file.exists():
            self.log_box.insert("end", f"No log file found:  {log_file}\n")
            self.lbl_log_info.configure(text="")
        else:
            try:
                with open(log_file, encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                shown = lines[-500:]
                self.log_box.insert("end", "".join(shown))
                self.log_box.see("end")
                self.lbl_log_info.configure(
                    text=f"{len(lines)} lines  —  showing last {len(shown)}"
                )
            except Exception as e:
                self.log_box.insert("end", f"Error reading log file: {e}\n")

        self.log_box.configure(state="disabled")

    def _refresh_proxies(self, accounts: list):
        from urllib.parse import urlparse
        for row in self.proxy_tree.get_children():
            self.proxy_tree.delete(row)
        for acc in accounts:
            proxy_url = acc.get("proxy", "")
            try:
                p        = urlparse(proxy_url)
                ptype    = p.scheme.upper() if p.scheme else "—"
                host     = p.hostname or "—"
                port     = str(p.port) if p.port else "—"
                username = p.username or "—"
            except Exception:
                ptype = host = port = username = "parse error"
            self.proxy_tree.insert("", "end", iid=acc["id"], values=(
                acc["id"],
                acc.get("email", ""),
                ptype,
                host,
                port,
                username,
            ))

    def _push_selected_proxy(self):
        sel = self.proxy_tree.selection()
        if not sel:
            return
        acc_id = sel[0]
        self._run_command(["python", "set_proxies.py", "--account", acc_id])

    # ── Run command ───────────────────────────────────────────────────────────

    def _run_from_tab(self):
        script = self.run_script_var.get()
        acc    = self.run_acc_var.get()
        cmd    = ["python", script]

        if acc == "-- All --":
            cmd.append("--all")
        else:
            cmd += ["--account", acc]

        week = self.force_week_var.get().strip()
        if week:
            cmd += ["--week", week]

        act = self.force_act_var.get().strip()
        if act:
            cmd += ["--activity", act]

        if self.dry_run_var.get():
            cmd.append("--dry-run")

        self._run_command(cmd)

    def _run_command(self, cmd: list):
        """Execute a command in a background thread and stream output to the Run tab."""
        self.tabs.set("Run")
        self.run_output.configure(state="normal")
        self.run_output.delete("1.0", "end")
        self.run_output.insert("end", f"> {' '.join(cmd)}\n\n")
        self.run_output.configure(state="disabled")
        self.btn_stop.configure(state="normal")

        def _target():
            try:
                self._process = subprocess.Popen(
                    cmd,
                    cwd=str(BASE_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                for line in self._process.stdout:
                    self._append_output(line)
                self._process.wait()
                self._append_output(
                    f"\n[Process finished — exit code {self._process.returncode}]\n"
                )
            except FileNotFoundError:
                self._append_output(
                    f"Error: script not found — '{cmd[1]}'\n"
                    "Make sure dashboard.py is run from the account-warmer/ directory.\n"
                )
            except Exception as e:
                self._append_output(f"Unexpected error: {e}\n")
            finally:
                self.after(0, lambda: self.btn_stop.configure(state="disabled"))
                self.after(3000, self.refresh_all)

        threading.Thread(target=_target, daemon=True).start()

    def _append_output(self, text: str):
        def _do():
            self.run_output.configure(state="normal")
            self.run_output.insert("end", text)
            self.run_output.see("end")
            self.run_output.configure(state="disabled")
        self.after(0, _do)

    def _stop_process(self):
        self._stop_requested = True
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self._append_output("\n[Process terminated by user]\n")
        self.btn_stop.configure(state="disabled")

    def _on_acc_selection_changed(self, _event=None):
        n = len(self.acc_tree.selection())
        self.lbl_sel_count.configure(text=f"{n} selected")

    def _select_all_accounts(self):
        self.acc_tree.selection_set(self.acc_tree.get_children())

    def _select_pending_accounts(self):
        pending = [iid for iid in self.acc_tree.get_children()
                   if "pending" in self.acc_tree.item(iid, "tags")]
        self.acc_tree.selection_set(pending)

    def _run_selected_accounts(self):
        sel = list(self.acc_tree.selection())
        if not sel:
            return
        cmds = [["python", "run.py", "--account", acc_id] for acc_id in sel]
        self._run_sequential(cmds)

    def _run_sequential(self, cmds: list):
        """Run a list of commands one after another, streaming output to the Run tab."""
        self.tabs.set("Run")
        self.run_output.configure(state="normal")
        self.run_output.delete("1.0", "end")
        ids = [c[c.index("--account") + 1] for c in cmds if "--account" in c]
        self.run_output.insert("end", f"> Campaign: {len(cmds)} account(s) — {', '.join(ids)}\n\n")
        self.run_output.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._stop_requested = False

        def _target():
            for i, cmd in enumerate(cmds, 1):
                if self._stop_requested:
                    self._append_output("\n[Campaign stopped by user]\n")
                    break
                self._append_output(f"── [{i}/{len(cmds)}]  {' '.join(cmd)} ──\n")
                try:
                    self._process = subprocess.Popen(
                        cmd,
                        cwd=str(BASE_DIR),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    )
                    for line in self._process.stdout:
                        self._append_output(line)
                    self._process.wait()
                    if self._process.returncode != 0:
                        self._append_output(f"[Exit code {self._process.returncode}]\n")
                except FileNotFoundError:
                    self._append_output(f"Error: run.py not found — run dashboard from account-warmer/\n")
                    break
                except Exception as e:
                    self._append_output(f"Error: {e}\n")
                    break
            else:
                self._append_output(f"\n[Campaign complete — {len(cmds)} account(s) ran]\n")
            self.after(0, lambda: self.btn_stop.configure(state="disabled"))
            self.after(3000, self.refresh_all)

        threading.Thread(target=_target, daemon=True).start()

    def _on_account_double_click(self, event):
        """Double-click an account row to jump to Run tab with that account pre-selected."""
        sel = self.acc_tree.selection()
        if sel:
            self.run_acc_var.set(sel[0])
            self.run_script_var.set("run.py")
            self.tabs.set("Run")

    # ── Auto refresh ──────────────────────────────────────────────────────────

    def _schedule_auto_refresh(self):
        self.after(60_000, self._auto_refresh)

    def _auto_refresh(self):
        if not (self._process and self._process.poll() is None):
            self.refresh_all()
        self.after(60_000, self._auto_refresh)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = WarmerDashboard()
    app.mainloop()
