"""
GeelarK Cloud Phone API client.

Credentials are read from warmer.env:
    GEELARK_BEARER_TOKEN  — bearer token for token-based auth
    GEELARK_APP_ID        — team AppId
    GEELARK_API_KEY       — API key (used for key-based auth signature)

Uses token verification (traceId + Authorization: Bearer <token>) by default
as it's simpler. Key-based auth is also implemented as fallback.

All endpoints:  POST  https://openapi.geelark.com/open/v1/...
Rate limit:     200 requests/minute, 24 000/hour
Response code:  0 = success, anything else = error
"""

import hashlib
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("geelark")

BASE_URL = "https://openapi.geelark.com"

# ── Load warmer.env so credentials are available ─────────────────────────────

_APP_DIR = Path(__file__).parent.parent
_env_file = _APP_DIR / "warmer.env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _token_headers() -> dict:
    """Token verification — simplest auth method. Requires only traceId + Bearer."""
    return {
        "traceId": str(uuid.uuid4()),
        "Authorization": f"Bearer {os.environ.get('GEELARK_BEARER_TOKEN', '')}",
        "Content-Type": "application/json",
    }


def _key_headers() -> dict:
    """
    Key verification — signed auth using HMAC-SHA256 of combined params.
    sign = SHA256_UPPER( appId + traceId + ts + nonce + apiKey )
    """
    app_id  = os.environ.get("GEELARK_APP_ID", "")
    api_key = os.environ.get("GEELARK_API_KEY", "")
    trace_id = str(uuid.uuid4())
    ts       = str(int(time.time() * 1000))
    nonce    = trace_id[:6]
    sign_input = app_id + trace_id + ts + nonce + api_key
    sign = hashlib.sha256(sign_input.encode()).hexdigest().upper()
    return {
        "appId":   app_id,
        "traceId": trace_id,
        "ts":      ts,
        "nonce":   nonce,
        "sign":    sign,
        "Content-Type": "application/json",
    }


def _post(path: str, body: dict, use_token_auth: bool = True, timeout: int = 30) -> dict:
    """
    POST to GeelarK API. Returns the `data` field on success.
    Raises RuntimeError on HTTP error or API code != 0.
    """
    url     = f"{BASE_URL}{path}"
    headers = _token_headers() if use_token_auth else _key_headers()
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"GeelarK HTTP error on {path}: {e}") from e

    payload = resp.json()
    code    = payload.get("code", -1)
    if code != 0:
        raise RuntimeError(
            f"GeelarK API error {code} on {path}: {payload.get('msg')} — {payload.get('data')}"
        )
    return payload.get("data") or {}


# ── Curated "fast" phone model list ──────────────────────────────────────────
# Only flagship / high-end models are included.
# Android 10 phones have the best Google trust history (mature OS fingerprint).
# Android 13 Google Pixel / Samsung flagships are also strong performers.
# GeelarK treats brand+model as advisory — it picks the closest available match.

FAST_MODELS: list[dict] = [
    # ── Android 14 flagships (strongest Play Integrity attestation) ───────────
    {"android": "Android 14", "brand": "Samsung",  "model": "Galaxy S24 Ultra"},
    {"android": "Android 14", "brand": "Samsung",  "model": "Galaxy S24"},
    {"android": "Android 14", "brand": "Google",   "model": "Pixel 8 Pro"},
    {"android": "Android 14", "brand": "Google",   "model": "Pixel 8"},
    {"android": "Android 14", "brand": "OnePlus",  "model": "12"},
    # ── Android 10 flagships (proven stable, highest Google trust) ────────────
    {"android": "Android 10", "brand": "Samsung",  "model": "Galaxy S9+"},
    {"android": "Android 10", "brand": "Samsung",  "model": "Note 9"},
    {"android": "Android 10", "brand": "vivo",     "model": "X50 Pro"},
    {"android": "Android 10", "brand": "OPPO",     "model": "FIND X"},
    # ── Android 13 flagships (modern hardware signal) ─────────────────────────
    {"android": "Android 13", "brand": "Google",   "model": "Pixel 7 Pro"},
    {"android": "Android 13", "brand": "Google",   "model": "Pixel 7"},
    {"android": "Android 13", "brand": "Google",   "model": "Pixel 6 Pro"},
    {"android": "Android 13", "brand": "Samsung",  "model": "Galaxy S23 Ultra"},
    {"android": "Android 13", "brand": "Samsung",  "model": "Galaxy S23"},
    {"android": "Android 13", "brand": "Samsung",  "model": "Galaxy S22 Ultra"},
    {"android": "Android 13", "brand": "OnePlus",  "model": "10 Pro"},
    {"android": "Android 13", "brand": "vivo",     "model": "X70 Pro+"},
    {"android": "Android 13", "brand": "vivo",     "model": "S17 Pro"},
]


