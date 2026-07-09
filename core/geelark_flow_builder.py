"""
core/geelark_flow_builder.py — Programmatic GAL flow construction for GeelarK.

Build RPA flows as Python objects and import them via the GeelarK API without
ever touching the rpa.geelark.com visual editor.

Quick start
-----------
    from core.geelark_flow_builder import FlowBuilder, flows

    # Use a pre-built template
    flow_id = flows.google_login_totp()   # creates/updates flow, returns its ID

    # Or build your own
    fb = FlowBuilder("My flow", "Description")
    fb.open_app("com.android.chrome", uri="https://example.com")
    fb.wait(3000)
    fb.wait_for_element("class", "android.widget.EditText")
    fb.type_text("{email}", remark="Enter email")
    fb.press_enter()
    flow_id = fb.save()                   # import and return flow ID

    # Run the flow on a phone
    from core.geelark_client import GeelarKClient
    task_id = GeelarKClient().run_custom_flow(
        flow_id, phone_id,
        param_map={"email": "user@gmail.com", "password": "secret", "totp_secret": "BASE32SECRET"}
    )

GAL format reference: AccountWarmer-Deploy/research/geelark_gal_flow_format.md
"""

from __future__ import annotations

import json
import logging
from typing import Optional

log = logging.getLogger("geelark.flow_builder")


# ── Low-level step constructors ───────────────────────────────────────────────

def _step(step_type: str, remark: str = "", **config_fields) -> dict:
    return {"type": step_type, "config": {"remark": remark, **config_fields}}


def step_open_app(package: str, uri: str = "", timeout_ms: int = 30000,
                  remark: str = "") -> dict:
    """Open an Android app, optionally to a specific URI / URL."""
    cfg: dict = {"packgename": package, "timeout": timeout_ms}
    if uri:
        cfg["uri"] = uri
    return _step("openApp", remark=remark, **cfg)


def step_wait(ms: int, remark: str = "") -> dict:
    """Fixed-duration pause."""
    return _step("waitTime", remark=remark,
                 timeout=ms, timeoutType="fixedValue",
                 timeoutMax=300_000, timeoutMin=1_000)


def step_wait_random(min_ms: int, max_ms: int, remark: str = "") -> dict:
    """Random-duration pause between min_ms and max_ms."""
    return _step("waitTime", remark=remark,
                 timeoutType="randomInterval",
                 timeoutMin=min_ms, timeoutMax=max_ms,
                 timeout=min_ms)


def step_wait_for_element(selector_type: str, selector: str,
                          search_time_ms: int = 15_000,
                          variable: str = "", remark: str = "",
                          error_type: str = None) -> dict:
    """Wait for an element to appear on screen (blocks until found or timeout).
    error_type: "skip" or "stop" — overrides the flow-level default for this step."""
    cfg = {
        "filters": [{"type": selector_type, "content": selector}],
        "searchTime": search_time_ms,
        "serial": 1, "serialType": "fixedValue",
        "variable": variable,
    }
    if error_type is not None:
        cfg["errorType"] = error_type
    return _step("waitEle", remark=remark, **cfg)


def step_click(selector_type: str, selector: str,
               search_time_ms: int = 5_000,
               serial: int = 1,
               save_result_to: str = "",
               remark: str = "",
               error_type: str = None) -> dict:
    """Click an element found by selector.
    error_type: "skip" or "stop" — overrides the flow-level default for this step."""
    cfg = {
        "filters": [{"type": selector_type, "content": selector}],
        "searchTime": search_time_ms,
        "serial": serial, "serialMin": 1, "serialMax": 50,
        "serialType": "fixedValue",
        "variable": save_result_to,
    }
    if error_type is not None:
        cfg["errorType"] = error_type
    return _step("click", remark=remark, **cfg)


def step_click_coord(x: int, y: int, random_range: int = 0,
                     remark: str = "") -> dict:
    """Click at an absolute screen coordinate."""
    return _step("clickCoord", remark=remark, x=x, y=y, randomRange=random_range)


