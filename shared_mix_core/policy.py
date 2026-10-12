from __future__ import annotations

from dataclasses import dataclass

from .calculator import MixInput, MixResult, compute_mix, compute_mix_step_breakdown


@dataclass(frozen=True)
class MixPolicy:
    name: str
    max_total_flavor_percent: float | None
    max_target_nic_mg_ml: float | None
    overpour_threshold_multiplier: float
    overpour_hard_stop: bool
    guided_require_order: bool
    underpour_tolerance_pct: float
    overpour_tolerance_pct: float
    tiny_vessel_tolerance_factor: float
    small_vessel_tolerance_factor: float
    large_vessel_tolerance_factor: float
    min_pour_tolerance_grams: float


@dataclass(frozen=True)
class PolicyEvaluation:
    warnings: tuple[str, ...]
    hard_stops: tuple[str, ...]


@dataclass(frozen=True)
class ProgressionDecision:
    allowed: bool
    decision: str
    warnings: tuple[str, ...]
    hard_stops: tuple[str, ...]


def tvw_mix_policy() -> MixPolicy:
    # Community-safe and lenient: communicate issues as warnings where practical.
    return MixPolicy(
        name="tvw_lenient",
        max_total_flavor_percent=35.0,
        max_target_nic_mg_ml=24.0,
        overpour_threshold_multiplier=1.08,
        overpour_hard_stop=False,
        guided_require_order=False,
        underpour_tolerance_pct=0.014,
        overpour_tolerance_pct=0.018,
        tiny_vessel_tolerance_factor=1.45,
        small_vessel_tolerance_factor=1.20,
        large_vessel_tolerance_factor=0.85,
        min_pour_tolerance_grams=0.01,
    )


def lab_mix_policy() -> MixPolicy:
    # Production-safe and stricter defaults.
    return MixPolicy(
        name="lab_strict",
        max_total_flavor_percent=30.0,
        max_target_nic_mg_ml=20.0,
        overpour_threshold_multiplier=1.02,
        overpour_hard_stop=True,
        guided_require_order=True,
        underpour_tolerance_pct=0.008,
        overpour_tolerance_pct=0.010,
        tiny_vessel_tolerance_factor=1.20,
        small_vessel_tolerance_factor=1.05,
        large_vessel_tolerance_factor=0.80,
        min_pour_tolerance_grams=0.01,
    )


def _vessel_tolerance_factor(*, policy: MixPolicy, vessel_size_ml: float) -> float:
    vessel = float(vessel_size_ml or 0.0)
    if vessel <= 30.0:
        return float(policy.tiny_vessel_tolerance_factor)
    if vessel <= 60.0:
        return float(policy.small_vessel_tolerance_factor)
    if vessel >= 250.0:
        return float(policy.large_vessel_tolerance_factor)
    return 1.0


def scaled_pour_tolerance_grams(
    *,
    policy: MixPolicy,
    target_grams: float,
    vessel_size_ml: float,
    direction: str,
) -> float:
    target = max(0.0, float(target_grams or 0.0))
    if target <= 0:
        return float(policy.min_pour_tolerance_grams)
    base_pct = (
        float(policy.overpour_tolerance_pct)
        if str(direction or "").strip().lower() == "over"
        else float(policy.underpour_tolerance_pct)
    )
    factor = _vessel_tolerance_factor(policy=policy, vessel_size_ml=vessel_size_ml)
    return max(float(policy.min_pour_tolerance_grams), target * base_pct * factor)


def normalize_ingredient_pour_category(*, category: str, name: str = "") -> str:
    """Map step payload to pour-policy tier: nicotine, flavor, pg, or vg."""
    cat = str(category or "").strip().lower()
    name_l = str(name or "").strip().lower()
    if cat == "nicotine" or "nicotine" in name_l:
        return "nicotine"
    if cat == "pg":
        return "pg"
    if cat == "vg":
        return "vg"
    return "flavor"


