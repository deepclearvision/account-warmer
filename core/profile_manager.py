"""
Multilogin Profile Manager
Communicates with the Multilogin X desktop app via launcher.mlx.yt:45001.
Starts a browser profile, returns a CDP websocket endpoint for Playwright,
and stops the profile when done.

Geolocation spoofing is applied to every session:
  - If a target business with coordinates is provided, the browser location
    is spoofed to within ~30-300m of that business.
  - Otherwise the browser location is spoofed to within ~2km of the
    account's configured city — correct region, not pinpoint.

This means every Maps interaction carries a consistent location signal
matching the account's supposed location.
"""

import random
import re
import string
import time
import requests
import urllib3
from urllib.parse import urlparse, urlunparse
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from core.logger import get_logger
from core.geolocation import coords_for_business, coords_for_account, coords_accuracy
from core.multilogin_auth import LOCAL_API, CLOUD_API, auth_headers
from core.proxy_guard import check_account_proxy, ProxyNotConfiguredError, ProxyVerificationError

# Suppress SSL warnings for the local launcher.mlx.yt HTTPS endpoint
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ── Proxy rotation helpers ─────────────────────────────────────────────────────

def _inject_session_id(proxy_url: str) -> str:
    """
    Append a fresh random session ID to the proxy username.
    Replaces any existing session ID so every call produces a new IP.

    Decodo / Smartproxy format:
      user-xxx-country-gb-city-london              (no session ID yet)
      user-xxx-country-gb-city-london-session-XYZ  (with existing session ID)

    Works with any provider using the -session-XXXX convention.
    """
    p        = urlparse(proxy_url)
    username = re.sub(r"-session-[a-zA-Z0-9]+$", "", p.username or "")
    password = p.password or ""

    session_id   = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    new_username = f"{username}-session-{session_id}"

    host   = p.hostname or ""
    port   = f":{p.port}" if p.port else ""
    netloc = f"{new_username}:{password}@{host}{port}"
    return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))


def _proxy_url_to_ml_dict(proxy_url: str) -> dict | None:
    """Parse a proxy URL into the dict Multilogin POST /profile/update expects."""
    if not proxy_url:
        return None
    try:
        p        = urlparse(proxy_url)
        type_map = {"socks5": "socks5", "socks4": "socks4",
                    "http": "http", "https": "http"}
        ptype    = type_map.get((p.scheme or "").lower())
        if not ptype or not p.hostname or not p.port:
            return None
        out = {"type": ptype, "host": p.hostname, "port": p.port}
        if p.username:
            out["username"] = p.username
        if p.password:
            out["password"] = p.password
        return out
    except Exception:
        return None


_ML_DEFAULT_FLAGS = {
    "audio_masking":        "natural",
    "fonts_masking":        "mask",
    "geolocation_masking":  "mask",
    "geolocation_popup":    "allow",
    "graphics_masking":     "mask",
    "graphics_noise":       "mask",
    "localization_masking": "mask",
    "media_devices_masking":"natural",
    "navigator_masking":    "mask",
    "ports_masking":        "mask",
    "proxy_masking":        "disabled",
    "screen_masking":       "natural",
    "timezone_masking":     "mask",
    "webrtc_masking":       "mask",
}


def _build_profile_update(profile_id: str, profile_name: str, proxy: dict,
                           geolocation: dict | None = None) -> dict:
    """Build the body for POST /profile/update with a custom proxy and optional geolocation.

    The proxy must be inside parameters.proxy (not top-level) for ML to save it.
    proxy_masking='disabled' tells ML to use the supplied proxy rather than its own.
    geolocation, if supplied, sets the profile's stored lat/lng so manual sessions
    also get the correct location without requiring Playwright to set it at runtime.
    """
    params: dict = {
        "proxy":       proxy,
        "flags":       _ML_DEFAULT_FLAGS,
        "storage":     {"is_local": False, "save_service_worker": False},
        "fingerprint": {},
    }
    if geolocation:
        params["geolocation"] = geolocation
    return {
        "profile_id": profile_id,
        "name":       profile_name,
        "parameters": params,
    }


