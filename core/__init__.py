"""
Core modules for Account Warmer.

── Public API (safe to import from either desktop or mobile code) ──────────
   from core.account_store import AccountStore, get_account_store
   from core.paths import DATA_DIR, LOGS_DIR, STATE_DIR, ...
   from core.logger import get_logger
   from core.activity_log import log_event

── Desktop-only (do NOT import from mobile code) ───────────────────────────
   from core.profile_manager import ProfileSession
   from core.orchestrator import AccountOrchestrator
   from core.humaniser import Humaniser
   from core.multilogin_auth import auth_headers, get_token
   from core.scheduler import get_scheduler
   from core.proxy_guard import check_account_proxy

── Mobile-only (do NOT import from desktop code) ───────────────────────────
   from core.geelark_client import GeelarKClient
   from core.geelark_flow_builder import GeelarkFlowBuilder
   from core.gps_spoofing import ...
"""
