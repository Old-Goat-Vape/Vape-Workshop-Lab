"""Guided mix session persistence and reads (community-facing; backed by mix_sessions tables)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from flask import g

from db import SqliteRetryStats, get_db, log_sqlite_lock_failure, sqlite_retry
from utils.guided_mix_perf import (
    get_guided_mix_request_id,
    guided_mix_perf_span,
    json_byte_size,
    log_guided_mix_stage,
)
from core.calculator import (
    ZERO_POUR_GRAMS_EPS,
    build_live_coach_summary,
    build_preemptive_coaching_for_step,
    build_runtime_narrative_step,
    evaluate_guided_step_progression,
    ingredient_over_requires_recovery,
    ingredient_pour_tolerance_grams,
    normalize_ingredient_pour_category,
    scaled_pour_tolerance_grams,
    tvw_mix_policy,
)
from models import recipes_repo
from services import tvw_mix_service


def _default_performance_summary() -> dict[str, int]:
    return {
        "overshoot_count": 0,
        "undershoot_count": 0,
        "recovery_count": 0,
        "near_miss_count": 0,
        "precise_pour_count": 0,
    }


def _display_target_grams(target_grams: float) -> float:
    """Bench display rounds to 0.01 g — tolerance decisions use this, not hidden precision."""
    return round(float(target_grams or 0.0), 2)


def _complete_linked_overpour_pour_step(
    db,
    *,
    session_id: int,
    after_recovery_step_no: int,
) -> bool:
    """After a serious-overpour recovery step, complete the deferred pour step (next in flow)."""
    rows = db.execute(
        """
        SELECT id, step_no, step_type, payload_json
        FROM mix_session_steps
        WHERE mix_session_id = ? AND completed_at IS NULL AND step_no > ?
        ORDER BY step_no ASC
        """,
        (int(session_id), int(after_recovery_step_no)),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"]) if isinstance(row["payload_json"], str) else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}
        row_type = str(row["step_type"] or payload.get("step_type") or "").lower()
        if row_type != "add_ingredient":
            continue
        if not bool(payload.get("overpour_recovery_pending")):
            continue
        payload.pop("overpour_recovery_pending", None)
        payload["overpour_recovery_resolved"] = True
        _stamp_pour_pending_bottle_move(payload)
        db.execute(
            """
            UPDATE mix_session_steps
            SET payload_json = ?, completed_at = datetime('now')
            WHERE id = ? AND mix_session_id = ?
            """,
            (json.dumps(payload), int(row["id"]), int(session_id)),
        )
        return True
    return False


POUR_STATE_ACTIVE = "add_ingredient"
POUR_STATE_PENDING_BOTTLE_MOVE = "pour_completed_pending_bottle_move"
POUR_STATE_PHYSICALLY_LEFT = "bottle_moved"


def _stamp_pour_pending_bottle_move(payload: dict[str, Any]) -> None:
    payload["pour_state"] = POUR_STATE_PENDING_BOTTLE_MOVE
    payload["bottle_moved"] = False
    payload.pop("bottle_moved_at", None)


def _stamp_bottle_physically_moved(payload: dict[str, Any]) -> None:
    payload["pour_state"] = POUR_STATE_PHYSICALLY_LEFT
    payload["bottle_moved"] = True


def _linked_pour_step_row_for_transition(db, session_id: int, transition_step_no: int):
    rows = db.execute(
        """
        SELECT id, step_no, step_type, payload_json, completed_at
        FROM mix_session_steps
        WHERE mix_session_id = ? AND step_no < ?
        ORDER BY step_no DESC
        LIMIT 8
        """,
        (int(session_id), int(transition_step_no)),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"]) if isinstance(row["payload_json"], str) else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}
        row_type = str(row["step_type"] or payload.get("step_type") or "").lower()
        if row_type == "add_ingredient":
            return row
    return None


def _confirm_bottle_moved_for_transition(
    db,
    *,
    session_id: int,
    transition_step_no: int,
    transition_payload: dict[str, Any] | None,
) -> bool:
    """Mark the pour step before this transition as physically moved left."""
    pour_row = _linked_pour_step_row_for_transition(db, int(session_id), int(transition_step_no))
    if not pour_row:
        return False
    try:
        pour_payload = (
            json.loads(pour_row["payload_json"]) if isinstance(pour_row["payload_json"], str) else {}
        )
    except (json.JSONDecodeError, TypeError):
        pour_payload = {}
    if not isinstance(pour_payload, dict):
        pour_payload = {}
    _stamp_bottle_physically_moved(pour_payload)
    db.execute(
        """
        UPDATE mix_session_steps
        SET payload_json = ?
        WHERE id = ? AND mix_session_id = ?
        """,
        (json.dumps(pour_payload), int(pour_row["id"]), int(session_id)),
    )
    tp = transition_payload if isinstance(transition_payload, dict) else {}
    display_name = str(
        pour_payload.get("name")
        or pour_payload.get("ingredient_name")
        or tp.get("name")
        or tp.get("ingredient_name")
        or ""
    ).strip()
    _append_mix_event_log(
        db,
        int(session_id),
        {
            "event": "bottle_moved",
            "ingredient": display_name,
            "ingredient_display_name": display_name,
            "step_id": int(pour_row["id"]),
            "step_no": int(pour_row["step_no"]),
            "timestamp": datetime_now_iso(),
        },
    )
    return True


def _undo_bottle_moved_for_transition(db, *, session_id: int, transition_step_no: int) -> None:
    pour_row = _linked_pour_step_row_for_transition(db, int(session_id), int(transition_step_no))
    if not pour_row:
        return
    try:
        pour_payload = (
            json.loads(pour_row["payload_json"]) if isinstance(pour_row["payload_json"], str) else {}
        )
    except (json.JSONDecodeError, TypeError):
        pour_payload = {}
    if not isinstance(pour_payload, dict):
        return
    if pour_row["completed_at"] is not None:
        _stamp_pour_pending_bottle_move(pour_payload)
        db.execute(
            """
            UPDATE mix_session_steps
            SET payload_json = ?
            WHERE id = ? AND mix_session_id = ?
            """,
            (json.dumps(pour_payload), int(pour_row["id"]), int(session_id)),
        )


def datetime_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _density_from_step_payload(step_payload: dict[str, Any]) -> float | None:
    """Recover g/ml density from step payload when present (no calculator change)."""
    try:
        dens = float(step_payload.get("density") or 0.0)
    except (TypeError, ValueError):
        dens = 0.0
    if dens > 0:
        return dens
    try:
        grams = float(step_payload.get("target_grams") or step_payload.get("grams") or 0.0)
        ml = float(step_payload.get("ml") or step_payload.get("target_ml") or 0.0)
    except (TypeError, ValueError):
        return None
    if grams > 0 and ml > 0:
        return grams / ml
    return None


def _classify_under_pour(*, display_target: float, measured: float) -> tuple[str, str]:
    """Return (classification, user message). Under-pours never hard-block the step.

    Message format: \"title\\nbody\" so the bench UI can render a two-line note.
    """
    # Zero-requirement pours must never prompt underpour confirmation.
    if float(display_target) <= ZERO_POUR_GRAMS_EPS:
        return "within_tolerance", ""
    under_by = float(display_target) - float(measured)
    if under_by <= 0.0005:
        return "within_tolerance", ""
    # Tiny flavour targets: ≤0.02 g under is normal bench behaviour.
    if under_by <= 0.02:
        return (
            "slight_under_target",
            (
                f"Slightly under by {under_by:.2f} g\n"
                "Close enough for this pour. One more drop may put you over target."
            ),
        )
    return (
        "large_under_target",
        (
            f"Still under by {under_by:.2f} g\n"
            "This pour does not look finished yet."
        ),
    )


def _is_large_under_classification(classification: str) -> bool:
    return str(classification or "").strip().lower() in {
        "large_under_target",
        "under_target_caution",  # legacy sessions
    }


def _sanitize_open_pour_payload_for_client(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip transient pour readings from open steps that are not in under-pour recovery."""
    if not isinstance(payload, dict):
        return {}
    if str(payload.get("step_type") or "").lower() != "add_ingredient":
        return payload
    if bool(payload.get("under_decision_pending")):
        return payload
    if bool(payload.get("nicotine_overpour_pending")) or bool(payload.get("nicotine_recovery_mode")):
        return payload
    if bool(payload.get("nicotine_over_decision_pending")) or bool(payload.get("nicotine_accept_confirm_pending")):
        return payload
    cleaned = dict(payload)
    for key in (
        "measured_grams",
        "display_measured_grams",
        "measured_ml",
        "actual_ml",
        "delta_grams",
        "partial_grams",
        "under_pour_mode",
        "under_classification",
        "needed_grams",
        "remaining_grams",
    ):
        cleaned.pop(key, None)
    return cleaned


def _classify_slight_over_pour(*, display_target: float, measured: float, base_adjusted: bool) -> tuple[str, str]:
    """Controlled over-target note (not recovery). Format: \"title\\nbody\"."""
    over_by = float(measured) - float(display_target)
    if over_by <= 0.0005:
        return "within_tolerance", ""
    body = (
        "Small overpour detected. PG was adjusted to protect final batch volume and ratio."
        if base_adjusted
        else "Small overpour detected. Continue, but keep the next pours controlled."
    )
    return (
        "slight_over_target",
        f"Slightly over by {over_by:.2f} g\n{body}",
    )


def _is_nicotine_payload(payload: dict[str, Any]) -> bool:
    cat = str(payload.get("category") or "").strip().lower()
    if cat == "nicotine":
        return True
    name = str(payload.get("name") or payload.get("ingredient_name") or "").lower()
    return "nicotine" in name


NICOTINE_STRICTNESS_PERSONAL = "personal"
NICOTINE_STRICTNESS_LAB_CLIENT = "lab_client"


def normalize_nicotine_strictness(mode: str | None) -> str:
    raw = str(mode or NICOTINE_STRICTNESS_PERSONAL).strip().lower().replace("-", "_")
    if raw in {"lab", "lab_client", "client", "labclient"}:
        return NICOTINE_STRICTNESS_LAB_CLIENT
    return NICOTINE_STRICTNESS_PERSONAL


def _session_nicotine_strictness(db, session_id: int) -> str:
    meta = _read_session_meta(db, int(session_id))
    return normalize_nicotine_strictness(meta.get("nicotine_strictness_mode"))


def evaluate_nicotine_overpour_policy(*, strictness: str, over_by: float) -> dict[str, Any]:
    """Return tier + whether accept/fix/recovery paths are allowed for nicotine over."""
    over = max(0.0, float(over_by or 0.0))
    mode = normalize_nicotine_strictness(strictness)
    if mode == NICOTINE_STRICTNESS_LAB_CLIENT:
        if over <= 0.05:
            return {
                "tier": "minor",
                "accept_allowed": True,
                "recovery_required": False,
                "strong_recommend_recovery": False,
                "warning": (
                    f"Nicotine over by {over:.2f} g\n"
                    "Client/lab mode: only slight overages may be accepted with confirmation."
                ),
            }
        return {
            "tier": "severe",
            "accept_allowed": False,
            "recovery_required": True,
            "strong_recommend_recovery": True,
            "warning": "Client/lab mode: nicotine over tolerance must be corrected.",
        }
    if over <= 0.05:
        return {
            "tier": "minor",
            "accept_allowed": True,
            "recovery_required": False,
            "strong_recommend_recovery": False,
            "warning": (
                f"Nicotine over by {over:.2f} g\n"
                "Personal mix: slight over may be acceptable by judgement, or you can fix the dose."
            ),
        }
    if over < 0.10:
        return {
            "tier": "moderate",
            "accept_allowed": True,
            "recovery_required": False,
            "strong_recommend_recovery": True,
            "warning": (
                f"Nicotine over by {over:.2f} g\n"
                "Correction strongly recommended. Accept only if you deliberately confirm."
            ),
        }
    return {
        "tier": "severe",
        "accept_allowed": False,
        "recovery_required": True,
        "strong_recommend_recovery": True,
        "warning": (
            f"Nicotine over by {over:.2f} g\n"
            "Correction required before continuing."
        ),
    }


def _log_nicotine_over_decision(
    db,
    session_id: int,
    *,
    strictness: str,
    target_grams: float,
    actual_grams: float,
    over_by_grams: float,
    action: str,
    warning: str,
    accept_allowed: bool,
) -> None:
    _append_nicotine_recovery_log(
        db,
        int(session_id),
        {
            "event": "nicotine_over_decision",
            "mode": normalize_nicotine_strictness(strictness),
            "target_grams": float(target_grams),
            "actual_grams": float(actual_grams),
            "over_by_grams": float(over_by_grams),
            "action": str(action),
            "warning": str(warning),
            "accept_allowed_by_policy": bool(accept_allowed),
        },
    )


def _classify_nicotine_under_pour(*, display_target: float, measured: float) -> tuple[str, str]:
    """Nicotine under-pour copy stresses final strength impact."""
    if float(display_target) <= ZERO_POUR_GRAMS_EPS:
        return "within_tolerance", ""
    under_by = float(display_target) - float(measured)
    if under_by <= 0.0005:
        return "within_tolerance", ""
    if under_by <= 0.02:
        return (
            "slight_under_target",
            (
                f"Nicotine under by {under_by:.2f} g\n"
                "Final nicotine strength will be lower than planned. "
                "Add more carefully or accept the lower dose."
            ),
        )
    return (
        "large_under_target",
        (
            f"Still under nicotine target by {under_by:.2f} g\n"
            "Final batch strength will be affected. Continue pouring, or accept a lower nicotine dose."
        ),
    )


def _classify_under_pour_for_step(
    *,
    payload: dict[str, Any],
    display_target: float,
    measured: float,
    batch_size_ml: float = 0.0,
    policy=None,
) -> tuple[str, str]:
    if float(display_target) <= ZERO_POUR_GRAMS_EPS:
        return "within_tolerance", ""
    if _is_nicotine_payload(payload):
        return _classify_nicotine_under_pour(display_target=display_target, measured=measured)
    if policy is None:
        policy = tvw_mix_policy()
    kind = _ingredient_kind(payload)
    under_by = float(display_target) - float(measured)
    if under_by <= 0.0005:
        return "within_tolerance", ""
    tol = ingredient_pour_tolerance_grams(
        ingredient_category=kind,
        target_grams=display_target,
        batch_size_ml=batch_size_ml,
        policy=policy,
        direction="under",
    )
    if kind in {"pg", "vg"} and under_by <= tol:
        return (
            "within_tolerance",
            (
                f"Slightly under by {under_by:.2f} g\n"
                "Carrier pour is flexible. Continue toward the target for this bottle size."
            ),
        )
    if kind in {"pg", "vg"}:
        return (
            "slight_under_target",
            (
                f"Slightly under by {under_by:.2f} g\n"
                "Carrier pour is flexible. Add a little more if you want to hit the estimate."
            ),
        )
    return _classify_under_pour(display_target=display_target, measured=measured)


def _classify_over_pour_for_step(
    *,
    payload: dict[str, Any],
    display_target: float,
    measured: float,
    batch_size_ml: float,
    policy,
    base_adjusted: bool = False,
) -> tuple[str, str]:
    if _is_nicotine_payload(payload):
        return "over_target", ""
    over_by = float(measured) - float(display_target)
    if over_by <= 0.0005:
        return "within_tolerance", ""
    kind = _ingredient_kind(payload)
    tol = ingredient_pour_tolerance_grams(
        ingredient_category=kind,
        target_grams=display_target,
        batch_size_ml=batch_size_ml,
        policy=policy,
        direction="over",
    )
    if kind in {"pg", "vg"} and over_by <= tol:
        return (
            "within_tolerance",
            (
                f"Slightly over by {over_by:.2f} g\n"
                "Carrier pour is within flexible range for this bottle size. Continue."
            ),
        )
    if kind == "vg":
        return (
            "slight_over_target",
            (
                f"Slightly over by {over_by:.2f} g\n"
                "VG final fill: focus on reaching the bottle size. Small differences are expected."
            ),
        )
    if kind == "pg":
        return (
            "slight_over_target",
            (
                f"Slightly over by {over_by:.2f} g\n"
                "PG carrier is flexible. Continue and watch the final bottle level."
            ),
        )
    return _classify_slight_over_pour(
        display_target=display_target,
        measured=measured,
        base_adjusted=base_adjusted,
    )


