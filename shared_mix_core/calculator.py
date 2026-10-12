from __future__ import annotations

from dataclasses import dataclass

PG_DENSITY = 1.036
VG_DENSITY = 1.26


@dataclass(frozen=True)
class FlavorInput:
    name: str
    percent: float
    density: float = PG_DENSITY
    brand: str = ""


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
    pg_ml: float
    vg_ml: float
    flavor_g: float
    nicotine_g: float
    pg_g: float
    vg_g: float


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

    nic_carrier = (request.nicotine_carrier or "PG").strip().upper()
    if nic_carrier == "VG":
        vg_after_nic = max(0.0, target_vg_ml - nicotine_ml)
        pg_after_nic = target_pg_ml
    else:
        pg_after_nic = max(0.0, target_pg_ml - nicotine_ml)
        vg_after_nic = target_vg_ml

    pg_ml = max(0.0, pg_after_nic - flavor_ml)
    vg_ml = max(0.0, vg_after_nic)

    flavor_g = sum((request.target_ml * (f.percent / 100.0)) * f.density for f in request.flavors)
    nicotine_density = VG_DENSITY if nic_carrier == "VG" else PG_DENSITY
    nicotine_g = nicotine_ml * nicotine_density
    pg_g = pg_ml * PG_DENSITY
    vg_g = vg_ml * VG_DENSITY

    return MixResult(
        total_flavor_percent=round(total_flavor_percent, 4),
        flavor_ml=round(flavor_ml, 4),
        nicotine_ml=round(nicotine_ml, 4),
        pg_ml=round(pg_ml, 4),
        vg_ml=round(vg_ml, 4),
        flavor_g=round(flavor_g, 4),
        nicotine_g=round(nicotine_g, 4),
        pg_g=round(pg_g, 4),
        vg_g=round(vg_g, 4),
    )


_STEP_ML_EPS = 0.008


def compute_mix_step_breakdown(request: MixInput) -> list[dict]:
    result = compute_mix(request)
    out: list[dict] = []
    step_no = 0

    for flv in request.flavors:
        ml_i = float(request.target_ml) * (float(flv.percent) / 100.0)
        if ml_i < _STEP_ML_EPS:
            continue
        g_i = ml_i * float(flv.density)
        step_no += 1
        brand = str(flv.brand or "").strip()
        label = str(flv.name or "").strip() or "Concentrate"
        title = f"{label}" + (f" · {brand}" if brand else "")
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
                "tare_hint": (
                    f"Tare the bottle/scale, then pour {label} until you add about "
                    f"{g_i:.2f} g (≈ {ml_i:.2f} ml)."
                ),
            }
        )

    def _add_base(*, category: str, title: str, ml_v: float, g_v: float) -> None:
        nonlocal step_no
        if ml_v < _STEP_ML_EPS:
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
                "tare_hint": (
                    f"Tare, then add {title.lower()} until about +{g_v:.2f} g (≈ {ml_v:.2f} ml)."
                ),
            }
        )

    _add_base(category="nicotine", title="Nicotine base", ml_v=result.nicotine_ml, g_v=result.nicotine_g)
    _add_base(category="pg", title="PG (base)", ml_v=result.pg_ml, g_v=result.pg_g)
    _add_base(category="vg", title="VG (base)", ml_v=result.vg_ml, g_v=result.vg_g)
    return out

