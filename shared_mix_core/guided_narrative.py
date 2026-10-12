from __future__ import annotations

from dataclasses import dataclass
from typing import Any


STEP_INSTRUCTION = "instruction"
STEP_TARE = "tare"
STEP_ADD_INGREDIENT = "add_ingredient"
STEP_CAUTION = "caution"
STEP_RECOVERY = "recovery"
STEP_TRANSITION = "transition"
STEP_COMPLETION = "completion"


@dataclass(frozen=True)
class NarrativeTone:
    profile: str
    strict: bool


def tone_for(profile: str) -> NarrativeTone:
    p = str(profile or "tvw").strip().lower()
    if p == "lab":
        return NarrativeTone(profile="lab", strict=True)
    return NarrativeTone(profile="tvw", strict=False)


def _stable_pick(options: list[str], *keys: Any) -> str:
    if not options:
        return ""
    seed = "|".join(str(k or "") for k in keys)
    idx = abs(hash(seed)) % len(options)
    return options[idx]


def _looks_like_any(text: str, needles: tuple[str, ...]) -> bool:
    t = str(text or "").lower()
    return any(n in t for n in needles)


def infer_ingredient_role(
    *,
    ingredient_name: str,
    ingredient_percent: float | None,
    ingredient_category: str | None = None,
    flavor_profile: str | None = None,
) -> str:
    name = str(ingredient_name or "").lower()
    cat = str(ingredient_category or "").lower()
    profile = str(flavor_profile or "").lower()
    pct = float(ingredient_percent or 0.0)

    sweetener_hints = ("sweetener", "sucralose", "sweet", "ethyl maltol", "cotton candy")
    coolant_hints = ("coolant", "koolada", "ws-23", "ws23", "ice", "menthol", "cool")
    additive_hints = ("additive", "saline", "sour", "acid", "smooth", "booster")

    joined = f"{name} {cat} {profile}"
    if _looks_like_any(joined, sweetener_hints):
        return "sweetener"
    if _looks_like_any(joined, coolant_hints):
        return "coolant"
    if _looks_like_any(joined, additive_hints):
        return "additive"

    if pct >= 5.0:
        return "base_main_note"
    if pct >= 2.0:
        return "support"
    if pct >= 0.8:
        return "accent"
    return "micro_accent"


def _role_blurb(role: str, tone: NarrativeTone) -> str:
    if role == "base_main_note":
        return (
            "This is a main-note layer that defines most of the recipe identity."
            if not tone.strict
            else "This is a dominant layer and sets the batch's primary profile direction."
        )
    if role == "support":
        return (
            "This supports and rounds the main notes."
            if not tone.strict
            else "This support layer stabilizes the core profile and body."
        )
    if role == "accent":
        return (
            "This accent shapes detail and top-note clarity."
            if not tone.strict
            else "This accent controls profile definition and perceived brightness."
        )
    if role == "micro_accent":
        return (
            "This is a micro-accent, so small measurement errors are more noticeable."
            if not tone.strict
            else "This is a micro-accent; tight dosing control is required."
        )
    if role == "sweetener":
        return (
            "This tunes sweetness and mouthfeel rather than becoming the headline flavor."
            if not tone.strict
            else "This adjusts sweetness/body and must stay controlled to avoid muting."
        )
    if role == "coolant":
        return (
            "This sets cooling impact, so overpour can quickly dominate the recipe."
            if not tone.strict
            else "This controls cooling intensity; excess can override intended balance."
        )
    return (
        "This is an additive layer that fine-tunes the final profile."
        if not tone.strict
        else "This additive modifies system behavior and should stay within target."
    )


def infer_ingredient_family(
    *,
    ingredient_name: str,
    ingredient_category: str | None = None,
    flavor_profile: str | None = None,
) -> str:
    name = str(ingredient_name or "").lower()
    cat = str(ingredient_category or "").lower()
    profile = str(flavor_profile or "").lower()
    joined = f"{name} {cat} {profile}"
    families: list[tuple[str, tuple[str, ...]]] = [
        ("fruit", ("fruit", "berry", "apple", "mango", "peach", "grape", "melon", "banana", "pineapple")),
        ("cream", ("cream", "custard", "vanilla", "milk", "yogurt", "ice cream")),
        ("bakery", ("bakery", "cake", "cookie", "donut", "pastry", "pie", "biscuit")),
        ("candy", ("candy", "gummy", "taffy", "sweets", "hard candy")),
        ("beverage", ("beverage", "cola", "soda", "coffee", "tea", "energy drink", "drink")),
        ("tobacco", ("tobacco", "cigar", "ry4", "cavendish")),
        ("menthol_ice", ("menthol", "ice", "frost", "polar", "chill")),
        ("citrus", ("citrus", "lemon", "lime", "orange", "grapefruit", "yuzu")),
        ("sweetener", ("sweetener", "sucralose", "ethyl maltol", "cotton candy", "sweet")),
        ("coolant", ("coolant", "ws-23", "ws23", "koolada", "cool")),
        ("additive", ("additive", "sour", "saline", "acid", "booster", "smooth")),
    ]
    for fam, hints in families:
        if _looks_like_any(joined, hints):
            return fam
    return "generic"


