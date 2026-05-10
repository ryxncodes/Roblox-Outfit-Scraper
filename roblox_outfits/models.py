from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PredictionResult:
    presentation_label: str | None
    presentation_confidence: float | None
    styles: list[dict[str, float]]
    flags: dict[str, bool]
    method: str
    needs_review: bool
    model_run_id: str | None = None
    warnings: list[str] | None = None


def require_ml() -> tuple[Any, Any, Any, Any, Any]:
    try:
        from joblib import dump, load
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.multiclass import OneVsRestClassifier
        from sklearn.preprocessing import MultiLabelBinarizer
    except ImportError as exc:
        raise SystemExit(
            "Classification dependencies are missing. Install them with: "
            "python -m pip install -r requirements-classifier.txt"
        ) from exc
    return dump, load, TfidfVectorizer, LogisticRegression, OneVsRestClassifier, MultiLabelBinarizer