def step_type_text(text: str,
                   selector_type: str = "class",
                   selector: str = "android.widget.EditText",
                   serial: int = 1,
                   clear: bool = True,
                   search_time_ms: int = 5_000,
                   remark: str = "") -> dict:
    """Type text into an element found by selector. Supports {variable} substitution."""
    return _step("inputText", remark=remark,
                 filters=[{"type": selector_type, "content": selector}],
                 text=text, clearText=clear,
                 serial=serial, serialType="fixedValue",
                 searchTime=search_time_ms)


def step_type_at_coord(text: str, x: int, y: int,
                       clear: bool = True, remark: str = "") -> dict:
    """Type text at a screen coordinate."""
    return _step("inputText", remark=remark,
                 x=x, y=y, text=text, clearText=clear,
                 inputMode="coord")


def step_press_key(key: str, remark: str = "") -> dict:
    """Press a key: 'enter', 'back', or 'del'."""
    return _step("pressKey", remark=remark, key=key)


def step_key_option(key_type: str, remark: str = "") -> dict:
    """
    Press a virtual key via keyOption (visual-editor style).
    key_type: 'enter' (submits Maps search), 'search', 'back', 'del', etc.
    NOTE: in Maps, keyOption enter submits the search; pressKey enter does NOT.
    """
    return _step("keyOption", remark=remark, keyType=key_type)


def step_back(remark: str = "Go back") -> dict:
    return _step("back", remark=remark)


def step_home(remark: str = "Go home") -> dict:
    return _step("home", remark=remark)


def step_scroll(direction: str = "bottom",
                min_px: int = 500, max_px: int = 700,
                position: list = None,
                remark: str = "") -> dict:
    """Scroll the screen. direction: 'top'|'bottom'|'left'|'right'."""
    return _step("scrollPage", remark=remark,
                 direction=direction,
                 distanceMin=min_px, distanceMax=max_px,
                 position=position or [540, 960],
                 randomWheelSleepTime=[300, 500])


def step_totp(key: str = "{totp_secret}", output_variable: str = "totp_code",
              remark: str = "Compute TOTP code") -> dict:
    """Generate a TOTP 6-digit code from a Base32 secret. Saves result to output_variable."""
    return _step("getData", remark=remark,
                 dataType="authenticatorCode",
                 key=key, variable=output_variable)


def step_get_element_attr(selector_type: str, selector: str,
                          get_type: str = "text",
                          variable: str = "",
                          search_time_ms: int = 5_000,
                          remark: str = "") -> dict:
    """Read an attribute of a UI element into a variable. get_type: 'text'|'desc'|'id'|etc."""
    return _step("getData", remark=remark,
                 dataType="getElement",
                 filters=[{"type": selector_type, "content": selector}],
                 searchTime=search_time_ms,
                 serial=1, serialType="fixedValue",
                 getType=get_type, variable=variable)


def step_http_get(url: str, variable: str = "", remark: str = "") -> dict:
    """Make an HTTP GET request and save the response body to a variable."""
    return _step("getData", remark=remark,
                 dataType="networkRequest",
                 url=url, variable=variable)


def step_if(condition_variable: str,
            relation: str = "exist",
            value: str = "",
            then_steps: list = None,
            else_steps: list = None,
            probability: int = None,
            remark: str = "") -> dict:
    """
    Conditional branch.
    relation: 'exist'|'notExist'|'equal'|'notEqual'|'random'
    For 'random': pass probability=30 for 30% chance.
    """
    cfg: dict = {
        "remark": remark,
        "condition": [condition_variable],
        "relation": relation,
        "hiddenChildren": False,
        "children": then_steps or [],
        "other": else_steps or [],
    }
    if relation == "equal" or relation == "notEqual":
        cfg["value"] = value
    if relation == "random" and probability is not None:
        cfg["probability"] = probability
    return {"type": "ifElse", "config": cfg}