def _family_blurb(family: str, tone: NarrativeTone) -> str:
    tvw = {
        "fruit": "Fruit layers are bright and easy to overpush, so keep the pour controlled.",
        "cream": "Cream layers shape body and smoothness, so dose accuracy protects texture.",
        "bakery": "Bakery layers carry depth and can quickly get heavy when overdosed.",
        "candy": "Candy notes can dominate sweetness quickly, so small errors are noticeable.",
        "beverage": "Beverage profiles rely on balance, so this pour keeps the mix drink-like instead of flat.",
        "tobacco": "Tobacco layers are nuanced; tight dosing keeps complexity without harsh drift.",
        "menthol_ice": "Ice/menthol intensity rises fast, so this target needs careful control.",
        "citrus": "Citrus brightness can become sharp if overpoured, so precision matters here.",
        "sweetener": "Sweetener shifts mouthfeel fast; keep it controlled to avoid muting flavor detail.",
        "coolant": "Coolants are high-impact at low dose, so avoid overshoot.",
        "additive": "Additives tune behavior in small margins, so accurate dosing is critical.",
        "generic": "This layer contributes to overall balance, so dose control keeps the recipe coherent.",
    }
    lab = {
        "fruit": "Fruit top-notes are high volatility drivers; strict control prevents profile skew.",
        "cream": "Cream body components require tight dosing to maintain target texture behavior.",
        "bakery": "Bakery compounds amplify heaviness when high; keep within target to avoid drift.",
        "candy": "Candy profile elements saturate rapidly; process control is required.",
        "beverage": "Beverage identity depends on ratio precision across volatile and body notes.",
        "tobacco": "Tobacco concentrates are profile-critical and must remain in controlled limits.",
        "menthol_ice": "Cooling agents are high-impact modifiers; overage causes process-level profile failure.",
        "citrus": "Citrus compounds shift sharply with minor error; strict control is mandatory.",
        "sweetener": "Sweetener dosing affects muting/carry; maintain exact target.",
        "coolant": "Coolant excess can override intended profile; no overshoot tolerance.",
        "additive": "Additives alter system behavior and require controlled dosing discipline.",
        "generic": "This component influences final spec and must stay in target tolerance.",
    }
    return (lab if tone.strict else tvw).get(family, (lab if tone.strict else tvw)["generic"])


def _tare_why(tone: NarrativeTone, family: str, category: str) -> str:
    if category == "nicotine":
        return (
            "Nicotine dosing starts with a clean tare to prevent hidden carry-over in a safety-critical step."
            if not tone.strict
            else "Nicotine tare is a mandatory safety gate; any carry-over is process-noncompliant."
        )
    if category in {"pg", "vg"}:
        return (
            "Base-liquid tare protects final ratio closure accuracy."
            if not tone.strict
            else "Carrier tare protects final ratio traceability and must be exact."
        )
    fam_line = _family_blurb(family, tone)
    return (
        f"Tare removes carry-over from prior pours. {fam_line}"
        if not tone.strict
        else f"Tare prevents cumulative dosing error. {fam_line}"
    )


