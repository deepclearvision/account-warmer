import os, requests, time
from pathlib import Path
from dotenv import load_dotenv

ENV_PATH = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\warmer.env")
load_dotenv(ENV_PATH)

API_KEY = os.getenv("SMSPOOL_API_KEY")
BASE_URL = "https://api.smspool.net/pool"

PLATFORM_SERVICE_KEYS = {
    "facebook":       "SMSPOOL_SERVICE_FACEBOOK",
    "twitter":        "SMSPOOL_SERVICE_TWITTER",
    "instagram_lite": "SMSPOOL_SERVICE_INSTAGRAM_LITE",
    "pinterest":      "SMSPOOL_SERVICE_PINTEREST",
    "reddit":         "SMSPOOL_SERVICE_REDDIT",
    "tiktok":         "SMSPOOL_SERVICE_TIKTOK",
}

def _get(path, params=None):
    url = "%s%s" % (BASE_URL, path)
    p = params or {}
    p["api_key"] = API_KEY
    r = requests.get(url, params=p, timeout=30)
    return r.json()

def _post(path, data=None):
    url = "%s%s" % (BASE_URL, path)
    d = data or {}
    d["api_key"] = API_KEY
    r = requests.post(url, data=d, timeout=30)
    return r.json()

def get_service_id(platform, default_country=None):
    env_key = PLATFORM_SERVICE_KEYS.get(platform)
    service_id = os.getenv(env_key) if env_key else None
    country_key = "SMSPOOL_COUNTRY_%s" % platform.upper()
    country = os.getenv(country_key) or default_country or os.getenv("SMSPOOL_COUNTRY", "GB")

    if service_id:
        return str(service_id), country

    keywords = {
        "facebook": ["facebook"],
        "twitter": ["twitter"],
        "instagram_lite": ["instagram"],
        "pinterest": ["pinterest"],
        "reddit": ["reddit"],
        "tiktok": ["tiktok"],
    }
    try:
        services = _get("/services")
        if isinstance(services, list):
            for kw in keywords.get(platform, [platform]):
                for s in services:
                    sid = str(s.get("ID") or s.get("id") or "")
                    name = (s.get("name") or s.get("service") or "").lower()
                    if kw in name:
                        return sid, country
    except Exception as e:
        print("Could not resolve SMS Pool service for %s: %s" % (platform, e))

    raise ValueError("No SMSPOOL_SERVICE_%s set and could not resolve automatically" % platform.upper())

def order_sms(platform, country=None):
    service_id, country = get_service_id(platform, country)
    print("Ordering SMS for %s: service=%s, country=%s" % (platform, service_id, country))
    return _post("/order", {"service": service_id, "country": country})

def check_sms(order_id):
    return _get("/check", {"order_id": order_id})

def wait_for_sms(order_id, timeout=180, interval=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = check_sms(order_id)
        status = r.get("status", 0)
        sms = r.get("sms", "")
        if status == 1 and sms:
            return sms
        time.sleep(interval)
    raise TimeoutError("No SMS received for order %s" % order_id)