def ingredient_pour_tolerance_grams(
    *,
    ingredient_category: str,
    target_grams: float,
    batch_size_ml: float,
    policy: MixPolicy,
    direction: str,
) -> float:
    """Batch-scaled gram tolerance by ingredient type (nicotine strictest, VG most lenient)."""
    kind = normalize_ingredient_pour_category(category=ingredient_category)
    batch = max(1.0, float(batch_size_ml or 1.0))
    target = max(0.0, float(target_grams or 0.0))
    vessel_factor = _vessel_tolerance_factor(policy=policy, vessel_size_ml=batch)
    is_over = str(direction or "").strip().lower() == "over"

    if kind == "vg":
        # Final fill: ~1 g slack at 100 ml, tighter on tiny bottles.
        return max(0.15, batch * 0.010) * vessel_factor
    if kind == "pg":
        # Flexible carrier adjuster: more room than flavours, still batch-scaled.
        return max(0.10, batch * 0.008) * vessel_factor
    if kind == "nicotine":
        base = max(0.02, batch * 0.002) * vessel_factor
        if target > 0:
            pct = float(policy.overpour_tolerance_pct if is_over else policy.underpour_tolerance_pct)
            base = max(base, target * pct * vessel_factor)
        return base

    # Flavours / concentrates — strict dose control.
    base = max(0.02, batch * 0.003) * vessel_factor
    if target > 0:
        pct = float(policy.overpour_tolerance_pct if is_over else policy.underpour_tolerance_pct)
        base = max(base, target * pct * vessel_factor)
    return base


def ingredient_over_requires_recovery(
    *,
    ingredient_category: str,
    display_target: float,
    measured: float,
    batch_size_ml: float,
    policy: MixPolicy,
) -> bool:
    """Whether a measured overpour should enter serious recovery (not slight-over / PG trim)."""
    over_by = float(measured) - float(display_target)
    if over_by <= 0.0005:
        return False
    kind = normalize_ingredient_pour_category(category=ingredient_category)
    if kind == "nicotine":
        return False
    tol = ingredient_pour_tolerance_grams(
        ingredient_category=kind,
        target_grams=display_target,
        batch_size_ml=batch_size_ml,
        policy=policy,
        direction="over",
    )
    ratio = (float(measured) / float(display_target)) if display_target > 0 else 999.0
    if kind == "flavor":
        return ratio > float(policy.overpour_threshold_multiplier)
    if kind == "pg":
        return over_by > tol * 4.0 or ratio > 1.20
    if kind == "vg":
        return over_by > tol * 3.0 or ratio > 1.25
    return ratio > float(policy.overpour_threshold_multiplier)


def build_tolerance_profile(*, policy: MixPolicy) -> dict[str, float]:
    return {
        "underpour_tolerance_pct": float(policy.underpour_tolerance_pct),
        "overpour_tolerance_pct": float(policy.overpour_tolerance_pct),
        "tiny_vessel_tolerance_factor": float(policy.tiny_vessel_tolerance_factor),
        "small_vessel_tolerance_factor": float(policy.small_vessel_tolerance_factor),
        "large_vessel_tolerance_factor": float(policy.large_vessel_tolerance_factor),
        "min_pour_tolerance_grams": float(policy.min_pour_tolerance_grams),
        "overpour_threshold_multiplier": float(policy.overpour_threshold_multiplier),
    }


def evaluate_mix_input(request: MixInput, policy: MixPolicy) -> PolicyEvaluation:
    warnings: list[str] = []
    hard_stops: list[str] = []

    total_pct = sum(float(f.percent or 0.0) for f in request.flavors)
    if policy.max_total_flavor_percent is not None and total_pct > float(policy.max_total_flavor_percent):
        msg = (
            f"Total flavor {total_pct:.2f}% exceeds {policy.max_total_flavor_percent:.2f}% policy limit "
            f"({policy.name})."
        )
        if policy.overpour_hard_stop:
            hard_stops.append(msg)
        else:
            warnings.append(msg)

    if (
        policy.max_target_nic_mg_ml is not None
        and float(request.target_nic_mg_ml or 0.0) > float(policy.max_target_nic_mg_ml)
    ):
        msg = (
            f"Target nicotine {float(request.target_nic_mg_ml):.2f} mg/ml exceeds "
            f"{policy.max_target_nic_mg_ml:.2f} mg/ml policy limit ({policy.name})."
        )
        if policy.overpour_hard_stop:
            hard_stops.append(msg)
        else:
            warnings.append(msg)

    if float(request.nic_base_mg_ml or 0.0) < 20.0:
        warnings.append("Nicotine base is unusually low strength; verify base bottle and units.")

    if float(request.target_ml or 0.0) <= 0:
        hard_stops.append("Target batch must be greater than zero.")

    return PolicyEvaluation(warnings=tuple(warnings), hard_stops=tuple(hard_stops))