def build_guided_narrative_steps(
    *,
    tone_profile: str,
    ingredients: list[dict[str, Any]],
    policy_name: str = "",
    policy_warnings: list[str] | None = None,
    policy_hard_stops: list[str] | None = None,
    session_seed: str = "",
) -> list[dict[str, Any]]:
    tone = tone_for(tone_profile)
    warnings = list(policy_warnings or [])
    hard_stops = list(policy_hard_stops or [])
    steps: list[dict[str, Any]] = []
    step_no = 0

    def push(step_type: str, **payload: Any) -> None:
        nonlocal step_no
        step_no += 1
        steps.append(
            {
                "step": step_no,
                "step_type": step_type,
                **payload,
            }
        )

    push(
        STEP_INSTRUCTION,
        title="Prepare your station",
        what=(
            "Set bottle, scale, and ingredients in sequence before the first pour."
            if tone.strict
            else "Set bottle, scale, and ingredients within reach so each step feels smooth."
        ),
        why=(
            "A prepared station reduces handling errors and keeps the batch auditable."
            if tone.strict
            else "Good setup lowers stress and helps you hit each target cleanly."
        ),
        tip=_stable_pick(
            [
                "Keep the bottle centered on the scale to avoid drift.",
                "Check your scale settles at 0.00 g before each confirmation.",
                "Lay ingredients in pour order to reduce mid-step distractions.",
            ],
            tone.profile,
            "instruction",
            session_seed,
        ),
        action_label="Station ready",
        severity="info",
    )

    for ing in ingredients:
        name = str(ing.get("name") or "Ingredient")
        brand = str(ing.get("brand") or "")
        category = str(ing.get("category") or "").lower()
        grams = float(ing.get("target_grams") or 0.0)
        try:
            target_ml = float(ing.get("ml") or ing.get("target_ml") or 0.0)
        except (TypeError, ValueError):
            target_ml = 0.0
        pct = ing.get("pct")
        pct_f = float(pct) if pct is not None else 0.0
        profile = str(ing.get("flavor_profile") or ing.get("flavour_profile") or "")
        role = infer_ingredient_role(
            ingredient_name=name,
            ingredient_percent=pct,
            ingredient_category=category,
            flavor_profile=profile,
        )
        family = infer_ingredient_family(
            ingredient_name=name,
            ingredient_category=category,
            flavor_profile=profile,
        )

        push(
            STEP_TARE,
            title=f"Tare before {name}",
            what=_stable_pick(
                [
                    "Reset the scale to 0.00 g before this pour.",
                    "Tare to 0.00 g now so this step starts clean.",
                    "Zero the scale and confirm stable 0.00 g.",
                ],
                tone.profile,
                "tare",
                family,
                name,
                session_seed,
            ),
            why=_tare_why(tone, family, category),
            tip=_stable_pick(
                [
                    "Do not pour until tare is confirmed.",
                    "If the scale drifts, re-tare before touching the bottle.",
                    "Treat tare as mandatory before every ingredient.",
                    "Confirm your bottle is stable before re-zeroing the scale.",
                ],
                tone.profile,
                "tare_tip",
                family,
                name,
                session_seed,
            ),
            action_label=("Tare confirmed" if tone.strict else "I tared to 0.00g"),
            severity="info",
            name=name,
            brand=brand,
            category=category,
        )

        if category == "nicotine":
            push(
                STEP_CAUTION,
                title="Nicotine handling caution",
                what=_stable_pick(
                    [
                        "Confirm gloves, controlled bench, and no interruptions before nicotine pour.",
                        "Pause and verify PPE, bottle identity, and nicotine carrier before pouring.",
                        "Nicotine step: hands stable, area clean, distractions removed.",
                        "Reconfirm nicotine base strength and carrier label before dispensing.",
                    ],
                    tone.profile,
                    "nic_caution",
                    session_seed,
                ),
                why=(
                    "Nicotine handling has higher safety consequence and needs deliberate control."
                    if not tone.strict
                    else "Nicotine is safety-critical and requires strict handling compliance."
                ),
                tip=(
                    "If uncertain, stop and re-check strength/carrier first."
                    if not tone.strict
                    else "No shortcuts: verify target, base strength, and carrier before dispensing."
                ),
                action_label="Safety confirmed",
                severity="warning",
                name=name,
                brand=brand,
                category=category,
            )

        if category == "pg":
            why = _stable_pick(
                [
                    "PG carries flavor and helps shape throat hit, so this pour closes ratio control.",
                    "PG adjusts sharpness and delivery; accurate dosing keeps profile definition intact.",
                    "PG is part of final carrier balance, so this step stabilizes consistency.",
                ]
                if not tone.strict
                else [
                    "PG finalizes carrier balance after concentrates/nicotine for traceable ratio closure.",
                    "PG dosing is a controlled ratio step and must match process target.",
                    "PG controls carrier behavior and throat profile; maintain exact process value.",
                ],
                tone.profile,
                "pg_why",
                session_seed,
            )
        elif category == "vg":
            why = _stable_pick(
                [
                    "VG builds density and vapor body, so this step sets final smoothness.",
                    "VG closes body and mouthfeel; precision keeps the finish consistent.",
                    "VG is your final balance anchor for texture and vapor profile.",
                ]
                if not tone.strict
                else [
                    "VG is added late to lock final viscosity and ratio after higher-impact components.",
                    "VG closure is process-critical for viscosity and final system behavior.",
                    "VG dosing finalizes carrier mass; maintain strict target control.",
                ],
                tone.profile,
                "vg_why",
                session_seed,
            )
        elif category == "nicotine":
            why = _stable_pick(
                [
                    "Nicotine is added before base balancing so later PG/VG can compensate precisely.",
                    "This nicotine step sets final strength trajectory, so precision matters immediately.",
                    "Nicotine dose is high-impact; this target protects both safety and recipe intent.",
                ]
                if not tone.strict
                else [
                    "Nicotine is sequenced before carrier close-out to preserve precise final base ratio.",
                    "Nicotine dosing is safety-critical and defines final strength compliance.",
                    "Nicotine is a controlled specification point; no drift is acceptable.",
                ],
                tone.profile,
                "nic_why",
                session_seed,
            )
        else:
            why = f"{_role_blurb(role, tone)} {_family_blurb(family, tone)} " + (
                f"At {pct_f:.2f}%, dose control matters for consistency."
                if pct is not None
                else "Dose control matters for consistency."
            )

        push(
            STEP_ADD_INGREDIENT,
            title=f"Add {grams:.2f}g {name}",
            what=f"Add {grams:.2f} g of {name}, then confirm measured amount.",
            why=why,
            tip=_stable_pick(
                [
                    "Approach target slowly in the last 0.10 g.",
                    "Pulse-pour near target to avoid overshoot.",
                    "Slow your flow as you enter the final margin.",
                    "Use short pulses as the scale approaches final value.",
                ],
                tone.profile,
                "add_tip",
                role,
                family,
                name,
                session_seed,
            ),
            action_label="Confirm pour",
            severity=("critical" if tone.strict else "info"),
            ingredient_id=ing.get("ingredient_id"),
            name=name,
            brand=brand,
            category=category,
            target_grams=round(grams, 4),
            ml=(round(target_ml, 4) if target_ml > 0 else None),
            pct=(round(pct_f, 4) if pct is not None else None),
            ingredient_role=role,
            ingredient_family=family,
            flavor_profile=profile or None,
        )

        push(
            STEP_TRANSITION,
            title="Checkpoint",
            what="Confirm this step before moving to the next action.",
            why=(
                "Checkpointing catches drift early and keeps the session recoverable."
                if not tone.strict
                else "Checkpoint discipline limits compounding process error."
            ),
            tip=_stable_pick(
                [
                    "Read back target vs actual before continuing.",
                    "If something looks off, pause now rather than later.",
                    "Proceed only when this pour record is clean.",
                ],
                tone.profile,
                "transition",
                name,
                session_seed,
            ),
            action_label="Continue",
            severity="info",
            name=name,
            brand=brand,
            category=category,
        )

    recovery_severity = "danger" if hard_stops else "warning"
    policy_context = ""
    if hard_stops:
        policy_context = hard_stops[0]
    elif warnings:
        policy_context = warnings[0]
    elif policy_name:
        policy_context = f"Policy context: {policy_name}."

    push(
        STEP_RECOVERY,
        title="Recovery guidance",
        what=(
            f"If a deviation occurs, run reconcile before sign-off. {policy_context}".strip()
            if policy_context
            else "If a deviation occurs, run reconcile before sign-off."
        ),
        why=(
            "Recovery protects the recipe trajectory after mistakes."
            if not tone.strict
            else "Recovery is required to restore controlled process state."
        ),
        tip=_stable_pick(
            [
                "Reconcile immediately after an overpour; do not guess-correct.",
                "If drift appears, pause and recover before the next pour.",
                "Use recovery early to avoid larger downstream corrections.",
            ]
            if not tone.strict
            else [
                "No continuation on unresolved deviations.",
                "Execute recovery protocol before any additional dispense.",
                "Recovery is a mandatory process gate when drift is detected.",
            ],
            tone.profile,
            "planned_recovery",
            recovery_severity,
            policy_name,
            session_seed,
        ),
        action_label=("Recovery acknowledged" if tone.strict else "Recovery checked"),
        severity=recovery_severity,
    )

    push(
        STEP_COMPLETION,
        title="Completion",
        what=(
            "Seal, label, and record the finished batch."
            if tone.strict
            else "Seal, label, and save this mix session."
        ),
        why=(
            "Close-out preserves traceability for repeatable production."
            if tone.strict
            else "Good close-out helps you repeat what worked next time."
        ),
        tip=_stable_pick(
            [
                "Confirm final volume and label details before storage.",
                "Verify your batch record is complete before ending session.",
                "Capture notes now while the process is fresh.",
            ],
            tone.profile,
            "completion",
            session_seed,
        ),
        action_label="Session complete",
        severity="success",
    )
    return steps


