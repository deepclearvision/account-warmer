"""
Proxies router — full proxy pool management.

Proxies live in config/proxies.yaml as a standalone pool.
Assigning a proxy to an account writes it to both proxies.yaml (assigned_to)
and accounts.yaml (proxy field), keeping the warmer in sync.

Supports two input formats:
  URL:   socks5://user:pass@host:1080
  Colon: 193.187.1.1:30755:USERNAME:PASSWORD
"""

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests as _requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.deps import load_accounts, save_accounts, load_proxies, save_proxies

router = APIRouter(prefix="/api/proxies", tags=["proxies"])


# ── Proxy parsing ─────────────────────────────────────────────────────────────

def _parse_proxy_line(raw: str) -> dict:
    """
    Parse a proxy string in either supported format.

    Format A — Standard URL:
        socks5://user:pass@host:port
        https://user:pass@host:port

    Format B — Colon-separated (ip:port:user:pass):
        193.187.142.31:30755:USERNAME:PASSWORD
    """
    raw = raw.strip()
    if not raw:
        return {"valid": False, "raw": raw, "error": "Empty string"}

    # Format B: 4 colon-separated fields where field[1] is all digits
    parts = raw.split(":")
    if len(parts) == 4 and parts[1].isdigit() and not raw.startswith(("http", "socks")):
        ip, port_str, username, password = parts
        try:
            port = int(port_str)
        except ValueError:
            return {"valid": False, "raw": raw, "format_detected": "colon",
                    "error": f"Invalid port: {port_str!r}"}
        normalised = f"http://{username}:{password}@{ip}:{port}"
        return {
            "valid": True, "raw": raw, "format_detected": "colon",
            "proxy_type": "HTTP", "host": ip, "port": port,
            "username": username, "password": password,
            "normalised_url": normalised, "error": "",
        }

    # Format A — URL
    try:
        p = urlparse(raw)
        scheme = p.scheme.lower()
        type_map = {
            "socks5": "SOCKS5", "socks4": "SOCKS4",
            "http": "HTTP",     "https": "HTTP",
        }
        proxy_type = type_map.get(scheme)
        if not proxy_type:
            return {"valid": False, "raw": raw, "format_detected": "url",
                    "error": f"Unrecognised scheme: {scheme!r}"}
        if not p.hostname or not p.port:
            return {"valid": False, "raw": raw, "format_detected": "url",
                    "error": "Could not parse host or port"}
        return {
            "valid": True, "raw": raw, "format_detected": "url",
            "proxy_type": proxy_type, "host": p.hostname, "port": p.port,
            "username": p.username or "", "password": p.password or "",
            "normalised_url": raw, "error": "",
        }
    except Exception as e:
        return {"valid": False, "raw": raw, "format_detected": "unknown", "error": str(e)}


def _next_id(proxies: list) -> str:
    existing = {p["id"] for p in proxies}
    n = 1
    while f"prx_{n:03d}" in existing:
        n += 1
    return f"prx_{n:03d}"


# ── Proxy testing ─────────────────────────────────────────────────────────────

