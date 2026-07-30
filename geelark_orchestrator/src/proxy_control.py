"""
proxy_control.py — StreamVia mobile proxy control with host-scoped TLS bypass.

Reads credentials from account-warmer/warmer.env (git-ignored).
TLS verification is disabled ONLY for mobile-proxy-140-166.streamvia.io:8000
because that endpoint uses a self-signed cert (standard for StreamVia infra).

The control endpoint is a web UI at /index.php — not a simple REST API.
GET returns HTML dashboard. POST with action=... triggers changes.
For plaintext responses, append ?format=txt.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

log = logging.getLogger("proxy_control")

# The only host where we bypass TLS — scoped narrowly
_STREAMVIA_HOST = "mobile-proxy-140-166.streamvia.io"
_STREAMVIA_CONTROL_PORT = 8000


def _load_env(path: Path) -> dict:
    """Parse a simple KEY=VALUE env file (no bash eval)."""
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
    return data


def _get_control_url() -> str:
    """Read GEELARK_PROXY_CONTROL_URL from warmer.env."""
    env_paths = [
        Path(__file__).resolve().parent.parent.parent / "account-warmer" / "warmer.env",
        Path(__file__).resolve().parent.parent.parent / "warmer.env",
    ]
    for p in env_paths:
        data = _load_env(p)
        if "GEELARK_PROXY_CONTROL_URL" in data:
            return data["GEELARK_PROXY_CONTROL_URL"]
    raise RuntimeError(
        "GEELARK_PROXY_CONTROL_URL not found in warmer.env. "
        "Place the rotated StreamVia password in account-warmer/warmer.env"
    )


def _extract_creds(control_url: str) -> tuple[str, str]:
    """Parse username:password from the URL."""
    parsed = urlparse(control_url)
    if parsed.username and parsed.password:
        return parsed.username, parsed.password
    raise RuntimeError("GEELARK_PROXY_CONTROL_URL missing username/password")


def _make_control_url(base: str, query: str = "") -> str:
    """Build a control URL pointing to /index.php with optional query."""
    parsed = urlparse(base)
    path = parsed.path.rstrip("/") + "/index.php"
    if query:
        path += "?" + query
    # Reconstruct with auth stripped (passed separately)
    return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}{path}"


def _host_scoped_post(url: str, auth: tuple[str, str], data: dict | None = None, timeout: int = 30) -> requests.Response:
    """POST with verify=False ONLY for the StreamVia control host."""
    parsed = urlparse(url)
    is_streamvia = parsed.hostname == _STREAMVIA_HOST and parsed.port == _STREAMVIA_CONTROL_PORT
    if not is_streamvia:
        raise RuntimeError(f"TLS bypass refused: {url} is not the StreamVia control endpoint")
    return requests.post(url, auth=auth, data=data, verify=False, timeout=timeout)


def _host_scoped_get(url: str, auth: tuple[str, str], timeout: int = 15) -> requests.Response:
    """GET with verify=False ONLY for the StreamVia control host."""
    parsed = urlparse(url)
    is_streamvia = parsed.hostname == _STREAMVIA_HOST and parsed.port == _STREAMVIA_CONTROL_PORT
    if not is_streamvia:
        raise RuntimeError(f"TLS bypass refused: {url} is not the StreamVia control endpoint")
    return requests.get(url, auth=auth, verify=False, timeout=timeout)


def _parse_ip_from_html(body: str) -> str | None:
    """Extract IP from HTML dashboard response."""
    m = re.search(r"Current External IP:\s*([\d.]+)", body)
    if m:
        return m.group(1)
    return None


def check_proxy_ip() -> str:
    """Query StreamVia control endpoint for current proxy IP.

    Returns the IP string (e.g. "31.94.24.183") or raises on failure.
    """
    control_url = _get_control_url()
    user, pw = _extract_creds(control_url)
    url = _make_control_url(control_url, query="format=txt")
    log.info("Checking proxy IP via %s", url)
    r = _host_scoped_get(url, auth=(user, pw))
    r.raise_for_status()
    body = r.text.strip()
    log.debug("Proxy check raw: %s", body[:200])
    # Try plaintext first (take only first line in case of multi-line response)
    first_line = body.splitlines()[0] if body else ""
    if first_line.startswith("Ready:"):
        ip = first_line.split(":", 1)[1].strip()
        return ip
    # Fall back to HTML parsing
    ip = _parse_ip_from_html(body)
    if ip:
        return ip
    raise RuntimeError(f"Unexpected proxy check response: {body[:300]}")


def change_proxy_ip() -> str:
    """Trigger a unique IP rotation via StreamVia changeipunique.

    Returns the new IP string after polling until Ready.
    """
    control_url = _get_control_url()
    user, pw = _extract_creds(control_url)
    url = _make_control_url(control_url)
    log.info("Requesting proxy IP rotation (changeipunique) via POST %s", url)
    r = _host_scoped_post(url, auth=(user, pw), data={"action": "changeipunique"})
    r.raise_for_status()
    body = r.text.strip()
    log.debug("Proxy rotation raw: %s", body[:300])

    # Poll until Ready (up to 3 minutes)
    check_url = _make_control_url(control_url, query="format=txt")
    deadline = time.time() + 180
    while time.time() < deadline:
        time.sleep(10)
        r2 = _host_scoped_get(check_url, auth=(user, pw))
        r2.raise_for_status()
        body2 = r2.text.strip()
        first_line2 = body2.splitlines()[0] if body2 else ""
        if first_line2.startswith("Ready:"):
            ip = first_line2.split(":", 1)[1].strip()
            log.info("New proxy IP confirmed: %s", ip)
            return ip
        ip = _parse_ip_from_html(body2)
        if ip:
            log.info("New proxy IP confirmed (HTML): %s", ip)
            return ip
        log.debug("Still busy: %s", body2[:200])

    raise RuntimeError("Proxy IP rotation timed out after 180s")
