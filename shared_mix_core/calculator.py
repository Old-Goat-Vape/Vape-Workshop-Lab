from __future__ import annotations

from dataclasses import dataclass

PG_DENSITY = 1.036
VG_DENSITY = 1.26

# Skip pour steps / treat as zero-requirement at or below this mass.
ZERO_POUR_GRAMS_EPS = 0.02
_STEP_ML_EPS = 0.008


CARRIER_SOURCE_EXPLICIT = "explicit"
CARRIER_SOURCE_ASSUMED_DEFAULT = "assumed_default"


@dataclass(frozen=True)
class FlavorInput:
    name: str
    percent: float
    density: float = PG_DENSITY
    brand: str = ""
    # Finished-liquid carrier contribution: "PG" or "VG".
    # When omitted/blank/unknown → PG (assumed_default). Density is never used for this.
    carrier: str = ""


@dataclass(frozen=True)
class MixInput:
    target_ml: float
    target_nic_mg_ml: float
    nic_base_mg_ml: float
    pg_ratio: float
    vg_ratio: float
    nicotine_carrier: str
    flavors: list[FlavorInput]


@dataclass(frozen=True)
class MixResult:
    total_flavor_percent: float
    flavor_ml: float
    nicotine_ml: float
    # Standalone base pours (what the operator adds from PG/VG bottles).
    pg_ml: float
    vg_ml: float
    flavor_g: float
    nicotine_g: float
    pg_g: float
    vg_g: float
    # Finished e-liquid carrier composition (flavours + nicotine + bases).
    final_pg_ml: float = 0.0
    final_vg_ml: float = 0.0
    final_pg_percent: float = 0.0
    final_vg_percent: float = 0.0
    target_pg_percent: float = 0.0
    target_vg_percent: float = 0.0
    flavor_pg_ml: float = 0.0
    flavor_vg_ml: float = 0.0
    nicotine_pg_ml: float = 0.0
    nicotine_vg_ml: float = 0.0
    ratio_achievable: bool = True
    ratio_warning: str = ""


def _ratio_as_percent(value: float) -> float:
    return value * 100.0 if 0 <= value <= 1 else value


def _coerce_ratio_pair(pg_ratio: float, vg_ratio: float) -> tuple[float, float]:
    pg = _ratio_as_percent(pg_ratio)
    vg = _ratio_as_percent(vg_ratio)
    if pg <= 0 and vg <= 0:
        return 30.0, 70.0
    total = pg + vg
    if total <= 0:
        return 30.0, 70.0
    return (pg / total) * 100.0, (vg / total) * 100.0


def resolve_carrier(raw: str | None) -> tuple[str, str]:
    """Return (carrier, source). Explicit PG/VG kept; anything else → PG assumed_default.

    Density must never decide carrier identity — only ml→grams conversion uses density.
    """
    text = str(raw or "").strip().upper()
    if text == "PG":
        return "PG", CARRIER_SOURCE_EXPLICIT
    if text == "VG":
        return "VG", CARRIER_SOURCE_EXPLICIT
    return "PG", CARRIER_SOURCE_ASSUMED_DEFAULT


def normalize_carrier(raw: str | None, *, density: float | None = None) -> str:
    """Return 'PG' or 'VG'. Unknown/blank → PG. ``density`` is ignored (API compat)."""
    _ = density  # intentionally unused — density is for grams only
    return resolve_carrier(raw)[0]


def format_carrier_display(
    raw: str | None = None,
    *,
    carrier: str | None = None,
    source: str | None = None,
) -> str:
    """User-facing label: PG, PG (assumed), or VG."""
    if carrier is not None and source is not None:
        c = str(carrier).strip().upper() or "PG"
        src = str(source).strip().lower() or CARRIER_SOURCE_ASSUMED_DEFAULT
    else:
        c, src = resolve_carrier(raw)
    if c == "VG":
        return "VG"
    if src == CARRIER_SOURCE_ASSUMED_DEFAULT:
        return "PG (assumed)"
    return "PG"