def _append_nicotine_recovery_log(db, session_id: int, entry: dict[str, Any]) -> None:
    meta = _read_session_meta(db, int(session_id))
    log = meta.get("nicotine_recovery_log")
    if not isinstance(log, list):
        log = []
    log.append(dict(entry))
    meta["nicotine_recovery_log"] = log
    if str(entry.get("event") or "") == "nicotine_recovery_complete":
        meta["nicotine_recovery_warning"] = "Nicotine recovery used. Scale was not tared."
    _write_session_meta(db, int(session_id), meta)


def _nicotine_recovery_response(
    *,
    policy,
    session_id: int,
    inserted_step_nos: list[int],
    reading_eval: dict[str, Any],
    message: str,
    decision: str,
    warnings: list[str] | None = None,
    awaiting_under: bool = False,
    awaiting_over_decision: bool = False,
) -> dict[str, Any]:
    db = get_db()
    return {
        "ok": True,
        "message": message,
        "awaiting_nicotine_recovery": True,
        "nicotine_recovery_active": True,
        "awaiting_nicotine_over_decision": awaiting_over_decision,
        "awaiting_under_decision": awaiting_under,
        "under_pour_active": awaiting_under,
        "inserted_step_nos": inserted_step_nos,
        "performance_summary": _session_performance_summary(db, int(session_id)),
        "reading": reading_eval,
        "policy": {
            "name": policy.name,
            "allowed": True,
            "decision": decision,
            "warnings": list(warnings or []),
            "hard_stops": [],
        },
    }


def _is_base_carrier_payload(payload: dict[str, Any]) -> bool:
    cat = str(payload.get("category") or "").strip().lower()
    if cat in {"pg", "vg", "base", "base_liquid"}:
        return True
    name = str(payload.get("name") or payload.get("ingredient_name") or "").lower()
    if "nicotine" in name or cat == "nicotine":
        return False
    return any(tok in name for tok in (" pg", "pg ", " vg", "vg ", "propylene", "glycerin", "glycerine", "base"))


def _is_pg_payload(payload: dict[str, Any]) -> bool:
    cat = str(payload.get("category") or "").strip().lower()
    if cat == "pg":
        return True
    name = str(payload.get("name") or payload.get("ingredient_name") or "").lower()
    return name.strip() == "pg" or name.startswith("pg ") or " propylene" in name


def _is_vg_payload(payload: dict[str, Any]) -> bool:
    cat = str(payload.get("category") or "").strip().lower()
    if cat == "vg":
        return True
    name = str(payload.get("name") or payload.get("ingredient_name") or "").lower()
    return name.strip() == "vg" or name.startswith("vg ") or "glycerin" in name or "glycerine" in name


def _ingredient_kind(payload: dict[str, Any]) -> str:
    return normalize_ingredient_pour_category(
        category=str(payload.get("category") or ""),
        name=str(payload.get("name") or payload.get("ingredient_name") or ""),
    )


def _parse_target_pgvg_ratio(target_pgvg: str | None) -> tuple[float, float]:
    raw = str(target_pgvg or "30/70").strip()
    parts = raw.split("/")
    try:
        pg = float(parts[0]) if parts else 30.0
    except (TypeError, ValueError):
        pg = 30.0
    try:
        vg = float(parts[1]) if len(parts) > 1 else 70.0
    except (TypeError, ValueError):
        vg = 70.0
    if pg + vg <= 0:
        return 30.0, 70.0
    return pg, vg


def _session_batch_context(
    db,
    session_id: int,
    *,
    session_row: Any | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, float]:
    row = session_row
    if row is None:
        row = db.execute(
            "SELECT target_ml, target_pgvg, result_json FROM mix_sessions WHERE id = ?",
            (int(session_id),),
        ).fetchone()
    batch_size_ml = float((row["target_ml"] if row else 0) or 0.0)
    pg_pct, vg_pct = _parse_target_pgvg_ratio(row["target_pgvg"] if row else None)
    parsed_meta = meta
    if parsed_meta is None and row is not None:
        raw = row["result_json"] if "result_json" in row.keys() else None
        if raw:
            try:
                loaded = json.loads(raw) if isinstance(raw, str) else raw
                parsed_meta = loaded if isinstance(loaded, dict) else None
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed_meta = None
    if isinstance(parsed_meta, dict):
        batch_size_ml = float(parsed_meta.get("batch_size_ml") or parsed_meta.get("vessel_size_ml") or batch_size_ml)
        if parsed_meta.get("target_pg_percent") is not None:
            pg_pct = float(parsed_meta.get("target_pg_percent"))
        if parsed_meta.get("target_vg_percent") is not None:
            vg_pct = float(parsed_meta.get("target_vg_percent"))
    return {
        "batch_size_ml": batch_size_ml,
        "target_pg_percent": pg_pct,
        "target_vg_percent": vg_pct,
    }


def _append_mix_event_log(db, session_id: int, entry: dict[str, Any]) -> None:
    meta = _read_session_meta(db, int(session_id))
    log = meta.get("mix_event_log")
    if not isinstance(log, list):
        log = []
    log.append(dict(entry))
    meta["mix_event_log"] = log
    _write_session_meta(db, int(session_id), meta)


def _pour_carrier_for_ratio(payload: dict[str, Any], *, default_nic_carrier: str = "PG") -> str | None:
    """Return PG/VG carrier for a completed pour, or None if not a carrier contribution."""
    cat = str(payload.get("category") or "").strip().lower()
    explicit = str(payload.get("carrier") or "").strip().upper()
    if explicit in {"PG", "VG"}:
        return explicit
    if cat == "pg" or _is_pg_payload(payload):
        return "PG"
    if cat == "vg" or _is_vg_payload(payload):
        return "VG"
    if cat == "nicotine" or _is_nicotine_payload(payload):
        nic = str(default_nic_carrier or "PG").strip().upper()
        return "VG" if nic == "VG" else "PG"
    if cat in {"concentrate", "flavor", "flavour"}:
        # Legacy steps without carrier: PG-based concentrates (industry default).
        return "PG"
    return None


def _estimate_final_ratio_from_steps(
  db,
  session_id: int,
  *,
  target_pg_percent: float,
  target_vg_percent: float,
  nicotine_carrier: str = "PG",
) -> tuple[float, float]:
    """Finished-liquid PG/VG estimate from completed pours (bases + flavour + nicotine carriers)."""
    rows = db.execute(
        """
        SELECT payload_json
        FROM mix_session_steps
        WHERE mix_session_id = ? AND completed_at IS NOT NULL
        ORDER BY step_no ASC
        """,
        (int(session_id),),
    ).fetchall()
    pg_ml = 0.0
    vg_ml = 0.0
    for row in rows:
        try:
            payload = json.loads(row["payload_json"]) if isinstance(row["payload_json"], str) else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if str(payload.get("step_type") or "").lower() != "add_ingredient":
            continue
        try:
            ml = float(payload.get("measured_ml") or payload.get("ml") or payload.get("target_ml") or 0.0)
            if payload.get("measured_grams") and payload.get("density"):
                ml = float(payload["measured_grams"]) / float(payload["density"])
        except (TypeError, ValueError):
            ml = 0.0
        carrier = _pour_carrier_for_ratio(payload, default_nic_carrier=nicotine_carrier)
        if carrier == "PG":
            pg_ml += ml
        elif carrier == "VG":
            vg_ml += ml
    total = pg_ml + vg_ml
    if total <= 0:
        return float(target_pg_percent), float(target_vg_percent)
    est_pg = round((pg_ml / total) * 100.0)
    est_vg = round((vg_ml / total) * 100.0)
    return est_pg, est_vg


def _short_ingredient_label(name: str) -> str:
    """Strip concentrate suffixes for batch-report readability."""
    label = str(name or "").strip()
    for suffix in (" Concentrate", " concentrate", " Flavor", " Flavour", " flavor", " flavour"):
        if label.endswith(suffix):
            label = label[: -len(suffix)].strip()
    return label or "Ingredient"


def _format_pg_adjustment_row(entry: dict[str, Any]) -> dict[str, Any]:
    ingredient = _short_ingredient_label(str(entry.get("source_ingredient") or ""))
    over_by = float(entry.get("excess_grams") or 0.0)
    cut_ml = float(entry.get("cut_ml") or 0.0)
    return {
        "ingredient": ingredient,
        "over_by_grams": round(over_by, 2),
        "pg_reduced_ml": round(cut_ml, 2),
        "label": f"{ingredient}: over by {over_by:.2f} g → PG reduced by {cut_ml:.2f} ml",
    }


def _format_accepted_deviation_row(entry: dict[str, Any]) -> dict[str, Any] | None:
    event = str(entry.get("event") or "").strip().lower()
    ingredient = _short_ingredient_label(str(entry.get("ingredient") or entry.get("source_ingredient") or ""))
    if event == "accepted_flavour_over":
        return {
            "kind": "flavour_over",
            "ingredient": ingredient,
            "label": f"{ingredient} accepted over target",
        }
    if event == "accepted_carrier_under":
        name_upper = ingredient.upper()
        if "VG" in name_upper or "GLYCERIN" in name_upper or name_upper == "VG":
            return {
                "kind": "vg_final_fill",
                "ingredient": ingredient,
                "label": "VG final fill accepted within tolerance",
            }
        return {
            "kind": "carrier_under",
            "ingredient": ingredient,
            "label": f"{ingredient} accepted under target within tolerance",
        }
    if event == "accepted_carrier_over":
        return {
            "kind": "carrier_over",
            "ingredient": ingredient,
            "label": f"{ingredient} accepted over target within tolerance",
        }
    return None


def _format_nicotine_event_row(entry: dict[str, Any]) -> dict[str, Any] | None:
    action = str(entry.get("action") or entry.get("event") or "").strip().lower()
    if action == "accepted_nicotine_over":
        over_by = float(entry.get("over_by_grams") or 0.0)
        return {
            "kind": "nicotine_over",
            "label": f"Nicotine accepted {over_by:.2f} g over target",
        }
    if action == "accepted_nicotine_under":
        under_by = float(entry.get("under_by_grams") or 0.0)
        return {
            "kind": "nicotine_under",
            "label": f"Nicotine accepted {under_by:.2f} g under target",
        }
    if action == "nicotine_recovery_complete":
        return {
            "kind": "nicotine_recovery",
            "label": "Nicotine dose confirmed after pour-back recovery",
        }
    return None


def _infer_nicotine_deviation_rows(db, session_id: int) -> list[dict[str, Any]]:
    """Fill nicotine deviation notes from completed pour payloads when not logged explicitly."""
    rows: list[dict[str, Any]] = []
    db_rows = db.execute(
        """
        SELECT payload_json FROM mix_session_steps
        WHERE mix_session_id = ? AND completed_at IS NOT NULL
        ORDER BY step_no ASC
        """,
        (int(session_id),),
    ).fetchall()
    for row in db_rows:
        try:
            payload = json.loads(row["payload_json"]) if isinstance(row["payload_json"], str) else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict) or not _is_nicotine_payload(payload):
            continue
        try:
            target = float(
                payload.get("display_target_grams")
                or payload.get("target_grams")
                or payload.get("grams")
                or 0.0
            )
            measured = float(payload.get("measured_grams") or 0.0)
        except (TypeError, ValueError):
            continue
        delta = measured - target
        if delta < -0.005:
            rows.append(
                {
                    "kind": "nicotine_under",
                    "label": f"Nicotine accepted {abs(delta):.2f} g under target",
                }
            )
        elif delta > 0.005:
            rows.append(
                {
                    "kind": "nicotine_over",
                    "label": f"Nicotine accepted {delta:.2f} g over target",
                }
            )
    return rows


def _ratio_shift_is_meaningful(target_pg: float, target_vg: float, est_pg: float, est_vg: float) -> bool:
    return abs(float(est_pg) - float(target_pg)) >= 2 or abs(float(est_vg) - float(target_vg)) >= 2


def _ratio_shift_explanation(
    *,
    pg_adjustments: list[dict[str, Any]],
    accepted_deviations: list[dict[str, Any]],
) -> str:
    flavour_overs = [d for d in accepted_deviations if str(d.get("kind") or "") == "flavour_over"]
    if pg_adjustments or flavour_overs:
        return "Final ratio shifted because PG was adjusted to compensate for accepted flavour overpours."
    if accepted_deviations:
        return "Final ratio shifted because accepted pour deviations changed the carrier balance in this batch."
    return "Final ratio differs from target because of accepted pour deviations during this batch."


def _build_notes_for_next_time(
    *,
    performance_summary: dict[str, Any] | None,
    flavour_over_count: int,
    coach_summary: dict[str, Any] | None,
) -> list[str]:
    perf = performance_summary if isinstance(performance_summary, dict) else {}
    overs = int(perf.get("overshoot_count") or 0)
    recoveries = int(perf.get("recovery_count") or 0)
    notes: list[str] = []
    if flavour_over_count >= 2 or overs >= 2:
        notes.append(
            "Several flavour pours were over target. Use shorter pulse pours near the final margin."
        )
    elif flavour_over_count == 1 or overs == 1:
        notes.append("One pour ran over target. Pause briefly before the final gram on your next mix.")
    if recoveries >= 1:
        notes.append("Recovery was needed this session. Slow down and confirm each correction before continuing.")
    if int(perf.get("undershoot_count") or 0) >= 2:
        notes.append("You stopped early on multiple pours. Commit closer to target before confirming.")
    coach_detail = str((coach_summary or {}).get("detail") or "").strip()
    generic = "Steady control. Keep your normal approach."
    if not notes and coach_detail and coach_detail != generic:
        notes.append(coach_detail)
    if not notes:
        notes.append("Good batch. Repeat the same controlled pace on your next mix.")
    return notes[:3]


def _completion_status_label(
    *,
    performance_summary: dict[str, Any] | None,
    has_adjustments: bool,
) -> str:
    recoveries = int((performance_summary or {}).get("recovery_count") or 0)
    if recoveries > 0 and has_adjustments:
        return "Complete with recovery and adjustments"
    if recoveries > 0:
        return "Complete with recovery"
    if has_adjustments:
        return "Complete with adjustments"
    return "Complete"


