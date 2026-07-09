"""
Multilogin X Authentication

Handles sign-in to the Multilogin cloud API and caches the Bearer token
locally so you don't need to log in on every script run.

Token is stored in logs/state/ml_token.json and refreshed automatically
when it expires.

Set your Multilogin credentials once in config/multilogin.yaml:

  email: your@email.com
  password: yourpassword
"""

import base64
import hashlib
import json
import os
import time
from pathlib import Path

import requests
import yaml

CLOUD_API  = "https://api.multilogin.com"
from core.paths import TOKEN_FILE, ML_CONFIG_FILE as CONFIG_FILE

# Multilogin X local launcher API (HTTPS on 45001)
LOCAL_API = "https://launcher.mlx.yt:45001"


def _load_credentials() -> tuple[str, str]:
    """Load Multilogin credentials from config/multilogin.yaml."""
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Multilogin credentials not found at {CONFIG_FILE}\n"
            "Create the file with:\n"
            "  email: your@email.com\n"
            "  password: yourpassword"
        )
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    email    = cfg.get("email", "").strip()
    password = cfg.get("password", "").strip()
    if not email or not password:
        raise ValueError("multilogin.yaml must contain email and password")
    return email, password


def _jwt_exp(token: str) -> float:
    """Decode the JWT exp claim without verifying the signature. Returns 0 on failure."""
    try:
        payload_b64 = token.split(".")[1]
        # Add padding so base64 doesn't complain
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64))
        return float(payload.get("exp", 0))
    except Exception:
        return 0


def _load_cached_token() -> str | None:
    """Return a cached token if it exists and hasn't expired."""
    if not TOKEN_FILE.exists():
        return None
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            data = json.load(f)
        token = data.get("token")
        if not token:
            return None
        # Use the JWT exp field so we track the real expiry regardless of
        # how long Multilogin issues tokens for. Refresh 60 s early to avoid
        # using a token that's about to expire mid-request.
        exp = _jwt_exp(token)
        if exp and time.time() < exp - 60:
            return token
        # Fallback: if exp couldn't be decoded, trust a 55-minute cache window
        # (conservative — Multilogin currently issues ~1 h tokens).
        if not exp and time.time() - data.get("fetched_at", 0) < 55 * 60:
            return token
    except Exception:
        pass
    return None


def _save_token(token: str) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"token": token, "fetched_at": time.time()}, f)


def _sign_in(email: str, password: str) -> str:
    """Sign in to Multilogin cloud API and return a Bearer token."""
    # Multilogin X requires the password as an MD5 hash
    password_md5 = hashlib.md5(password.encode()).hexdigest()
    try:
        resp = requests.post(
            f"{CLOUD_API}/user/signin",
            json={"email": email, "password": password_md5},
            timeout=20,
        )
    except requests.RequestException as e:
        raise RuntimeError(f"Could not reach Multilogin API: {e}")

    if resp.status_code != 200:
        raise RuntimeError(
            f"Multilogin sign-in failed ({resp.status_code}): {resp.text[:300]}\n"
            "Check your email/password in config/multilogin.yaml"
        )

    data  = resp.json()
    token = (
        data.get("data", {}).get("token")
        or data.get("token")
        or data.get("access_token")
    )
    if not token:
        raise RuntimeError(
            f"No token in Multilogin sign-in response. Keys: {list(data.keys())}"
        )
    return token


def get_token(force_refresh: bool = False) -> str:
    """
    Return a valid Multilogin Bearer token.

    Uses a file-based lock so that when multiple subprocesses find the token
    expired at the same time, only ONE calls the Multilogin sign-in API.
    The others wait, then read the freshly-written token from disk.
    Without this, parallel subprocesses all hit sign-in simultaneously and
    Multilogin returns 429 rate-limit errors on every request.
    """
    if not force_refresh:
        cached = _load_cached_token()
        if cached:
            return cached

    # Acquire a cross-process lock before signing in.
    # os.O_EXCL | os.O_CREAT is atomic on Windows and POSIX.
    lock_path = TOKEN_FILE.parent / "ml_token.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_acquired = False
    deadline = time.time() + 30  # wait at most 30 s for the lock

    try:
        while time.time() < deadline:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                lock_acquired = True
                break
            except (FileExistsError, OSError):
                time.sleep(0.5)

        if not lock_acquired:
            # Timed out waiting — another process may have just refreshed; try cache
            cached = _load_cached_token()
            if cached:
                return cached
            # Still nothing: fall through and sign in anyway

        # Re-check the cache now that we hold the lock — another process may
        # have already refreshed it while we were waiting.
        if not force_refresh:
            cached = _load_cached_token()
            if cached:
                return cached

        email, password = _load_credentials()
        token = _sign_in(email, password)
        _save_token(token)
        return token

    finally:
        if lock_acquired:
            try:
                lock_path.unlink()
            except Exception:
                pass


def auth_headers(force_refresh: bool = False) -> dict:
    """Return HTTP headers dict with Bearer token for API calls."""
    return {"Authorization": f"Bearer {get_token(force_refresh)}"}


def list_profiles() -> list:
    """
    Fetch all profiles from the Multilogin cloud API.
    Returns a list of profile dicts, each containing 'id', 'folder_id', 'name', etc.
    """
    headers = auth_headers()
    url     = f"{CLOUD_API}/profile/search"
    body    = {
        "is_removed":   False,
        "limit":        100,
        "offset":       0,
        "search_text":  "",
        "storage_type": "all",
        "order_by":     "created_at",
        "sort":         "asc",
    }

    def _do_search(hdrs: dict) -> list | None:
        try:
            resp = requests.post(url, headers=hdrs, json=body, timeout=20)
        except requests.RequestException as e:
            raise RuntimeError(f"Could not reach Multilogin API: {e}")
        if resp.status_code == 200:
            data = resp.json()
            profiles = data.get("data", {}).get("profiles")
            if isinstance(profiles, list):
                return profiles
        elif resp.status_code == 401:
            return None  # signal to retry with fresh token
        raise RuntimeError(
            f"Profile search failed ({resp.status_code}): {resp.text[:300]}"
        )

    result = _do_search(headers)
    if result is None:
        # Token expired — refresh once and retry
        headers = auth_headers(force_refresh=True)
        result  = _do_search(headers)

    if result is None:
        raise RuntimeError(
            "Profile search returned 401 even after token refresh.\n"
            "Check your credentials in config/multilogin.yaml"
        )

    return result
