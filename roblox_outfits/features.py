from __future__ import annotations

import json
from typing import Any


def asset_type_name(asset: dict[str, Any]) -> str:
    asset_type = asset.get("assetType")
    if isinstance(asset_type, dict):
        return str(asset_type.get("name", ""))
    return str(asset.get("assetTypeName", ""))


def normalize_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": asset.get("id"),
        "name": str(asset.get("name", "")),
        "assetTypeName": asset_type_name(asset),
    }


def assets_from_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    assets = entry.get("assets")
    if isinstance(assets, list) and assets:
        return [normalize_asset(asset) for asset in assets if isinstance(asset, dict)]
    return [
        {"id": asset_id, "name": "", "assetTypeName": ""}
        for asset_id in entry.get("assetIds", [])
        if isinstance(asset_id, int)
    ]


def entry_text(entry: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.append(str(entry.get("name", "")))
    parts.append(str(entry.get("avatarType", "")))
    parts.extend(str(tag) for tag in entry.get("tags", []))
    for asset in assets_from_entry(entry):
        parts.append(str(asset.get("name", "")))
        parts.append(str(asset.get("assetTypeName", "")))
    return " ".join(part for part in parts if part).lower()


def row_to_entry(row: Any) -> dict[str, Any]:
    raw = json.loads(row["raw_json"] or "{}")
    raw["entryId"] = row["entry_id"]
    raw["reviewStatus"] = row["review_status"]
    raw["rejected"] = bool(row["rejected"])
    return raw
