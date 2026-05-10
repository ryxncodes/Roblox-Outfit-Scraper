from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .features import assets_from_entry, entry_text
from .labels import FLAGS

LAYERED_TYPES = {"jacketaccessory", "sweateraccessory", "dressskirtaccessory", "shirtaccessory", "pantsaccessory"}


def load_known_assets(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def deterministic_flags(entry: dict[str, Any], known_assets: dict[str, Any] | None = None) -> dict[str, bool]:
    known_assets = known_assets or {}
    assets = assets_from_entry(entry)
    asset_ids = {asset.get("id") for asset in assets}
    names = " ".join(str(asset.get("name", "")).lower() for asset in assets)
    types = {str(asset.get("assetTypeName", "")).lower() for asset in assets}
    avatar_type = str(entry.get("avatarType", "")).upper()

    flags = {flag: False for flag in FLAGS}
    flags["headless"] = _known_match("headless", known_assets, asset_ids, names)
    flags["korblox"] = _known_match("korblox", known_assets, asset_ids, names)
    flags["layered_clothing"] = bool(types.intersection(LAYERED_TYPES))
    flags["classic_clothing"] = "shirt" in types and "pants" in types
    flags["r6"] = avatar_type == "R6"
    flags["r15"] = avatar_type == "R15"
    flags["default_clothing"] = "default-clothing" in entry_text(entry)
    return flags


def _known_match(key: str, known_assets: dict[str, Any], asset_ids: set[Any], names: str) -> bool:
    config = known_assets.get(key, {})
    ids = set(config.get("assetIds", []))
    hints = [str(value).lower() for value in config.get("nameHints", [])]
    return bool(ids.intersection(asset_ids)) or any(hint in names for hint in hints)


def style_hint_scores(text: str, hints: dict[str, list[str]]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for label, keywords in hints.items():
        matches = sum(1 for keyword in keywords if keyword.lower() in text)
        if matches:
            scores[label] = min(0.25, 0.08 * matches)
    return scores