def delete_profile(profile_id: str, permanently: bool = True) -> dict:
    """
    Permanently delete a Multilogin browser profile via the cloud API.

    Endpoint: POST {CLOUD_API}/profile/remove  body {"ids": [id], "permanently": bool}

    Module-level (no running session required). Follows the same auth-retry
    pattern as multilogin_auth.list_profiles(): on a 401 the token is refreshed
    once and the call retried.

    Returns a dict describing the outcome:
        {"success": True,  "profile_id": id, "status": "deleted"}
        {"success": True,  "profile_id": id, "status": "not_found"}   # already gone
        {"success": False, "profile_id": id, "status": "error", "detail": "..."}

    A profile that is already absent (HTTP 404 or a not-found API code) is
    reported as success — the desired end state (profile gone) is achieved.
    """
    log = get_logger("ml-delete")
    if not profile_id:
        return {"success": False, "profile_id": profile_id,
                "status": "error", "detail": "empty profile_id"}

    url  = f"{CLOUD_API}/profile/remove"
    body = {"ids": [profile_id], "permanently": permanently}

    def _do_delete(hdrs: dict):
        """Return (outcome_dict, needs_token_refresh)."""
        try:
            resp = requests.post(url, headers={**hdrs, "Content-Type": "application/json"},
                                 json=body, timeout=20)
        except requests.RequestException as e:
            return {"success": False, "profile_id": profile_id,
                    "status": "error", "detail": f"network error: {e}"}, False

        if resp.status_code == 401:
            return None, True  # signal caller to refresh token and retry

        if resp.status_code == 404:
            return {"success": True, "profile_id": profile_id,
                    "status": "not_found"}, False

        if resp.status_code == 200:
            # Multilogin returns a status object in the body; inspect it for
            # a not-found code so an already-deleted profile still reads as success.
            try:
                payload = resp.json()
            except Exception:
                payload = {}
            status = payload.get("status", {}) if isinstance(payload, dict) else {}
            err_code = str(status.get("error_code", "") or "").upper()
            if err_code in ("", "OK", "SUCCESS"):
                return {"success": True, "profile_id": profile_id,
                        "status": "deleted"}, False
            if "NOT_FOUND" in err_code or "NOT_EXIST" in err_code:
                return {"success": True, "profile_id": profile_id,
                        "status": "not_found"}, False
            return {"success": False, "profile_id": profile_id,
                    "status": "error", "detail": f"{err_code}: {status.get('message', '')}"}, False

        return {"success": False, "profile_id": profile_id,
                "status": "error",
                "detail": f"HTTP {resp.status_code}: {resp.text[:200]}"}, False

    try:
        headers = auth_headers()
    except Exception as e:
        return {"success": False, "profile_id": profile_id,
                "status": "error", "detail": f"auth failed: {e}"}

    outcome, needs_refresh = _do_delete(headers)
    if needs_refresh:
        # Token expired — refresh once and retry.
        try:
            headers = auth_headers(force_refresh=True)
        except Exception as e:
            return {"success": False, "profile_id": profile_id,
                    "status": "error", "detail": f"token refresh failed: {e}"}
        outcome, still_401 = _do_delete(headers)
        if still_401 or outcome is None:
            return {"success": False, "profile_id": profile_id,
                    "status": "error", "detail": "401 even after token refresh"}

    if outcome.get("success"):
        log.info("ML profile %s: %s", profile_id, outcome.get("status"))
    else:
        log.warning("ML profile %s delete failed: %s", profile_id, outcome.get("detail"))
    return outcome


def get_default_folder_id() -> str | None:
    """
    Return a Multilogin folder id to create profiles in.
    Prefers a 'browser' folder; falls back to the first folder. None on failure.
    Endpoint: GET {CLOUD_API}/workspace/folders
    """
    try:
        resp = requests.get(f"{CLOUD_API}/workspace/folders",
                            headers=auth_headers(), timeout=15)
        if resp.status_code == 200:
            folders = resp.json().get("data", {}).get("folders", [])
            for f in folders:
                if f.get("folder_type") == "browser":
                    return f.get("folder_id")
            if folders:
                return folders[0].get("folder_id")
    except Exception as e:
        get_logger("ml-create").warning("Could not fetch ML folders: %s", e)
    return None


