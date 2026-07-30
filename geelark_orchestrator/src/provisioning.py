"""
provisioning.py — Pre-run fitness gate: a phone must be provisioned AND maps-verified.

Section 9.10 of the Master Reference:
  - provisioned        : dumpsys verified — Fake GPS is the active mock provider.
  - maps_verified        : BEHAVIOUR-based — Maps opens to a usable search screen with
                           NO Play Store / update interstitial intercepting.
                           NOT just "package installed".
  - proxy_routing_confirmed (runtime, run_one_phone.py) : outbound IP via the APP path
                           matches the assigned StreamVia proxy IP. Mandatory because
                           proxy routing is model-dependent (single-interface phones
                           tunnel correctly; multi-interface phones leak). Checked per
                           run with Chrome -> checkip.amazonaws.com before any interaction.

The orchestrator MUST refuse to dispatch a normal run to a phone that fails
any check. Abort loud, name the phone, name the failed check.
"""

from __future__ import annotations

from data_layer import DataError, Profile


class ProvisioningError(DataError):
    """Raised when a phone is not fit to run (unprovisioned or maps unverified)."""


def check_phone_fit(profile: Profile) -> tuple[bool, str]:
    """
    Return (True, "") if the phone is fit to run.
    Return (False, reason) if not, with a plain-language reason naming the
    profile and the failed check.
    """
    if not profile.provisioned:
        return (
            False,
            f"Phone {profile.profile_key} not provisioned — "
            f"Fake GPS mock provider not verified via dumpsys (Section 9.10)."
        )
    if not profile.maps_verified:
        return (
            False,
            f"Phone {profile.profile_key} maps not verified — "
            f"Maps must open to a usable search screen with NO Play Store / "
            f"update interstitial (Section 9.10). Package-installed is insufficient."
        )
    return True, ""


def assert_phone_fit(profile: Profile) -> None:
    """Convenience wrapper: raise ProvisioningError if the phone is unfit."""
    fit, reason = check_phone_fit(profile)
    if not fit:
        raise ProvisioningError(reason)