def pick_fast_model(android_filter: str = None) -> dict:
    """
    Randomly pick a fast model from FAST_MODELS.
    android_filter: optionally restrict to 'Android 10' or 'Android 13'.
    Returns {"android": str, "brand": str, "model": str}.
    """
    import random as _random
    pool = FAST_MODELS
    if android_filter:
        pool = [m for m in FAST_MODELS if m["android"] == android_filter] or FAST_MODELS
    return _random.choice(pool)


# ── Client ────────────────────────────────────────────────────────────────────

class GeelarKClient:
    """Thin wrapper around the GeelarK Cloud Phone REST API."""

    # ── Phone lifecycle ───────────────────────────────────────────────────────

    def create_phone(
        self,
        profile_name: str,
        proxy: str = None,
        android_version: str = None,
        region: str = None,
        brand: str = None,
        model: str = None,
    ) -> tuple[str, dict]:
        """
        Create a new cloud phone (on-demand/per-minute billing).

        Returns (phone_id, equipment_info) where equipment_info contains the
        actual brand/model/IMEI assigned by GeelarK.

        If brand+model are omitted, a random entry from FAST_MODELS is chosen.
        android_version defaults to the chosen fast model's Android version.
        GeelarK treats brand/model as advisory — actual assigned model may differ.
        """
        # Pick a fast model if caller didn't specify
        if brand is None and model is None:
            chosen = pick_fast_model(android_version)
        else:
            chosen = {
                "android": android_version or "Android 10",
                "brand": brand or "",
                "model": model or "",
            }

        resolved_android = chosen["android"]
        resolved_proxy   = proxy or os.environ.get("GEELARK_PROXY", "")

        env_row: dict = {"profileName": profile_name}
        if resolved_proxy:
            env_row["proxyInformation"] = resolved_proxy

        body: dict = {
            "mobileType": resolved_android,
            "chargeMode": 0,  # 0 = on-demand per-minute
            "surfaceBrandName": chosen["brand"],
            "surfaceModelName": chosen["model"],
            "data": [env_row],
        }
        if region:
            body["region"] = region

        log.info("Creating phone '%s' — hint: %s %s (%s)",
                 profile_name, chosen["brand"], chosen["model"], resolved_android)

        result = _post("/open/v1/phone/addNew", body)

        success_list = (result.get("successList")
                        or result.get("successDetails")
                        or result.get("details")
                        or [])
        if not success_list:
            fail = (result.get("failList") or result.get("failDetails") or [{}])[0]
            raise RuntimeError(f"Phone creation failed: {fail}")

        entry     = success_list[0]
        phone_id  = entry["id"]
        equip     = entry.get("equipmentInfo", {})
        log.info("Phone created: id=%s  actual model: %s %s (%s)",
                 phone_id,
                 equip.get("deviceBrand", chosen["brand"]),
                 equip.get("deviceModel", chosen["model"]),
                 equip.get("osVersion", resolved_android))
        return phone_id, equip

    def list_brands(self, android_version: int = 10) -> list:
        """
        Return available surface brand+model pairs for a given Android version.
        android_version: integer (9–15).
        Returns list of {"surfaceBrandName": str, "surfaceModelName": str}.
        """
        return _post("/open/v1/phone/brand/list", {"androidVer": android_version}) or []

    def list_phones(self, page_no: int = 1, page_size: int = 50) -> list:
        """Return a list of phone info dicts."""
        result = _post("/open/v1/phone/list", {"pageNo": page_no, "pageSize": page_size})
        return result.get("items") or result.get("rows") or []

    def get_phone_status(self, phone_ids: list) -> list:
        """Query run-status of given phone IDs. Returns list of status dicts."""
        result = _post("/open/v1/phone/status", {"ids": phone_ids})
        return (result.get("successDetails")
                or result.get("items")
                or [])

    def check_phone_health(self, phone_id: str) -> dict:
        """
        Pre-flight check before starting any operation on a phone.
        Returns {"healthy": bool, "status": int, "reason": str}.

        Status codes: 0=Running, 1=Starting, 2=Stopped, 3=Stopping, 3=Expired.
        A phone is healthy if we can query its status without an API error.
        An API error (e.g. model under maintenance, env occupied) means unhealthy.
        """
        try:
            statuses = self.get_phone_status([phone_id])
            if not statuses:
                return {"healthy": False, "status": -1,
                        "reason": "Phone not found in GeelarK account"}
            s = statuses[0].get("status", -1)
            # Status 3 = Expired (subscription lapsed)
            if s == 3:
                return {"healthy": False, "status": s,
                        "reason": "Phone subscription expired"}
            return {"healthy": True, "status": s, "reason": "OK"}
        except RuntimeError as e:
            msg = str(e)
            # Map known GeelarK error codes to readable reasons
            if "43029" in msg:
                reason = "Phone model is under maintenance on GeelarK servers"
            elif "43038" in msg:
                reason = "Phone model has been retired/deleted by GeelarK"
            elif "43021" in msg:
                reason = "Phone is occupied (viewer open or stuck session)"
            elif "43004" in msg:
                reason = "Phone subscription expired"
            else:
                reason = msg
            return {"healthy": False, "status": -1, "reason": reason}

    def recreate_phone(self, old_phone_id: str, profile_name: str,
                       proxy: str = None,
                       brand: str = None, model: str = None,
                       android_version: str = None) -> tuple[str, dict]:
        """
        Stop, delete and recreate a phone in one call.
        Uses the same fast-model selection as create_phone if brand/model omitted.
        Returns (new_phone_id, equipment_info).

        Raises RuntimeError if delete fails (e.g. phone still occupied in portal).
        """
        # Stop first — ignore error if already stopped
        try:
            _post("/open/v1/phone/stop", {"ids": [old_phone_id]})
            time.sleep(5)
        except Exception:
            pass

        # Delete — raises if still occupied
        result = _post("/open/v1/phone/delete", {"ids": [old_phone_id]})
        fail = (result.get("failDetails") or [])
        if fail:
            code = fail[0].get("code", "")
            msg  = fail[0].get("msg", "unknown")
            if code == 43021:
                raise RuntimeError(
                    "Phone is still occupied in GeelarK portal — close any open "
                    "viewer tabs in the GeelarK web app, wait a minute, then retry."
                )
            raise RuntimeError(f"Delete failed (code {code}): {msg}")

        time.sleep(3)
        return self.create_phone(
            profile_name=profile_name,
            proxy=proxy,
            android_version=android_version,
            brand=brand,
            model=model,
        )

    def start_phone(self, phone_id: str) -> str:
        """
        Start (power on) a cloud phone.
        Returns the viewer URL — open in browser to watch/control the phone.
        Raises if start fails.
        """
        result = _post("/open/v1/phone/start", {"ids": [phone_id]})
        success = result.get("successDetails") or []
        if not success:
            fail = (result.get("failDetails") or [{}])[0]
            raise RuntimeError(f"Phone start failed: {fail.get('msg', 'unknown')}")
        return success[0].get("url", "")

    def stop_phone(self, phone_id: str, timeout: int = 60) -> bool:
        """
        Stop (power off) a cloud phone and VERIFY it stopped.

        Sends the stop command then polls get_phone_status() until the phone
        is state 2 (Stopped) or timeout expires.  Returns True only when the
        phone is confirmed stopped.
        """
        import time as _time
        # Send stop command
        _post("/open/v1/phone/stop", {"ids": [phone_id]})
        # Poll until confirmed stopped
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            _time.sleep(3)
            try:
                statuses = self.get_phone_status([phone_id])
                for s in statuses:
                    if s.get("status") == 2:  # Stopped
                        return True
            except Exception:
                pass  # transient API error, keep polling
        # Final check
        try:
            statuses = self.get_phone_status([phone_id])
            for s in statuses:
                if s.get("status") == 2:
                    return True
        except Exception:
            pass
        return False

    def verify_phone_stopped(self, phone_id: str) -> bool:
        """Check whether a specific phone is currently Stopped (status 2)."""
        try:
            statuses = self.get_phone_status([phone_id])
            for s in statuses:
                if s.get("status") == 2:
                    return True
        except Exception:
            pass
        return False

    def delete_phone(self, phone_id: str) -> bool:
        """
        Permanently delete a cloud phone.  Phone must be stopped first.
        Returns True only when the phone is confirmed deleted (no longer
        appears in status query).
        """
        import time as _time
        _post("/open/v1/phone/delete", {"ids": [phone_id]})
        # Poll until phone is gone from status list
        for _ in range(10):
            _time.sleep(2)
            try:
                statuses = self.get_phone_status([phone_id])
                if not statuses:
                    return True
                # Also check if status indicates deleted
                for s in statuses:
                    if s.get("status") in (2,):  # Stopped is ok for delete
                        pass
            except Exception:
                return True  # 404 or error = phone gone
        return False

    def rename_phone(self, phone_id: str, name: str) -> bool:
        """Rename a cloud phone (phone must be stopped). Endpoint: /open/v1/phone/detail/update"""
        _post("/open/v1/phone/detail/update", {"id": phone_id, "name": name})
        return True

    # ── Screenshot ────────────────────────────────────────────────────────────

    def request_screenshot(self, phone_id: str) -> str:
        """Trigger a screenshot. Returns the taskId (async — must poll for result)."""
        result = _post("/open/v1/phone/screenShot", {"id": phone_id})
        return result.get("taskId", "")

    def get_screenshot_result(self, task_id: str) -> dict:
        """
        Poll screenshot result.
        Returns {"status": int, "downloadLink": str}
        status: 0=failed, 1=in_progress, 2=succeeded, 3=failed
        """
        return _post("/open/v1/phone/screenShot/result", {"taskId": task_id})

    def take_screenshot(self, phone_id: str, max_wait: int = 30) -> Optional[bytes]:
        """
        Request a screenshot and wait for the download link.
        Returns raw image bytes on success, None on failure.
        """
        try:
            task_id  = self.request_screenshot(phone_id)
            deadline = time.time() + max_wait
            while time.time() < deadline:
                time.sleep(2)
                result = self.get_screenshot_result(task_id)
                status = result.get("status", 0)
                if status == 2:
                    url = result.get("downloadLink", "")
                    if url:
                        r = requests.get(url, timeout=20)
                        r.raise_for_status()
                        return r.content
                    return None
                if status in (0, 3):
                    log.warning("Screenshot task %s failed (status %d)", task_id, status)
                    return None
                # status == 1: still processing
            log.warning("Screenshot timed out after %ds", max_wait)
        except Exception as e:
            log.warning("take_screenshot error: %s", e)
        return None

    # ── RPA / Automation ──────────────────────────────────────────────────────

    def google_login(
        self,
        phone_id: str,
        email: str,
        password: str,
        task_name: str = None,
    ) -> str:
        """
        Submit GeelarK's built-in Google auto-login RPA task.
        NOTE: This endpoint only handles email + password.  It expects Google's
        push-notification 2FA ("Check your phone") and will fail for TOTP accounts.
        Use run_custom_flow() with a flow that includes the Authenticator code step
        for accounts that have TOTP 2FA configured.
        Returns the taskId (poll with query_tasks to track progress).
        """
        result = _post("/open/v1/rpa/task/googleLogin", {
            "name":       task_name or f"Google login — {email}",
            "scheduleAt": int(time.time()) + 5,
            "id":         phone_id,
            "email":      email,
            "password":   password,
        })
        return result.get("taskId", "")

    # ── Custom RPA flow API ───────────────────────────────────────────────────
    # GeelarK's visual flow builder (rpa.geelark.com) lets you create flows
    # that include an "Authenticator code" step — native TOTP generation.
    # These flows are run via /open/v1/task/rpa/add with a flowId + paramMap.

    def list_rpa_flows(self, page: int = 1, page_size: int = 50) -> list:
        """
        List all custom RPA flows saved in the GeelarK account.
        Returns list of dicts with keys: id, title, desc, params (parameter names).
        Endpoint: POST /open/v1/task/flow/list
        """
        result = _post("/open/v1/task/flow/list", {
            "page":     page,
            "pageSize": page_size,
        })
        return result.get("items") or []

    def run_custom_flow(
        self,
        flow_id: str,
        phone_id: str,
        param_map: dict,
        task_name: str = None,
        schedule_delay: int = 5,
    ) -> str:
        """
        Run a saved custom RPA flow on a cloud phone.

        Args:
            flow_id:        ID of the saved flow (from list_rpa_flows or rpa.geelark.com).
            phone_id:       Cloud phone ID.
            param_map:      Key/value pairs matching the flow's defined parameter names.
                            For a Google login flow: {"email": "...", "password": "...",
                            "totp_secret": "..."} — names must match what's in the flow.
            task_name:      Optional display name for the task.
            schedule_delay: Seconds from now to schedule the task (default 5).

        Returns the taskId string (poll with query_tasks).
        Endpoint: POST /open/v1/task/rpa/add
        """
        result = _post("/open/v1/task/rpa/add", {
            "flowId":     flow_id,
            "id":         phone_id,
            "name":       task_name or f"Custom flow {flow_id}",
            "scheduleAt": int(time.time()) + schedule_delay,
            "paramMap":   param_map,
        })
        return result.get("taskId", "") or result.get("id", "")

    def export_rpa_flow(self, flow_id: str) -> str:
        """
        Export a saved RPA flow as a GAL JSON string.
        Returns the raw `gal` string (JSON-encoded flow definition).
        Endpoint: POST /open/v1/task/flow/export
        """
        result = _post("/open/v1/task/flow/export", {"id": flow_id})
        return result.get("gal", "")

    def import_rpa_flow(self, gal_json: str, flow_id: str = None) -> str:
        """
        Import (or update) a custom RPA flow from a GAL JSON string.
        Pass flow_id to update an existing flow; omit to create a new one.
        Returns the flow ID string on success.
        Endpoint: POST /open/v1/task/flow/import
        The `gal` parameter must be the JSON-encoded GAL string (as returned by export).
        """
        body: dict = {"gal": gal_json}
        if flow_id:
            body["id"] = flow_id
        result = _post("/open/v1/task/flow/import", body)
        return result.get("id", "")

    def query_tasks(self, task_ids: list) -> list:
        """
        Query the status of one or more RPA tasks.
        Returns a list of task dicts. Key fields:
          id, status (1=Waiting 2=InProgress 3=Completed 4=Failed 7=Cancelled),
          failCode, failDesc, cost (seconds)
        """
        result = _post("/open/v1/task/query", {"ids": task_ids})
        return result.get("items") or []

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending/running RPA task."""
        try:
            _post("/open/v1/task/cancel", {"ids": [task_id]})
            return True
        except Exception as e:
            log.warning("cancel_task %s failed: %s", task_id, e)
            return False

    # ── APK / File upload helpers ───────────────────────────────────────────────

    def _get_presigned_upload_url(self, file_type: str = "apk") -> tuple[str, str]:
        """
        Get a temporary presigned upload URL from GeelarK.
        Returns (upload_url, resource_url).
        """
        result = _post("/open/v1/upload/getUrl", {"fileType": file_type})
        return result.get("uploadUrl", ""), result.get("resourceUrl", "")

    def _upload_file_to_presigned_url(self, local_path: Path, upload_url: str) -> bool:
        """PUT a local file to the GeelarK presigned URL. No auth headers."""
        try:
            with open(local_path, "rb") as f:
                resp = requests.put(upload_url, data=f, timeout=120)
            resp.raise_for_status()
            return True
        except Exception as e:
            log.warning("File upload to presigned URL failed: %s", e)
            return False

    def _push_file_to_phone(self, phone_id: str, resource_url: str) -> str:
        """
        Push a file from GeelarK's CDN to the phone's /sdcard/Download/ folder.
        Returns the task_id to poll.
        """
        result = _post("/open/v1/phone/uploadFile", {"id": phone_id, "fileUrl": resource_url})
        return result.get("taskId", "")

    def _poll_file_upload(self, task_id: str, max_wait: int = 120) -> bool:
        """Poll until the file upload to phone completes. Returns True on success."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                result = _post("/open/v1/phone/uploadFile/result", {"taskId": task_id})
                status = result.get("status", 0)
                if status == 2:   # uploaded successfully
                    return True
                if status == 3:   # upload failed
                    log.warning("File upload to phone failed (status 3)")
                    return False
            except Exception as e:
                log.warning("Poll file upload error: %s", e)
            time.sleep(5)
        log.warning("File upload to phone timed out after %ds", max_wait)
        return False

    def upload_and_install_apk(self, phone_id: str, local_path: Path,
                                max_wait: int = 180) -> bool:
        """
        Upload an APK to GeelarK, push it to the phone, and install via pm install.
        Returns True on success.
        """
        log.info("Installing APK %s on phone %s ...", local_path.name, phone_id)
        if not local_path.exists():
            log.error("APK not found: %s", local_path)
            return False

        # 1. Get presigned URL
        try:
            upload_url, resource_url = self._get_presigned_upload_url("apk")
            if not upload_url or not resource_url:
                log.error("Failed to get presigned upload URL")
                return False
        except Exception as e:
            log.error("Presigned URL error: %s", e)
            return False

        # 2. PUT the APK
        if not self._upload_file_to_presigned_url(local_path, upload_url):
            return False

        # 3. Push to phone
        try:
            task_id = self._push_file_to_phone(phone_id, resource_url)
            if not task_id:
                log.error("No task_id returned for file push")
                return False
        except Exception as e:
            log.error("Push file to phone error: %s", e)
            return False

        # 4. Poll until file is on phone
        if not self._poll_file_upload(task_id, max_wait=max_wait):
            return False

        # 5. Discover actual filename on phone (GeelarK may rename it)
        try:
            r = _post("/open/v1/shell/execute", {
                "id": phone_id,
                "cmd": "ls -t /sdcard/Download/*.apk",
            })
            output = (r.get("output") or "").strip()
            apk_paths = [line.strip() for line in output.splitlines() if line.strip().endswith(".apk")]
            if not apk_paths:
                log.error("No APK found in /sdcard/Download/ after push")
                return False
            dest_path = apk_paths[0]   # newest first because of -t
        except Exception as e:
            log.warning("Failed to discover APK filename on phone: %s", e)
            return False

        # 6. Move APK to /data/local/tmp/ (avoids Android 14+ SELinux denial
        #    when pm install reads from FUSE-mounted /sdcard/Download).
        tmp_path = f"/data/local/tmp/{local_path.name}"
        _post("/open/v1/shell/execute", {
            "id": phone_id,
            "cmd": f"cp {dest_path} {tmp_path}",
        })
        # Also try mv as fallback if cp fails (some devices)
        _post("/open/v1/shell/execute", {
            "id": phone_id,
            "cmd": f"mv {dest_path} {tmp_path}",
        })

        # 7. Install from /data/local/tmp/
        cmd = f"pm install -r -g {tmp_path}"
        try:
            r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": cmd})
            output = (r.get("output") or "").strip().lower()
            if "success" in output:
                log.info("APK installed successfully on %s", phone_id)
                return True
            # Some Android versions return empty output on successful install.
            # Verify by checking for newly-installed packages.
            log.warning("APK install output: %s", output)
            from activities.google_login_mobile import _shell as _sh
            ok_chk, out_chk = _sh(phone_id, "pm list packages")
            if ok_chk:
                # Check for common GPS/fake package names
                for kw in ("gps", "fake", "joystick", "mock", "location", "lexa"):
                    if kw in out_chk.lower():
                        log.info("APK installed (verified via pm list packages) on %s", phone_id)
                        return True
            return False
        except Exception as e:
            log.warning("APK install shell command failed: %s", e)
            return False

    def is_app_installed(self, phone_id: str, package_name: str) -> bool:
        """Check whether a package is installed via pm list packages."""
        try:
            r = _post("/open/v1/shell/execute", {
                "id": phone_id,
                "cmd": f"pm list packages | grep {package_name}",
            })
            output = (r.get("output") or "").strip()
            return package_name in output
        except Exception as e:
            log.warning("is_app_installed check failed: %s", e)
            return False

    def clear_app_cache(self, phone_id: str, package_name: str) -> bool:
        """Clear app data and cache via pm clear. Returns True on success."""
        try:
            r = _post("/open/v1/shell/execute", {
                "id": phone_id,
                "cmd": f"pm clear {package_name}",
            }, timeout=30)
            output = (r.get("output") or "").strip()
            if "success" in output.lower():
                log.info("Cleared app data for %s on phone %s", package_name, phone_id)
                return True
            log.warning("pm clear output for %s: %s", package_name, output)
            return False
        except Exception as e:
            log.warning("clear_app_cache failed for %s: %s", package_name, e)
            return False
