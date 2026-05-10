from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import ClassifierConfig
from .features import entry_text, row_to_entry
from .heuristics import deterministic_flags, load_known_assets, style_hint_scores
from .labels import STYLE_LABELS
from .models import PredictionResult, require_ml


def load_style_hints(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def classify_workspace(conn: Any, config: ClassifierConfig, limit: int | None = None) -> int:
    rows = list(
        conn.execute(
            """
            SELECT * FROM outfits
            WHERE rejected = 0
              AND review_status IN ('unreviewed', 'skipped', 'uncertain')
            ORDER BY updated_at ASC
            """
        )
    )
    if limit:
        rows = rows[:limit]
    known_assets = load_known_assets(config.known_assets_path)
    style_hints = load_style_hints(config.style_hints_path)
    model = _load_model(config.resolved_model_path) if config.resolved_model_path.exists() else None
    count = 0
    for row in rows:
        entry = row_to_entry(row)
        result = classify_entry(entry, config, known_assets, style_hints, model)
        save_prediction(conn, row["entry_id"], result)
        count += 1
    return count


def classify_entry(
    entry: dict[str, Any],
    config: ClassifierConfig,
    known_assets: dict[str, Any],
    style_hints: dict[str, list[str]],
    model: dict[str, Any] | None,
) -> PredictionResult:
    text = entry_text(entry)
    flags = deterministic_flags(entry, known_assets)
    style_scores = {label: 0.0 for label in STYLE_LABELS}
    presentation_label = "androgynous_or_unclear"
    presentation_confidence = 0.0
    warnings: list[str] = []
    method_parts = ["heuristics"]

    if model:
        vector = model["vectorizer"].transform([text])
        presentation_model = model["presentationModel"]
        if hasattr(presentation_model, "predict_proba"):
            probs = presentation_model.predict_proba(vector)[0]
            idx = int(probs.argmax())
            presentation_label = str(presentation_model.classes_[idx])
            presentation_confidence = float(probs[idx])
        else:
            presentation_label = str(presentation_model.predict(vector)[0])
            presentation_confidence = 0.51

        style_model = model["styleModel"]
        labels = list(model["styleBinarizer"].classes_)
        if hasattr(style_model, "predict_proba"):
            probs = style_model.predict_proba(vector)
            for label, score in zip(labels, probs[0]):
                style_scores[label] = max(style_scores.get(label, 0.0), float(score))
        method_parts.append("metadata_text_v1")
    else:
        warnings.append("metadata model missing")

    for label, boost in style_hint_scores(text, style_hints).items():
        style_scores[label] = min(1.0, style_scores.get(label, 0.0) + boost)
    if flags.get("classic_clothing"):
        style_scores["classic_blocky"] = max(style_scores["classic_blocky"], 0.35)
    if flags.get("korblox"):
        style_scores["rich_flex"] = max(style_scores["rich_flex"], 0.25)

    styles = [
        {"label": label, "confidence": round(score, 4)}
        for label, score in sorted(style_scores.items(), key=lambda item: item[1], reverse=True)
        if score >= config.style_threshold
    ][: config.top_styles]
    if not styles:
        highest = max(style_scores.values()) if style_scores else 0.0
        styles = [{"label": "other_uncertain", "confidence": round(highest, 4)}]

    highest_style = styles[0]["confidence"] if styles else 0.0
    needs_review = (
        highest_style < config.review_threshold
        or presentation_confidence < config.review_threshold
        or model is None
    )
    method = "+".join(method_parts)
    return PredictionResult(
        presentation_label=presentation_label,
        presentation_confidence=round(presentation_confidence, 4),
        styles=styles,
        flags=flags,
        method=method,
        needs_review=needs_review,
        model_run_id=model.get("modelRunId") if model else None,
        warnings=warnings,
    )


def save_prediction(conn: Any, entry_id: str, result: PredictionResult) -> None:
    conn.execute(
        """
        INSERT INTO predictions(
            entry_id, presentation_label, presentation_confidence, styles_json, flags_json,
            method, needs_review, model_run_id, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(entry_id) DO UPDATE SET
            presentation_label=excluded.presentation_label,
            presentation_confidence=excluded.presentation_confidence,
            styles_json=excluded.styles_json,
            flags_json=excluded.flags_json,
            method=excluded.method,
            needs_review=excluded.needs_review,
            model_run_id=excluded.model_run_id,
            updated_at=excluded.updated_at
        """,
        (
            entry_id,
            result.presentation_label,
            result.presentation_confidence,
            json.dumps(result.styles),
            json.dumps(result.flags),
            result.method,
            1 if result.needs_review else 0,
            result.model_run_id,
            int(time.time()),
        ),
    )


def _load_model(path: Path) -> dict[str, Any]:
    _dump, load, *_rest = require_ml()
    return load(path)
