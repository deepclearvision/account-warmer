"""
geelark_client.py — Thin wrapper around the Geelark OpenAPI.

================================  HONESTY NOTICE  ============================
The functions in this file that TOUCH THE NETWORK are SCAFFOLDS, not verified
working code. They are marked `# UNVERIFIED`. They encode the *shape* of what
the orchestrator needs, but the exact endpoint paths, auth signing, and request
bodies MUST be confirmed against the official docs before trusting them:

    - API docs:    https://open.geelark.com
    - OpenAPI repo: github.com/GeeLark/geelark-openapi

The single most important unknown (see plan §3 Q1):
    >>> Can a triggered RPA task accept input parameters at launch? <<<
    - If YES -> Path 1: pass resolved values in `params` (startParamMap).
    - If NO  -> Path 2: clone the flow JSON, substitute values, upload, trigger.

`build_task_payload()` below is written for Path 1. If it turns out to be
Path 2, only `flow_adapter.py` (not this file) should need to change — that's
why the RunPlan->payload step is isolated.

Everything in resolver.py / data_layer.py is REAL and TESTED and needs none of
this. You can debug the entire decision layer offline today. This file is only
needed once you want to drive an actual cloud phone.
=============================================================================
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Optional

# `requests` is the usual choice; left as an import the Code session will wire up.
# import requests


class GeelarkClient:
    def __init__(self, base_url: str, api_key: str, api_secret: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret

    # ---- auth -------------------------------------------------------------
    def _headers(self) -> dict:
        # UNVERIFIED: Geelark uses an API key + signature scheme. Confirm the
        # exact header names and signing algorithm in the official docs.
        return {
            "Content-Type": "application/json",
            "X-API-KEY": self.api_key,
            # "X-SIGN": _sign(...),  # confirm signing
        }

    # ---- cloud phone lifecycle -------------------------------------------
    def start_phone(self, geelark_profile_id: str) -> dict:
        """UNVERIFIED. Start the cloud phone; it must be running before a task.
        Remember ADB enable is async (~3s) on some setups."""
        raise NotImplementedError("Confirm endpoint at open.geelark.com, then implement.")

    def stop_phone(self, geelark_profile_id: str) -> dict:
        """UNVERIFIED."""
        raise NotImplementedError

    # ---- RPA task trigger -------------------------------------------------
    def trigger_task(self, geelark_profile_id: str, flow_id: str, params: dict) -> str:
        """
        UNVERIFIED. Trigger an RPA flow on a phone, returning a task_id.
        `params` is the resolved scalar values from a RunPlan (Path 1).
        Confirm: endpoint path, body shape, how flow is referenced, whether
        `params`/startParamMap is even accepted (plan §3 Q1).
        """
        raise NotImplementedError("Confirm task-trigger endpoint + param support first.")

    def get_task_result(self, task_id: str) -> dict:
        """UNVERIFIED. Poll task status; on completion fetch log + (on failure)
        the failing step and screenshot URL."""
        raise NotImplementedError


def build_task_payload(run_plan) -> dict:
    """
    REAL (pure) — turn a RunPlan into the flat scalar map a flow would consume.
    This is deliberately separate from the network code so that whether the
    values travel via Path 1 (params) or Path 2 (template substitution), the
    mapping from plan -> named values lives in exactly one place.

    These keys are the variables your RPA flow expects (matching the Master
    Reference data variables, e.g. ${business_name}, ${random_search_term}).
    """
    if run_plan.abort:
        raise ValueError(f"Refusing to build payload for aborted plan: {run_plan.abort_reason}")
    return {
        "business_name": run_plan.business_name,
        "business_lat": run_plan.gps_lat,     # the resolved point to spoof
        "business_lng": run_plan.gps_lng,
        "random_search_term": run_plan.search_term,
        "random_branded_search": run_plan.branded_term or "",
        "business_goal": run_plan.business_goal,
        # carried for traceability so the device log can echo the run_id:
        "run_id": run_plan.run_id,
    }
