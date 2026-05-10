from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .features import entry_text, row_to_entry
from .labels import PRESENTATION_LABELS, STYLE_LABELS
from .models import require_ml


def train_metadata_model(conn: Any, model_path: Path) -> dict[str, Any]:
    dump, _load, TfidfVectorizer, LogisticRegression, OneVsRestClassifier, MultiLabelBinarizer = require_ml()
    rows = list(
        conn.execute(
            """
            SELECT o.*, l.presentation_label, l.style_labels_json
            FROM outfits o
            JOIN labels l ON l.entry_id = o.entry_id
            WHERE o.rejected = 0
              AND o.review_status IN ('approved', 'corrected')
              AND (l.presentation_label IS NOT NULL OR l.style_labels_json != '[]')
            """
        )
    )
    if len(rows) < 5:
        raise SystemExit("Need at least 5 approved/corrected labeled outfits before training.")

    texts: list[str] = []
    presentation_y: list[str] = []
    style_y: list[list[str]] = []
    for row in rows:
        entry = row_to_entry(row)
        texts.append(entry_text(entry))
        presentation_y.append(row["presentation_label"] or "androgynous_or_unclear")
        styles = [label for label in json.loads(row["style_labels_json"] or "[]") if label in STYLE_LABELS]
        style_y.append(styles or ["other_uncertain"])

    vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1, max_features=12000)
    x = vectorizer.fit_transform(texts)
    presentation_model = LogisticRegression(max_iter=1000, class_weight="balanced")
    presentation_model.fit(x, presentation_y)

    mlb = MultiLabelBinarizer()
    y_styles = mlb.fit_transform(style_y)
    style_model = OneVsRestClassifier(LogisticRegression(max_iter=1000, class_weight="balanced"))
    style_model.fit(x, y_styles)

    model_run_id = f"metadata_{int(time.time())}"
    model = {
        "modelRunId": model_run_id,
        "kind": "metadata_text_v1",
        "vectorizer": vectorizer,
        "presentationModel": presentation_model,
        "styleModel": style_model,
        "styleBinarizer": mlb,
        "presentationLabels": list(PRESENTATION_LABELS),
        "styleLabels": list(STYLE_LABELS),
        "trainedAt": int(time.time()),
        "trainingRows": len(rows),
    }
    model_path.parent.mkdir(parents=True, exist_ok=True)
    dump(model, model_path)
    conn.execute(
        "INSERT OR REPLACE INTO model_runs(model_run_id, model_type, path, metrics_json, created_at) VALUES(?, ?, ?, ?, ?)",
        (
            model_run_id,
            "metadata_text_v1",
            str(model_path),
            json.dumps({"trainingRows": len(rows)}),
            int(time.time()),
        ),
    )
    return {"modelRunId": model_run_id, "trainingRows": len(rows), "path": str(model_path)}