def step_loop(times: int, body_steps: list,
              index_variable: str = "loop_index",
              remark: str = "") -> dict:
    """Repeat body_steps a fixed number of times."""
    return {"type": "forTimes", "config": {
        "remark": remark,
        "times": times,
        "variableIndex": index_variable,
        "hiddenChildren": False,
        "children": body_steps,
    }}


# ── FlowBuilder class ─────────────────────────────────────────────────────────

class FlowBuilder:
    """
    Fluent builder for GeelarK GAL flows.

    Usage:
        fb = FlowBuilder("My flow", "Does X, Y, Z")
        fb.open_app("com.android.chrome", uri="https://accounts.google.com/signin")
        fb.wait(5000)
        fb.wait_for_element("class", "android.widget.EditText")
        fb.type_text("{email}")
        fb.press_enter()
        flow_id = fb.save()
    """

    def __init__(self, title: str, desc: str = "",
                 timeout_minutes: int = 30,
                 error_type: str = "skip"):
        self.title = title
        self.desc = desc
        self.timeout_minutes = timeout_minutes
        self.error_type = error_type   # "skip" or "stop"
        self._steps: list[dict] = []

    # ── Append raw step ───────────────────────────────────────────────────────

    def add(self, step: dict) -> "FlowBuilder":
        """Append a raw step dict (use step_* constructors above)."""
        self._steps.append(step)
        return self

    # ── Fluent helpers ────────────────────────────────────────────────────────

    def open_app(self, package: str, uri: str = "",
                 timeout_ms: int = 30_000, remark: str = "") -> "FlowBuilder":
        return self.add(step_open_app(package, uri, timeout_ms, remark))

    def wait(self, ms: int, remark: str = "") -> "FlowBuilder":
        return self.add(step_wait(ms, remark))

    def wait_random(self, min_ms: int, max_ms: int, remark: str = "") -> "FlowBuilder":
        return self.add(step_wait_random(min_ms, max_ms, remark))

    def wait_for_element(self, selector_type: str, selector: str,
                         search_time_ms: int = 15_000,
                         variable: str = "", remark: str = "",
                         error_type: str = None) -> "FlowBuilder":
        return self.add(step_wait_for_element(selector_type, selector,
                                              search_time_ms, variable, remark,
                                              error_type=error_type))

    def click(self, selector_type: str, selector: str,
              search_time_ms: int = 5_000,
              serial: int = 1, remark: str = "",
              error_type: str = None) -> "FlowBuilder":
        return self.add(step_click(selector_type, selector,
                                   search_time_ms, serial, "", remark,
                                   error_type=error_type))

    def click_coord(self, x: int, y: int,
                    random_range: int = 0, remark: str = "") -> "FlowBuilder":
        return self.add(step_click_coord(x, y, random_range, remark))

    def type_text(self, text: str,
                  selector_type: str = "class",
                  selector: str = "android.widget.EditText",
                  serial: int = 1,
                  clear: bool = True,
                  search_time_ms: int = 5_000,
                  remark: str = "") -> "FlowBuilder":
        return self.add(step_type_text(text, selector_type, selector,
                                       serial, clear, search_time_ms, remark))

    def press_enter(self, remark: str = "") -> "FlowBuilder":
        return self.add(step_press_key("enter", remark))

    def key_option(self, key_type: str, remark: str = "") -> "FlowBuilder":
        """Press a virtual key via keyOption (visual-editor style)."""
        return self.add(step_key_option(key_type, remark))

    def press_back(self, remark: str = "") -> "FlowBuilder":
        return self.add(step_press_key("back", remark))

    def press_delete(self, remark: str = "") -> "FlowBuilder":
        return self.add(step_press_key("del", remark))

    def go_back(self, remark: str = "") -> "FlowBuilder":
        return self.add(step_back(remark))

    def go_home(self, remark: str = "") -> "FlowBuilder":
        return self.add(step_home(remark))

    def scroll_down(self, min_px: int = 500, max_px: int = 700,
                    remark: str = "") -> "FlowBuilder":
        return self.add(step_scroll("bottom", min_px, max_px, remark=remark))

    def scroll_up(self, min_px: int = 500, max_px: int = 700,
                  remark: str = "") -> "FlowBuilder":
        return self.add(step_scroll("top", min_px, max_px, remark=remark))

    def get_totp(self, key: str = "{totp_secret}",
                 output_variable: str = "totp_code",
                 remark: str = "Compute TOTP code") -> "FlowBuilder":
        return self.add(step_totp(key, output_variable, remark))

    def http_get(self, url: str, variable: str = "",
                 remark: str = "") -> "FlowBuilder":
        return self.add(step_http_get(url, variable, remark))

    # ── Build & export ────────────────────────────────────────────────────────

    def build_gal(self) -> dict:
        """Return the GAL flow as a Python dict (not yet JSON-encoded)."""
        return {
            "title": self.title,
            "desc": self.desc,
            "content": {
                "contentType": "phone",
                "errorType": self.error_type,
                "isDebug": False,
                "timeOut": str(self.timeout_minutes),
                "contents": self._steps,
            },
        }

    def to_gal_string(self) -> str:
        """Return the GAL flow JSON-encoded as a string (for the import API)."""
        return json.dumps(self.build_gal(), ensure_ascii=False)

    def save(self, flow_id: str = None) -> str:
        """
        Import (or update) this flow via the GeelarK API.
        Pass flow_id to update an existing flow; omit to create a new one.
        Returns the flow ID.
        """
        from core.geelark_client import GeelarKClient
        client = GeelarKClient()
        fid = client.import_rpa_flow(self.to_gal_string(), flow_id=flow_id)
        action = "updated" if flow_id else "created"
        log.info("Flow '%s' %s — id=%s", self.title, action, fid)
        return fid