def _validate_mix_request(request: MixInput) -> None:
    if request.target_ml <= 0:
        raise ValueError("target_ml must be greater than zero.")
    if request.nic_base_mg_ml <= 0:
        raise ValueError("nic_base_mg_ml must be greater than zero.")
    if request.target_nic_mg_ml < 0:
        raise ValueError("target_nic_mg_ml cannot be negative.")
    for flv in request.flavors:
        if flv.percent < 0:
            raise ValueError("Flavor percentages cannot be negative.")
        if flv.density <= 0:
            raise ValueError("Flavor density must be greater than zero.")


def compute_mix(request: MixInput) -> MixResult:
    """Compute pour amounts and finished-liquid PG/VG composition.

    Final PG/VG is the estimated carrier split of the finished e-liquid, including
    flavour and nicotine carriers. Standalone PG/VG pours are the balance needed
    to approach the requested target when mathematically possible.
    """
    _validate_mix_request(request)

    pg_target, vg_target = _coerce_ratio_pair(request.pg_ratio, request.vg_ratio)
    total_flavor_percent = sum(item.percent for item in request.flavors)
    flavor_ml = request.target_ml * (total_flavor_percent / 100.0)
    nicotine_ml = (
        request.target_ml * request.target_nic_mg_ml / request.nic_base_mg_ml
        if request.target_nic_mg_ml > 0
        else 0.0
    )

    target_pg_ml = request.target_ml * (pg_target / 100.0)
    target_vg_ml = request.target_ml * (vg_target / 100.0)

    flavor_pg_ml = 0.0
    flavor_vg_ml = 0.0
    for flv in request.flavors:
        ml_i = request.target_ml * (float(flv.percent) / 100.0)
        carrier, _src = resolve_carrier(flv.carrier)
        if carrier == "VG":
            flavor_vg_ml += ml_i
        else:
            flavor_pg_ml += ml_i

    nic_carrier, _nic_src = resolve_carrier(request.nicotine_carrier)
    nicotine_pg_ml = nicotine_ml if nic_carrier == "PG" else 0.0
    nicotine_vg_ml = nicotine_ml if nic_carrier == "VG" else 0.0

    # Standalone bases make up the remainder of the *target* finished carriers.
    pg_needed = target_pg_ml - flavor_pg_ml - nicotine_pg_ml
    vg_needed = target_vg_ml - flavor_vg_ml - nicotine_vg_ml

    ratio_achievable = True
    ratio_warning = ""
    pg_ml = pg_needed
    vg_ml = vg_needed

    if pg_needed < -_STEP_ML_EPS or vg_needed < -_STEP_ML_EPS:
        ratio_achievable = False
        # Clamp pours at zero — cannot remove carrier already contributed by ingredients.
        pg_ml = max(0.0, pg_needed)
        vg_ml = max(0.0, vg_needed)
        excess_pg = max(0.0, -pg_needed)
        excess_vg = max(0.0, -vg_needed)
        parts = []
        if excess_pg > _STEP_ML_EPS:
            parts.append(
                f"PG-based ingredients already contribute {excess_pg + target_pg_ml:.1f} ml "
                f"(target allows {target_pg_ml:.1f} ml)"
            )
        if excess_vg > _STEP_ML_EPS:
            parts.append(
                f"VG-based ingredients already contribute {excess_vg + target_vg_ml:.1f} ml "
                f"(target allows {target_vg_ml:.1f} ml)"
            )
        ratio_warning = (
            "Target PG/VG cannot be reached with the current carrier composition. "
            + "; ".join(parts)
            + "."
        )

    final_pg_ml = flavor_pg_ml + nicotine_pg_ml + max(0.0, pg_ml)
    final_vg_ml = flavor_vg_ml + nicotine_vg_ml + max(0.0, vg_ml)
    final_total = final_pg_ml + final_vg_ml
    if final_total > 0:
        final_pg_percent = (final_pg_ml / final_total) * 100.0
        final_vg_percent = (final_vg_ml / final_total) * 100.0
    else:
        final_pg_percent, final_vg_percent = pg_target, vg_target

    flavor_g = sum((request.target_ml * (f.percent / 100.0)) * f.density for f in request.flavors)
    nicotine_density = VG_DENSITY if nic_carrier == "VG" else PG_DENSITY
    nicotine_g = nicotine_ml * nicotine_density
    pg_g = max(0.0, pg_ml) * PG_DENSITY
    vg_g = max(0.0, vg_ml) * VG_DENSITY

    return MixResult(
        total_flavor_percent=round(total_flavor_percent, 4),
        flavor_ml=round(flavor_ml, 4),
        nicotine_ml=round(nicotine_ml, 4),
        pg_ml=round(max(0.0, pg_ml), 4),
        vg_ml=round(max(0.0, vg_ml), 4),
        flavor_g=round(flavor_g, 4),
        nicotine_g=round(nicotine_g, 4),
        pg_g=round(pg_g, 4),
        vg_g=round(vg_g, 4),
        final_pg_ml=round(final_pg_ml, 4),
        final_vg_ml=round(final_vg_ml, 4),
        final_pg_percent=round(final_pg_percent, 4),
        final_vg_percent=round(final_vg_percent, 4),
        target_pg_percent=round(pg_target, 4),
        target_vg_percent=round(vg_target, 4),
        flavor_pg_ml=round(flavor_pg_ml, 4),
        flavor_vg_ml=round(flavor_vg_ml, 4),
        nicotine_pg_ml=round(nicotine_pg_ml, 4),
        nicotine_vg_ml=round(nicotine_vg_ml, 4),
        ratio_achievable=ratio_achievable,
        ratio_warning=ratio_warning,
    )


