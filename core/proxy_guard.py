"""
core/proxy_guard.py — Pre-session proxy verification gate.

Checks that:
  1. A proxy is configured for the account
  2. The proxy connects and routes traffic correctly
  3. The exit IP is NOT the machine's real IP (no leak)

Raises ProxyNotConfiguredError or ProxyVerificationError on failure.
The session should be aborted when either is raised.
"""

import requests

# IP echo services — tried in order, first success wins
_IP_ECHO_URLS = [
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://icanhazip.com",
]


class ProxyNotConfiguredError(Exception):
    """No proxy is configured for this account."""


class ProxyVerificationError(Exception):
    """Proxy is configured but failed verification (connection failure or IP leak)."""


def get_machine_ip(timeout: int = 10) -> str | None:
    """Return the machine's real outbound IP (no proxy), or None if unreachable."""
    for url in _IP_ECHO_URLS:
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp.text.strip()
        except Exception:
            continue
    return None


def verify_proxy(proxy_url: str, machine_ip: str | None = None, timeout: int = 20) -> str:
    """
    Verify proxy_url routes traffic correctly and its exit IP differs from machine_ip.

    Returns the proxy exit IP on success.
    Raises ProxyVerificationError on connection failure or IP leak.
    """
    proxies  = {"http": proxy_url, "https": proxy_url}
    exit_ip  = None
    last_err = None

    for url in _IP_ECHO_URLS:
        try:
            resp = requests.get(
                url, proxies=proxies, timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code == 200:
                exit_ip = resp.text.strip()
                break
        except Exception as e:
            last_err = str(e)
            continue

    if not exit_ip:
        raise ProxyVerificationError(
            f"Proxy did not connect — could not reach any IP echo service. "
            f"Last error: {last_err}"
        )

    if machine_ip and exit_ip == machine_ip:
        raise ProxyVerificationError(
            f"IP LEAK — proxy exit IP ({exit_ip}) matches machine real IP. "
            f"Session would run on bare machine IP — aborting."
        )

    return exit_ip


def check_account_proxy(account: dict, log=None) -> str:
    """
    Full pre-session proxy check for an account dict from accounts.yaml.

    Steps:
      1. Confirm proxy field is non-empty
      2. Get machine real IP (best-effort, for leak detection)
      3. Verify proxy connects and exit IP differs from machine IP

    Returns exit IP string on success.
    Raises ProxyNotConfiguredError or ProxyVerificationError on failure.
    """
    proxy_url = account.get("proxy", "").strip()
    if not proxy_url:
        raise ProxyNotConfiguredError(
            f"Account {account.get('id', '?')} has no proxy configured — "
            f"session would run on bare machine IP."
        )

    if log:
        log.info("Proxy guard: verifying proxy before session start …")

    machine_ip = get_machine_ip()
    if log:
        if machine_ip:
            log.debug(f"Proxy guard: machine real IP = {machine_ip}")
        else:
            log.warning("Proxy guard: could not determine machine real IP — leak check weakened")

    exit_ip = verify_proxy(proxy_url, machine_ip=machine_ip)

    if log:
        log.info(f"Proxy guard: OK — exit IP = {exit_ip}")

    return exit_ip
