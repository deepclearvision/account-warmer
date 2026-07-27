"""
SMS Pool — Phone Verification for Social Account Creation.

Usage:
    from core.sms_verification import order_number, poll_code, cancel_order

    # Request a number for Facebook verification
    order = order_number("facebook")
    phone_number = order["number"]
    order_id = order["order_id"]

    # ... enter phone_number into the app's sign-up form ...

    # Wait for SMS code
    code = poll_code(order_id, timeout=120)
    if code:
        # ... enter code into the app ...
        pass
    else:
        cancel_order(order_id)
"""

import logging
import time
from pathlib import Path

import requests
import yaml

log = logging.getLogger("sms_verification")

# ── Load config ────────────────────────────────────────────────────────────────

def _load_api_key() -> str:
    settings_file = Path(__file__).parent.parent / "config" / "settings.yaml"
    try:
        data = yaml.safe_load(settings_file.read_text(encoding="utf-8")) or {}
        return (data.get("sms_pool") or {}).get("api_key", "")
    except Exception:
        return ""

_SMS_POOL_KEY = _load_api_key()
_BASE = "https://api.smspool.net"

# ── Service name mapping ───────────────────────────────────────────────────────

# SMS Pool uses numeric service IDs or short names:
# Full service list: https://api.smspool.net/service/list
_SERVICE_MAP = {
    "facebook":   "1",
    "instagram":  "3",
    "tiktok":     "18",
    "pinterest":  "14",
    "linkedin":   "4",
    "reddit":     "10",
    "twitter":    "2",
    "telegram":   "6",
    "whatsapp":   "5",
    "google":     "8",
}


def get_balance() -> float:
    """Return current SMS Pool account balance in USD."""
    try:
        r = requests.get(
            f"{_BASE}/balance",
            params={"key": _SMS_POOL_KEY},
            timeout=15,
        )
        data = r.json()
        return float(data.get("balance", 0))
    except Exception as e:
        log.warning("SMS Pool balance check failed: %s", e)
        return 0.0


def order_number(service: str, country: str = "UK") -> dict | None:
    """
    Request a phone number for the given service.

    Args:
        service: Short name (e.g. "facebook", "instagram") or numeric ID.
        country: Country code (default "UK").

    Returns:
        dict with keys: order_id, number, service, country, price
        None on failure.
    """
    service_id = _SERVICE_MAP.get(service.lower(), service)
    try:
        r = requests.get(
            f"{_BASE}/order/add",
            params={
                "key":     _SMS_POOL_KEY,
                "service": service_id,
                "country": country,
            },
            timeout=20,
        )
        data = r.json()
        if data.get("success") == 1:
            return {
                "order_id": data.get("order_id", ""),
                "number":   data.get("number", ""),
                "service":  service,
                "country":  country,
                "price":    data.get("price", 0),
            }
        error = data.get("message", "unknown")
        log.warning("SMS Pool order failed: %s", error)
        return None
    except Exception as e:
        log.warning("SMS Pool order error: %s", e)
        return None


def poll_code(order_id: str, timeout: int = 120,
              poll_interval: int = 5) -> str | None:
    """
    Poll for the SMS verification code.

    Args:
        order_id: The order ID from order_number().
        timeout: Maximum seconds to wait (default 120).
        poll_interval: Seconds between polls (default 5).

    Returns:
        The verification code string, or None on timeout/cancel.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(
                f"{_BASE}/order/check",
                params={"key": _SMS_POOL_KEY, "order": order_id},
                timeout=10,
            )
            data = r.json()
            status = data.get("status", 0)

            # status 3 = SMS received
            if status == 3:
                code = data.get("sms", "").strip()
                if code:
                    log.info("SMS code received: %s", code)
                    return code

            # status 2 = cancelled / expired
            if status == 2:
                log.warning("SMS order %s cancelled/expired", order_id)
                return None

            # status 1 = pending / waiting
            time.sleep(poll_interval)
        except Exception as e:
            log.warning("SMS Pool poll error: %s", e)
            time.sleep(poll_interval)

    log.warning("SMS poll timed out after %ds for order %s", timeout, order_id)
    return None


def cancel_order(order_id: str) -> bool:
    """Cancel an SMS Pool order (frees the number)."""
    try:
        r = requests.get(
            f"{_BASE}/order/cancel",
            params={"key": _SMS_POOL_KEY, "order": order_id},
            timeout=10,
        )
        data = r.json()
        return data.get("success") == 1
    except Exception as e:
        log.warning("SMS Pool cancel error: %s", e)
        return False
