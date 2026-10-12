"""Parse and validate guided-mix targets submitted from the recipe detail page."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class GuidedMixFormError(ValueError):
    """Invalid guided-mix form with field-level errors and preserved input."""

    def __init__(self, *, errors: dict[str, str], values: dict[str, str]) -> None:
        self.errors = errors
        self.values = values
        summary = errors.get("mix_summary") or next(iter(errors.values()), "Invalid mix settings.")
        super().__init__(summary)


@dataclass(frozen=True)
class GuidedMixTargets:
    requested_version_id: int | None
    target_ml: float
    target_nic_mg_ml: float
    nic_base_mg_ml: float
    pg_ratio: float
    vg_ratio: float
    nicotine_carrier: str

    def as_create_kwargs(self) -> dict[str, Any]:
        return {
            "requested_version_id": self.requested_version_id,
            "target_ml": self.target_ml,
            "target_nic_mg_ml": self.target_nic_mg_ml,
            "nic_base_mg_ml": self.nic_base_mg_ml,
            "pg_ratio": self.pg_ratio,
            "vg_ratio": self.vg_ratio,
            "nicotine_carrier": self.nicotine_carrier,
        }


def _raw_values(data: Mapping[str, Any]) -> dict[str, str]:
    keys = (
        "version_id",
        "target_ml",
        "target_nic_mg_ml",
        "pg_ratio",
        "vg_ratio",
        "nic_base_mg_ml",
        "nicotine_carrier",
    )
    return {k: str(data.get(k) or "").strip() for k in keys}


def _parse_float(raw: str, *, field: str, errors: dict[str, str], minimum: float | None = None) -> float | None:
    if raw == "":
        errors[field] = "This field is required."
        return None
    try:
        value = float(raw)
    except ValueError:
        errors[field] = "Enter a valid number."
        return None
    if minimum is not None and value < minimum:
        errors[field] = f"Must be at least {minimum:g}."
        return None
    return value


def parse_guided_mix_form(data: Mapping[str, Any]) -> GuidedMixTargets:
    """Validate recipe-detail guided mix POST fields. Raises GuidedMixFormError."""
    values = _raw_values(data)
    errors: dict[str, str] = {}

    version_raw = values["version_id"]
    requested_version_id: int | None = None
    if version_raw:
        if not version_raw.isdigit():
            errors["version_id"] = "Choose a valid recipe version."
        else:
            requested_version_id = int(version_raw)

    target_ml = _parse_float(values["target_ml"], field="target_ml", errors=errors, minimum=5.0)
    target_nic = _parse_float(values["target_nic_mg_ml"], field="target_nic_mg_ml", errors=errors, minimum=0.0)
    pg_ratio = _parse_float(values["pg_ratio"], field="pg_ratio", errors=errors, minimum=0.0)
    vg_ratio = _parse_float(values["vg_ratio"], field="vg_ratio", errors=errors, minimum=0.0)
    nic_base = _parse_float(values["nic_base_mg_ml"], field="nic_base_mg_ml", errors=errors, minimum=1.0)

    carrier = values["nicotine_carrier"].upper() or "PG"
    if carrier not in ("PG", "VG"):
        errors["nicotine_carrier"] = "Nicotine carrier must be PG or VG."
        carrier = values["nicotine_carrier"]

    if pg_ratio is not None and vg_ratio is not None and abs((pg_ratio + vg_ratio) - 100.0) > 0.01:
        msg = "PG and VG must total 100%."
        errors.setdefault("pg_ratio", msg)
        errors.setdefault("vg_ratio", msg)

    if errors:
        raise GuidedMixFormError(errors=errors, values=values)

    assert target_ml is not None
    assert target_nic is not None
    assert pg_ratio is not None
    assert vg_ratio is not None
    assert nic_base is not None

    return GuidedMixTargets(
        requested_version_id=requested_version_id,
        target_ml=target_ml,
        target_nic_mg_ml=target_nic,
        nic_base_mg_ml=nic_base,
        pg_ratio=pg_ratio,
        vg_ratio=vg_ratio,
        nicotine_carrier=carrier,
    )


def mix_form_from_query_args(args: Mapping[str, Any]) -> dict[str, str] | None:
    """Prefill mix panel from legacy GET /guided-mix redirects."""
    keys = ("target_ml", "target_nic_mg_ml", "pg_ratio", "vg_ratio", "nic_base_mg_ml", "nicotine_carrier", "version_id")
    if not any(str(args.get(k) or "").strip() for k in keys):
        return None
    out = _raw_values(args)
    if args.get("version_id"):
        out["version_id"] = str(args.get("version_id") or "").strip()
    return out