def _build_guided_mix_summary(
    db,
    session_id: int,
    *,
    session_row: Any,
    recipe_name: str | None = None,
    performance_summary: dict[str, Any] | None = None,
    coach_summary: dict[str, Any] | None = None,
    include_raw_event_log: bool = False,
) -> dict[str, Any]:
    ctx = _session_batch_context(db, int(session_id), session_row=session_row)
    batch_size_ml = float(ctx["batch_size_ml"])
    pg_pct = float(ctx["target_pg_percent"])
    vg_pct = float(ctx["target_vg_percent"])
    meta = _read_session_meta(db, int(session_id))
    event_log = list(meta.get("mix_event_log") or [])
    raw_pg_adjustments = [e for e in event_log if str(e.get("event") or "") == "pg_adjusted"]
    raw_accepted = [e for e in event_log if str(e.get("event") or "").startswith("accepted_")]
    raw_nicotine = list(meta.get("nicotine_recovery_log") or [])
    pg_adjustments = [_format_pg_adjustment_row(e) for e in raw_pg_adjustments]
    accepted_deviations: list[dict[str, Any]] = []
    for entry in raw_accepted:
        row = _format_accepted_deviation_row(entry)
        if row:
            accepted_deviations.append(row)
    nicotine_events: list[dict[str, Any]] = []
    seen_nicotine_labels: set[str] = set()
    for entry in raw_nicotine:
        row = _format_nicotine_event_row(entry)
        if row and row["label"] not in seen_nicotine_labels:
            nicotine_events.append(row)
            seen_nicotine_labels.add(row["label"])
    for row in _infer_nicotine_deviation_rows(db, int(session_id)):
        if row["label"] not in seen_nicotine_labels:
            nicotine_events.append(row)
            seen_nicotine_labels.add(row["label"])
    nic_carrier = str(meta.get("nicotine_carrier") or "PG")
    est_pg, est_vg = _estimate_final_ratio_from_steps(
        db,
        int(session_id),
        target_pg_percent=pg_pct,
        target_vg_percent=vg_pct,
        nicotine_carrier=nic_carrier,
    )
    poured_ml = 0.0
    rows = db.execute(
        """
        SELECT payload_json FROM mix_session_steps
        WHERE mix_session_id = ? AND completed_at IS NOT NULL
        ORDER BY step_no ASC
        """,
        (int(session_id),),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"]) if isinstance(row["payload_json"], str) else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if str(payload.get("step_type") or "").lower() != "add_ingredient":
            continue
        try:
            ml = float(payload.get("measured_ml") or payload.get("ml") or payload.get("target_ml") or 0.0)
        except (TypeError, ValueError):
            ml = 0.0
        poured_ml += ml
    flavour_over_count = sum(1 for d in accepted_deviations if d.get("kind") == "flavour_over")
    has_adjustments = bool(pg_adjustments or accepted_deviations or nicotine_events)
    user_notes, user_notes_updated_at = _user_notes_from_meta(meta)
    planned = meta.get("planned_composition") if isinstance(meta.get("planned_composition"), dict) else {}
    ratio_achievable = bool(planned.get("ratio_achievable", True))
    ratio_warning = str(planned.get("ratio_warning") or "").strip()
    # Prefer poured estimate; fall back to planned finished composition when pours incomplete.
    if poured_ml <= 0 and planned:
        try:
            est_pg = round(float(planned.get("final_pg_percent")))
            est_vg = round(float(planned.get("final_vg_percent")))
        except (TypeError, ValueError):
            pass
    ratio_shift_explanation = None
    if not ratio_achievable and ratio_warning:
        ratio_shift_explanation = ratio_warning
    elif _ratio_shift_is_meaningful(pg_pct, vg_pct, est_pg, est_vg):
        ratio_shift_explanation = _ratio_shift_explanation(
            pg_adjustments=pg_adjustments,
            accepted_deviations=accepted_deviations,
        )
    target_nic = 0.0
    if session_row is not None and "target_nic" in session_row.keys():
        try:
            target_nic = float(session_row["target_nic"] or 0.0)
        except (TypeError, ValueError):
            target_nic = 0.0
    elif meta.get("target_nic_mg_ml") is not None:
        try:
            target_nic = float(meta.get("target_nic_mg_ml") or 0.0)
        except (TypeError, ValueError):
            target_nic = 0.0
    total_flavour_pct = planned.get("total_flavor_percent")
    try:
        total_flavour_pct = float(total_flavour_pct) if total_flavour_pct is not None else None
    except (TypeError, ValueError):
        total_flavour_pct = None
    summary = {
        "recipe_name": str(recipe_name or "").strip() or None,
        "summary_title": "Finished batch",
        "batch_size_ml": batch_size_ml,
        "target_nic_mg_ml": round(target_nic, 4),
        "total_flavor_percent": total_flavour_pct,
        "target_pg_percent": pg_pct,
        "target_vg_percent": vg_pct,
        "target_ratio_label": f"{int(round(pg_pct))}/{int(round(vg_pct))}",
        "estimated_final_volume_ml": round(poured_ml if poured_ml > 0 else batch_size_ml, 2),
        "estimated_final_pg_percent": est_pg,
        "estimated_final_vg_percent": est_vg,
        "estimated_final_ratio_label": f"{int(est_pg)}/{int(est_vg)}",
        "ratio_achievable": ratio_achievable,
        "ratio_warning": ratio_warning or None,
        "standalone_pg_ml": planned.get("standalone_pg_ml"),
        "standalone_vg_ml": planned.get("standalone_vg_ml"),
        "standalone_pg_g": planned.get("standalone_pg_g"),
        "standalone_vg_g": planned.get("standalone_vg_g"),
        "completion_status": _completion_status_label(
            performance_summary=performance_summary,
            has_adjustments=has_adjustments,
        ),
        "ratio_shift_explanation": ratio_shift_explanation,
        "pg_adjustments_heading": (
            "PG adjusted after accepted flavour overpours" if pg_adjustments else None
        ),
        "pg_adjustments": pg_adjustments,
        "accepted_deviations": accepted_deviations,
        "nicotine_events": nicotine_events,
        "notes_for_next_time": _build_notes_for_next_time(
            performance_summary=performance_summary,
            flavour_over_count=flavour_over_count,
            coach_summary=coach_summary,
        ),
        "nicotine_recovery_warning": meta.get("nicotine_recovery_warning"),
        "user_notes": user_notes,
        "user_notes_updated_at": user_notes_updated_at,
    }
    if include_raw_event_log:
        summary["raw_event_log"] = event_log
    return summary


def _adjust_pg_for_flavour_deviation(
    *,
    db,
    session_id: int,
    excess_grams: float,
    pour_density: float | None,
    exclude_step_id: int | None = None,
    batch_size_ml: float,
    target_pg_percent: float,
    target_vg_percent: float,
    source_ingredient: str = "",
) -> dict[str, Any] | None:
    """Trim only the open PG pour target after an accepted flavour deviation (not VG, not nicotine)."""
    excess = float(excess_grams or 0.0)
    if excess <= 0.0005:
        return None
    dens = float(pour_density or 0.0)
    excess_ml = (excess / dens) if dens > 0 else excess
    if excess_ml <= 0.0005:
        return None

    rows = db.execute(
        """
        SELECT id, payload_json
        FROM mix_session_steps
        WHERE mix_session_id = ? AND completed_at IS NULL
        ORDER BY step_no ASC
        """,
        (int(session_id),),
    ).fetchall()
    for row in rows:
        if exclude_step_id is not None and int(row["id"]) == int(exclude_step_id):
            continue
        try:
            payload = json.loads(row["payload_json"]) if isinstance(row["payload_json"], str) else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict) or str(payload.get("step_type") or "").lower() != "add_ingredient":
            continue
        if not _is_pg_payload(payload):
            continue
        try:
            grams = float(payload.get("target_grams") or payload.get("grams") or 0.0)
            ml = float(payload.get("ml") or payload.get("target_ml") or 0.0)
        except (TypeError, ValueError):
            continue
        if grams <= 0 and ml <= 0:
            continue
        step_dens = _density_from_step_payload(payload) or 1.0
        available_ml = ml if ml > 0 else (grams / step_dens)
        if available_ml <= 0.0005:
            continue
        cut_ml = min(available_ml, excess_ml)
        new_ml = max(0.0, available_ml - cut_ml)
        new_grams = new_ml * step_dens
        original_grams = float(payload.get("original_pg_target_grams") or grams)
        original_ml = float(payload.get("original_pg_target_ml") or available_ml)
        payload["original_pg_target_grams"] = round(original_grams, 4)
        payload["original_pg_target_ml"] = round(original_ml, 4)
        payload["ml"] = round(new_ml, 4)
        payload["target_ml"] = round(new_ml, 4)
        payload["grams"] = round(new_grams, 4)
        payload["target_grams"] = round(new_grams, 4)
        payload["display_target_grams"] = round(new_grams, 2)
        payload["base_adjusted_for_overpour"] = True
        payload["base_adjusted_cut_ml"] = round(cut_ml, 4)
        payload["pg_adjusted"] = True
        meta_for_carrier = _read_session_meta(db, int(session_id))
        est_pg, est_vg = _estimate_final_ratio_from_steps(
            db,
            int(session_id),
            target_pg_percent=target_pg_percent,
            target_vg_percent=target_vg_percent,
            nicotine_carrier=str(meta_for_carrier.get("nicotine_carrier") or "PG"),
        )
        payload["pg_adjustment_note"] = (
            "PG adjusted to protect final batch volume and ratio.\n"
            f"Original PG target: {original_grams:.2f} g / {original_ml:.2f} ml\n"
            f"Adjusted PG target: {new_grams:.2f} g / {new_ml:.2f} ml\n"
            f"Target ratio: {int(target_pg_percent)}/{int(target_vg_percent)}\n"
            f"Estimated final ratio: {int(est_pg)}/{int(est_vg)}"
        )
        db.execute(
            """
            UPDATE mix_session_steps
            SET payload_json = ?
            WHERE id = ? AND mix_session_id = ?
            """,
            (json.dumps(payload), int(row["id"]), int(session_id)),
        )
        entry = {
            "event": "pg_adjusted",
            "source_ingredient": source_ingredient,
            "excess_grams": float(excess),
            "cut_ml": float(cut_ml),
            "original_pg_grams": float(original_grams),
            "original_pg_ml": float(original_ml),
            "adjusted_pg_grams": float(new_grams),
            "adjusted_pg_ml": float(new_ml),
            "batch_size_ml": float(batch_size_ml),
            "target_ratio": f"{int(target_pg_percent)}/{int(target_vg_percent)}",
            "estimated_final_ratio": f"{int(est_pg)}/{int(est_vg)}",
        }
        _append_mix_event_log(db, int(session_id), entry)
        return entry
    return None


def _soft_adjust_remaining_base_for_overpour(
    *,
    db,
    session_id: int,
    excess_grams: float,
    pour_density: float | None,
    exclude_step_id: int | None = None,
    batch_size_ml: float = 0.0,
    target_pg_percent: float = 30.0,
    target_vg_percent: float = 70.0,
    source_ingredient: str = "",
) -> bool:
    """Backward-compatible wrapper: flavour deviations adjust PG only."""
    result = _adjust_pg_for_flavour_deviation(
        db=db,
        session_id=session_id,
        excess_grams=excess_grams,
        pour_density=pour_density,
        exclude_step_id=exclude_step_id,
        batch_size_ml=batch_size_ml,
        target_pg_percent=target_pg_percent,
        target_vg_percent=target_vg_percent,
        source_ingredient=source_ingredient,
    )
    return result is not None


