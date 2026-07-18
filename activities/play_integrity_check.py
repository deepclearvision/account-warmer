"""
Play Integrity API Checker — Audit all GeelarK phones.

Installs the "Play Integrity API Checker" app (by Nikolas Spiridakis,
package com.henrikherzig.playintegritychecker) on each cloud phone, runs
the integrity check, and parses the uiautomator XML to extract boolean
verdicts for all three integrity levels.

Proxy IP is rotated before each phone.  If two phones ever receive the
same IP the audit ABORTS immediately — linked IPs destroy account isolation.

Usage:
    from activities.play_integrity_check import run_integrity_audit

    results = run_integrity_audit(accounts)
    # → list[dict] with keys: account_id, email, phone_id, device_brand,
    #   device_model, os_version, proxy_ip, integrity{basic,device,strong},
    #   tier, parsed, error, screenshot_path
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("play_integrity")

PACKAGE = "gr.nikolasspyr.integritycheck"
APK_NAME = "play-integrity-checker.apk"

# ---------------------------------------------------------------------------
# Helpers imported from sibling modules
# ---------------------------------------------------------------------------

from activities.google_login_mobile import (
    _shell, _find_and_tap, _get_window_focus, _rotate_proxy_ip,
    _wait_for_foreground_app,
)
from activities.mobile_warmup import _wait_for_phone_ready


def _get_apk_path() -> Path | None:
    """Resolve the Play Integrity Checker APK or bundle path.

    Looks in data/ and the app root for:
      - play-integrity-checker.apk   (plain APK)
      - play-integrity-checker.xapk  (APKPure bundle)
      - play-integrity-checker.apkm  (APKMirror bundle)
    """
    app_dir = Path(__file__).parent.parent
    for ext in (".apk", ".xapk", ".apkm"):
        for base in (app_dir / "data", app_dir):
            p = base / f"play-integrity-checker{ext}"
            if p.exists():
                return p
    # Also check Downloads
    dl = Path(os.environ.get("USERPROFILE", "")) / "Downloads"
    for ext in (".apk", ".xapk", ".apkm"):
        p = dl / f"play-integrity-checker{ext}"
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------
# XML parser — extract integrity verdicts from uiautomator dump
# ---------------------------------------------------------------------------

def _parse_verdict_from_xml(xml: str) -> dict:
    """
    Determine whether the integrity check has completed and, if so, extract
    the verdict from a uiautomator XML dump.

    Strategy (avoids fragile emoji/indicator detection):
      1. Error dialog visible → check completed with error → parsed=True
      2. CHECK button still visible → check NOT yet run → parsed=False
      3. Labels present + no CHECK button → check completed successfully
         → verdict labels ARE the results → parsed=True

    Returns:
        {"basic_integrity": bool, "device_integrity": bool,
         "strong_integrity": bool, "parsed": bool,
         "app_error": str | None}
    """
    if not xml:
        return {"basic_integrity": False, "device_integrity": False,
                "strong_integrity": False, "parsed": False, "app_error": None}

    # ── 1. Check for error dialogs ──────────────────────────────────────
    error_match = re.search(
        r'text="([^"]*(?:Integrity API Error|Error|PLAY_STORE_ACCOUNT_NOT_FOUND|'
        r'CLIENT_TRANSIENT_ERROR|NETWORK_ERROR|TOO_MANY_REQUESTS|INTERNAL_ERROR)'
        r'[^"]*)"',
        xml, re.IGNORECASE,
    )
    if error_match:
        err_msg = error_match.group(1)[:200]
        return {"basic_integrity": False, "device_integrity": False,
                "strong_integrity": False, "parsed": True, "app_error": err_msg}

    # ── 2. Check if CHECK button is still visible ────────────────────────
    # If CHECK/Check/Run/Start button is on screen, the check hasn't run yet.
    check_btn_visible = bool(re.search(
        r'text="(?:CHECK|Check Integrity|CHECK INTEGRITY|Run|Start|Play Integrity)"',
        xml,
    ))
    if check_btn_visible:
        return {"basic_integrity": False, "device_integrity": False,
                "strong_integrity": False, "parsed": False, "app_error": None}

    # ── 3. Check for verdict labels (check completed, no error) ─────────
    basic  = "MEETS_BASIC_INTEGRITY" in xml
    device = "MEETS_DEVICE_INTEGRITY" in xml
    strong = "MEETS_STRONG_INTEGRITY" in xml

    if basic or device or strong:
        return {
            "basic_integrity":  basic,
            "device_integrity": device,
            "strong_integrity": strong,
            "parsed":           True,
            "app_error":        None,
        }

    # ── 4. No recognised state ──────────────────────────────────────────
    return {"basic_integrity": False, "device_integrity": False,
            "strong_integrity": False, "parsed": False, "app_error": None}


def _classify_tier(integrity: dict) -> str:
    """Classify phone into a tier based on integrity verdicts."""
    if integrity.get("strong_integrity"):
        return "strong"
    if integrity.get("device_integrity"):
        return "device_only"
    if integrity.get("basic_integrity"):
        return "basic_only"
    return "failed"


# ---------------------------------------------------------------------------
# Split APK bundle installer (handles .xapk / .apkm bundles)
# ---------------------------------------------------------------------------

def _install_split_apk_bundle(phone_id: str, bundle_path: Path,
                               client=None) -> bool:
    """
    Install a split APK bundle (.xapk or .apkm) on a cloud phone using
    Android's session-based pm install (install-create / install-write /
    install-commit).

    The bundle is a ZIP containing a base APK and zero or more
    split_config.*.apk files.  Each is uploaded to the phone, then
    installed as a single session.

    Returns True if the app ends up installed (package visible in pm list).
    """
    import tempfile
    import zipfile

    if client is None:
        from core.geelark_client import GeelarKClient
        client = GeelarKClient()

    log.info("Installing split APK bundle from %s …", bundle_path.name)

    try:
        with zipfile.ZipFile(bundle_path, "r") as zf:
            # Find all .apk entries (base + configs)
            apk_entries = sorted(
                [n for n in zf.namelist() if n.endswith(".apk")],
                key=lambda n: (0 if "base" in n.lower() or not n.startswith("split_") else 1, n)
            )
            if not apk_entries:
                log.error("No APK files found in bundle %s", bundle_path.name)
                return False

            log.info("Bundle contains %d APK(s): %s", len(apk_entries),
                     ", ".join(apk_entries[:5]) + ("…" if len(apk_entries) > 5 else ""))

            # Extract each APK to a temp dir and upload to phone
            with tempfile.TemporaryDirectory() as tmp:
                phone_paths: list[str] = []
                for entry in apk_entries:
                    extracted = Path(tmp) / entry.replace("/", "_")
                    with zf.open(entry) as src:
                        extracted.write_bytes(src.read())
                    log.info("Extracted %s → %s (%d bytes)", entry,
                             extracted.name, extracted.stat().st_size)

                    # Upload via GeelarK presigned URL → push to phone
                    upload_url, resource_url = client._get_presigned_upload_url("apk")
                    if not upload_url or not resource_url:
                        log.error("Failed to get presigned URL for %s", entry)
                        return False
                    if not client._upload_file_to_presigned_url(extracted, upload_url):
                        log.error("Failed to upload %s to GeelarK CDN", entry)
                        return False
                    task_id = client._push_file_to_phone(phone_id, resource_url)
                    if not task_id:
                        log.error("Failed to push %s to phone", entry)
                        return False
                    if not client._poll_file_upload(task_id, max_wait=120):
                        log.error("Push %s to phone timed out", entry)
                        return False

                    # Find the uploaded file and rename to a known path
                    # (ls -t returns newest first; we need the one we just pushed)
                    r = _post_shell(phone_id,
                                    "ls -t /sdcard/Download/*.apk 2>/dev/null | head -1")
                    output = (r.get("output") or "").strip()
                    src_path = output.splitlines()[0].strip() if output.strip() else ""
                    if not src_path or not src_path.endswith(".apk"):
                        log.error("Could not locate uploaded %s on phone (output: %r)", entry, output[:80])
                        return False

                    # Rename to a deterministic name so later steps don't pick the wrong file
                    dest = f"/sdcard/Download/integrity_{len(phone_paths)}.apk"
                    r = _post_shell(phone_id, f"mv {src_path} {dest}")
                    log.info("  %s → %s on phone (%s)", entry, dest,
                             "OK" if r.get("code", 0) == 0 or "Success" in str(r) else str(r)[:60])
                    phone_paths.append(dest)

                # ── Session-based install ──────────────────────────────
                # 1. Create session
                r = _post_shell(phone_id, "pm install-create -r -g -t")
                out = (r.get("output") or "").strip()
                m = re.search(r"(\d+)", out)
                if not m:
                    log.error("install-create failed: %s", out)
                    return False
                session_id = m.group(1)
                log.info("Install session created: %s", session_id)

                # 2. Write each APK
                for idx, ppath in enumerate(phone_paths):
                    r = _post_shell(phone_id,
                                    f"pm install-write {session_id} {idx} {ppath}")
                    out = (r.get("output") or "").strip().lower()
                    if "success" not in out:
                        log.error("install-write %d failed: %s", idx, out)
                        # Try to abort the session
                        _post_shell(phone_id, f"pm install-abandon {session_id}")
                        return False
                    log.info("install-write %d/%d OK", idx + 1, len(phone_paths))

                # 3. Commit
                r = _post_shell(phone_id, f"pm install-commit {session_id}")
                out = (r.get("output") or "").strip().lower()
                if "success" not in out:
                    log.error("install-commit failed: %s", out)
                    return False
                log.info("install-commit OK — %s installed", PACKAGE)

    except Exception as e:
        log.error("Split APK install failed: %s", e)
        return False

    # Final verification
    return client.is_app_installed(phone_id, PACKAGE)


def _post_shell(phone_id: str, cmd: str) -> dict:
    """Thin wrapper around GeelarK shell execute for split-APK install."""
    from core.geelark_client import _post
    try:
        return _post("/open/v1/shell/execute", {"id": phone_id, "cmd": cmd})
    except Exception as e:
        log.warning("Shell command failed (%s): %s", cmd[:60], e)
        return {"output": str(e)}


# ---------------------------------------------------------------------------
# Per-phone check
# ---------------------------------------------------------------------------

def check_play_integrity(phone_id: str, acc_id: str = "",
                         email: str = "", apk_path: Path | None = None,
                         client=None) -> dict:
    """
    Run the Play Integrity check on a single GeelarK cloud phone.

    Args:
        phone_id:  GeelarK phone ID
        acc_id:    account label (for logging)
        email:     account email (for result row)
        apk_path:  path to the APK (auto-resolved if None)
        client:    existing GeelarKClient instance (created if None)

    Returns dict with keys:
        account_id, email, phone_id, device_brand, device_model,
        os_version, proxy_ip, integrity{basic,device,strong}, tier,
        parsed, error, screenshot_path
    """
    from core.geelark_client import GeelarKClient

    if client is None:
        client = GeelarKClient()

    result = {
        "account_id":    acc_id,
        "email":         email,
        "phone_id":      phone_id,
        "device_brand":  "",
        "device_model":  "",
        "os_version":    "",
        "proxy_ip":      "",
        "integrity":     {"basic_integrity": False, "device_integrity": False,
                          "strong_integrity": False},
        "tier":          "",
        "parsed":        False,
        "error":         None,
        "screenshot_path": "",
    }

    # ── Enrich with device info from GeelarK ──────────────────────────────
    try:
        all_phones = client.list_phones(page_size=50)
        for p in all_phones:
            if p.get("id") == phone_id:
                equip = p.get("equipmentInfo", {})
                result["device_brand"] = equip.get("deviceBrand", "")
                result["device_model"] = equip.get("deviceModel", "")
                result["os_version"]   = equip.get("osVersion", "")
                break
    except Exception:
        pass

    # ── 1. Start phone ────────────────────────────────────────────────────
    log.info("[%s] Starting phone %s …", acc_id, phone_id)
    try:
        client.start_phone(phone_id)
    except Exception as e:
        result["error"] = f"Failed to start phone: {e}"
        log.warning("[%s] %s", acc_id, result["error"])
        return result

    # ── 2. Wait for boot ──────────────────────────────────────────────────
    if not _wait_for_phone_ready(phone_id, acc_id, timeout=90):
        result["error"] = "Phone did not boot in 90s"
        log.warning("[%s] %s", acc_id, result["error"])
        _stop_quietly(client, phone_id)
        return result

    # ── 3. Install APK (handles both single APK and split bundles) ─────
    if not client.is_app_installed(phone_id, PACKAGE):
        apk = apk_path or _get_apk_path()
        if not apk:
            result["error"] = (
                f"APK '{APK_NAME}' not found. Download 'Play Integrity API Checker' "
                f"by Nikolas Spiridakis and place it in account-warmer/data/"
            )
            log.error("[%s] %s", acc_id, result["error"])
            _stop_quietly(client, phone_id)
            return result

        log.info("[%s] Installing %s …", acc_id, APK_NAME)

        # Check if it's a split bundle (.xapk / .apkm) or a plain APK
        if apk.suffix.lower() in (".xapk", ".apkm"):
            ok = _install_split_apk_bundle(phone_id, apk, client)
        else:
            ok = client.upload_and_install_apk(phone_id, apk, max_wait=180)

        if not ok:
            result["error"] = f"Failed to install {APK_NAME}"
            log.warning("[%s] %s", acc_id, result["error"])
            _stop_quietly(client, phone_id)
            return result
        log.info("[%s] %s installed.", acc_id, APK_NAME)
    else:
        log.info("[%s] %s already installed.", acc_id, APK_NAME)

    # ── 4. Launch the app ─────────────────────────────────────────────────
    log.info("[%s] Launching %s …", acc_id, PACKAGE)
    _shell(phone_id, f"am force-stop {PACKAGE}")
    time.sleep(1)
    _shell(phone_id,
           f"monkey -p {PACKAGE} -c android.intent.category.LAUNCHER 1")
    if not _wait_for_foreground_app(phone_id, PACKAGE, timeout=15, acc_id=acc_id):
        # Retry once
        _shell(phone_id,
               f"monkey -p {PACKAGE} -c android.intent.category.LAUNCHER 1")
        _wait_for_foreground_app(phone_id, PACKAGE, timeout=10, acc_id=acc_id)
    time.sleep(3)  # let the UI fully render

    # ── 5. Run integrity check (with retry on transient errors) ────────
    verdict: dict = {"basic_integrity": False, "device_integrity": False,
                     "strong_integrity": False, "parsed": False}

    for attempt in range(1, 4):  # up to 3 attempts (initial + 2 retries)
        if attempt > 1:
            backoff = 5 * (2 ** (attempt - 2))  # 5s, 10s
            log.info("[%s] Retry attempt %d/3 after %ds backoff …", acc_id, attempt, backoff)
            time.sleep(backoff)

        # (Re-)launch app on retry
        if attempt > 1:
            _shell(phone_id, f"am force-stop {PACKAGE}")
            time.sleep(1)
            _shell(phone_id,
                   f"monkey -p {PACKAGE} -c android.intent.category.LAUNCHER 1")
            _wait_for_foreground_app(phone_id, PACKAGE, timeout=12, acc_id=acc_id)
            time.sleep(3)

        # Tap CHECK button — always try on every attempt
        tapped = _find_and_tap(phone_id, [
            "CHECK", "Check Integrity", "CHECK INTEGRITY",
            "Run", "RUN", "Start", "START", "Play Integrity Check",
        ])
        if tapped:
            log.info("[%s] Tapped CHECK (attempt %d).", acc_id, attempt)
        else:
            log.info("[%s] No CHECK button found (attempt %d) — app may auto-run.", acc_id, attempt)

        # Poll for completion
        for interval in (2, 2, 3, 3, 5, 5, 5, 5):
            time.sleep(interval)
            ok, xml = _shell(phone_id,
                             "uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml")
            if not ok or not xml:
                continue

            verdict = _parse_verdict_from_xml(xml)
            if verdict["parsed"]:
                err = verdict.get("app_error", "")
                # CLIENT_TRANSIENT_ERROR (-17) → retry
                if "CLIENT_TRANSIENT_ERROR" in (err or "").upper() or "-17" in (err or ""):
                    log.warning("[%s] Transient error on attempt %d — will retry.", acc_id, attempt)
                    break  # exit poll loop, go to next retry attempt
                # Other result (success or non-retryable error) → done
                if err:
                    log.info("[%s] App returned error (attempt %d): %s", acc_id, attempt, err)
                else:
                    log.info("[%s] Verdict (attempt %d): basic=%s device=%s strong=%s",
                             acc_id, attempt,
                             verdict["basic_integrity"], verdict["device_integrity"],
                             verdict["strong_integrity"])
                break  # exit poll loop
        else:
            # Poll loop exhausted without result
            continue  # go to next retry attempt

        # If we got here with a non-transient result, stop retrying
        if verdict["parsed"] and "CLIENT_TRANSIENT_ERROR" not in (verdict.get("app_error") or "").upper():
            break

    result["integrity"] = {
        "basic_integrity":  verdict["basic_integrity"],
        "device_integrity": verdict["device_integrity"],
        "strong_integrity": verdict["strong_integrity"],
    }
    result["parsed"] = verdict["parsed"]
    result["tier"]  = _classify_tier(verdict)

    # Surface app-level errors (e.g. no Play Store account)
    if verdict.get("app_error"):
        result["error"] = verdict["app_error"]

    # ── 7. Screenshot as backup (always capture if parsing failed) ────────
    if not verdict["parsed"]:
        log.warning("[%s] Could not parse verdict from XML — capturing screenshot.", acc_id)
        try:
            from core.paths import LOGS_DIR
            from datetime import datetime
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            name = f"{acc_id}_integrity_{ts}.png"
            shot_path = LOGS_DIR / "screenshots" / name
            shot_path.parent.mkdir(parents=True, exist_ok=True)
            img = client.take_screenshot(phone_id, max_wait=15)
            if img:
                shot_path.write_bytes(img)
                result["screenshot_path"] = f"screenshots/{name}"
                log.info("[%s] Screenshot saved: %s", acc_id, name)
        except Exception as e:
            log.warning("[%s] Screenshot failed: %s", acc_id, e)

    # ── 8. Stop phone ─────────────────────────────────────────────────────
    _stop_quietly(client, phone_id)

    log.info("[%s] Integrity check complete: tier=%s", acc_id, result["tier"])
    return result


def _stop_quietly(client, phone_id: str) -> None:
    """Stop a phone, swallowing any error."""
    try:
        client.stop_phone(phone_id)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Batch audit — runs sequentially with IP dedup
# ---------------------------------------------------------------------------

def run_integrity_audit(accounts: list, progress_callback=None) -> list[dict]:
    """
    Run the Play Integrity check on every provisioned phone.

    Args:
        accounts: list of dicts from geelark_accounts.yaml
        progress_callback: optional callable(dict) for real-time updates

    Returns:
        list of per-phone result dicts (see check_play_integrity docstring).

    ABORTS if _rotate_proxy_ip() returns an IP that was already used for
    a previous phone in this run — duplicate IPs mean the accounts are
    linked at the proxy level and warming trust is destroyed.
    """
    from core.geelark_client import GeelarKClient

    client = GeelarKClient()
    apk_path = _get_apk_path()
    seen_ips: set[str] = set()
    results: list[dict] = []
    total = len(accounts)

    log.info("Starting Play Integrity audit for %d account(s).", total)

    for idx, acc in enumerate(accounts):
        acc_id   = acc.get("id", "unknown")
        phone_id = acc.get("geelark_phone_id")
        email    = acc.get("email", "")

        if progress_callback:
            progress_callback({
                "phase": "running",
                "current_account": acc_id,
                "progress": f"{idx + 1}/{total}",
                "results_so_far": len(results),
            })

        if not phone_id:
            log.info("[%s] No phone provisioned — skipping.", acc_id)
            results.append({
                "account_id": acc_id, "email": email, "phone_id": "",
                "device_brand": "", "device_model": "", "os_version": "",
                "proxy_ip": "", "integrity": {"basic_integrity": False,
                "device_integrity": False, "strong_integrity": False},
                "tier": "", "parsed": False,
                "error": "No phone provisioned", "screenshot_path": "",
            })
            continue

        # ── Health check ──────────────────────────────────────────────────
        health = client.check_phone_health(phone_id)
        if not health["healthy"]:
            log.warning("[%s] Phone unhealthy: %s — skipping.", acc_id, health["reason"])
            results.append({
                "account_id": acc_id, "email": email, "phone_id": phone_id,
                "device_brand": acc.get("device_brand", ""),
                "device_model": acc.get("device_model", ""),
                "os_version": acc.get("os_version", ""),
                "proxy_ip": "", "integrity": {"basic_integrity": False,
                "device_integrity": False, "strong_integrity": False},
                "tier": "", "parsed": False,
                "error": f"Phone unhealthy: {health['reason']}",
                "screenshot_path": "",
            })
            continue

        # ── Rotate proxy IP — ABORT on duplicate ──────────────────────────
        log.info("[%s] Rotating proxy IP …", acc_id)
        new_ip = _rotate_proxy_ip(acc_id)
        if new_ip:
            if new_ip in seen_ips:
                log.critical(
                    "DUPLICATE PROXY IP DETECTED: %s was used for account %s "
                    "and has now been assigned to %s. "
                    "Aborting audit — accounts would be linked.",
                    new_ip,
                    next(r["account_id"] for r in results if r["proxy_ip"] == new_ip),
                    acc_id,
                )
                # Tag the in-progress results with the abort marker
                results.append({
                    "account_id": acc_id, "email": email, "phone_id": phone_id,
                    "device_brand": acc.get("device_brand", ""),
                    "device_model": acc.get("device_model", ""),
                    "os_version": acc.get("os_version", ""),
                    "proxy_ip": new_ip,
                    "integrity": {"basic_integrity": False,
                    "device_integrity": False, "strong_integrity": False},
                    "tier": "", "parsed": False,
                    "error": f"ABORTED: duplicate proxy IP {new_ip}",
                    "screenshot_path": "",
                })
                if progress_callback:
                    progress_callback({
                        "phase": "aborted",
                        "reason": f"Duplicate proxy IP {new_ip} — audit aborted to "
                                  f"protect account isolation.",
                        "current_account": acc_id,
                        "progress": f"{idx + 1}/{total}",
                        "results_so_far": len(results),
                    })
                return results

            seen_ips.add(new_ip)
            log.info("[%s] Proxy IP: %s (%d unique IPs used so far)",
                     acc_id, new_ip, len(seen_ips))
        else:
            log.warning("[%s] Proxy rotation returned no IP — proceeding with current IP.", acc_id)
            new_ip = ""

        # ── Run the check ─────────────────────────────────────────────────
        row = check_play_integrity(
            phone_id=phone_id,
            acc_id=acc_id,
            email=email,
            apk_path=apk_path,
            client=client,
        )
        row["proxy_ip"] = new_ip
        results.append(row)

        # Brief pause between phones to let GeelarK API breathe
        if idx < total - 1:
            time.sleep(3)

    ok_count    = sum(1 for r in results if r.get("parsed") and not r.get("error"))
    skip_count  = sum(1 for r in results if r.get("error") and "No phone" in str(r.get("error", "")))
    fail_count  = len(results) - ok_count - skip_count

    log.info("Audit complete: %d OK, %d failed, %d skipped (no phone).",
             ok_count, fail_count, skip_count)

    if progress_callback:
        progress_callback({
            "phase": "complete",
            "progress": f"{total}/{total}",
            "summary": f"{ok_count} OK, {fail_count} failed, {skip_count} skipped",
            "results_so_far": len(results),
        })

    return results
