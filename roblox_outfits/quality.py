from __future__ import annotations

from typing import Any

from .features import assets_from_entry

JUNK_ONLY_TYPES = {
    "dynamichead",
    "moodanimation",
    "eyebrowaccessory",
    "eyelashaccessory",
    "climbanimation",
    "fallanimation",
    "idleanimation",
    "jumpanimation",
    "runanimation",
    "swimanimation",
    "walkanimation",
    "leftarm",
    "rightarm",
    "leftleg",
    "rightleg",
    "torso",
    "leftshoeaccessory",
    "rightshoeaccessory",
}

STYLE_SIGNAL_TYPES = {
    "shirt",
    "pants",
    "tshirt",
    "hat",
    "hairaccessory",
    "faceaccessory",
    "neckaccessory",
    "shoulderaccessory",
    "frontaccessory",
    "backaccessory",
    "waistaccessory",
    "jacketaccessory",
    "sweateraccessory",
    "shortsaccessory",
    "dressskirtaccessory",
    "tshirtaccessory",
    "shirtaccessory",
    "pantsaccessory",
}

CLOTHING_TYPES = {
    "shirt",
    "pants",
    "tshirt",
    "jacketaccessory",
    "sweateraccessory",
    "shortsaccessory",
    "dressskirtaccessory",
    "tshirtaccessory",
    "shirtaccessory",
    "pantsaccessory",
}


def is_low_information_saved_outfit(entry: dict[str, Any]) -> bool:
    if entry.get("source") != "savedOutfit":
        return False
    assets = assets_from_entry(entry)
    types = {str(asset.get("assetTypeName", "")).replace(" ", "").lower() for asset in assets}
    if not assets:
        return True
    if types and types.issubset(JUNK_ONLY_TYPES):
        return True
    style_signal_count = sum(1 for asset in assets if _type(asset) in STYLE_SIGNAL_TYPES)
    clothing_count = sum(1 for asset in assets if _type(asset) in CLOTHING_TYPES)
    if len(assets) <= 4 and clothing_count == 0:
        return True
    if style_signal_count < 2 and clothing_count == 0:
        return True
    return False


def _type(asset: dict[str, Any]) -> str:
    return str(asset.get("assetTypeName", "")).replace(" ", "").lower()