def _test_sync(proxy_url: str, timeout: int = 15) -> dict:
    """Blocking proxy test — run in a thread via asyncio.to_thread."""
    start = time.time()
    try:
        proxies = {"http": proxy_url, "https": proxy_url}
        resp = _requests.get(
            "https://www.google.com",
            proxies=proxies,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        latency = int((time.time() - start) * 1000)
        ok      = resp.status_code < 400
        return {"ok": ok, "status_code": resp.status_code,
                "latency_ms": latency, "error": ""}
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        return {"ok": False, "status_code": None,
                "latency_ms": latency, "error": str(e)[:300]}


async def _test_async(proxy_url: str) -> dict:
    return await asyncio.to_thread(_test_sync, proxy_url)


def _apply_test_result(proxy: dict, result: dict) -> None:
    """Write test result fields into a proxy dict in-place."""
    proxy["test_status"]    = "ok" if result["ok"] else "failed"
    proxy["last_tested"]    = datetime.now().isoformat()
    proxy["test_latency_ms"] = result.get("latency_ms")
    proxy["test_error"]     = result.get("error", "")


# ── Request models ────────────────────────────────────────────────────────────

class ParseRequest(BaseModel):
    lines: list[str]


class ImportRequest(BaseModel):
    proxies:  list[str]
    category: str = ""


class AssignRequest(BaseModel):
    account_id: str
    push_to_ml: bool = True


class UpdateRequest(BaseModel):
    category: Optional[str] = None


class BulkDeleteRequest(BaseModel):
    ids: list[str]


class BulkAssignRequest(BaseModel):
    ids:        list[str]
    account_id: str
    push_to_ml: bool = True


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/parse")
async def parse_proxies(body: ParseRequest):
    """Parse a list of raw proxy strings and return structured results."""
    return [_parse_proxy_line(line) for line in body.lines if line.strip()]


@router.get("")
async def list_proxies():
    """Return all proxies in the pool with account assignment details."""
    proxies  = await load_proxies()
    accounts = await load_accounts()
    acc_map  = {a["id"]: a.get("email", a["id"]) for a in accounts}

    # Count how many accounts currently use each proxy URL
    usage_by_url: dict[str, int] = {}
    for a in accounts:
        url = a.get("proxy", "")
        if url:
            usage_by_url[url] = usage_by_url.get(url, 0) + 1

    result = []
    for p in proxies:
        row = dict(p)
        row["assigned_email"] = acc_map.get(p.get("assigned_to") or "", "")
        row["usage_count"]    = usage_by_url.get(p["url"], 0)
        result.append(row)
    return result


@router.get("/accounts")
async def list_accounts_for_assign():
    """Return minimal account list for assignment dropdowns."""
    accounts = await load_accounts()
    return [{"id": a["id"], "email": a.get("email", a["id"])} for a in accounts]


@router.get("/categories")
async def list_categories():
    """Return all unique proxy categories."""
    proxies = await load_proxies()
    cats = sorted({p.get("category", "") for p in proxies if p.get("category")})
    return cats


@router.post("/import")
async def import_proxies(body: ImportRequest):
    """Add proxies from a paste list into the proxy pool."""
    proxies      = await load_proxies()
    existing_urls = {p["url"] for p in proxies}
    added   = []
    skipped = 0

    for raw in body.proxies:
        parsed = _parse_proxy_line(raw.strip())
        if not parsed.get("valid"):
            skipped += 1
            continue
        url = parsed["normalised_url"]
        if url in existing_urls:
            skipped += 1
            continue
        new_proxy = {
            "id":            _next_id(proxies + added),
            "url":           url,
            "proxy_type":    parsed["proxy_type"],
            "host":          parsed["host"],
            "port":          parsed["port"],
            "username":      parsed.get("username", ""),
            "category":      body.category,
            "test_status":   "unknown",
            "last_tested":   None,
            "test_latency_ms": None,
            "test_error":    None,
            "assigned_to":   None,
        }
        proxies.append(new_proxy)
        added.append(new_proxy)
        existing_urls.add(url)

    await save_proxies(proxies)
    return {"added": len(added), "skipped": skipped}


@router.put("/{proxy_id}")
async def update_proxy(proxy_id: str, body: UpdateRequest):
    """Update proxy metadata (category etc.)."""
    proxies = await load_proxies()
    target  = next((p for p in proxies if p["id"] == proxy_id), None)
    if not target:
        raise HTTPException(404, f"Proxy {proxy_id!r} not found")
    if body.category is not None:
        target["category"] = body.category
    await save_proxies(proxies)
    return target


@router.post("/{proxy_id}/test")
async def test_one(proxy_id: str):
    """Test a single proxy against google.com."""
    proxies = await load_proxies()
    target  = next((p for p in proxies if p["id"] == proxy_id), None)
    if not target:
        raise HTTPException(404, f"Proxy {proxy_id!r} not found")
    result = await _test_async(target["url"])
    _apply_test_result(target, result)
    await save_proxies(proxies)
    return {
        **result,
        "proxy_id":    proxy_id,
        "test_status": target["test_status"],
        "last_tested": target["last_tested"],
    }


@router.post("/test-all")
async def test_all():
    """Test all proxies sequentially against google.com."""
    proxies = await load_proxies()
    if not proxies:
        return {"tested": 0, "ok": 0, "failed": 0}
    ok = failed = 0
    for p in proxies:
        result = await _test_async(p["url"])
        _apply_test_result(p, result)
        if result["ok"]:
            ok += 1
        else:
            failed += 1
    await save_proxies(proxies)
    return {"tested": len(proxies), "ok": ok, "failed": failed}


@router.put("/{proxy_id}/assign")
async def assign_one(proxy_id: str, body: AssignRequest):
    """Assign a proxy to one account (updates both proxies.yaml and accounts.yaml)."""
    proxies  = await load_proxies()
    accounts = await load_accounts()
    target   = next((p for p in proxies if p["id"] == proxy_id), None)
    account  = next((a for a in accounts if a["id"] == body.account_id), None)
    if not target:
        raise HTTPException(404, f"Proxy {proxy_id!r} not found")
    if not account:
        raise HTTPException(404, f"Account {body.account_id!r} not found")

    # Clear any other proxy currently assigned to this account
    for p in proxies:
        if p["id"] != proxy_id and p.get("assigned_to") == body.account_id:
            p["assigned_to"] = None

    # Clear proxy from previous account if being reassigned
    old_acc_id = target.get("assigned_to")
    if old_acc_id and old_acc_id != body.account_id:
        for a in accounts:
            if a["id"] == old_acc_id and a.get("proxy") == target["url"]:
                a["proxy"] = ""

    target["assigned_to"] = body.account_id
    account["proxy"]      = target["url"]

    await save_proxies(proxies)
    await save_accounts(accounts)

    ml_pushed = False
    if body.push_to_ml and account.get("multilogin_profile_id"):
        try:
            from core.multilogin_auth import auth_headers
            from set_proxies import push_proxy
            ml_pushed = push_proxy(account, auth_headers())
        except Exception:
            pass

    return {"proxy_id": proxy_id, "account_id": body.account_id, "ml_pushed": ml_pushed}


@router.post("/bulk-assign")
async def bulk_assign(body: BulkAssignRequest):
    """Assign multiple proxies to multiple accounts (one proxy per account, in order)."""
    proxies  = await load_proxies()
    accounts = await load_accounts()
    acc_map  = {a["id"]: a for a in accounts}
    account  = acc_map.get(body.account_id)
    if not account:
        raise HTTPException(404, f"Account {body.account_id!r} not found")

    assigned = 0
    for proxy_id in body.ids:
        target = next((p for p in proxies if p["id"] == proxy_id), None)
        if not target:
            continue
        # Clear old assignment
        old = target.get("assigned_to")
        if old and old != body.account_id:
            old_acc = acc_map.get(old)
            if old_acc and old_acc.get("proxy") == target["url"]:
                old_acc["proxy"] = ""
        target["assigned_to"] = body.account_id
        account["proxy"]      = target["url"]
        assigned += 1

    await save_proxies(proxies)
    await save_accounts(accounts)

    ml_pushed = False
    if body.push_to_ml and assigned > 0 and account.get("multilogin_profile_id"):
        try:
            from core.multilogin_auth import auth_headers
            from set_proxies import push_proxy
            ml_pushed = push_proxy(account, auth_headers())
        except Exception:
            pass

    return {"assigned": assigned, "account_id": body.account_id, "ml_pushed": ml_pushed}


@router.delete("/bulk-delete")
async def bulk_delete(body: BulkDeleteRequest):
    """Delete multiple proxies and clear them from any assigned accounts."""
    ids      = set(body.ids)
    proxies  = await load_proxies()
    accounts = await load_accounts()

    to_clear = {
        p["assigned_to"]
        for p in proxies
        if p["id"] in ids and p.get("assigned_to")
    }
    for a in accounts:
        if a["id"] in to_clear:
            a["proxy"] = ""

    new_list = [p for p in proxies if p["id"] not in ids]
    removed  = len(proxies) - len(new_list)

    await save_proxies(new_list)
    if to_clear:
        await save_accounts(accounts)
    return {"deleted": removed}


@router.delete("/{proxy_id}")
async def delete_one(proxy_id: str):
    """Delete a single proxy and clear it from the assigned account."""
    proxies = await load_proxies()
    target  = next((p for p in proxies if p["id"] == proxy_id), None)
    if not target:
        raise HTTPException(404, f"Proxy {proxy_id!r} not found")

    if target.get("assigned_to"):
        accounts = await load_accounts()
        for a in accounts:
            if a["id"] == target["assigned_to"] and a.get("proxy") == target["url"]:
                a["proxy"] = ""
        await save_accounts(accounts)

    await save_proxies([p for p in proxies if p["id"] != proxy_id])
    return {"deleted": proxy_id}