def create_profile(name: str, folder_id: str, os_type: str = "windows",
                   browser_type: str = "mimic", proxy_url: str = None) -> dict:
    """
    Create a Multilogin browser profile via the cloud API.

    Endpoint: POST {CLOUD_API}/profile/create
    Body: {browser_type, os_type, folder_id, name, parameters:{flags, fingerprint, storage[, proxy]}}
    Returns data.ids[0] as the new profile id on HTTP 200/201.

    Same auth-retry pattern as delete_profile (401 → refresh once → retry).
    Proxy is optional at creation time — the warmer pushes the account's proxy
    to the profile at session launch anyway (_push_proxy_to_ml).

    Returns:
        {"success": True,  "profile_id": id, "folder_id": folder_id}
        {"success": False, "error": "..."}
    """
    log = get_logger("ml-create")
    if not name or not folder_id:
        return {"success": False, "error": "name and folder_id are required"}

    params: dict = {
        "flags":       _ML_DEFAULT_FLAGS,
        "fingerprint": {},
        "storage":     {"is_local": False, "save_service_worker": False},
    }
    proxy = _proxy_url_to_ml_dict(proxy_url) if proxy_url else None
    if proxy:
        params["proxy"] = proxy

    body = {
        "browser_type": browser_type,
        "os_type":      os_type,
        "folder_id":    folder_id,
        "name":         name,
        "parameters":   params,
    }
    url = f"{CLOUD_API}/profile/create"

    def _do(hdrs: dict):
        try:
            r = requests.post(url, headers={**hdrs, "Content-Type": "application/json"},
                             json=body, timeout=30)
        except requests.RequestException as e:
            return {"success": False, "error": f"network error: {e}"}, False
        if r.status_code == 401:
            return None, True
        if r.status_code in (200, 201):
            try:
                data = r.json().get("data", {})
            except Exception:
                data = {}
            ids = data.get("ids") or ([data.get("id")] if data.get("id") else [])
            if ids:
                return {"success": True, "profile_id": ids[0], "folder_id": folder_id}, False
            return {"success": False, "error": f"no id in response: {r.text[:200]}"}, False
        return {"success": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}, False

    try:
        headers = auth_headers()
    except Exception as e:
        return {"success": False, "error": f"auth failed: {e}"}

    out, needs_refresh = _do(headers)
    if needs_refresh:
        try:
            headers = auth_headers(force_refresh=True)
        except Exception as e:
            return {"success": False, "error": f"token refresh failed: {e}"}
        out, still_401 = _do(headers)
        if still_401 or out is None:
            return {"success": False, "error": "401 even after token refresh"}

    if out.get("success"):
        log.info("ML profile created: %s (%s)", out["profile_id"], name)
    else:
        log.warning("ML profile create failed for %s: %s", name, out.get("error"))
    return out