def compute_mix_with_policy(
    request: MixInput,
    *,
    policy: MixPolicy,
    hard_fail: bool,
) -> tuple[MixResult, PolicyEvaluation]:
    evaluation = evaluate_mix_input(request, policy)
    if hard_fail and evaluation.hard_stops:
        raise ValueError("; ".join(evaluation.hard_stops))
    return compute_mix(request), evaluation


def compute_mix_step_breakdown_with_policy(
    request: MixInput,
    *,
    policy: MixPolicy,
    hard_fail: bool,
) -> tuple[list[dict], PolicyEvaluation]:
    evaluation = evaluate_mix_input(request, policy)
    if hard_fail and evaluation.hard_stops:
        raise ValueError("; ".join(evaluation.hard_stops))
    return compute_mix_step_breakdown(request), evaluation


def overpour_requires_hard_stop(
    *,
    poured_ml: float,
    target_ml: float,
    policy: MixPolicy,
) -> bool:
    if float(target_ml or 0.0) <= 0:
        return True
    ratio = float(poured_ml) / float(target_ml)
    return bool(policy.overpour_hard_stop and ratio > float(policy.overpour_threshold_multiplier))


def evaluate_overpour(
    *,
    poured_ml: float,
    target_ml: float,
    policy: MixPolicy,
) -> ProgressionDecision:
    if float(target_ml or 0.0) <= 0:
        return ProgressionDecision(
            allowed=False,
            decision="invalid_target",
            warnings=(),
            hard_stops=("Target volume for reconcile must be greater than zero.",),
        )
    ratio = float(poured_ml) / float(target_ml)
    over_limit = ratio > float(policy.overpour_threshold_multiplier)
    if not over_limit:
        return ProgressionDecision(
            allowed=True,
            decision="within_threshold",
            warnings=(),
            hard_stops=(),
        )
    msg = (
        f"Overpour ratio {ratio:.3f} exceeds policy threshold "
        f"{policy.overpour_threshold_multiplier:.3f} ({policy.name})."
    )
    if policy.overpour_hard_stop:
        return ProgressionDecision(
            allowed=False,
            decision="hard_stop",
            warnings=(),
            hard_stops=(msg,),
        )
    return ProgressionDecision(
        allowed=True,
        decision="warning_only",
        warnings=(msg,),
        hard_stops=(),
    )


def evaluate_guided_step_progression(
    *,
    policy: MixPolicy,
    requested_step_no: int,
    next_open_step_no: int | None,
    undo: bool,
) -> ProgressionDecision:
    if undo:
        return ProgressionDecision(
            allowed=True,
            decision="undo_allowed",
            warnings=(),
            hard_stops=(),
        )
    if not policy.guided_require_order or next_open_step_no is None:
        return ProgressionDecision(
            allowed=True,
            decision="order_not_enforced",
            warnings=(),
            hard_stops=(),
        )
    if int(requested_step_no) == int(next_open_step_no):
        return ProgressionDecision(
            allowed=True,
            decision="in_order",
            warnings=(),
            hard_stops=(),
        )
    msg = (
        f"Step {int(requested_step_no)} is out of sequence. "
        f"Next required step is {int(next_open_step_no)} ({policy.name})."
    )
    if policy.overpour_hard_stop:
        return ProgressionDecision(
            allowed=False,
            decision="guided_order_blocked",
            warnings=(),
            hard_stops=(msg,),
        )
    return ProgressionDecision(
        allowed=True,
        decision="guided_order_warning",
        warnings=(msg,),
        hard_stops=(),
    )