def build_runtime_narrative_step(
    *,
    event_type: str,
    tone_profile: str,
    severity: str,
    context: dict[str, Any] | None = None,
    performance_summary: dict[str, int] | None = None,
    policy_name: str = "",
    session_seed: str = "",
) -> dict[str, Any]:
    """Build a dynamic runtime step payload with contextual coaching voice."""
    tone = tone_for(tone_profile)
    evt = str(event_type or "").strip().lower()
    ctx = context if isinstance(context, dict) else {}
    ingredient_name = str(ctx.get("ingredient_name") or "ingredient").strip() or "ingredient"
    ingredient_id = ctx.get("ingredient_id")
    target = float(ctx.get("target_amount") or 0.0)
    measured = float(ctx.get("measured_amount") or 0.0)
    delta = float(ctx.get("delta_amount") or (measured - target))
    delta_abs = abs(delta)
    perf = performance_summary if isinstance(performance_summary, dict) else {}
    overshoot_count = int(perf.get("overshoot_count") or 0)
    undershoot_count = int(perf.get("undershoot_count") or 0)
    recovery_count = int(perf.get("recovery_count") or 0)
    precise_count = int(perf.get("precise_pour_count") or 0)
    severity_norm = str(severity or "warning").strip().lower()
    family = infer_ingredient_family(
        ingredient_name=ingredient_name,
        ingredient_category=str(ctx.get("ingredient_category") or ""),
        flavor_profile=str(ctx.get("flavor_profile") or ""),
    )
    category = str(ctx.get("ingredient_category") or "").lower()
    is_capacity = bool(ctx.get("required_vessel")) or evt in {"capacity_exceeded", "reconcile_required"}
    is_nic = category == "nicotine" or _looks_like_any(ingredient_name, ("nic", "nicotine"))
    is_pg_vg = category in {"pg", "vg"} or _looks_like_any(ingredient_name, (" pg", " vg", "prop", "glycerin", "glycerine"))
    serious_breach = severity_norm in {"danger", "critical"} or delta_abs >= max(0.15, target * 0.05 if target > 0 else 0.15)
    base = {
        "severity": severity_norm,
        "dynamic": True,
        "tone_profile": tone.profile,
        "source_event": evt or "runtime",
        "ingredient_id": ingredient_id,
        "ingredient_name": ingredient_name if ingredient_name != "ingredient" else "",
        "target_amount": target,
        "measured_amount": measured,
        "delta_amount": delta,
        "policy_name": str(policy_name or ""),
    }

    if evt in {"overpour", "policy_block", "reconcile_required", "restart_guidance", "recovery_continue"}:
        if target > 0 and measured > 0:
            what = (
                f"{ingredient_name} measured {measured:.2f}g vs target {target:.2f}g (delta {delta:+.2f}g)."
            )
        elif is_capacity:
            required_vessel = float(ctx.get("required_vessel") or 0.0)
            what = (
                f"Current vessel is below required reconcile capacity ({required_vessel:.1f}ml)."
                if required_vessel > 0
                else "Current vessel capacity is below reconcile requirements."
            )
        else:
            what = _stable_pick(
                [
                    "A runtime deviation was detected in this guided flow.",
                    "This step was inserted because the mix moved outside normal bounds.",
                    "Runtime condition triggered recovery guidance.",
                ],
                tone.profile,
                evt,
                session_seed,
            )
        if is_nic:
            why = (
                "Nicotine deviation changes both safety and target strength, so controlled recovery is essential."
                if not tone.strict
                else "Nicotine deviation is safety-critical and requires immediate controlled correction."
            )
        elif is_pg_vg:
            why = (
                "Base-ratio drift affects the whole batch balance, so reconcile protects final feel."
                if not tone.strict
                else "Carrier-ratio drift breaks process specification and must be corrected before continuation."
            )
        elif is_capacity:
            why = (
                "Insufficient capacity prevents safe reconcile and can force uncontrolled overflow."
                if not tone.strict
                else "Capacity shortfall blocks compliant reconcile execution."
            )
        else:
            why = (
                f"{_family_blurb(family, tone)} Recovery prevents this deviation from compounding."
                if not tone.strict
                else f"{_family_blurb(family, tone)} Controlled recovery is required before continuation."
            )
        tip_pool_tvw = [
            "Run reconcile now, then continue with adjusted targets.",
            "Apply recovery before attempting the next ingredient step.",
            "Complete recovery and confirm the next guided target.",
        ]
        tip_pool_lab = [
            "Stop progression. Execute reconcile protocol before any further pours.",
            "Resolve this block in controlled sequence, then re-enter guided flow.",
            "Process gate active: perform recovery and verify updated targets.",
        ]
        if is_nic:
            tip_pool_tvw = [
                "Pause, verify nic base/carrying solvent, then reconcile before next pour.",
                "Recover this nicotine step first, then continue with corrected targets.",
                "Confirm safety checks and strength before resuming.",
            ]
            tip_pool_lab = [
                "Suspend progression and execute nicotine correction protocol immediately.",
                "Verify base strength/carrier and reconcile before any continuation.",
                "No further dispense until nicotine deviation is closed.",
            ]
        elif is_pg_vg:
            tip_pool_tvw = [
                "Reconcile base ratio, then continue with updated PG/VG targets.",
                "Correct carrier drift now to keep mouthfeel and balance consistent.",
                "Apply ratio correction before advancing.",
            ]
            tip_pool_lab = [
                "Reconcile carrier ratio to specification before continuation.",
                "Close PG/VG deviation using controlled correction sequence.",
                "Do not continue until ratio correction is verified.",
            ]
        elif is_capacity:
            tip_pool_tvw = [
                "Move to a larger vessel, then run reconcile again.",
                "Increase vessel capacity first so recovery can complete safely.",
                "Select required vessel size and retry reconcile.",
            ]
            tip_pool_lab = [
                "Provision compliant vessel capacity before rerunning reconcile.",
                "Capacity gate active: increase vessel size, then repeat controlled reconcile.",
                "No continuation until required vessel capacity is available.",
            ]
        elif not serious_breach:
            tip_pool_tvw = [
                "This is a small overshoot - reconcile gently and continue.",
                "Small overpour noted; apply light correction before next step.",
                "Small deviation: confirm correction and proceed.",
            ]
            tip_pool_lab = [
                "Minor breach still requires logged correction before continuation.",
                "Apply correction protocol even for small deviation, then proceed.",
                "Small tolerance breach: reconcile and verify before advancing.",
            ]
        if overshoot_count >= 2:
            tip_pool_tvw.append("You have repeated overshoots this session - slow to pulse pours in the last margin.")
            tip_pool_lab.append("Repeated overshoot trend detected. Enforce reduced flow near target.")
        if undershoot_count >= 2:
            tip_pool_tvw.append("You are stopping early often - commit a little closer to target before confirming.")
            tip_pool_lab.append("Repeated undershoot trend detected. Increase approach discipline to target.")
        if recovery_count >= 2:
            tip_pool_tvw.append("Multiple recoveries are active - follow each correction fully before the next step.")
            tip_pool_lab.append("Recovery recurrence detected. Apply full corrective protocol before continuation.")
        if precise_count >= 3 and not serious_breach:
            tip_pool_tvw.append("Great consistency so far - keep the same controlled finish into target.")
            tip_pool_lab.append("Recent precision trend is compliant. Maintain current approach discipline.")
        tip = _stable_pick(
            tip_pool_lab if tone.strict else tip_pool_tvw,
            tone.profile,
            evt,
            ingredient_name,
            family,
            session_seed,
        )
        if evt == "overpour" and serious_breach:
            action_label = "Apply correction" if bool(ctx.get("correction_ready")) else "Review recovery plan"
        else:
            action_label = "Recovery complete" if tone.strict else "I resolved this"
        return {
            "step_type": STEP_RECOVERY,
            "title": (
                "Recovery required"
                if tone.strict
                else _stable_pick(
                    ["Recovery step", "Guided recovery", "Recovery checkpoint", "Correction step"],
                    tone.profile,
                    evt,
                    family,
                    session_seed,
                )
            ),
            "what": what,
            "why": why,
            "tip": tip,
            "action_label": action_label,
            **base,
        }

    if evt in {"tare_required", "tare_confirm"}:
        return {
            "step_type": STEP_TARE,
            "title": "Tare confirmation",
            "what": _stable_pick(
                [
                    "Reset scale to 0.00g before continuing.",
                    "Tare now to clear carry-over from previous pour.",
                    "Confirm stable 0.00g prior to next ingredient.",
                ],
                tone.profile,
                evt,
                session_seed,
            ),
            "why": (
                "Tare prevents hidden error from affecting the next target."
                if not tone.strict
                else "Tare is a mandatory process gate before the next controlled pour."
            ),
            "tip": (
                "No rush - wait for stable zero before you continue."
                if not tone.strict
                else "Proceed only after verified zero reading."
            ),
            "action_label": "Tare confirmed",
            **base,
        }

    return {
        "step_type": STEP_CAUTION,
        "title": _stable_pick(["Policy caution", "Runtime caution", "Guided caution"], tone.profile, evt, session_seed),
        "what": (
            f"{ingredient_name} is outside expected range ({delta:+.2f}g from target)."
            if (target > 0 and measured > 0)
            else "A caution condition was raised during guided progression."
        ),
        "why": (
            (
                "Small drift acknowledged now keeps later corrections lighter."
                if not tone.strict
                else "Caution acknowledgement is required to keep controlled sequence integrity."
            )
            if not is_nic
            else (
                "Even minor nicotine drift matters, so confirm before continuing."
                if not tone.strict
                else "Nicotine caution requires explicit acknowledgement before continuation."
            )
        ),
        "tip": _stable_pick(
            [
                "Review the caution and continue when ready.",
                "Confirm values, then proceed to the next guided action.",
                "If unsure, pause and re-check target before continuing.",
            ]
            if not tone.strict
            else [
                "Acknowledge caution and continue under controlled process.",
                "Verify values and proceed only when within expected bounds.",
                "Do not bypass caution review in production flow.",
            ],
            tone.profile,
            evt,
            ingredient_name,
            session_seed,
        ),
        "action_label": "Caution acknowledged",
        **base,
    }