class ProfileSession:
    """Context manager that starts a Multilogin profile and yields a Playwright page."""

    def __init__(self, profile_id: str, account_id: str, timeout: int = 60,
                 account: dict = None, business: dict = None,
                 folder_id: str | None = None):
        self.profile_id  = profile_id
        self.folder_id   = folder_id or (account or {}).get("multilogin_folder_id") or ""
        self.account_id  = account_id
        self.timeout     = timeout
        self._account    = account or {}
        self._business   = business
        self.log         = get_logger(account_id)
        self._playwright  = None
        self._browser: Browser | None = None
        self._verified_ip: str | None = None

    async def __aenter__(self) -> Page:
        # ── Pre-session proxy gate ─────────────────────────────────────────────
        # Verify the proxy connects and the exit IP is not the machine's real IP
        # BEFORE opening the browser. Raises on failure — session is aborted.
        try:
            self._verified_ip = check_account_proxy(self._account, log=self.log)
        except (ProxyNotConfiguredError, ProxyVerificationError) as e:
            self.log.error(f"PROXY GUARD BLOCKED SESSION: {e}")
            raise

        # Push proxy to ML BEFORE starting the browser. Multilogin reads the
        # proxy settings at profile launch time — pushing after start has no
        # effect on the current session. _rotate_proxy() and _push_proxy_to_ml()
        # are mutually exclusive (rotate handles its own push).
        self._rotate_proxy()
        self._push_proxy_to_ml()

        ws_url = self._start_profile()
        self._playwright = await async_playwright().start()
        self._browser    = await self._playwright.chromium.connect_over_cdp(ws_url)
        context: BrowserContext = self._browser.contexts[0]

        # Apply geolocation spoofing to the browser context
        await self._apply_geolocation(context)

        # Block video/audio streaming to avoid wasting proxy data.
        # The page visit is still registered with Google; only the media
        # payload (which can be GBs per session) is suppressed.
        await self._block_media_streams(context)

        # Close any stale tabs from previous sessions, then start from a blank page
        # so activities always navigate from a known clean state.
        for stale in context.pages[1:]:
            try:
                await stale.close()
            except Exception:
                pass
        page: Page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("about:blank", wait_until="domcontentloaded")
        self.log.info(f"Profile started — connected via CDP | profile_id={self.profile_id}")

        # Check Google login status and persist it to login_status.json
        await self._check_login_status(page)

        return page

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._stop_profile()
        if exc_type:
            self.log.error(f"Session ended with error: {exc_val}")
        else:
            self.log.info("Profile stopped cleanly.")

    async def _check_login_status(self, page: Page) -> None:
        """
        Quick check (~3s) whether the account is logged into Google.
        Navigates to google.com and looks for the signed-in account avatar.
        Result is written to STATE_DIR/login_status.json.
        """
        import json
        from datetime import datetime
        from core.paths import STATE_DIR

        status_file = STATE_DIR / "login_status.json"
        try:
            existing = json.loads(status_file.read_text()) if status_file.exists() else {}
        except Exception:
            existing = {}

        try:
            await page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)

            # Logged in: account avatar button is present
            # Logged out: "Sign in" button is visible
            logged_in = await page.locator(
                'a[aria-label*="Google Account"], '
                'a[href*="myaccount.google.com"], '
                'img[alt*="Google Account"]'
            ).count() > 0

            status = "logged_in" if logged_in else "not_logged_in"
            self.log.info(f"Login check: {status}")

            existing[self.account_id] = {
                "status":     status,
                "checked_at": datetime.now().isoformat(),
            }
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            status_file.write_text(json.dumps(existing, indent=2))

            # If not logged in, write an ERROR line so the analyser picks it up
            if not logged_in:
                self.log.error(f"login_status: not_logged_in — account needs manual re-login")

        except Exception as e:
            self.log.warning(f"Login check skipped: {e}")
            # Don't overwrite a previous good status if check fails
            if self.account_id not in existing:
                existing[self.account_id] = {"status": "unknown", "checked_at": None}
                try:
                    status_file.write_text(json.dumps(existing, indent=2))
                except Exception:
                    pass

        # Navigate back to blank so activities start clean
        try:
            await page.goto("about:blank", wait_until="domcontentloaded")
        except Exception:
            pass

    async def _apply_geolocation(self, context: BrowserContext) -> None:
        """
        Spoof the browser geolocation.
        Business coordinates take priority over account city coordinates.
        """
        coords = None

        # Priority 1: near the target business
        if self._business:
            coords = coords_for_business(self._business)
            if coords:
                self.log.debug(
                    f"Geolocation spoofed near business: "
                    f"{coords[0]:.4f}, {coords[1]:.4f}"
                )

        # Priority 2: near the account's city
        if not coords and self._account:
            coords = coords_for_account(self._account)
            if coords:
                self.log.debug(
                    f"Geolocation spoofed near account city: "
                    f"{coords[0]:.4f}, {coords[1]:.4f}"
                )

        if coords:
            try:
                await context.grant_permissions(["geolocation"])
                await context.set_geolocation({
                    "latitude":  coords[0],
                    "longitude": coords[1],
                    "accuracy":  coords_accuracy(),
                })
            except Exception as e:
                self.log.warning(f"Could not set geolocation: {e}")

    def _push_proxy_to_ml(self) -> None:
        """
        Push the proxy URL from accounts.yaml to the Multilogin cloud profile
        BEFORE the browser is started, so ML applies it at launch time.

        Skipped if rotate_proxy=true (that path handles its own push with a fresh
        session ID each time).

        Raises RuntimeError on failure — the session must not start without a
        confirmed proxy push, otherwise the browser runs with the old/no proxy.
        """
        proxy_url = self._account.get("proxy", "")
        if not proxy_url:
            return
        if self._account.get("rotate_proxy", False):
            return  # handled by _rotate_proxy()

        payload = _proxy_url_to_ml_dict(proxy_url)
        if not payload:
            raise RuntimeError(
                f"Cannot parse proxy URL for account {self._account.get('id', '?')} — "
                f"session aborted. Check the proxy URL format in accounts.yaml."
            )

        profile_name = self._account.get("email", self.profile_id)
        try:
            resp = requests.post(
                f"{CLOUD_API}/profile/update",
                headers={**auth_headers(), "Content-Type": "application/json"},
                json=_build_profile_update(self.profile_id, profile_name, payload),
                timeout=15,
            )
            if resp.status_code == 200:
                self.log.info(f"Proxy synced to ML: {payload['host']}:{payload['port']}")
            else:
                raise RuntimeError(
                    f"ML proxy push failed (HTTP {resp.status_code}): {resp.text[:200]} — "
                    f"session aborted to prevent running without proxy."
                )
        except requests.RequestException as e:
            raise RuntimeError(
                f"ML proxy push network error: {e} — "
                f"session aborted to prevent running without proxy."
            ) from e

    async def _block_media_streams(self, context: BrowserContext) -> None:
        """
        Intercept and abort video/audio streaming requests.

        Blocks the actual media payload (adaptive stream segments, video files)
        while allowing the page HTML, thumbnails, and metadata through normally.
        This means YouTube/BBC/etc pages still load and register as visits —
        only the bandwidth-heavy video data is dropped.

        Patterns blocked:
          - YouTube video CDN (googlevideo.com)
          - HLS/DASH adaptive stream segments (.m3u8, .ts, .m4s)
          - Common video file extensions (.mp4, .webm, .mkv)
          - Audio-only stream segments (.m4a)
        """
        _MEDIA_PATTERNS = [
            "**/*.m3u8",
            "**/*.ts",
            "**/*.m4s",
            "**/*.m4a",
            "**/*.mp4",
            "**/*.webm",
            "**/*.mkv",
            "**googlevideo.com**",
            "**/videoplayback**",
        ]

        async def _abort_media(route):
            await route.abort()

        for pattern in _MEDIA_PATTERNS:
            try:
                await context.route(pattern, _abort_media)
            except Exception:
                pass

        self.log.debug("Media streaming blocked to conserve proxy bandwidth")

    def _rotate_proxy(self) -> None:
        """
        If rotate_proxy: true is set on the account, generate a fresh session ID,
        rebuild the proxy URL, and push it to the Multilogin profile via the API.

        This ensures every session starts on a brand-new IP drawn from the
        provider's rotating residential pool.

        Enable per account in config/accounts.yaml:
            rotate_proxy: true

        Proxy URL in accounts.yaml should be the base URL without a session ID:
            socks5://user-xxx-country-gb-city-london:password@gate.decodo.com:10004

        A new session ID (e.g. -session-k7mx29ab) is appended automatically
        before each session and pushed to the Multilogin profile.
        """
        if not self._account.get("rotate_proxy", False):
            return

        proxy_url = self._account.get("proxy", "")
        if not proxy_url:
            return

        new_url = _inject_session_id(proxy_url)
        payload = _proxy_url_to_ml_dict(new_url)
        if not payload:
            self.log.warning("rotate_proxy: could not parse proxy URL — skipping rotation")
            return

        parsed = urlparse(new_url)
        session_id = (parsed.username or "").split("-session-")[-1]
        self.log.info(f"Rotating proxy — new session ID: {session_id}")

        profile_name = self._account.get("email", self.profile_id)
        try:
            resp = requests.post(
                f"{CLOUD_API}/profile/update",
                headers={**auth_headers(), "Content-Type": "application/json"},
                json=_build_profile_update(self.profile_id, profile_name, payload),
                timeout=15,
            )
            if resp.status_code == 200:
                self.log.info(f"Proxy updated on Multilogin profile — {payload['host']}:{payload['port']}")
            else:
                self.log.warning(
                    f"Proxy rotation API call failed — HTTP {resp.status_code}: "
                    f"{resp.text[:200]}  (session will use existing proxy)"
                )
        except requests.RequestException as e:
            self.log.warning(f"Proxy rotation network error: {e}  (session will use existing proxy)")

    def _start_profile(self) -> str:
        if not self.folder_id:
            raise RuntimeError(
                f"Cannot start profile {self.profile_id}: folder_id is missing.\n"
                "Run export_multilogin.py to re-export profiles (the folder ID is now "
                "included), then re-import with import_accounts.py."
            )

        url = (
            f"{LOCAL_API}/api/v2/profile"
            f"/f/{self.folder_id}/p/{self.profile_id}"
            f"/start?automation_type=playwright&headless_mode=false"
        )
        self.log.info(f"Starting Multilogin profile {self.profile_id} …")

        deadline = time.time() + self.timeout
        last_err = None
        headers  = auth_headers()

        while time.time() < deadline:
            try:
                resp = requests.get(url, headers=headers, timeout=10, verify=False)
                if resp.status_code == 200:
                    data = resp.json()
                    # v2 API returns data.port — pass as http:// so Playwright
                    # fetches /json/version and resolves the ws debugger URL itself
                    port = data.get("data", {}).get("port") or data.get("port")
                    ws   = data.get("value", "")
                    if port:
                        ws = f"http://127.0.0.1:{port}"
                    if ws.startswith(("ws://", "http://")):
                        self.log.debug(f"CDP endpoint: {ws}")
                        return ws
                    self.log.warning(f"Unexpected response: {data}")
                elif resp.status_code == 401:
                    # Launcher returned 401.  Do NOT use force_refresh=True here:
                    # the JWT may still be valid (launcher returns spurious 401s
                    # when under load), and force_refresh calls _sign_in() which
                    # Multilogin rate-limits (HTTP 429) when multiple subprocesses
                    # hit 401 simultaneously — crashing every account in the batch.
                    # auth_headers() without force_refresh reads from cache and only
                    # signs in if the JWT is genuinely expired.
                    self.log.info("Auth 401 from launcher — re-reading token …")
                    try:
                        headers = auth_headers()
                    except Exception as _ref_err:
                        last_err = f"401 — token re-read failed: {_ref_err}"
                        continue
                    last_err = "Token re-read after 401, retrying"
                elif resp.status_code == 400:
                    body = resp.json()
                    code = body.get("status", {}).get("error_code", "")
                    if code == "PROFILE_ALREADY_RUNNING":
                        # Profile is open manually — stop it then let the loop retry
                        self.log.info("Profile already running — stopping it to restart in automation mode …")
                        self._stop_profile()
                        # Wait long enough for Multilogin to fully close the browser
                        # (multiple tabs or a slow machine can take several seconds)
                        time.sleep(8)
                        last_err = "Stopped running profile, retrying"
                    else:
                        last_err = f"HTTP 400 {code}: {resp.text[:200]}"
                elif resp.status_code == 500:
                    body_text = resp.text[:300]
                    if "Machine id mismatch" in body_text:
                        # Token was obtained on a different machine session —
                        # force-refresh and retry immediately.
                        self.log.info("Machine id mismatch — force-refreshing token …")
                        headers  = auth_headers(force_refresh=True)
                        last_err = "Machine id mismatch, token refreshed, retrying"
                    else:
                        last_err = f"HTTP 500: {body_text}"
                else:
                    last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except requests.RequestException as e:
                last_err = str(e)
            time.sleep(2)

        raise RuntimeError(
            f"Could not start Multilogin profile {self.profile_id} "
            f"within {self.timeout}s. Last error: {last_err}\n"
            "Make sure Multilogin X desktop app is running and you are signed in.\n"
            "Check credentials in config/multilogin.yaml"
        )

    def _stop_profile(self) -> None:
        url = f"{LOCAL_API}/api/v1/profile/stop/p/{self.profile_id}"
        try:
            requests.get(url, headers=auth_headers(), timeout=15, verify=False)
            self.log.info(f"Profile {self.profile_id} stopped via API.")
        except requests.RequestException as e:
            self.log.warning(f"Could not stop profile via API: {e}")
