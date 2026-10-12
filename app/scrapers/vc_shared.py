from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Tuple


def normalize_text(value: str) -> str:
    s = (value or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s&/+\-]", "", s)
    return s


def normalize_flavor_name(value: str) -> str:
    return normalize_text(value)


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", ".")
    if not s:
        return None
    s = s.replace("%", "")
    s = re.sub(r"[^0-9.\-]", "", s)
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def compute_content_hash(payload: dict[str, Any]) -> str:
    stable = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def parse_steep(text: str) -> Tuple[float | None, str | None]:
    s = normalize_text(text)
    if not s:
        return None, None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(day|days|week|weeks|month|months|hour|hours)", s)
    if not m:
        return None, None
    val = to_float(m.group(1))
    unit = m.group(2)
    if unit.endswith("s"):
        unit = unit[:-1]
    return val, unit


def detect_has_any_token(texts: Iterable[str], tokens: Iterable[str]) -> bool:
    hay = " ".join([(t or "").lower() for t in texts])
    for tok in tokens:
        if re.search(rf"\b{re.escape((tok or '').lower())}\b", hay):
            return True
    return False