def build_preemptive_coaching_for_step(
    *,
    step_type: str,
    tone_profile: str,
    performance_summary: dict[str, int] | None = None,
    step_payload: dict[str, Any] | None = None,
    session_seed: str = "",
) -> dict[str, str]:
    """Adaptive pre-pour coaching for planned steps based on session performance."""
    if str(step_type or "").lower() != STEP_ADD_INGREDIENT:
        return {}
    tone = tone_for(tone_profile)
    perf = performance_summary if isinstance(performance_summary, dict) else {}
    overshoot_count = int(perf.get("overshoot_count") or 0)
    undershoot_count = int(perf.get("undershoot_count") or 0)
    recovery_count = int(perf.get("recovery_count") or 0)
    precise_count = int(perf.get("precise_pour_count") or 0)
    p = step_payload if isinstance(step_payload, dict) else {}
    name = str(p.get("name") or "this ingredient")

    tip_line = ""
    preview_line = ""
    if overshoot_count >= 2:
        tip_line = (
            "Session trend: repeated overshoot. Slow to short pulse pours in the final margin."
            if not tone.strict
            else "Performance trend: repeated overshoot. Reduce flow and apply final-margin pulse control."
        )
        preview_line = (
            "Pre-emptive: approach this pour slower to prevent another overshoot."
            if not tone.strict
            else "Pre-emptive control: slow approach required to prevent repeat overshoot."
        )
    elif undershoot_count >= 2:
        tip_line = (
            "Session trend: repeated undershoot. Commit a little closer to target before confirming."
            if not tone.strict
            else "Performance trend: repeated undershoot. Increase commit-to-target discipline before confirmation."
        )
        preview_line = (
            f"Pre-emptive: finish {name} closer to target before sign-off."
            if not tone.strict
            else f"Pre-emptive control: drive {name} to target before confirmation."
        )
    elif recovery_count >= 2:
        tip_line = (
            "Session trend: multiple recoveries. Move deliberately and verify each scale settle before pour."
            if not tone.strict
            else "Performance trend: repeated recovery events. Enforce strict verify-before-pour sequence."
        )
        preview_line = (
            "Pre-emptive: careful controlled pour to avoid another recovery cycle."
            if not tone.strict
            else "Pre-emptive control: avoid additional recovery by strict process adherence."
        )
    elif precise_count >= 3:
        tip_line = (
            _stable_pick(
                [
                    "Great precision trend - keep the same controlled finish.",
                    "You are hitting targets well; maintain this steady approach.",
                    "Strong consistency so far - keep the same last-margin pacing.",
                ],
                tone.profile,
                "precise_tip",
                name,
                session_seed,
            )
            if not tone.strict
            else _stable_pick(
                [
                    "Precision trend is compliant; maintain current pour discipline.",
                    "Consistent target control detected - hold this process behavior.",
                    "Performance is on-spec; keep the same controlled approach.",
                ],
                tone.profile,
                "precise_tip",
                name,
                session_seed,
            )
        )
        preview_line = (
            "Pre-emptive: repeat your same controlled finish."
            if not tone.strict
            else "Pre-emptive control: repeat current compliant pour behavior."
        )
    if not tip_line:
        return {}
    return {"tip_line": tip_line, "preview_line": preview_line}