def compute_mix_step_breakdown(request: MixInput) -> list[dict]:
    result = compute_mix(request)
    out: list[dict] = []
    step_no = 0

    for flv in request.flavors:
        ml_i = float(request.target_ml) * (float(flv.percent) / 100.0)
        g_i = ml_i * float(flv.density)
        if ml_i < _STEP_ML_EPS or g_i <= ZERO_POUR_GRAMS_EPS:
            continue
        step_no += 1
        brand = str(flv.brand or "").strip()
        label = str(flv.name or "").strip() or "Concentrate"
        title = f"{label}" + (f" · {brand}" if brand else "")
        carrier, carrier_source = resolve_carrier(flv.carrier)
        out.append(
            {
                "step": step_no,
                "category": "concentrate",
                "name": label,
                "brand": brand,
                "percent": round(float(flv.percent), 4),
                "ml": round(ml_i, 4),
                "grams": round(g_i, 4),
                "title": title,
                "carrier": carrier,
                "carrier_source": carrier_source,
                "carrier_label": format_carrier_display(carrier=carrier, source=carrier_source),
                "density": round(float(flv.density), 4),
                "tare_hint": (
                    f"Tare the bottle/scale, then pour {label} until you add about "
                    f"{g_i:.2f} g (≈ {ml_i:.2f} ml)."
                ),
            }
        )

    def _add_base(
        *,
        category: str,
        title: str,
        ml_v: float,
        g_v: float,
        carrier: str | None = None,
    ) -> None:
        nonlocal step_no
        if ml_v < _STEP_ML_EPS or g_v <= ZERO_POUR_GRAMS_EPS:
            return
        step_no += 1
        out.append(
            {
                "step": step_no,
                "category": category,
                "name": title,
                "brand": "",
                "percent": None,
                "ml": round(float(ml_v), 4),
                "grams": round(float(g_v), 4),
                "title": title,
                "carrier": carrier or ("VG" if category == "vg" else "PG"),
                "tare_hint": (
                    f"Tare, then add {title.lower()} until about +{g_v:.2f} g (≈ {ml_v:.2f} ml)."
                ),
            }
        )

    nic_carrier, _nic_src = resolve_carrier(request.nicotine_carrier)
    _add_base(
        category="nicotine",
        title="Nicotine base",
        ml_v=result.nicotine_ml,
        g_v=result.nicotine_g,
        carrier=nic_carrier,
    )
    _add_base(category="pg", title="PG (base)", ml_v=result.pg_ml, g_v=result.pg_g, carrier="PG")
    _add_base(category="vg", title="VG (base)", ml_v=result.vg_ml, g_v=result.vg_g, carrier="VG")
    return out