def _attach_checkpoint_note_to_next_transition(
    *,
    db,
    session_id: int,
    after_step_no: int,
    note: str,
    note_kind: str = "slight_over",
) -> None:
    """Persist bench note on the next unfinished transition so Bottle Moved still shows it."""
    rows = db.execute(
        """
        SELECT id, step_no, step_type, payload_json
        FROM mix_session_steps
        WHERE mix_session_id = ? AND completed_at IS NULL AND step_no > ?
        ORDER BY step_no ASC
        """,
        (int(session_id), int(after_step_no)),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"]) if isinstance(row["payload_json"], str) else {}
        except (json.JSONDecodeError, TypeError):
            continue
        st = str(payload.get("step_type") or row["step_type"] or "").lower()
        if st != "transition":
            continue
        payload["checkpoint_note"] = str(note)
        payload["checkpoint_note_kind"] = str(note_kind)
        db.execute(
            """
            UPDATE mix_session_steps
            SET payload_json = ?
            WHERE id = ? AND mix_session_id = ?
            """,
            (json.dumps(payload), int(row["id"]), int(session_id)),
        )
        return


def _session_meta_cache_key(session_id: int) -> str:
    return f"guided_session_meta_{int(session_id)}"


def _invalidate_session_meta_cache(session_id: int) -> None:
    key = _session_meta_cache_key(session_id)
    if hasattr(g, key):
        delattr(g, key)


def _read_session_meta(db, session_id: int, *, use_cache: bool = True) -> dict[str, Any]:
    key = _session_meta_cache_key(session_id)
    if use_cache and hasattr(g, key):
        cached = getattr(g, key)
        if isinstance(cached, dict):
            return cached
    row = db.execute("SELECT result_json FROM mix_sessions WHERE id = ?", (int(session_id),)).fetchone()
    if not row or not row["result_json"]:
        parsed: dict[str, Any] = {}
    else:
        try:
            loaded = json.loads(row["result_json"]) if isinstance(row["result_json"], str) else {}
            parsed = loaded if isinstance(loaded, dict) else {}
        except (json.JSONDecodeError, TypeError):
            parsed = {}
    if use_cache:
        setattr(g, key, parsed)
    return parsed


def _write_session_meta(db, session_id: int, meta: dict[str, Any]) -> None:
    db.execute(
        "UPDATE mix_sessions SET result_json = ? WHERE id = ?",
        (json.dumps(meta or {}), int(session_id)),
    )
    _invalidate_session_meta_cache(session_id)
    setattr(g, _session_meta_cache_key(session_id), dict(meta or {}))


def _user_notes_from_meta(meta: dict[str, Any] | None) -> tuple[str, str | None]:
    data = meta if isinstance(meta, dict) else {}
    notes = str(data.get("user_notes") or "")
    updated = data.get("user_notes_updated_at")
    updated_at = str(updated).strip() if updated else None
    return notes, updated_at


def _normalize_user_notes(raw: Any, *, max_len: int = 4000) -> str:
    text = str(raw or "")
    if len(text) > max_len:
        text = text[:max_len]
    return text


def save_guided_session_user_notes(
    *,
    session_id: int,
    user_id: int,
    user_notes: str,
) -> dict[str, Any]:
    """Persist optional mixer-written batch notes on a completed guided session."""
    db = get_db()
    own = db.execute(
        "SELECT id FROM mix_sessions WHERE id = ? AND user_id = ?",
        (int(session_id), int(user_id)),
    ).fetchone()
    if not own:
        return {"ok": False, "message": "Mix session not found."}
    open_row = db.execute(
        """
        SELECT COUNT(1) AS c
        FROM mix_session_steps
        WHERE mix_session_id = ? AND completed_at IS NULL
        """,
        (int(session_id),),
    ).fetchone()
    if int(open_row["c"] or 0) > 0:
        return {"ok": False, "message": "Notes can only be saved after the mix is complete."}
    meta = _read_session_meta(db, int(session_id))
    normalized = _normalize_user_notes(user_notes)
    meta["user_notes"] = normalized
    meta["user_notes_updated_at"] = datetime_now_iso()
    _write_session_meta(db, int(session_id), meta)
    db.commit()
    notes, updated_at = _user_notes_from_meta(meta)
    return {
        "ok": True,
        "user_notes": notes,
        "user_notes_updated_at": updated_at,
    }


_RECIPE_COMPLETED_GUIDED_SESSIONS_SQL = """
    SELECT
        ms.id,
        ms.user_id,
        ms.recipe_version_id,
        ms.target_ml,
        ms.target_nic,
        ms.target_pgvg,
        ms.result_json,
        ms.created_at,
        ms.completed_at,
        u.username AS mixer_username,
        rv.version_number,
        r.id AS recipe_id,
        r.name AS recipe_name
    FROM mix_sessions ms
    JOIN recipe_versions rv ON rv.id = ms.recipe_version_id
    JOIN recipes r ON r.id = rv.recipe_id
    LEFT JOIN users u ON u.id = ms.user_id
    WHERE r.id = ?
      AND LOWER(COALESCE(ms.mode, '')) = 'guided'
      AND ms.completed_at IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM mix_session_steps s
        WHERE s.mix_session_id = ms.id AND s.completed_at IS NULL
      )
    ORDER BY ms.completed_at DESC, ms.id DESC
"""


def _recipe_session_note_row(row: Any) -> dict[str, Any] | None:
    try:
        meta = json.loads(row["result_json"]) if isinstance(row["result_json"], str) else {}
    except (json.JSONDecodeError, TypeError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    note_text, updated_at = _user_notes_from_meta(meta)
    note_text = str(note_text or "").strip()
    if not note_text:
        return None
    batch_ctx = _session_batch_context(get_db(), int(row["id"]), session_row=row)
    return {
        "session_id": int(row["id"]),
        "note_text": note_text,
        "updated_at": updated_at or row["completed_at"] or row["created_at"],
        "completed_at": row["completed_at"],
        "created_at": row["created_at"],
        "mixer_username": str(row["mixer_username"] or "").strip() or None,
        "mixer_user_id": int(row["user_id"]) if row["user_id"] is not None else None,
        "batch_size_ml": float(batch_ctx.get("batch_size_ml") or row["target_ml"] or 0.0),
        "target_pgvg": str(row["target_pgvg"] or "30/70"),
        "target_nic": float(row["target_nic"] or 0.0),
        "version_number": row["version_number"],
        "recipe_version_id": int(row["recipe_version_id"]),
    }


def list_recipe_mixer_notes(
    *,
    recipe_id: int,
    limit: int = 3,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Latest user-written mixer notes from completed guided sessions for a recipe."""
    db = get_db()
    rows = db.execute(_RECIPE_COMPLETED_GUIDED_SESSIONS_SQL, (int(recipe_id),)).fetchall()
    all_notes: list[dict[str, Any]] = []
    for row in rows:
        parsed = _recipe_session_note_row(row)
        if parsed:
            all_notes.append(parsed)
    all_notes.sort(key=lambda n: str(n.get("updated_at") or ""), reverse=True)
    total = len(all_notes)
    lim = max(1, min(20, int(limit or 3)))
    off = max(0, int(offset or 0))
    return all_notes[off : off + lim], total


def _build_recipe_batch_history_entry(db, row: Any, *, recipe_name: str | None = None) -> dict[str, Any]:
    session_id = int(row["id"])
    try:
        meta = json.loads(row["result_json"]) if isinstance(row["result_json"], str) else {}
    except (json.JSONDecodeError, TypeError):
        meta = {}
    user_notes, user_notes_updated_at = _user_notes_from_meta(meta if isinstance(meta, dict) else {})
    perf = meta.get("performance") if isinstance(meta, dict) and isinstance(meta.get("performance"), dict) else {}
    coach_summary = build_live_coach_summary(
        tone_profile=str(meta.get("tone_profile") or "tvw"),
        performance_summary=perf if isinstance(perf, dict) else None,
        session_seed=f"history:{session_id}",
    )
    mix_summary = _build_guided_mix_summary(
        db,
        session_id,
        session_row=row,
        recipe_name=recipe_name or row["recipe_name"],
        performance_summary=perf if isinstance(perf, dict) else None,
        coach_summary=coach_summary,
        include_raw_event_log=True,
    )
    return {
        "session_id": session_id,
        "mixer_username": str(row["mixer_username"] or "").strip() or None,
        "mixer_user_id": int(row["user_id"]) if row["user_id"] is not None else None,
        "batch_size_ml": float(mix_summary.get("batch_size_ml") or row["target_ml"] or 0.0),
        "target_pgvg": str(row["target_pgvg"] or mix_summary.get("target_ratio_label") or "30/70"),
        "target_nic": float(row["target_nic"] or 0.0),
        "version_number": row["version_number"],
        "recipe_version_id": int(row["recipe_version_id"]),
        "completed_at": row["completed_at"],
        "created_at": row["created_at"],
        "user_notes": user_notes,
        "user_notes_updated_at": user_notes_updated_at,
        "mix_summary": mix_summary,
    }


def list_recipe_batch_history(
    *,
    recipe_id: int,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """Completed guided mix sessions for a recipe with readable batch reports."""
    db = get_db()
    rows = db.execute(_RECIPE_COMPLETED_GUIDED_SESSIONS_SQL, (int(recipe_id),)).fetchall()
    total = len(rows)
    page_i = max(1, int(page or 1))
    page_size_i = max(1, min(50, int(page_size or 20)))
    offset = (page_i - 1) * page_size_i
    slice_rows = rows[offset : offset + page_size_i]
    out: list[dict[str, Any]] = []
    for row in slice_rows:
        out.append(_build_recipe_batch_history_entry(db, row))
    return out, total


def update_guided_session_vessel_ml(*, session_id: int, user_id: int, vessel_ml: float) -> bool:
    """Persist active vessel/container size for tolerance scaling and coherent recovery (mix_sessions.result_json)."""
    db = get_db()
    own = db.execute(
        "SELECT id FROM mix_sessions WHERE id = ? AND user_id = ?",
        (int(session_id), int(user_id)),
    ).fetchone()
    if not own:
        return False
    meta = _read_session_meta(db, int(session_id))
    meta["vessel_size_ml"] = float(vessel_ml)
    _write_session_meta(db, int(session_id), meta)
    db.commit()
    return True


def _session_performance_summary(
    db,
    session_id: int,
    *,
    meta: dict[str, Any] | None = None,
) -> dict[str, int]:
    if meta is None:
        meta = _read_session_meta(db, session_id)
    perf = meta.get("performance") if isinstance(meta.get("performance"), dict) else {}
    base = _default_performance_summary()
    for k in list(base.keys()):
        try:
            base[k] = int(perf.get(k) or 0)
        except (TypeError, ValueError):
            base[k] = 0
    return base


def _bump_session_performance(db, session_id: int, **increments: int) -> dict[str, int]:
    meta = _read_session_meta(db, session_id)
    perf = meta.get("performance") if isinstance(meta.get("performance"), dict) else _default_performance_summary()
    for key, inc in increments.items():
        if key not in _default_performance_summary():
            continue
        try:
            add_i = int(inc or 0)
        except (TypeError, ValueError):
            add_i = 0
        current = int(perf.get(key) or 0)
        perf[key] = max(0, current + add_i)
    meta["performance"] = perf
    _write_session_meta(db, session_id, meta)
    out = _default_performance_summary()
    for k in out:
        out[k] = int(perf.get(k) or 0)
    return out


def _insert_dynamic_step(
    *,
    db,
    session_id: int,
    at_step_no: int,
    step_type: str,
    title: str,
    what: str,
    why: str,
    tip: str,
    action_label: str,
    severity: str,
    source_event: str,
    tone_profile: str,
) -> int:
    """Insert a new step into active flow by shifting later step numbers."""
    db.execute(
        """
        UPDATE mix_session_steps
        SET step_no = step_no + 1
        WHERE mix_session_id = ? AND step_no >= ?
        """,
        (int(session_id), int(at_step_no)),
    )
    payload = {
        "step_type": str(step_type),
        "title": str(title),
        "what": str(what),
        "why": str(why),
        "tip": str(tip),
        "action_label": str(action_label),
        "severity": str(severity),
        "dynamic": True,
        "source_event": str(source_event or "policy"),
        "tone_profile": str(tone_profile or "tvw"),
    }
    db.execute(
        """
        INSERT INTO mix_session_steps (mix_session_id, step_no, step_type, payload_json, completed_at)
        VALUES (?, ?, ?, ?, NULL)
        """,
        (int(session_id), int(at_step_no), str(step_type), json.dumps(payload)),
    )
    return int(at_step_no)


def _insert_dynamic_payload_step_if_missing(
    *,
    db,
    session_id: int,
    at_step_no: int,
    payload: dict[str, Any],
) -> int | None:
    step_type = str(payload.get("step_type") or "caution")
    source_event = str(payload.get("source_event") or "runtime")
    title = str(payload.get("title") or "")
    return _insert_dynamic_step_if_missing(
        db=db,
        session_id=session_id,
        at_step_no=at_step_no,
        step_type=step_type,
        title=title,
        what=str(payload.get("what") or ""),
        why=str(payload.get("why") or ""),
        tip=str(payload.get("tip") or ""),
        action_label=str(payload.get("action_label") or "Acknowledge"),
        severity=str(payload.get("severity") or "warning"),
        source_event=source_event,
        tone_profile=str(payload.get("tone_profile") or "tvw"),
    )


def _insert_dynamic_step_if_missing(
    *,
    db,
    session_id: int,
    at_step_no: int,
    step_type: str,
    title: str,
    what: str,
    why: str,
    tip: str,
    action_label: str,
    severity: str,
    source_event: str,
    tone_profile: str,
) -> int | None:
    """Avoid duplicate open dynamic steps with same event + type + title."""
    exists = db.execute(
        """
        SELECT 1
        FROM mix_session_steps
        WHERE mix_session_id = ?
          AND completed_at IS NULL
          AND step_type = ?
          AND json_extract(payload_json, '$.dynamic') = 1
          AND json_extract(payload_json, '$.source_event') = ?
          AND COALESCE(json_extract(payload_json, '$.title'), '') = ?
        LIMIT 1
        """,
        (int(session_id), str(step_type), str(source_event), str(title)),
    ).fetchone()
    if exists:
        return None
    return _insert_dynamic_step(
        db=db,
        session_id=session_id,
        at_step_no=at_step_no,
        step_type=step_type,
        title=title,
        what=what,
        why=why,
        tip=tip,
        action_label=action_label,
        severity=severity,
        source_event=source_event,
        tone_profile=tone_profile,
    )


def _append_policy_driven_dynamic_steps(
    *,
    db,
    session_id: int,
    policy_name: str,
    warnings: list[str],
    hard_stops: list[str],
) -> list[int]:
    inserted: list[int] = []
    next_open = db.execute(
        """
        SELECT step_no
        FROM mix_session_steps
        WHERE mix_session_id = ? AND completed_at IS NULL
        ORDER BY step_no ASC
        LIMIT 1
        """,
        (int(session_id),),
    ).fetchone()
    at_no = int(next_open["step_no"]) if next_open else 10_000
    is_lab = "lab" in str(policy_name or "").lower()
    tone_profile = "lab" if is_lab else "tvw"
    perf = _session_performance_summary(db, session_id)
    if hard_stops:
        payload = build_runtime_narrative_step(
            event_type="policy_block",
            tone_profile=tone_profile,
            severity="danger",
            context={"message": hard_stops[0]},
            policy_name=policy_name,
            performance_summary=perf,
            session_seed=f"{session_id}:{at_no}",
        )
        payload["what"] = str(hard_stops[0] or payload.get("what") or "")
        payload["source_event"] = "policy_hard_stop"
        pos = _insert_dynamic_payload_step_if_missing(db=db, session_id=session_id, at_step_no=at_no, payload=payload)
        if pos is not None:
            inserted.append(int(pos))
    elif warnings:
        payload = build_runtime_narrative_step(
            event_type="policy_warning",
            tone_profile=tone_profile,
            severity="warning",
            context={"message": warnings[0]},
            policy_name=policy_name,
            performance_summary=perf,
            session_seed=f"{session_id}:{at_no}",
        )
        payload["what"] = str(warnings[0] or payload.get("what") or "")
        payload["source_event"] = "policy_warning"
        pos = _insert_dynamic_payload_step_if_missing(db=db, session_id=session_id, at_step_no=at_no, payload=payload)
        if pos is not None:
            inserted.append(int(pos))
    return inserted


def create_guided_mix_session(
    *,
    user_id: int,
    recipe_id: int,
    requested_version_id: int | None,
    target_ml: float,
    target_nic_mg_ml: float,
    nic_base_mg_ml: float,
    pg_ratio: float,
    vg_ratio: float,
    nicotine_carrier: str,
    nicotine_strictness_mode: str | None = None,
    production_job_id: int | None = None,
    production_job_recipe_id: int | None = None,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Returns (session_id, resolved_version_id, steps).

    Builds steps in memory, then writes session + steps in one concise transaction.
    Does not store a duplicate full step list in result_json (steps live in mix_session_steps).
    Does not build completion summaries at create time.

    production_job_id / production_job_recipe_id must only be set for explicit
    Lab “Mix for this job” starts. Personal Workshop mixes leave both NULL.
    """
    from utils.guided_mix_perf import log_guided_mix_stage

    job_id = int(production_job_id) if production_job_id is not None else None
    job_recipe_id = int(production_job_recipe_id) if production_job_recipe_id is not None else None
    if (job_id is None) ^ (job_recipe_id is None):
        raise ValueError("production_job_id and production_job_recipe_id must be set together.")

    with guided_mix_perf_span("create_guided_mix_session", recipe_id=int(recipe_id)) as span:
        with guided_mix_perf_span("resolve_version", recipe_id=int(recipe_id)):
            resolved_version_id = recipes_repo.resolve_recipe_version_id_for_mix(
                recipe_id=recipe_id,
                requested_version_id=requested_version_id,
            )
        with guided_mix_perf_span("build_steps", version_id=int(resolved_version_id)):
            steps = tvw_mix_service.guided_mix_steps_for_recipe_version(
                recipe_id=recipe_id,
                version_id=resolved_version_id,
                target_ml=float(target_ml),
                target_nic_mg_ml=float(target_nic_mg_ml),
                nic_base_mg_ml=float(nic_base_mg_ml),
                pg_ratio=float(pg_ratio),
                vg_ratio=float(vg_ratio),
                nicotine_carrier=str(nicotine_carrier or "PG"),
            )
            planned_composition = tvw_mix_service.planned_finished_composition_for_recipe_version(
                recipe_id=recipe_id,
                version_id=resolved_version_id,
                target_ml=float(target_ml),
                target_nic_mg_ml=float(target_nic_mg_ml),
                nic_base_mg_ml=float(nic_base_mg_ml),
                pg_ratio=float(pg_ratio),
                vg_ratio=float(vg_ratio),
                nicotine_carrier=str(nicotine_carrier or "PG"),
            )
        span["step_count"] = len(steps)

        # Minimal meta only — no steps[], no mix_summary, no raw_event_log.
        session_meta = {
            "step_model": "guided_assistant_v1",
            "tone_profile": "tvw",
            "nicotine_carrier": str(nicotine_carrier or "PG"),
            "nic_base_mg_ml": float(nic_base_mg_ml),
            "target_nic_mg_ml": float(target_nic_mg_ml),
            "vessel_size_ml": float(target_ml),
            "batch_size_ml": float(target_ml),
            "target_pg_percent": float(pg_ratio),
            "target_vg_percent": float(vg_ratio),
            "planned_composition": planned_composition,
            "performance": _default_performance_summary(),
            "nicotine_strictness_mode": normalize_nicotine_strictness(nicotine_strictness_mode),
            "mix_event_log": [],
        }
        if job_id is not None:
            session_meta["production_job_id"] = job_id
            session_meta["production_job_recipe_id"] = job_recipe_id
        meta_json = json.dumps(session_meta)
        span["result_json_bytes"] = json_byte_size(meta_json)
        step_rows = [
            (
                int(step["step"]),
                str(step.get("step_type") or "instruction"),
                json.dumps(step),
            )
            for step in steps
        ]

        def _persist() -> int:
            db = get_db()
            # Interactive start: fail/retry quickly instead of blocking up to 60s on contention.
            db.execute("PRAGMA busy_timeout=3000;")
            stage = "session_insert"
            req_id = get_guided_mix_request_id()
            try:
                try:
                    cur = db.execute(
                        """
                        INSERT INTO mix_sessions (
                            user_id, recipe_version_id, mode, target_ml, target_nic, target_pgvg,
                            result_json, created_at, completed_at,
                            production_job_id, production_job_recipe_id
                        )
                        VALUES (?, ?, 'guided', ?, ?, ?, ?, datetime('now'), NULL, ?, ?)
                        """,
                        (
                            int(user_id),
                            resolved_version_id,
                            float(target_ml),
                            float(target_nic_mg_ml),
                            f"{pg_ratio}/{vg_ratio}",
                            meta_json,
                            job_id,
                            job_recipe_id,
                        ),
                    )
                    session_id = int(cur.lastrowid)
                    stage = "step_insert"
                    if step_rows:
                        db.executemany(
                            """
                            INSERT INTO mix_session_steps (mix_session_id, step_no, step_type, payload_json, completed_at)
                            VALUES (?, ?, ?, ?, NULL)
                            """,
                            [(session_id, step_no, step_type, payload) for step_no, step_type, payload in step_rows],
                        )
                    stage = "commit"
                    db.commit()
                    return session_id
                except sqlite3.OperationalError as exc:
                    log_sqlite_lock_failure(
                        stage=stage,
                        exc=exc,
                        request_id=req_id,
                        context="guided_mix_create_session",
                    )
                    raise
            finally:
                db.execute("PRAGMA busy_timeout=60000;")

        with guided_mix_perf_span("persist_session_and_steps", step_count=len(step_rows)):
            session_id = sqlite_retry(
                _persist,
                attempts=8,
                base_delay_s=0.05,
                lock_context="guided_mix_create_session",
                lock_stage="persist_session_and_steps",
                request_id=get_guided_mix_request_id(),
            )
        log_guided_mix_stage(
            "create-session",
            session_id=session_id,
            version_id=resolved_version_id,
            step_count=len(steps),
            result_json_bytes=span.get("result_json_bytes"),
        )
        return session_id, resolved_version_id, steps


def _notify_production_job_session_completed(session_id: int) -> None:
    """If this guided session is job-linked and fully complete, post production once."""
    try:
        from services import production_job_service

        production_job_service.complete_batch_for_guided_session(mix_session_id=int(session_id))
    except Exception:
        # Never block guided-mix completion on Lab job bookkeeping errors.
        pass


def record_quick_mix_session(
    *,
    user_id: int,
    recipe_version_id: int,
    target_ml: float,
    target_nic_mg_ml: float,
    nic_base_mg_ml: float,
    pg_ratio: float,
    vg_ratio: float,
    nicotine_carrier: str,
) -> int:
    """Append a quick-mix row (no steps) for personal mix history."""
    db = get_db()
    raw = str(nicotine_carrier or "PG").strip().upper()
    carrier_norm = "VG" if raw == "VG" else "PG"
    meta = json.dumps(
        {
            "kind": "quick",
            "nicotine_carrier": carrier_norm,
            "nic_base_mg_ml": float(nic_base_mg_ml),
        }
    )
    cur = db.execute(
        """
        INSERT INTO mix_sessions (user_id, recipe_version_id, mode, target_ml, target_nic, target_pgvg, result_json, created_at, completed_at)
        VALUES (?, ?, 'quick', ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            int(user_id),
            int(recipe_version_id),
            float(target_ml),
            float(target_nic_mg_ml),
            f"{float(pg_ratio)}/{float(vg_ratio)}",
            meta,
        ),
    )
    session_id = int(cur.lastrowid)
    db.commit()
    return session_id


def _parse_session_meta(result_json: Any) -> tuple[float, str]:
    nic_base = 100.0
    carrier = "PG"
    if not result_json:
        return nic_base, carrier
    try:
        rj = json.loads(result_json) if isinstance(result_json, str) else (result_json or {})
        if isinstance(rj, dict):
            nb = rj.get("nic_base_mg_ml")
            if nb is not None:
                nic_base = float(nb)
            c = str(rj.get("nicotine_carrier") or "PG").strip().upper()
            carrier = c if c in ("PG", "VG") else "PG"
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return nic_base, carrier


def list_user_mix_sessions(
    *,
    user_id: int,
    page: int = 1,
    page_size: int = 20,
    filter_mode: str = "all",
) -> tuple[list[dict[str, Any]], int]:
    """Guided and quick rows for the signed-in user (paged, status-aware ordering)."""
    db = get_db()
    page_i = max(1, int(page or 1))
    page_size_i = max(1, min(100, int(page_size or 20)))
    offset = (page_i - 1) * page_size_i
    f = str(filter_mode or "all").strip().lower()
    if f in ("done",):
        f = "completed"
    mode_where = "LOWER(COALESCE(ms.mode, 'quick')) IN ('guided', 'quick')"
    if f == "guided":
        mode_where = "LOWER(COALESCE(ms.mode, 'quick')) = 'guided'"
    elif f == "quick":
        mode_where = "LOWER(COALESCE(ms.mode, 'quick')) = 'quick'"

    status_where = "1=1"
    if f == "in_progress":
        status_where = "LOWER(COALESCE(base.mode, 'quick')) = 'guided' AND base.open_steps > 0"
    elif f == "completed":
        status_where = (
            "(LOWER(COALESCE(base.mode, 'quick')) = 'quick' "
            "OR (LOWER(COALESCE(base.mode, 'quick')) = 'guided' AND base.open_steps = 0))"
        )

    total_row = db.execute(
        f"""
        WITH base AS (
            SELECT
                ms.id,
                ms.mode,
                (
                    SELECT COUNT(1) FROM mix_session_steps s
                    WHERE s.mix_session_id = ms.id AND s.completed_at IS NULL
                ) AS open_steps
            FROM mix_sessions ms
            WHERE ms.user_id = ? AND {mode_where}
        )
        SELECT COUNT(1) AS c
        FROM base
        WHERE {status_where}
        """,
        (int(user_id),),
    ).fetchone()
    total = int(total_row["c"] or 0) if total_row else 0

    rows = db.execute(
        """
        WITH base AS (
            SELECT
                ms.id,
                ms.mode,
                ms.recipe_version_id,
                ms.target_ml,
                ms.target_nic,
                ms.target_pgvg,
                ms.result_json,
                ms.created_at,
                ms.completed_at,
                r.id AS recipe_id,
                r.name AS recipe_name,
                r.slug AS recipe_slug,
                rv.version_number,
                (
                    SELECT COUNT(1) FROM mix_session_steps s
                    WHERE s.mix_session_id = ms.id AND s.completed_at IS NULL
                ) AS open_steps,
                (
                    SELECT COUNT(1) FROM mix_session_steps s WHERE s.mix_session_id = ms.id
                ) AS total_steps
            FROM mix_sessions ms
            JOIN recipe_versions rv ON rv.id = ms.recipe_version_id
            JOIN recipes r ON r.id = rv.recipe_id
            WHERE ms.user_id = ? AND """
        + mode_where
        + """
        )
        SELECT *
        FROM base
        WHERE """
        + status_where
        + """
        ORDER BY
            CASE
                WHEN LOWER(COALESCE(base.mode, 'quick')) = 'guided' AND base.open_steps > 0 THEN 0
                ELSE 1
            END ASC,
            COALESCE(base.completed_at, base.created_at) DESC,
            base.id DESC
        LIMIT ? OFFSET ?
        """,
        (int(user_id), page_size_i, offset),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        mode = str(d.get("mode") or "quick").lower()
        d["mode"] = mode
        open_s = int(d["open_steps"] or 0)
        total_s = int(d["total_steps"] or 0)
        d["open_steps"] = open_s
        d["total_steps"] = total_s
        if mode == "guided":
            d["in_progress"] = open_s > 0
            d["completed"] = total_s > 0 and open_s == 0
        else:
            d["in_progress"] = False
            d["completed"] = True
        d["completed_at"] = d.get("completed_at")
        nb, car = _parse_session_meta(d.get("result_json"))
        d["nic_base_mg_ml"] = nb
        d["nicotine_carrier"] = car
        notes, notes_updated = "", None
        try:
            rj_raw = d.get("result_json")
            if rj_raw:
                rj = json.loads(rj_raw) if isinstance(rj_raw, str) else (rj_raw or {})
                notes, notes_updated = _user_notes_from_meta(rj if isinstance(rj, dict) else {})
        except (json.JSONDecodeError, TypeError):
            notes, notes_updated = "", None
        d["user_notes"] = notes
        d["user_notes_updated_at"] = notes_updated
        del d["result_json"]
        out.append(d)
    return out, total


def list_user_guided_sessions(*, user_id: int, limit: int = 30) -> list[dict[str, Any]]:
    db = get_db()
    lim = max(1, min(100, int(limit)))
    rows = db.execute(
        """
        SELECT
            ms.id,
            ms.recipe_version_id,
            ms.target_ml,
            ms.target_nic,
            ms.target_pgvg,
            ms.result_json,
            ms.created_at,
            ms.completed_at,
            r.id AS recipe_id,
            r.name AS recipe_name,
            r.slug AS recipe_slug,
            rv.version_number,
            (
                SELECT COUNT(1) FROM mix_session_steps s
                WHERE s.mix_session_id = ms.id AND s.completed_at IS NULL
            ) AS open_steps,
            (
                SELECT COUNT(1) FROM mix_session_steps s WHERE s.mix_session_id = ms.id
            ) AS total_steps
        FROM mix_sessions ms
        JOIN recipe_versions rv ON rv.id = ms.recipe_version_id
        JOIN recipes r ON r.id = rv.recipe_id
        WHERE ms.user_id = ? AND LOWER(COALESCE(ms.mode, '')) = 'guided'
        ORDER BY ms.created_at DESC
        LIMIT ?
        """,
        (int(user_id), lim),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["open_steps"] = int(d["open_steps"] or 0)
        d["total_steps"] = int(d["total_steps"] or 0)
        d["in_progress"] = d["open_steps"] > 0
        nb, car = _parse_session_meta(d.get("result_json"))
        d["nic_base_mg_ml"] = nb
        d["nicotine_carrier"] = car
        notes, notes_updated = "", None
        try:
            rj_raw = d.get("result_json")
            if rj_raw:
                rj = json.loads(rj_raw) if isinstance(rj_raw, str) else (rj_raw or {})
                notes, notes_updated = _user_notes_from_meta(rj if isinstance(rj, dict) else {})
        except (json.JSONDecodeError, TypeError):
            notes, notes_updated = "", None
        d["user_notes"] = notes
        d["user_notes_updated_at"] = notes_updated
        del d["result_json"]
        out.append(d)
    return out


def get_guided_session_bundle(
    *,
    session_id: int,
    user_id: int,
    for_client: bool = True,
    include_mix_summary: bool | None = None,
) -> dict[str, Any] | None:
    """Session row + steps with parsed payloads + recipe/version labels + progress hints."""
    with guided_mix_perf_span(
        "build_session_bundle",
        session_id=int(session_id),
        for_client=bool(for_client),
    ) as span:
        bundle = _build_guided_session_bundle_inner(
            session_id=session_id,
            user_id=user_id,
            for_client=for_client,
            include_mix_summary=include_mix_summary,
            span=span,
        )
        if bundle is not None and for_client:
            span["response_payload_bytes"] = json_byte_size(bundle)
        return bundle


def _build_guided_session_bundle_inner(
    *,
    session_id: int,
    user_id: int,
    for_client: bool,
    include_mix_summary: bool | None,
    span: dict[str, Any],
) -> dict[str, Any] | None:
    db = get_db()
    session_row = db.execute(
        """
        SELECT id, user_id, recipe_version_id, mode, target_ml, target_nic, target_pgvg,
               result_json, created_at, completed_at,
               production_job_id, production_job_recipe_id
        FROM mix_sessions
        WHERE id = ? AND user_id = ?
        """,
        (session_id, user_id),
    ).fetchone()
    if not session_row:
        # Older DBs mid-migration may lack job columns — fall back.
        try:
            session_row = db.execute(
                """
                SELECT id, user_id, recipe_version_id, mode, target_ml, target_nic, target_pgvg,
                       result_json, created_at, completed_at
                FROM mix_sessions
                WHERE id = ? AND user_id = ?
                """,
                (session_id, user_id),
            ).fetchone()
        except Exception:
            session_row = None
    if not session_row:
        return None
    # Seed meta cache from the row we already loaded — avoid a second SELECT + parse.
    raw_meta = session_row["result_json"]
    try:
        seeded = json.loads(raw_meta) if isinstance(raw_meta, str) else (raw_meta or {})
        if isinstance(seeded, dict):
            setattr(g, _session_meta_cache_key(int(session_id)), seeded)
    except (json.JSONDecodeError, TypeError):
        pass
    version_row = recipes_repo.fetch_version(int(session_row["recipe_version_id"]))
    recipe_id = int(version_row["recipe_id"]) if version_row else 0
    recipe_row = recipes_repo.fetch_recipe(recipe_id) if recipe_id else None
    steps_raw = db.execute(
        """
        SELECT id, mix_session_id, step_no, step_type, payload_json, completed_at
        FROM mix_session_steps
        WHERE mix_session_id = ?
        ORDER BY step_no ASC
        """,
        (session_id,),
    ).fetchall()
    steps_out: list[dict[str, Any]] = []
    open_step_no: int | None = None
    for s in steps_raw:
        d = dict(s)
        raw_pl = d.get("payload_json")
        try:
            d["payload"] = json.loads(raw_pl) if isinstance(raw_pl, str) else (raw_pl or {})
        except json.JSONDecodeError:
            d["payload"] = {}
        if for_client:
            d.pop("payload_json", None)
        if d.get("completed_at") is None:
            d["payload"] = _sanitize_open_pour_payload_for_client(
                d["payload"] if isinstance(d.get("payload"), dict) else {}
            )
        if d.get("completed_at") is None and open_step_no is None:
            open_step_no = int(d["step_no"])
        steps_out.append(d)
    session_meta = _read_session_meta(db, int(session_id))
    span["result_json_bytes"] = json_byte_size(session_row["result_json"])
    span["mix_event_log_entries"] = len(session_meta.get("mix_event_log") or [])
    span["completed_steps"] = sum(1 for x in steps_out if x.get("completed_at") is not None)
    span["total_steps"] = len(steps_out)
    perf_summary = _session_performance_summary(db, int(session_id), meta=session_meta)
    for s in steps_out:
        if s.get("completed_at") is not None:
            continue
        payload = s.get("payload") if isinstance(s.get("payload"), dict) else {}
        if str(payload.get("dynamic") or "").lower() == "true" or bool(payload.get("dynamic")):
            continue
        adaptive = build_preemptive_coaching_for_step(
            step_type=str(payload.get("step_type") or s.get("step_type") or ""),
            tone_profile="tvw",
            performance_summary=perf_summary,
            step_payload=payload,
            session_seed=f"{session_id}:{int(s.get('step_no') or 0)}",
        )
        if adaptive:
            tip_line = str(adaptive.get("tip_line") or "").strip()
            preview_line = str(adaptive.get("preview_line") or "").strip()
            base_tip = str(payload.get("tip") or "").strip()
            if tip_line:
                payload["tip"] = f"{base_tip} {tip_line}".strip() if base_tip else tip_line
            if preview_line:
                payload["preemptive_preview"] = preview_line
            s["payload"] = payload
    done_count = sum(1 for x in steps_out if x.get("completed_at") is not None)
    total = len(steps_out)
    nic_carrier = "PG"
    nic_base_saved = 100.0
    tone_profile_saved = "tvw"
    nicotine_strictness_saved = NICOTINE_STRICTNESS_PERSONAL
    try:
        rj = session_meta if session_meta else {}
        if isinstance(rj, dict):
            c = str(rj.get("nicotine_carrier") or "PG").strip().upper()
            nic_carrier = c if c in ("PG", "VG") else "PG"
            nic_base_saved = float(rj.get("nic_base_mg_ml") or 100.0)
            tp = str(rj.get("tone_profile") or "tvw").strip().lower()
            tone_profile_saved = "lab" if tp == "lab" else "tvw"
            nicotine_strictness_saved = normalize_nicotine_strictness(rj.get("nicotine_strictness_mode"))
    except (TypeError, ValueError):
        pass
    coach_summary = build_live_coach_summary(
        tone_profile=tone_profile_saved,
        performance_summary=perf_summary,
        session_seed=f"{session_id}:{done_count}:{total}",
    )
    user_notes, user_notes_updated_at = _user_notes_from_meta(session_meta)
    batch_ctx = _session_batch_context(
        db, int(session_id), session_row=session_row, meta=session_meta
    )
    mix_summary = None
    should_build_summary = include_mix_summary if include_mix_summary is not None else (total > 0 and done_count >= total)
    span["mix_summary_rebuilt"] = bool(should_build_summary)
    if should_build_summary:
        mix_summary = _build_guided_mix_summary(
            db,
            int(session_id),
            session_row=session_row,
            recipe_name=recipe_row["name"] if recipe_row else None,
            performance_summary=perf_summary,
            coach_summary=coach_summary,
            include_raw_event_log=not for_client,
        )
    bundle: dict[str, Any] = {
        "id": int(session_row["id"]),
        "user_id": int(session_row["user_id"]),
        "recipe_version_id": int(session_row["recipe_version_id"]),
        "mode": session_row["mode"],
        "target_ml": float(session_row["target_ml"] or 0.0),
        "batch_size_ml": float(batch_ctx["batch_size_ml"]),
        "target_nic": float(session_row["target_nic"] or 0.0),
        "target_pgvg": session_row["target_pgvg"],
        "target_pg_percent": float(batch_ctx["target_pg_percent"]),
        "target_vg_percent": float(batch_ctx["target_vg_percent"]),
        "nicotine_carrier": nic_carrier,
        "nic_base_mg_ml": nic_base_saved,
        "nicotine_strictness_mode": nicotine_strictness_saved,
        "created_at": session_row["created_at"],
        "completed_at": session_row["completed_at"],
        "steps": steps_out,
        "recipe_id": recipe_id,
        "recipe_name": recipe_row["name"] if recipe_row else None,
        "recipe_slug": recipe_row.get("slug") if recipe_row else None,
        "version_number": version_row["version_number"] if version_row else None,
        "progress": {
            "completed_steps": done_count,
            "total_steps": total,
            "current_step_no": open_step_no,
            "all_done": total > 0 and done_count >= total,
        },
        "performance_summary": perf_summary,
        "coach_summary": coach_summary,
        "mix_summary": mix_summary,
        "user_notes": user_notes,
        "user_notes_updated_at": user_notes_updated_at,
        "production_job_id": (
            int(session_row["production_job_id"])
            if "production_job_id" in session_row.keys() and session_row["production_job_id"] is not None
            else None
        ),
        "production_job_recipe_id": (
            int(session_row["production_job_recipe_id"])
            if "production_job_recipe_id" in session_row.keys() and session_row["production_job_recipe_id"] is not None
            else None
        ),
        "production_job": None,
    }
    try:
        from services import production_job_service

        bundle["production_job"] = production_job_service.production_context_for_session(int(session_id))
    except Exception:
        bundle["production_job"] = None
    if not for_client:
        bundle["result_json"] = session_row["result_json"]
    return bundle


def complete_guided_step(
    *,
    session_id: int,
    user_id: int,
    step_no: int,
    undo: bool = False,
) -> bool:
    outcome = complete_guided_step_with_policy(
        session_id=session_id,
        user_id=user_id,
        step_no=step_no,
        undo=undo,
        policy=tvw_mix_policy(),
    )
    return bool(outcome.get("ok"))


def complete_guided_step_with_policy(
    *,
    session_id: int,
    user_id: int,
    step_no: int,
    undo: bool = False,
    measured_grams: float | None = None,
    policy=None,
    accept_under: bool = False,
    under_action: str | None = None,
    nicotine_action: str | None = None,
) -> dict[str, Any]:
    db = get_db()
    if policy is None:
        policy = tvw_mix_policy()
    own = db.execute(
        "SELECT id FROM mix_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not own:
        return {
            "ok": False,
            "message": "Mix session not found.",
            "policy": {
                "name": policy.name,
                "allowed": False,
                "decision": "session_missing",
                "warnings": [],
                "hard_stops": ["Mix session not found."],
            },
        }
    next_open = db.execute(
        """
        SELECT step_no
        FROM mix_session_steps
        WHERE mix_session_id = ? AND completed_at IS NULL
        ORDER BY step_no ASC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    next_open_step_no = int(next_open["step_no"]) if next_open else None
    progression = evaluate_guided_step_progression(
        policy=policy,
        requested_step_no=int(step_no),
        next_open_step_no=next_open_step_no,
        undo=bool(undo),
    )
    inserted_step_nos = _append_policy_driven_dynamic_steps(
        db=db,
        session_id=int(session_id),
        policy_name=str(getattr(policy, "name", "")),
        warnings=list(progression.warnings),
        hard_stops=list(progression.hard_stops),
    )
    if not progression.allowed:
        db.commit()
        return {
            "ok": False,
            "message": progression.hard_stops[0] if progression.hard_stops else "Step progression blocked.",
            "inserted_step_nos": inserted_step_nos,
            "performance_summary": _session_performance_summary(db, int(session_id)),
            "policy": {
                "name": policy.name,
                "allowed": False,
                "decision": progression.decision,
                "warnings": list(progression.warnings),
                "hard_stops": list(progression.hard_stops),
            },
        }
    # Persist dynamic rows before any early exit (reading_required, missing step, etc.).
    db.commit()
    step_row = db.execute(
        """
        SELECT id, step_no, step_type, payload_json, completed_at
        FROM mix_session_steps
        WHERE mix_session_id = ? AND step_no = ?
        LIMIT 1
        """,
        (int(session_id), int(step_no)),
    ).fetchone()
    if not step_row:
        return {
            "ok": False,
            "message": "Guided step not found.",
            "inserted_step_nos": inserted_step_nos,
            "policy": {
                "name": policy.name,
                "allowed": False,
                "decision": "step_missing",
                "warnings": [],
                "hard_stops": ["Guided step not found."],
            },
        }
    target_step_id = int(step_row["id"])
    step_payload: dict[str, Any] = {}
    if step_row and step_row["payload_json"]:
        try:
            step_payload = json.loads(step_row["payload_json"]) if isinstance(step_row["payload_json"], str) else {}
        except (json.JSONDecodeError, TypeError):
            step_payload = {}
    reading_eval: dict[str, Any] | None = None
    if not undo and str((step_payload or {}).get("step_type") or "").lower() == "add_ingredient":
        action = str(under_action or "").strip().lower()
        if action and bool((step_payload or {}).get("under_decision_pending")):
            try:
                stored_measured = float(
                    measured_grams
                    if measured_grams is not None
                    else (step_payload or {}).get("measured_grams")
                    or 0.0
                )
            except (TypeError, ValueError):
                stored_measured = 0.0
            display_target = float(
                (step_payload or {}).get("display_target_grams")
                or _display_target_grams(
                    float((step_payload or {}).get("target_grams") or (step_payload or {}).get("grams") or 0.0)
                )
            )
            under_by = max(0.0, display_target - round(stored_measured, 2))
            classification = str((step_payload or {}).get("under_classification") or "large_under_target")
            if action == "save_partial":
                step_payload["partial_grams"] = float(stored_measured)
                step_payload["under_pour_mode"] = "change_bottle"
                step_payload["measured_grams"] = float(stored_measured)
            elif action in {"continue_pouring", "ack_change_bottle"}:
                step_payload["under_pour_mode"] = "continue"
                step_payload["measured_grams"] = float(stored_measured)
            else:
                return {
                    "ok": False,
                    "message": "Unknown under-pour action.",
                    "inserted_step_nos": inserted_step_nos,
                    "policy": {
                        "name": policy.name,
                        "allowed": False,
                        "decision": "invalid_under_action",
                        "warnings": [],
                        "hard_stops": ["Unknown under-pour action."],
                    },
                }
            step_payload["under_decision_pending"] = True
            db.execute(
                """
                UPDATE mix_session_steps
                SET payload_json = ?
                WHERE id = ? AND mix_session_id = ?
                """,
                (json.dumps(step_payload), int(target_step_id), int(session_id)),
            )
            db.commit()
            reading_eval = {
                "target_grams": float((step_payload or {}).get("target_grams") or 0.0),
                "display_target_grams": float(display_target),
                "measured_grams": float(stored_measured),
                "display_measured_grams": round(stored_measured, 2),
                "remaining_grams": float(under_by),
                "needed_grams": float(under_by),
                "partial_grams": float(step_payload.get("partial_grams") or 0.0),
                "under_pour_mode": str(step_payload.get("under_pour_mode") or ""),
                "classification": classification,
            }
            return {
                "ok": True,
                "message": "Under-pour flow updated.",
                "awaiting_under_decision": True,
                "under_pour_active": True,
                "inserted_step_nos": inserted_step_nos,
                "performance_summary": _session_performance_summary(db, int(session_id)),
                "reading": reading_eval,
                "policy": {
                    "name": policy.name,
                    "allowed": True,
                    "decision": classification,
                    "warnings": [],
                    "hard_stops": [],
                },
            }

        nicotine_skip_classification = False
        nic_action = str(nicotine_action or "").strip().lower()
        nic_strictness = _session_nicotine_strictness(db, int(session_id))

        def _persist_step_payload() -> None:
            db.execute(
                """
                UPDATE mix_session_steps
                SET payload_json = ?
                WHERE id = ? AND mix_session_id = ?
                """,
                (json.dumps(step_payload), int(target_step_id), int(session_id)),
            )
            db.commit()

        def _nicotine_pour_back_response(*, reading: dict[str, Any], message: str) -> dict[str, Any]:
            return _nicotine_recovery_response(
                policy=policy,
                session_id=int(session_id),
                inserted_step_nos=inserted_step_nos,
                reading_eval=reading,
                message=message,
                decision="nicotine_overpour_pending",
                warnings=[
                    "Stop pouring.",
                    "Pour the nicotine back into its original bottle.",
                    "Put the mixing bottle back on the scale.",
                    "Do not tare the scale.",
                    "Some nicotine may remain in the mixing bottle. If you tare now, the next pour can make the dose too strong.",
                ],
            )

        if (
            not undo
            and _is_nicotine_payload(step_payload or {})
            and nic_action == "cancel_accept"
            and bool((step_payload or {}).get("nicotine_accept_confirm_pending"))
        ):
            display_target = float(
                (step_payload or {}).get("display_target_grams")
                or _display_target_grams(
                    float((step_payload or {}).get("target_grams") or (step_payload or {}).get("grams") or 0.0)
                )
            )
            measured_stored = float((step_payload or {}).get("measured_grams") or 0.0)
            over_by = max(0.0, round(measured_stored, 2) - display_target)
            step_payload.pop("nicotine_accept_confirm_pending", None)
            step_payload["nicotine_over_decision_pending"] = True
            _persist_step_payload()
            reading_eval = {
                "display_target_grams": float(display_target),
                "measured_grams": float(measured_stored),
                "display_measured_grams": round(measured_stored, 2),
                "over_by_grams": float(over_by),
                "classification": "nicotine_over_decision",
                "nicotine_strictness_mode": nic_strictness,
                "nicotine_accept_allowed": bool((step_payload or {}).get("nicotine_accept_allowed")),
                "nicotine_over_tier": str((step_payload or {}).get("nicotine_over_tier") or ""),
            }
            return _nicotine_recovery_response(
                policy=policy,
                session_id=int(session_id),
                inserted_step_nos=inserted_step_nos,
                reading_eval=reading_eval,
                message="Choose to accept the overpour or fix the nicotine dose.",
                decision="nicotine_over_decision",
                warnings=[str((step_payload or {}).get("nicotine_over_warning") or "")],
                awaiting_over_decision=True,
            )

        if (
            not undo
            and _is_nicotine_payload(step_payload or {})
            and nic_action == "accept_over"
            and bool((step_payload or {}).get("nicotine_over_decision_pending"))
        ):
            if not bool((step_payload or {}).get("nicotine_accept_allowed")):
                return {
                    "ok": False,
                    "message": "Nicotine accept is not allowed for this overpour in the current strictness mode.",
                    "inserted_step_nos": inserted_step_nos,
                    "policy": {
                        "name": policy.name,
                        "allowed": False,
                        "decision": "nicotine_accept_blocked",
                        "warnings": [],
                        "hard_stops": ["Client/lab mode: nicotine over tolerance must be corrected."],
                    },
                }
            display_target = float(
                (step_payload or {}).get("display_target_grams")
                or _display_target_grams(
                    float((step_payload or {}).get("target_grams") or (step_payload or {}).get("grams") or 0.0)
                )
            )
            measured_stored = float((step_payload or {}).get("measured_grams") or 0.0)
            over_by = max(0.0, round(measured_stored, 2) - display_target)
            step_payload.pop("nicotine_over_decision_pending", None)
            step_payload["nicotine_accept_confirm_pending"] = True
            _persist_step_payload()
            reading_eval = {
                "display_target_grams": float(display_target),
                "measured_grams": float(measured_stored),
                "display_measured_grams": round(measured_stored, 2),
                "over_by_grams": float(over_by),
                "classification": "nicotine_accept_confirm",
                "nicotine_strictness_mode": nic_strictness,
            }
            return _nicotine_recovery_response(
                policy=policy,
                session_id=int(session_id),
                inserted_step_nos=inserted_step_nos,
                reading_eval=reading_eval,
                message="Confirm accept nicotine over target.",
                decision="nicotine_accept_confirm",
                warnings=[
                    "Accept nicotine over target?",
                    f"Target: {display_target:.2f} g",
                    f"Actual: {round(measured_stored, 2):.2f} g",
                    f"Over by: {over_by:.2f} g",
                    "Final nicotine strength may be higher than the recipe target.",
                    "This will be saved in the batch log.",
                ],
            )

        if (
            not undo
            and _is_nicotine_payload(step_payload or {})
            and nic_action == "fix_dose"
            and bool((step_payload or {}).get("nicotine_over_decision_pending"))
        ):
            display_target = float(
                (step_payload or {}).get("display_target_grams")
                or _display_target_grams(
                    float((step_payload or {}).get("target_grams") or (step_payload or {}).get("grams") or 0.0)
                )
            )
            measured_stored = float((step_payload or {}).get("measured_grams") or 0.0)
            over_by = max(0.0, round(measured_stored, 2) - display_target)
            step_payload.pop("nicotine_over_decision_pending", None)
            step_payload["nicotine_overpour_pending"] = True
            _log_nicotine_over_decision(
                db,
                int(session_id),
                strictness=nic_strictness,
                target_grams=display_target,
                actual_grams=measured_stored,
                over_by_grams=over_by,
                action="nicotine_recovery_started",
                warning=str((step_payload or {}).get("nicotine_over_warning") or ""),
                accept_allowed=bool((step_payload or {}).get("nicotine_accept_allowed")),
            )
            _persist_step_payload()
            reading_eval = {
                "display_target_grams": float(display_target),
                "measured_grams": float(measured_stored),
                "over_by_grams": float(over_by),
                "classification": "nicotine_over_target",
                "nicotine_strictness_mode": nic_strictness,
            }
            return _nicotine_pour_back_response(
                reading=reading_eval,
                message="Fix nicotine dose — pour back required.",
            )

        if (
            not undo
            and _is_nicotine_payload(step_payload or {})
            and nic_action == "confirm_accept_over"
            and bool((step_payload or {}).get("nicotine_accept_confirm_pending"))
        ):
            display_target = float(
                (step_payload or {}).get("display_target_grams")
                or _display_target_grams(
                    float((step_payload or {}).get("target_grams") or (step_payload or {}).get("grams") or 0.0)
                )
            )
            measured_stored = float((step_payload or {}).get("measured_grams") or 0.0)
            display_measured = round(measured_stored, 2)
            over_by = max(0.0, display_measured - display_target)
            target = float((step_payload or {}).get("target_grams") or (step_payload or {}).get("grams") or 0.0)
            _log_nicotine_over_decision(
                db,
                int(session_id),
                strictness=nic_strictness,
                target_grams=display_target,
                actual_grams=measured_stored,
                over_by_grams=over_by,
                action="accepted_nicotine_over",
                warning=str((step_payload or {}).get("nicotine_over_warning") or ""),
                accept_allowed=bool((step_payload or {}).get("nicotine_accept_allowed")),
            )
            for key in (
                "nicotine_accept_confirm_pending",
                "nicotine_over_decision_pending",
                "nicotine_overpour_pending",
                "nicotine_recovery_mode",
                "nicotine_poured_back",
                "nicotine_overpour_detected_at_grams",
                "recovery_current_scale_grams",
                "nicotine_over_tier",
                "nicotine_accept_allowed",
                "nicotine_over_warning",
            ):
                step_payload.pop(key, None)
            step_payload["measured_grams"] = float(measured_stored)
            step_payload["display_target_grams"] = float(display_target)
            step_payload["target_grams"] = float(target)
            step_payload["delta_grams"] = float(measured_stored - display_target)
            dens = _density_from_step_payload(step_payload)
            if dens and dens > 0:
                step_payload["density"] = float(dens)
                step_payload["measured_ml"] = float(measured_stored) / dens
                step_payload["actual_ml"] = float(measured_stored) / dens
            _stamp_pour_pending_bottle_move(step_payload)
            _persist_step_payload()
            db.execute(
                """
                UPDATE mix_session_steps
                SET completed_at = datetime('now')
                WHERE id = ? AND mix_session_id = ?
                """,
                (int(target_step_id), int(session_id)),
            )
            open_row = db.execute(
                """
                SELECT COUNT(1) AS c
                FROM mix_session_steps
                WHERE mix_session_id = ? AND completed_at IS NULL
                """,
                (session_id,),
            ).fetchone()
            if int(open_row["c"] or 0) <= 0:
                db.execute(
                    "UPDATE mix_sessions SET completed_at = datetime('now') WHERE id = ? AND user_id = ?",
                    (session_id, user_id),
                )
                session_just_completed = True
            else:
                db.execute(
                    "UPDATE mix_sessions SET completed_at = NULL WHERE id = ? AND user_id = ?",
                    (session_id, user_id),
                )
                session_just_completed = False
            db.commit()
            if session_just_completed:
                _notify_production_job_session_completed(int(session_id))
            reading_eval = {
                "target_grams": float(target),
                "display_target_grams": float(display_target),
                "measured_grams": float(measured_stored),
                "display_measured_grams": float(display_measured),
                "over_by_grams": float(over_by),
                "classification": "nicotine_over_accepted",
                "nicotine_strictness_mode": nic_strictness,
            }
            return {
                "ok": True,
                "message": "Nicotine over accepted and logged.",
                "inserted_step_nos": inserted_step_nos,
                "performance_summary": _session_performance_summary(db, int(session_id)),
                "reading": reading_eval,
                "policy": {
                    "name": policy.name,
                    "allowed": True,
                    "decision": "nicotine_over_accepted",
                    "warnings": ["Nicotine over accepted. Final strength may be higher than target."],
                    "hard_stops": [],
                },
            }

        if (
            not undo
            and _is_nicotine_payload(step_payload or {})
            and nic_action == "poured_back"
            and bool((step_payload or {}).get("nicotine_overpour_pending"))
        ):
            display_target = float(
                (step_payload or {}).get("display_target_grams")
                or _display_target_grams(
                    float((step_payload or {}).get("target_grams") or (step_payload or {}).get("grams") or 0.0)
                )
            )
            step_payload["nicotine_overpour_pending"] = False
            step_payload["nicotine_recovery_mode"] = True
            step_payload["nicotine_poured_back"] = True
            step_payload.pop("under_decision_pending", None)
            step_payload.pop("under_pour_mode", None)
            step_payload.pop("under_classification", None)
            _append_nicotine_recovery_log(
                db,
                int(session_id),
                {
                    "event": "nicotine_poured_back",
                    "step_no": int(step_no),
                    "display_target_grams": float(display_target),
                },
            )
            _bump_session_performance(db, int(session_id), recovery_count=1)
            db.execute(
                """
                UPDATE mix_session_steps
                SET payload_json = ?
                WHERE id = ? AND mix_session_id = ?
                """,
                (json.dumps(step_payload), int(target_step_id), int(session_id)),
            )
            db.commit()
            reading_eval = {
                "display_target_grams": float(display_target),
                "target_grams": float((step_payload or {}).get("target_grams") or 0.0),
                "classification": "nicotine_recovery_active",
                "nicotine_recovery_mode": True,
                "needed_grams": float(display_target),
            }
            return _nicotine_recovery_response(
                policy=policy,
                session_id=int(session_id),
                inserted_step_nos=inserted_step_nos,
                reading_eval=reading_eval,
                message="Nicotine recovery mode — do not tare. Enter the current scale reading.",
                decision="nicotine_recovery_active",
                warnings=[
                    "Do not tare.",
                    "Some nicotine may remain in the mixing bottle. If you tare now, the next pour can make the dose too strong.",
                ],
            )

        if measured_grams is None:
            return {
                "ok": False,
                "message": "Enter measured grams before completing this ingredient step.",
                "inserted_step_nos": inserted_step_nos,
                "policy": {
                    "name": policy.name,
                    "allowed": False,
                    "decision": "reading_required",
                    "warnings": [],
                    "hard_stops": ["Measured reading is required for ingredient steps."],
                },
            }
        target = float(step_payload.get("target_grams") or step_payload.get("grams") or 0.0)
        batch_ctx = _session_batch_context(db, int(session_id))
        vessel_size_ml = float(batch_ctx.get("batch_size_ml") or 0.0)
        batch_size_ml = vessel_size_ml
        target_pg_percent = float(batch_ctx.get("target_pg_percent") or 30.0)
        target_vg_percent = float(batch_ctx.get("target_vg_percent") or 70.0)
        ingredient_kind = _ingredient_kind(step_payload or {})
        under_tolerance = scaled_pour_tolerance_grams(
            policy=policy,
            target_grams=target,
            vessel_size_ml=vessel_size_ml,
            direction="under",
        )
        over_tolerance = scaled_pour_tolerance_grams(
            policy=policy,
            target_grams=target,
            vessel_size_ml=vessel_size_ml,
            direction="over",
        )
        ingredient_tol_under = ingredient_pour_tolerance_grams(
            ingredient_category=ingredient_kind,
            target_grams=target,
            batch_size_ml=batch_size_ml,
            policy=policy,
            direction="under",
        )
        ingredient_tol_over = ingredient_pour_tolerance_grams(
            ingredient_category=ingredient_kind,
            target_grams=target,
            batch_size_ml=batch_size_ml,
            policy=policy,
            direction="over",
        )
        measured = float(measured_grams or 0.0)
        display_target = _display_target_grams(target)
        display_measured = round(measured, 2)
        # Bench notes use the same 0.01 g rounding the mixer sees on the card / scale LCD.
        under_by = display_target - display_measured
        over_by = display_measured - display_target
        # Compare against displayed 0.01 g target so hidden decimals (e.g. 0.314 → 0.31) don't punish.
        delta = measured - display_target
        ratio = (measured / display_target) if display_target > 0 else 0.0
        reading_eval = {
            "target_grams": float(target),
            "display_target_grams": float(display_target),
            "measured_grams": float(measured),
            "display_measured_grams": float(display_measured),
            "delta_grams": float(delta),
            "under_tolerance_grams": float(under_tolerance),
            "over_tolerance_grams": float(over_tolerance),
            "ingredient_tolerance_grams": float(ingredient_tol_over),
            "batch_size_ml": float(batch_size_ml),
            "target_pg_percent": float(target_pg_percent),
            "target_vg_percent": float(target_vg_percent),
            "classification": "within_tolerance",
        }
        if target < 0:
            return {
                "ok": False,
                "message": "Invalid target grams for this guided step.",
                "inserted_step_nos": inserted_step_nos,
                "policy": {
                    "name": policy.name,
                    "allowed": False,
                    "decision": "invalid_target",
                    "warnings": [],
                    "hard_stops": ["Target grams cannot be negative for ingredient steps."],
                },
                "reading": reading_eval,
            }

        # Zero / near-zero required amount: auto-satisfy — never prompt underpour.
        if display_target <= ZERO_POUR_GRAMS_EPS or target <= ZERO_POUR_GRAMS_EPS:
            reading_eval["classification"] = "within_tolerance"
            reading_eval["measured_grams"] = float(measured)
            reading_eval["display_measured_grams"] = float(display_measured)
            if step_payload is not None:
                step_payload["measured_grams"] = float(measured)
                step_payload["target_grams"] = float(target)
                step_payload["display_target_grams"] = float(display_target)
                step_payload["delta_grams"] = float(delta)
                step_payload.pop("under_decision_pending", None)
                step_payload.pop("under_pour_mode", None)
                step_payload.pop("under_classification", None)
                dens = _density_from_step_payload(step_payload)
                if dens and dens > 0:
                    step_payload["density"] = float(dens)
                    step_payload["measured_ml"] = float(measured) / dens
                    step_payload["actual_ml"] = float(measured) / dens
                db.execute(
                    """
                    UPDATE mix_session_steps
                    SET payload_json = ?, completed_at = datetime('now')
                    WHERE id = ? AND mix_session_id = ?
                    """,
                    (json.dumps(step_payload), int(target_step_id), int(session_id)),
                )
                db.commit()
            return {
                "ok": True,
                "message": "Zero-requirement step satisfied.",
                "inserted_step_nos": inserted_step_nos,
                "performance_summary": _session_performance_summary(db, int(session_id)),
                "reading": reading_eval,
                "policy": {
                    "name": policy.name,
                    "allowed": True,
                    "decision": "within_tolerance",
                    "warnings": [],
                    "hard_stops": [],
                },
            }

        if (
            not undo
            and _is_nicotine_payload(step_payload or {})
            and bool((step_payload or {}).get("nicotine_recovery_mode"))
        ):
            step_payload["recovery_current_scale_grams"] = float(measured)
            step_payload["measured_grams"] = float(measured)
            step_payload["display_target_grams"] = float(display_target)
            _append_nicotine_recovery_log(
                db,
                int(session_id),
                {
                    "event": "recovery_current_scale_grams",
                    "step_no": int(step_no),
                    "grams": float(measured),
                    "display_grams": float(display_measured),
                },
            )
            reading_eval["recovery_current_scale_grams"] = float(measured)
            reading_eval["needed_grams"] = float(max(0.0, under_by))
            reading_eval["remaining_grams"] = float(max(0.0, under_by))
            if over_by > 0.0005:
                reading_eval["classification"] = "nicotine_still_over_target"
                db.execute(
                    """
                    UPDATE mix_session_steps
                    SET payload_json = ?
                    WHERE id = ? AND mix_session_id = ?
                    """,
                    (json.dumps(step_payload), int(target_step_id), int(session_id)),
                )
                db.commit()
                return _nicotine_recovery_response(
                    policy=policy,
                    session_id=int(session_id),
                    inserted_step_nos=inserted_step_nos,
                    reading_eval=reading_eval,
                    message="Still over nicotine target after pour-back.",
                    decision="nicotine_still_over_target",
                    warnings=[
                        "Still over target.",
                        "Pour a little more back into the nicotine bottle, then place the mixing bottle back on the scale.",
                        "Do not tare.",
                    ],
                )
            if under_by > 0.0005 and not accept_under:
                classification, under_msg = _classify_nicotine_under_pour(
                    display_target=display_target,
                    measured=measured,
                )
                reading_eval["classification"] = classification
                step_payload["under_decision_pending"] = True
                step_payload["under_classification"] = classification
                step_payload["under_pour_mode"] = (
                    "decision" if classification == "slight_under_target" else "continue"
                )
                db.execute(
                    """
                    UPDATE mix_session_steps
                    SET payload_json = ?
                    WHERE id = ? AND mix_session_id = ?
                    """,
                    (json.dumps(step_payload), int(target_step_id), int(session_id)),
                )
                db.commit()
                return _nicotine_recovery_response(
                    policy=policy,
                    session_id=int(session_id),
                    inserted_step_nos=inserted_step_nos,
                    reading_eval=reading_eval,
                    message="Nicotine under target — add more from the current scale reading or accept the lower dose.",
                    decision=classification,
                    warnings=[under_msg] if under_msg else [],
                    awaiting_under=True,
                )
            _append_nicotine_recovery_log(
                db,
                int(session_id),
                {
                    "event": "nicotine_recovery_complete",
                    "step_no": int(step_no),
                    "final_accepted_nicotine_grams": float(measured),
                    "display_grams": float(display_measured),
                    "warning": "Nicotine recovery used. Scale was not tared.",
                },
            )
            step_payload.pop("nicotine_recovery_mode", None)
            step_payload.pop("nicotine_overpour_pending", None)
            step_payload.pop("nicotine_poured_back", None)
            step_payload.pop("nicotine_overpour_detected_at_grams", None)
            step_payload.pop("recovery_current_scale_grams", None)
            step_payload.pop("under_decision_pending", None)
            step_payload.pop("under_pour_mode", None)
            step_payload.pop("under_classification", None)
            nicotine_skip_classification = True
            reading_eval["classification"] = "within_tolerance"
            _bump_session_performance(db, int(session_id), recovery_count=1)

        if not nicotine_skip_classification and over_by > 0.0005:
            reading_eval["classification"] = "over_target"
            over_limit = ingredient_over_requires_recovery(
                ingredient_category=ingredient_kind,
                display_target=display_target,
                measured=display_measured,
                batch_size_ml=batch_size_ml,
                policy=policy,
            )
            if over_limit:
                reading_eval["classification"] = "over_target_recovery_required"
            dens = _density_from_step_payload(step_payload or {})
            target_ml = None
            measured_ml = None
            try:
                target_ml_val = float((step_payload or {}).get("ml") or (step_payload or {}).get("target_ml") or 0.0)
                if target_ml_val > 0:
                    target_ml = target_ml_val
            except (TypeError, ValueError):
                target_ml = None
            if dens and dens > 0:
                measured_ml = float(measured) / dens
                if target_ml is None and display_target > 0:
                    target_ml = float(display_target) / dens

            if _is_nicotine_payload(step_payload or {}):
                if bool((step_payload or {}).get("nicotine_accept_confirm_pending")):
                    return _nicotine_recovery_response(
                        policy=policy,
                        session_id=int(session_id),
                        inserted_step_nos=inserted_step_nos,
                        reading_eval=reading_eval,
                        message="Confirm accept nicotine over target.",
                        decision="nicotine_accept_confirm",
                        warnings=[
                            "Accept nicotine over target?",
                            f"Target: {display_target:.2f} g",
                            f"Actual: {display_measured:.2f} g",
                            f"Over by: {over_by:.2f} g",
                            "Final nicotine strength may be higher than the recipe target.",
                            "This will be saved in the batch log.",
                        ],
                    )
                if bool((step_payload or {}).get("nicotine_over_decision_pending")):
                    return _nicotine_recovery_response(
                        policy=policy,
                        session_id=int(session_id),
                        inserted_step_nos=inserted_step_nos,
                        reading_eval=reading_eval,
                        message="Choose to accept the overpour or fix the nicotine dose.",
                        decision="nicotine_over_decision",
                        warnings=[str((step_payload or {}).get("nicotine_over_warning") or "")],
                        awaiting_over_decision=True,
                    )
                if bool((step_payload or {}).get("nicotine_overpour_pending")):
                    _persist_step_payload()
                    return _nicotine_pour_back_response(
                        reading=reading_eval,
                        message="Nicotine over target — pour back before continuing.",
                    )

                over_policy = evaluate_nicotine_overpour_policy(
                    strictness=nic_strictness,
                    over_by=over_by,
                )
                step_payload["measured_grams"] = float(measured)
                step_payload["display_target_grams"] = float(display_target)
                step_payload["target_grams"] = float(target)
                step_payload["nicotine_over_tier"] = str(over_policy.get("tier") or "")
                step_payload["nicotine_accept_allowed"] = bool(over_policy.get("accept_allowed"))
                step_payload["nicotine_over_warning"] = str(over_policy.get("warning") or "")
                step_payload["nicotine_strictness_mode"] = nic_strictness
                _log_nicotine_over_decision(
                    db,
                    int(session_id),
                    strictness=nic_strictness,
                    target_grams=display_target,
                    actual_grams=measured,
                    over_by_grams=over_by,
                    action="nicotine_overpour_detected",
                    warning=str(over_policy.get("warning") or ""),
                    accept_allowed=bool(over_policy.get("accept_allowed")),
                )
                _bump_session_performance(db, int(session_id), overshoot_count=1)
                reading_eval["classification"] = "nicotine_over_target"
                reading_eval["over_by_grams"] = float(over_by)
                reading_eval["nicotine_strictness_mode"] = nic_strictness
                reading_eval["nicotine_over_tier"] = str(over_policy.get("tier") or "")
                reading_eval["nicotine_accept_allowed"] = bool(over_policy.get("accept_allowed"))

                if bool(over_policy.get("recovery_required")):
                    step_payload["nicotine_overpour_pending"] = True
                    step_payload["nicotine_overpour_detected_at_grams"] = float(measured)
                    _bump_session_performance(db, int(session_id), recovery_count=1)
                    _persist_step_payload()
                    warnings = [str(over_policy.get("warning") or "")]
                    if (
                        not bool(over_policy.get("accept_allowed"))
                        and nic_strictness == NICOTINE_STRICTNESS_LAB_CLIENT
                    ):
                        warnings.append("Client/lab mode: nicotine over tolerance must be corrected.")
                    warnings.extend([
                        "Stop pouring.",
                        "Pour the nicotine back into its original bottle.",
                        "Put the mixing bottle back on the scale.",
                        "Do not tare the scale.",
                    ])
                    return _nicotine_recovery_response(
                        policy=policy,
                        session_id=int(session_id),
                        inserted_step_nos=inserted_step_nos,
                        reading_eval=reading_eval,
                        message="Nicotine over target — correction required.",
                        decision="nicotine_overpour_pending",
                        warnings=warnings,
                    )

                step_payload["nicotine_over_decision_pending"] = True
                _persist_step_payload()
                reading_eval["classification"] = "nicotine_over_decision"
                warn_lines = [ln for ln in str(over_policy.get("warning") or "").split("\n") if ln.strip()]
                return _nicotine_recovery_response(
                    policy=policy,
                    session_id=int(session_id),
                    inserted_step_nos=inserted_step_nos,
                    reading_eval=reading_eval,
                    message="Nicotine over target — accept or fix the dose.",
                    decision="nicotine_over_decision",
                    warnings=warn_lines,
                    awaiting_over_decision=True,
                )

            if not over_limit:
                # Controlled slight over: flavour may trim PG; carriers use flexible tolerance.
                base_adjusted = False
                if ingredient_kind == "flavor":
                    base_adjusted = _soft_adjust_remaining_base_for_overpour(
                        db=db,
                        session_id=int(session_id),
                        excess_grams=float(over_by),
                        pour_density=dens,
                        exclude_step_id=int(target_step_id),
                        batch_size_ml=batch_size_ml,
                        target_pg_percent=target_pg_percent,
                        target_vg_percent=target_vg_percent,
                        source_ingredient=str((step_payload or {}).get("name") or ""),
                    )
                    if base_adjusted:
                        _append_mix_event_log(
                            db,
                            int(session_id),
                            {
                                "event": "accepted_flavour_over",
                                "ingredient": str((step_payload or {}).get("name") or ""),
                                "target_grams": float(display_target),
                                "measured_grams": float(measured),
                                "over_by_grams": float(over_by),
                                "batch_size_ml": float(batch_size_ml),
                                "pg_adjusted": True,
                            },
                        )
                classification, over_msg = _classify_over_pour_for_step(
                    payload=step_payload or {},
                    display_target=display_target,
                    measured=measured,
                    batch_size_ml=batch_size_ml,
                    policy=policy,
                    base_adjusted=base_adjusted,
                )
                reading_eval["classification"] = classification
                reading_eval["base_adjusted"] = bool(base_adjusted)
                reading_eval["over_by_grams"] = float(over_by)
                perf_inc = {"overshoot_count": 1}
                if over_by <= float(over_tolerance) * 1.5:
                    perf_inc["near_miss_count"] = 1
                _bump_session_performance(db, int(session_id), **perf_inc)
                progression = type(progression)(
                    allowed=True,
                    decision=classification,
                    warnings=tuple(list(progression.warnings) + ([over_msg] if over_msg else [])),
                    hard_stops=(),
                )
                if over_msg:
                    _attach_checkpoint_note_to_next_transition(
                        db=db,
                        session_id=int(session_id),
                        after_step_no=int(step_no),
                        note=over_msg,
                        note_kind="slight_over",
                    )
            else:
                perf_inc = {"overshoot_count": 1}
                if over_by <= float(over_tolerance) * 1.5:
                    perf_inc["near_miss_count"] = 1
                _bump_session_performance(db, int(session_id), **perf_inc)
                context = {
                    "ingredient_id": step_payload.get("ingredient_id"),
                    "ingredient_name": str(step_payload.get("name") or ""),
                    "target_amount": float(display_target),
                    "measured_amount": float(measured),
                    "target_grams": float(display_target),
                    "measured_grams": float(measured),
                    "actual_grams": float(measured),
                }
                if dens:
                    context["density"] = float(dens)
                if target_ml is not None:
                    context["target_ml"] = float(target_ml)
                if measured_ml is not None:
                    context["actual_ml"] = float(measured_ml)
                    context["measured_ml"] = float(measured_ml)
                dyn_outcome = insert_dynamic_guided_event_step(
                    session_id=int(session_id),
                    user_id=int(user_id),
                    event_type="overpour",
                    message=(
                        f"Overpour on {context['ingredient_name'] or 'ingredient'}: "
                        f"target {display_target:.2f}g, measured {measured:.2f}g."
                    ),
                    detail="Reconcile required before continuing. Review correction.",
                    severity="danger",
                    tone_profile="lab" if "lab" in str(policy.name or "").lower() else "tvw",
                    context=context,
                )
                if dyn_outcome.get("inserted_step_no") is not None:
                    inserted_step_nos.append(int(dyn_outcome["inserted_step_no"]))
                if bool(getattr(policy, "overpour_hard_stop", False)):
                    db.commit()
                    return {
                        "ok": False,
                        "message": "Over target beyond strict policy threshold. Complete recovery step first.",
                        "inserted_step_nos": inserted_step_nos,
                        "performance_summary": _session_performance_summary(db, int(session_id)),
                        "policy": {
                            "name": policy.name,
                            "allowed": False,
                            "decision": "over_target_hard_stop",
                            "warnings": [],
                            "hard_stops": [
                                (
                                    f"Measured {measured:.2f}g exceeds threshold for target {display_target:.2f}g "
                                    f"({policy.name})."
                                )
                            ],
                        },
                        "reading": reading_eval,
                    }
                reading_eval["over_by_grams"] = float(over_by)
                if step_payload is not None:
                    step_payload["measured_grams"] = float(measured)
                    step_payload["target_grams"] = float(target)
                    step_payload["display_target_grams"] = float(display_target)
                    step_payload["delta_grams"] = float(delta)
                    step_payload["over_by_grams"] = float(over_by)
                    step_payload["overpour_recovery_pending"] = True
                    dens = _density_from_step_payload(step_payload)
                    if dens and dens > 0:
                        step_payload["density"] = float(dens)
                        step_payload["measured_ml"] = float(measured) / dens
                        step_payload["actual_ml"] = float(measured) / dens
                    db.execute(
                        """
                        UPDATE mix_session_steps
                        SET payload_json = ?
                        WHERE id = ? AND mix_session_id = ?
                        """,
                        (json.dumps(step_payload), int(target_step_id), int(session_id)),
                    )
                    db.commit()
                return {
                    "ok": True,
                    "message": "Serious overpour — review recovery before continuing.",
                    "awaiting_overpour_recovery": True,
                    "inserted_step_nos": inserted_step_nos,
                    "performance_summary": _session_performance_summary(db, int(session_id)),
                    "reading": reading_eval,
                    "policy": {
                        "name": policy.name,
                        "allowed": True,
                        "decision": "over_target_recovery_required",
                        "warnings": [],
                        "hard_stops": [],
                    },
                }
        elif not nicotine_skip_classification and under_by > 0.0005:
            # Under display target: guidance only — never hard-block.
            classification, under_msg = _classify_under_pour_for_step(
                payload=step_payload or {},
                display_target=display_target,
                measured=measured,
                batch_size_ml=batch_size_ml,
                policy=policy,
            )
            reading_eval["classification"] = classification
            reading_eval["remaining_grams"] = float(max(0.0, under_by))
            reading_eval["needed_grams"] = float(max(0.0, under_by))
            already_pending = bool((step_payload or {}).get("under_decision_pending"))
            if classification != "within_tolerance" and not already_pending:
                _bump_session_performance(db, int(session_id), undershoot_count=1)
            if under_msg:
                progression = type(progression)(
                    allowed=True,
                    decision=classification,
                    warnings=tuple(list(progression.warnings) + [under_msg]),
                    hard_stops=(),  # never promote under-pours to hard stops
                )
            else:
                _bump_session_performance(db, int(session_id), precise_pour_count=1)

            carrier_lenient = ingredient_kind in {"pg", "vg"} and classification in {
                "within_tolerance",
                "slight_under_target",
            }
            if carrier_lenient and under_msg:
                _append_mix_event_log(
                    db,
                    int(session_id),
                    {
                        "event": "accepted_carrier_under",
                        "ingredient": str((step_payload or {}).get("name") or ingredient_kind),
                        "target_grams": float(display_target),
                        "measured_grams": float(measured),
                        "under_by_grams": float(under_by),
                        "batch_size_ml": float(batch_size_ml),
                    },
                )
                if under_msg:
                    _attach_checkpoint_note_to_next_transition(
                        db=db,
                        session_id=int(session_id),
                        after_step_no=int(step_no),
                        note=under_msg,
                        note_kind="carrier_guidance",
                    )

            # Persist reading but do not complete until mixer accepts (or pours to target/over).
            if (
                classification != "within_tolerance"
                and not accept_under
                and not undo
                and not carrier_lenient
            ):
                if step_payload is not None:
                    step_payload["measured_grams"] = float(measured)
                    step_payload["target_grams"] = float(target)
                    step_payload["display_target_grams"] = float(display_target)
                    step_payload["delta_grams"] = float(delta)
                    step_payload["under_decision_pending"] = True
                    step_payload["under_classification"] = classification
                    if _is_large_under_classification(classification):
                        step_payload["under_pour_mode"] = "decision"
                    else:
                        step_payload.pop("under_pour_mode", None)
                        step_payload.pop("partial_grams", None)
                    dens = _density_from_step_payload(step_payload)
                    if dens and dens > 0:
                        step_payload["density"] = float(dens)
                        step_payload["measured_ml"] = float(measured) / dens
                        step_payload["actual_ml"] = float(measured) / dens
                    db.execute(
                        """
                        UPDATE mix_session_steps
                        SET payload_json = ?
                        WHERE id = ? AND mix_session_id = ?
                        """,
                        (json.dumps(step_payload), int(target_step_id), int(session_id)),
                    )
                    db.commit()
                is_large = _is_large_under_classification(classification)
                reading_eval["under_pour_mode"] = "decision" if is_large else ""
                return {
                    "ok": True,
                    "message": (
                        "Large under-pour — continue pouring, save partial, or accept underdose."
                        if is_large
                        else (
                            "Nicotine under target — add more carefully or accept the lower dose."
                            if _is_nicotine_payload(step_payload or {})
                            else "Slight under-pour — accept this pour or try one drop."
                        )
                    ),
                    "awaiting_under_decision": True,
                    "under_pour_active": True,
                    "inserted_step_nos": inserted_step_nos,
                    "performance_summary": _session_performance_summary(db, int(session_id)),
                    "reading": reading_eval,
                    "policy": {
                        "name": policy.name,
                        "allowed": True,
                        "decision": classification,
                        "warnings": list(progression.warnings),
                        "hard_stops": [],
                    },
                }
        else:
            _bump_session_performance(db, int(session_id), precise_pour_count=1)

        if step_payload is not None:
            step_payload["measured_grams"] = float(measured)
            step_payload["target_grams"] = float(target)
            step_payload["display_target_grams"] = float(display_target)
            step_payload["delta_grams"] = float(delta)
            step_payload.pop("under_decision_pending", None)
            step_payload.pop("under_pour_mode", None)
            step_payload.pop("under_classification", None)
            step_payload.pop("partial_grams", None)
            _stamp_pour_pending_bottle_move(step_payload)
            dens = _density_from_step_payload(step_payload)
            if dens and dens > 0:
                step_payload["density"] = float(dens)
                step_payload["measured_ml"] = float(measured) / dens
                step_payload["actual_ml"] = float(measured) / dens
            db.execute(
                """
                UPDATE mix_session_steps
                SET payload_json = ?
                WHERE id = ? AND mix_session_id = ?
                """,
                (json.dumps(step_payload), int(target_step_id), int(session_id)),
            )

        db.commit()

    retry_stats = SqliteRetryStats()
    result_json_before = _read_session_meta(db, int(session_id))
    result_json_bytes_before = json_byte_size(result_json_before)

    def _persist_step_completion() -> None:
        nonlocal_session_flag = {"completed": False}
        step_type = str(
            (step_payload or {}).get("step_type") or (step_row["step_type"] if step_row else "") or ""
        ).lower()
        if undo:
            if step_type == "transition":
                _undo_bottle_moved_for_transition(
                    db, session_id=int(session_id), transition_step_no=int(step_no)
                )
            db.execute(
                """
                UPDATE mix_session_steps
                SET completed_at = NULL
                WHERE mix_session_id = ? AND step_no = ?
                """,
                (session_id, int(step_no)),
            )
            db.execute(
                "UPDATE mix_sessions SET completed_at = NULL WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )
        else:
            db.execute(
                """
                UPDATE mix_session_steps
                SET completed_at = datetime('now')
                WHERE id = ? AND mix_session_id = ?
                """,
                (int(target_step_id), int(session_id)),
            )
            open_row = db.execute(
                """
                SELECT COUNT(1) AS c
                FROM mix_session_steps
                WHERE mix_session_id = ? AND completed_at IS NULL
                """,
                (session_id,),
            ).fetchone()
            if int(open_row["c"] or 0) <= 0:
                db.execute(
                    "UPDATE mix_sessions SET completed_at = datetime('now') WHERE id = ? AND user_id = ?",
                    (session_id, user_id),
                )
                nonlocal_session_flag["completed"] = True
            else:
                db.execute(
                    "UPDATE mix_sessions SET completed_at = NULL WHERE id = ? AND user_id = ?",
                    (session_id, user_id),
                )
        db.commit()
        if nonlocal_session_flag["completed"]:
            _notify_production_job_session_completed(int(session_id))

    with guided_mix_perf_span(
        "db_tx_step_completion",
        session_id=int(session_id),
        step_no=int(step_no),
        undo=bool(undo),
    ) as tx_span:
        sqlite_retry(_persist_step_completion, attempts=15, base_delay_s=0.05, stats=retry_stats)
        tx_span["sqlite_retry_count"] = max(0, retry_stats.attempts - 1)
        tx_span["sqlite_busy_retries"] = retry_stats.busy_retries
        tx_span["sqlite_busy_occurrences"] = len(retry_stats.busy_occurrences)

    result_json_after = _read_session_meta(db, int(session_id))
    result_json_bytes_after = json_byte_size(result_json_after)
    completion_step_type = str(
        (step_payload or {}).get("step_type") or (step_row["step_type"] if step_row else "") or ""
    ).lower()
    if not undo and completion_step_type == "transition":
        _confirm_bottle_moved_for_transition(
            db,
            session_id=int(session_id),
            transition_step_no=int(step_no),
            transition_payload=step_payload,
        )
        db.commit()
    recovery_payload_type = str(
        (step_payload or {}).get("step_type") or (step_row["step_type"] if step_row else "") or ""
    ).lower()
    if (
        not undo
        and recovery_payload_type == "recovery"
        and str((step_payload or {}).get("source_event") or "").lower() == "overpour"
    ):
        _complete_linked_overpour_pour_step(
            db,
            session_id=int(session_id),
            after_recovery_step_no=int(step_no),
        )
        db.commit()
    return {
        "ok": True,
        "message": "Step updated.",
        "inserted_step_nos": inserted_step_nos,
        "performance_summary": _session_performance_summary(db, int(session_id), meta=result_json_after),
        "reading": reading_eval,
        "policy": {
            "name": policy.name,
            "allowed": True,
            "decision": progression.decision,
            "warnings": list(progression.warnings),
            "hard_stops": list(progression.hard_stops),
        },
        "_perf": {
            "result_json_bytes_before": result_json_bytes_before,
            "result_json_bytes_after": result_json_bytes_after,
            "mix_event_log_entries": len(result_json_after.get("mix_event_log") or []),
            "sqlite_retry_count": max(0, retry_stats.attempts - 1),
            "sqlite_busy_retries": retry_stats.busy_retries,
        },
    }


def insert_dynamic_guided_event_step(
    *,
    session_id: int,
    user_id: int,
    event_type: str,
    message: str,
    detail: str = "",
    severity: str = "warning",
    tone_profile: str = "tvw",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Public entry point to insert event-driven runtime steps."""
    db = get_db()
    own = db.execute(
        "SELECT id FROM mix_sessions WHERE id = ? AND user_id = ?",
        (int(session_id), int(user_id)),
    ).fetchone()
    if not own:
        return {"ok": False, "message": "Mix session not found.", "inserted_step_no": None}
    event = str(event_type or "").strip().lower() or "policy_warning"
    sev = str(severity or "warning").strip().lower()
    tone = "lab" if str(tone_profile or "").strip().lower() == "lab" else "tvw"
    ctx = context if isinstance(context, dict) else {}
    ingredient_id = ctx.get("ingredient_id")
    ingredient_name = str(ctx.get("ingredient_name") or "").strip()
    try:
        target_amount = float(ctx.get("target_amount") or 0.0)
    except (TypeError, ValueError):
        target_amount = 0.0
    try:
        measured_amount = float(ctx.get("measured_amount") or 0.0)
    except (TypeError, ValueError):
        measured_amount = 0.0
    delta_amount = measured_amount - target_amount
    next_open = db.execute(
        """
        SELECT step_no
        FROM mix_session_steps
        WHERE mix_session_id = ? AND completed_at IS NULL
        ORDER BY step_no ASC
        LIMIT 1
        """,
        (int(session_id),),
    ).fetchone()
    at_no = int(next_open["step_no"]) if next_open else 10_000
    perf_summary = _session_performance_summary(db, int(session_id))
    narrative_payload = build_runtime_narrative_step(
        event_type=event,
        tone_profile=tone,
        severity=sev,
        context={
            "ingredient_id": ingredient_id,
            "ingredient_name": ingredient_name,
            "target_amount": float(target_amount),
            "measured_amount": float(measured_amount),
            "delta_amount": float(delta_amount),
            "message": message,
            "detail": detail,
        },
        performance_summary=perf_summary,
        session_seed=f"{session_id}:{event}:{at_no}",
    )
    if message:
        narrative_payload["what"] = str(message)
    if detail:
        narrative_payload["tip"] = str(detail)
    narrative_payload["source_event"] = event
    pos = _insert_dynamic_payload_step_if_missing(
        db=db,
        session_id=int(session_id),
        at_step_no=at_no,
        payload=narrative_payload,
    )
    if pos is not None and (ingredient_id is not None or ingredient_name or target_amount > 0 or measured_amount > 0):
        inserted_row = db.execute(
            """
            SELECT payload_json
            FROM mix_session_steps
            WHERE mix_session_id = ? AND step_no = ?
            LIMIT 1
            """,
            (int(session_id), int(pos)),
        ).fetchone()
        payload_obj: dict[str, Any]
        if inserted_row and inserted_row["payload_json"]:
            try:
                payload_obj = json.loads(inserted_row["payload_json"]) if isinstance(inserted_row["payload_json"], str) else {}
            except (json.JSONDecodeError, TypeError):
                payload_obj = {}
        else:
            payload_obj = {}
        payload_obj["ingredient_id"] = ingredient_id
        payload_obj["ingredient_name"] = ingredient_name
        payload_obj["target_amount"] = float(target_amount)
        payload_obj["measured_amount"] = float(measured_amount)
        payload_obj["delta_amount"] = float(delta_amount)
        db.execute(
            """
            UPDATE mix_session_steps
            SET payload_json = ?
            WHERE mix_session_id = ? AND step_no = ?
            """,
            (json.dumps(payload_obj), int(session_id), int(pos)),
        )
    perf_inc: dict[str, int] = {}
    if event in {"overpour"}:
        perf_inc["overshoot_count"] = 1
    if event in {"policy_warning", "tare_required"}:
        perf_inc["near_miss_count"] = 1
    if event in {"policy_block", "reconcile_required", "restart_guidance", "recovery_continue", "overpour"}:
        perf_inc["recovery_count"] = 1
    performance_after = _bump_session_performance(db, int(session_id), **perf_inc) if perf_inc else perf_summary
    db.commit()
    return {
        "ok": True,
        "message": "Dynamic step inserted." if pos is not None else "Matching dynamic step already exists.",
        "inserted_step_no": int(pos) if pos is not None else None,
        "performance_summary": performance_after,
    }
