from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ClassifierConfig:
    workspace: Path = Path(".workspace")
    style_threshold: float = 0.55
    review_threshold: float = 0.70
    top_styles: int = 3
    use_metadata_model: bool = True
    use_image_model: bool = False
    known_assets_path: Path = Path("roblox_outfits/known_assets.json")
    style_hints_path: Path = Path("roblox_outfits/style_hints.json")
    model_path: Path | None = None
    thumbnail_size: str = "420x420"

    @property
    def resolved_model_path(self) -> Path:
        return self.model_path or self.workspace / "models" / "metadata_model.joblib"


def load_json(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_classifier_config(path: Path | None = None, workspace: Path | None = None) -> ClassifierConfig:
    raw = load_json(path)
    ws = Path(workspace or raw.get("workspace", ".workspace"))
    return ClassifierConfig(
        workspace=ws,
        style_threshold=float(raw.get("styleThreshold", 0.55)),
        review_threshold=float(raw.get("reviewThreshold", 0.70)),
        top_styles=int(raw.get("topStyles", 3)),
        use_metadata_model=bool(raw.get("useMetadataModel", True)),
        use_image_model=bool(raw.get("useImageModel", False)),
        known_assets_path=Path(raw.get("knownAssetConfigPath", "roblox_outfits/known_assets.json")),
        style_hints_path=Path(raw.get("styleHintsPath", "roblox_outfits/style_hints.json")),
        model_path=Path(raw["modelPath"]) if raw.get("modelPath") else None,
        thumbnail_size=str(raw.get("thumbnailSize", "420x420")),
    )
