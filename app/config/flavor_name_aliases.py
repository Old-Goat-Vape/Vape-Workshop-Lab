from __future__ import annotations

from typing import Dict, List

# Alias map for imported naming variants -> internal canonical targets.
# Keep aliases normalized at runtime; raw values here are human-readable.
FLAVOR_NAME_ALIASES: List[Dict[str, str]] = [
    {
        "alias_raw": "CAP Strawberry",
        "brand_canonical": "CAP",
        "target_flavor_name": "Sweet Strawberry",
        "notes": "Common shorthand for CAP Sweet Strawberry",
        "confidence_override": "0.95",
    },
    {
        "alias_raw": "Meringue",
        "brand_canonical": "Flavour Art",
        "target_flavor_name": "Meringue",
        "notes": "Defaults to FA when brand omitted in imported rows",
        "confidence_override": "0.9",
    },
]