# ── Pre-built flow templates ──────────────────────────────────────────────────

class _FlowTemplates:
    """
    Factory for standard flows used by the account warmer.

    Each method builds and imports the flow, returning its ID.
    Pass an existing flow_id to update in-place rather than create a new one.

    All flows accept parameters via paramMap at task dispatch time.
    """

    def google_login_totp(self, flow_id: str = None) -> str:
        """
        Complete Google sign-in flow with TOTP 2FA support.

        Required paramMap keys when running:
            email       — Google account email
            password    — Google account password
            totp_secret — Base32 TOTP secret (Google Authenticator seed)

        Strategy:
          1. Open Chrome to accounts.google.com/signin
          2. Wait for email field → type {email} → Enter
          3. Wait for password field → type {password} → Enter
          4. Wait for 2FA screen to load
          5. Compute TOTP code from {totp_secret}  ← native GeelarK step
          6. Wait for TOTP input field → type {totp_code} → Enter
          7. Wait for login completion
        """
        fb = FlowBuilder(
            title="Google Login + TOTP",
            desc=(
                "Automates Google account login with TOTP 2FA. "
                "Pass email, password, totp_secret via paramMap."
            ),
            timeout_minutes=10,
            error_type="skip",
        )

        # Step 1 — Open Chrome directly to Google sign-in
        fb.open_app(
            "com.android.chrome",
            uri="https://accounts.google.com/signin",
            remark="Open Chrome to Google sign-in",
        )
        fb.wait(5000, "Wait for page load")

        # Step 2 — Email
        fb.wait_for_element("class", "android.widget.EditText",
                            search_time_ms=15_000, remark="Wait for email field")
        fb.type_text("{email}", remark="Enter email")
        fb.press_enter("Submit email")
        fb.wait(3000, "Wait for password screen")

        # Step 3 — Password
        fb.wait_for_element("class", "android.widget.EditText",
                            search_time_ms=15_000, remark="Wait for password field")
        fb.type_text("{password}", remark="Enter password")
        fb.press_enter("Submit password")
        fb.wait(5000, "Wait for 2FA screen")

        # Step 4 — TOTP: compute code first, then wait for the input field
        # (generate as late as possible to avoid the 30-second TOTP window expiring)
        fb.get_totp("{totp_secret}", "totp_code", "Compute TOTP code")
        fb.wait_for_element("class", "android.widget.EditText",
                            search_time_ms=15_000, remark="Wait for TOTP input field")
        fb.type_text("{totp_code}", remark="Enter TOTP code")
        fb.press_enter("Submit TOTP")

        # Step 5 — Wait for post-login redirect
        fb.wait(10_000, "Wait for login completion")

        return fb.save(flow_id=flow_id)

    def google_login_totp_with_method_select(self, flow_id: str = None) -> str:
        """
        Extended Google login flow that handles accounts which land on a
        '2-step verification method' selection screen before the TOTP entry screen.

        Some accounts (especially on first login from a new device) show a screen
        asking which 2FA method to use. This flow clicks the Google Authenticator
        option (first list item) before proceeding to TOTP entry.

        Required paramMap: email, password, totp_secret
        """
        fb = FlowBuilder(
            title="Google Login + TOTP (with method select)",
            desc=(
                "Google login with 2FA method selection screen handling. "
                "Clicks 'Google Authenticator' option before entering TOTP code. "
                "Pass email, password, totp_secret via paramMap."
            ),
            timeout_minutes=10,
            error_type="skip",
        )

        # Open Chrome
        fb.open_app("com.android.chrome",
                    uri="https://accounts.google.com/signin",
                    remark="Open Chrome to Google sign-in")
        fb.wait(5000, "Wait for page load")

        # Email
        fb.wait_for_element("class", "android.widget.EditText",
                            search_time_ms=15_000, remark="Wait for email field")
        fb.type_text("{email}", remark="Enter email")
        fb.press_enter("Submit email")
        fb.wait(3000, "Wait for password screen")

        # Password
        fb.wait_for_element("class", "android.widget.EditText",
                            search_time_ms=15_000, remark="Wait for password field")
        fb.type_text("{password}", remark="Enter password")
        fb.press_enter("Submit password")
        fb.wait(5000, "Wait for 2FA screen / method select")

        # Try tapping the first list row (Google Authenticator option).
        # On screens that go straight to code entry this step is a no-op
        # because there's no list row to click — the flow error_type=skip
        # means it will proceed even if this element isn't found.
        fb.add(step_click("class", "android.widget.LinearLayout",
                          search_time_ms=5_000, serial=1,
                          remark="Tap Google Authenticator option (if method select shown)"))
        fb.wait(3000, "Wait for TOTP entry screen")

        # TOTP
        fb.get_totp("{totp_secret}", "totp_code", "Compute TOTP code")
        fb.wait_for_element("class", "android.widget.EditText",
                            search_time_ms=15_000, remark="Wait for TOTP input field")
        fb.type_text("{totp_code}", remark="Enter TOTP code")
        fb.press_enter("Submit TOTP")
        fb.wait(10_000, "Wait for login completion")

        return fb.save(flow_id=flow_id)

    def google_add_account_via_settings(self, flow_id: str = None) -> str:
        """
        Add Google account via Android's native Settings Add Account wizard.

        Unlike the Chrome-based flows, this properly registers the account in
        Android's AccountManager, which is required for Maps, Gmail, and YouTube
        to access the account.

        The flow opens Settings itself, navigates to Add Account → Google, and
        then drives the GMS native sign-in wizard (email → password → 2FA → TOTP).
        Because the GMS wizard uses native Android widgets (not Chrome WebView),
        uiautomator can find all input fields reliably.

        Design notes:
         - openApp uses uri="android.settings.ADD_ACCOUNT_SETTINGS" which may open
           the account-type picker directly on supported devices.  If the URI is
           not handled and Settings opens to its home screen instead, the navigation
           clicks (Accounts / Users & accounts → Add account) take over.
         - All navigation clicks are error_type=skip so the flow is resilient:
           if the device jumped straight to the account type picker the Settings
           navigation steps are harmlessly skipped.

        Required paramMap keys: email, password, totp_secret
        """
        fb = FlowBuilder(
            title="Google Add Account via Settings",
            desc=(
                "Adds Google account via Android Settings Add Account wizard. "
                "Opens Settings, navigates Accounts → Add account → Google, then "
                "drives the native GMS sign-in wizard (email/password/TOTP). "
                "Properly registers the account in Android AccountManager so that "
                "Maps, Gmail, and YouTube can access it. "
                "Pass email, password, totp_secret via paramMap."
            ),
            timeout_minutes=10,
            error_type="skip",
        )

        # Open Settings.
        # We navigate to Add Account via the Settings search bar — this is more
        # reliable than scrolling because "Accounts" is below the fold on the
        # 720×1440 AOSP Android 10 screen and GeelarK does not auto-scroll.
        fb.open_app("com.android.settings",
                    remark="Open Android Settings")
        fb.wait(3000, "Wait for Settings to load")

        # Tap the Settings search bar (visible at the top of the Settings home).
        # Resource-id confirmed from uiautomator dump: search_action_bar
        fb.click("id", "com.android.settings:id/search_action_bar",
                 search_time_ms=5000,
                 remark="Tap Settings search bar")
        fb.wait(1000, "Wait for search to open")

        # Type 'Add account' into the search field
        fb.type_text("Add account",
                     selector_type="class",
                     selector="android.widget.EditText",
                     search_time_ms=5000,
                     remark="Type Add account in Settings search")
        fb.wait(2000, "Wait for search results")

        # Click the 'Add account' result
        fb.click("text", "Add account",
                 search_time_ms=5000,
                 remark="Tap Add account from search results")
        fb.wait(2000, "Wait for account-type picker")

        # Select Google from the account type picker
        fb.click("text", "Google",
                 search_time_ms=5000,
                 remark="Select Google account type")
        fb.wait(3000, "Wait for Google sign-in activity to appear")

        # Email
        fb.wait_for_element("class", "android.widget.EditText",
                            search_time_ms=15_000, remark="Wait for email field")
        fb.type_text("{email}", remark="Enter email address")
        fb.press_enter("Submit email")
        fb.wait(3000, "Wait for password screen")

        # Password
        fb.wait_for_element("class", "android.widget.EditText",
                            search_time_ms=15_000, remark="Wait for password field")
        fb.type_text("{password}", remark="Enter password")
        fb.press_enter("Submit password")
        fb.wait(5000, "Wait for 2FA screen")

        # 2FA method select — tap first LinearLayout (Google Authenticator row).
        # Skipped if the account goes directly to the TOTP entry screen.
        fb.add(step_click("class", "android.widget.LinearLayout",
                          search_time_ms=5_000, serial=1,
                          remark="Tap Google Authenticator option (if method select shown)"))
        fb.wait(3000, "Wait for TOTP entry screen")

        # TOTP — compute code late to avoid the 30-second window expiring
        fb.get_totp("{totp_secret}", "totp_code", "Compute TOTP code")
        fb.wait_for_element("class", "android.widget.EditText",
                            search_time_ms=15_000, remark="Wait for TOTP input field")
        fb.type_text("{totp_code}", remark="Enter TOTP code")
        fb.press_enter("Submit TOTP")

        # Wait for the GMS wizard to complete and register the account.
        # AccountManager is typically updated within a few seconds of completion.
        fb.wait(15_000, "Wait for account registration in AccountManager")

        return fb.save(flow_id=flow_id)


# Singleton — import and call e.g. flows.google_login_totp()
flows = _FlowTemplates()
