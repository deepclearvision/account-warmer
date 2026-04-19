"""
Claude API Analyser

For errors the rule-based engine doesn't recognise, this module sends the
error lines + account config to Claude and gets back a plain-English
explanation and a proposed fix.

Requires ANTHROPIC_API_KEY in warmer.env.
"""

import os
from pathlib import Path

# Ensure warmer.env is loaded (paths.py does this, but import it to be safe)
from core.paths import APP_DIR, STATE_DIR  # noqa: F401 — triggers env load


def _get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Add it to warmer.env:\n"
            "  ANTHROPIC_API_KEY=sk-ant-..."
        )
    return key


def analyse_unknown_error(fix: dict, account: dict) -> dict:
    """
    Send error lines to Claude and get back an explanation + proposed fix.
    Updates the fix dict in-place with proposed_fix and fix_action, then returns it.
    """
    try:
        import anthropic
    except ImportError:
        fix["proposed_fix"] = "anthropic package not installed — run: pip install anthropic"
        fix["confidence"]   = "error"
        return fix

    try:
        api_key = _get_api_key()
    except RuntimeError as e:
        fix["proposed_fix"] = str(e)
        fix["confidence"]   = "error"
        return fix

    error_text  = "\n".join(fix.get("error_lines", []))
    account_ctx = (
        f"Account ID: {account.get('id')}\n"
        f"Strategy: {account.get('strategy', 'standard')}\n"
        f"Location: {account.get('location', 'unknown')}\n"
        f"Week: {account.get('week', '?')}\n"
        f"Proxy set: {'yes' if account.get('proxy') else 'no'}\n"
        f"Multilogin profile ID: {account.get('multilogin_profile_id', 'unknown')}"
    )

    prompt = f"""You are analysing errors from an automated Google account warming script.
The script uses Playwright via Multilogin browser profiles to simulate human activity.

Account context:
{account_ctx}

Error lines from today's log:
{error_text}

Please respond with:
1. A one-sentence plain-English explanation of what went wrong (max 20 words).
2. A one-sentence description of the best fix (max 20 words).
3. Which fix action applies (one of: clear_proxy, stop_profile, force_token_refresh, flag_relogin, pause_account, manual_intervention_required).

Format your response exactly like this:
EXPLANATION: <explanation>
FIX: <fix description>
ACTION: <action>"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()

        explanation = ""
        fix_desc    = ""
        action_type = "manual_intervention_required"

        for line in text.splitlines():
            if line.startswith("EXPLANATION:"):
                explanation = line.split(":", 1)[1].strip()
            elif line.startswith("FIX:"):
                fix_desc = line.split(":", 1)[1].strip()
            elif line.startswith("ACTION:"):
                action_type = line.split(":", 1)[1].strip()

        fix["proposed_fix"] = f"{explanation} — {fix_desc}" if explanation else fix_desc
        fix["fix_action"]   = {
            "type":       action_type,
            "account_id": fix["account_id"],
            "profile_id": fix.get("profile_id", ""),
        }
        fix["confidence"]   = "ai"
        fix["ai_raw"]       = text

    except Exception as e:
        fix["proposed_fix"] = f"Claude API error: {e}"
        fix["confidence"]   = "error"

    return fix


def enrich_unknown_fixes(accounts: list[dict]) -> int:
    """
    Find all pending fixes with confidence='pending_ai', call Claude for each,
    and update them in the fixes file.
    Returns the number of fixes enriched.
    """
    from core.error_analyser import _load_fixes, _save_fixes

    fixes      = _load_fixes()
    acc_map    = {a["id"]: a for a in accounts}
    enriched   = 0

    for fix in fixes:
        if fix.get("status") != "pending":
            continue
        if fix.get("confidence") != "pending_ai":
            continue

        account = acc_map.get(fix["account_id"], {})
        analyse_unknown_error(fix, account)
        enriched += 1

    if enriched:
        _save_fixes(fixes)

    return enriched
