from __future__ import annotations

# VC DTL scraper config: benchmark eligibility and crawl limits.
VC_DTL_CONFIG = {
    "include_adapted_recipes": False,
    "thresholds": {
        "hard_min_total_flavour_pct": 3.0,
        "hard_max_total_flavour_pct": 30.0,
        "preferred_clean_range": (6.0, 20.0),
    },
    "crawl": {
        "delay_s": 0.5,
        "max_pages": 20,
        "max_recipes": 300,
        # Keep admin requests responsive while preserving a usable detail parsing window.
        "max_runtime_seconds": 90,
        "discovery_runtime_budget_seconds": 75,
        "detail_runtime_budget_seconds": 55,
        "min_detail_attempts_before_runtime_stop": 10,
        # Admin debug override: disable runtime stop guards.
        "vc_dtl_disable_runtime_guard": False,
        # Admin diagnostics: queue already-completed IDs and bypass completed filter.
        "vc_dtl_ignore_completed_filter": False,
        # Placeholder for future persisted cursor; currently forces a fresh discovery pass.
        "vc_dtl_reset_discovery_cursor": False,
        "seed_scan_radius": 15000,
        "seed_scan_request_budget": 600,
        "seed_scan_timeout_seconds": 6,
        "id_scan_end_hint_offset": 20000,
        "id_scan": {
            "enabled": True,
            "start_id": 1,
            # Keep fallback scan in a historically active range to reduce private-page churn.
            "end_id": 70000,
            "request_budget": 400,
            "max_consecutive_misses": 120,
            "timeout_seconds": 8,
        },
    },
}