def build_live_coach_summary(
    *,
    tone_profile: str,
    performance_summary: dict[str, int] | None = None,
    session_seed: str = "",
) -> dict[str, str]:
    """Compact live coaching summary for session-level status strip."""
    tone = tone_for(tone_profile)
    perf = performance_summary if isinstance(performance_summary, dict) else {}
    overshoot_count = int(perf.get("overshoot_count") or 0)
    undershoot_count = int(perf.get("undershoot_count") or 0)
    recovery_count = int(perf.get("recovery_count") or 0)
    near_miss_count = int(perf.get("near_miss_count") or 0)
    precise_count = int(perf.get("precise_pour_count") or 0)

    label = "Coach status"
    severity = "info"
    detail = (
        "Steady control. Keep your normal approach."
        if not tone.strict
        else "Control state nominal. Maintain process discipline."
    )

    if recovery_count >= 2:
        label = "Recoveries active"
        severity = "warning" if not tone.strict else "danger"
        detail = (
            "You have multiple recoveries this session. Slow down and complete each correction fully."
            if not tone.strict
            else "Repeated recovery events detected. Enforce strict corrective protocol before each continuation."
        )
    elif overshoot_count >= 2:
        label = "Overshooting trend"
        severity = "warning"
        detail = (
            "You are overshooting slightly. Use shorter pulse pours in the final margin."
            if not tone.strict
            else "Overshoot trend detected. Reduce flow and apply final-margin pulse control."
        )
    elif undershoot_count >= 2:
        label = "Undershooting trend"
        severity = "warning"
        detail = (
            "You are stopping early. Commit closer to target before confirming."
            if not tone.strict
            else "Undershoot trend detected. Increase commit-to-target discipline before confirmation."
        )
    elif precise_count >= 3:
        label = "Precision improving"
        severity = "success"
        detail = _stable_pick(
            [
                "Great consistency so far. Keep this controlled finish.",
                "Precision is improving. Repeat the same pace into target.",
                "You are landing cleanly - maintain your current rhythm.",
            ]
            if not tone.strict
            else [
                "Precision trend is compliant. Maintain current controlled approach.",
                "Stable target control detected. Continue with current process discipline.",
                "Performance improving. Hold the same spec-focused finish.",
            ],
            tone.profile,
            "coach_precision",
            session_seed,
        )
    elif near_miss_count >= 2:
        label = "Near-miss trend"
        severity = "info" if not tone.strict else "warning"
        detail = (
            "Close calls are increasing. Add a brief pause before final grams."
            if not tone.strict
            else "Near-miss frequency rising. Add settle-check before final target approach."
        )
    elif overshoot_count > 0 or undershoot_count > 0 or recovery_count > 0:
        label = "Coach watching"
        severity = "info"
        if overshoot_count > 0:
            detail = (
                "Recent slight overpour noted. Keep the next pours controlled."
                if not tone.strict
                else "Recent over-target noted. Maintain strict pace and verification."
            )
        elif undershoot_count > 0:
            detail = (
                "Recent underpours noted. Commit a little closer when you still have room."
                if not tone.strict
                else "Recent under-target noted. Maintain strict approach to displayed target."
            )
        else:
            detail = (
                "Recovery history noted. Keep controlled pacing for the next pours."
                if not tone.strict
                else "Recovery history noted. Maintain strict pace and verification."
            )

    return {"label": label, "detail": detail, "severity": severity}

