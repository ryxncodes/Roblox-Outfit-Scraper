from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from .labels import FLAGS, PRESENTATION_LABELS, REVIEW_STATUSES, STYLE_LABELS
    from .workspace import Workspace, set_review
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from roblox_outfits.labels import FLAGS, PRESENTATION_LABELS, REVIEW_STATUSES, STYLE_LABELS
    from roblox_outfits.workspace import Workspace, set_review


def run(workspace: str = ".workspace") -> None:
    try:
        import streamlit as st
    except ImportError as exc:
        raise SystemExit(
            "Dashboard dependencies are missing. Install them with: "
            "python -m pip install -r requirements-classifier.txt"
        ) from exc

    ws = Workspace(Path(workspace))
    ws.init()
    st.set_page_config(page_title="Roblox Outfit Review", layout="wide")
    st.title("Roblox Outfit Review")

    if "review_index" not in st.session_state:
        st.session_state.review_index = 0

    with ws.connect() as conn:
        stats = {
            "Review queue": conn.execute(
                "SELECT COUNT(*) FROM outfits WHERE rejected=0 AND review_status IN ('unreviewed','uncertain')"
            ).fetchone()[0],
            "Approved": conn.execute(
                "SELECT COUNT(*) FROM outfits WHERE rejected=0 AND review_status IN ('approved','corrected')"
            ).fetchone()[0],
            "Rejected": conn.execute("SELECT COUNT(*) FROM outfits WHERE rejected=1").fetchone()[0],
            "Thumbnails": conn.execute("SELECT COUNT(*) FROM thumbnails WHERE path IS NOT NULL").fetchone()[0],
        }
        st.write(stats)

        statuses = st.sidebar.multiselect("Review status", REVIEW_STATUSES, default=["unreviewed", "uncertain"])
        source = st.sidebar.selectbox("Source", ["all", "currentAvatar", "savedOutfit"])
        avatar_type = st.sidebar.selectbox("Avatar type", ["all", "R6", "R15", "unknown"])
        only_needs_review = st.sidebar.checkbox("Needs review predictions", value=False)
        require_thumbnail = st.sidebar.checkbox("Only cached thumbnails", value=True)
        username_filter = st.sidebar.text_input("Username contains")
        limit = st.sidebar.number_input("Queue size", min_value=1, max_value=500, value=100)

        rows = fetch_review_rows(
            conn,
            statuses,
            source,
            avatar_type,
            only_needs_review,
            require_thumbnail,
            username_filter,
            int(limit),
        )
        if not rows:
            st.info("No outfits match the current filters.")
            return

        if st.sidebar.button("Previous"):
            st.session_state.review_index = max(0, st.session_state.review_index - 1)
            st.rerun()
        if st.sidebar.button("Next"):
            st.session_state.review_index = min(len(rows) - 1, st.session_state.review_index + 1)
            st.rerun()

        index = min(int(st.session_state.review_index), len(rows) - 1)
        st.caption(f"Showing {index + 1} of {len(rows)} matching outfits")
        render_card(st, conn, rows[index], on_done=lambda: advance(st, len(rows)))


def fetch_review_rows(conn, statuses, source, avatar_type, only_needs_review, require_thumbnail, username_filter, limit):
    where = ["o.rejected = 0"]
    params: list[object] = []
    if statuses:
        where.append("o.review_status IN ({})".format(",".join("?" for _ in statuses)))
        params.extend(statuses)
    if source != "all":
        where.append("o.source = ?")
        params.append(source)
    if avatar_type != "all":
        where.append("o.avatar_type = ?")
        params.append(avatar_type)
    if only_needs_review:
        where.append("COALESCE(p.needs_review, 1) = 1")
    if require_thumbnail:
        where.append("t.path IS NOT NULL")
    if username_filter:
        where.append("LOWER(o.username) LIKE ?")
        params.append(f"%{username_filter.lower()}%")
    sql = """
        SELECT o.*, t.path AS thumbnail_path, t.status AS thumbnail_status,
               p.presentation_label AS pred_presentation, p.presentation_confidence,
               p.styles_json AS pred_styles, p.flags_json AS pred_flags, p.needs_review,
               l.presentation_label AS label_presentation, l.style_labels_json, l.flags_json
        FROM outfits o
        LEFT JOIN thumbnails t ON t.entry_id = o.entry_id
        LEFT JOIN predictions p ON p.entry_id = o.entry_id
        LEFT JOIN labels l ON l.entry_id = o.entry_id
        WHERE {where}
        ORDER BY
            CASE o.review_status WHEN 'uncertain' THEN 0 WHEN 'unreviewed' THEN 1 ELSE 2 END,
            COALESCE(p.needs_review, 1) DESC,
            o.updated_at DESC
        LIMIT ?
    """.format(where=" AND ".join(where))
    params.append(limit)
    return list(conn.execute(sql, tuple(params)))


def render_card(st, conn, row, on_done) -> None:
    entry = json.loads(row["raw_json"] or "{}")
    assets = entry.get("assets", [])
    pred_styles = json.loads(row["pred_styles"] or "[]")
    pred_flags = json.loads(row["pred_flags"] or "{}")
    label_styles = json.loads(row["style_labels_json"] or "[]")
    label_flags = json.loads(row["flags_json"] or "{}")
    key = row["entry_id"]

    left, middle, right = st.columns([1.1, 1.2, 1.4])
    with left:
        if row["thumbnail_path"] and Path(row["thumbnail_path"]).exists():
            st.image(row["thumbnail_path"], width="stretch")
        else:
            st.info(f"Thumbnail: {row['thumbnail_status'] or 'missing'}")
        st.subheader(entry.get("username") or f"User {entry.get('userId')}")
        st.write(
            {
                "source": entry.get("source"),
                "userId": entry.get("userId"),
                "outfitId": entry.get("outfitId"),
                "avatarType": entry.get("avatarType"),
                "groupId": entry.get("groupId"),
                "rank": entry.get("rank"),
                "roleName": entry.get("roleName"),
            }
        )

    with middle:
        st.caption("Worn assets")
        st.dataframe(
            [
                {
                    "name": asset.get("name", ""),
                    "type": asset.get("assetTypeName") or asset.get("assetType", {}).get("name", ""),
                }
                for asset in assets
            ],
            width="stretch",
            hide_index=True,
        )
        if pred_styles:
            st.caption("Predicted styles")
            st.dataframe(pred_styles, width="stretch", hide_index=True)
        if row["pred_presentation"]:
            st.write(
                {
                    "predictedPresentation": row["pred_presentation"],
                    "presentationConfidence": row["presentation_confidence"],
                    "needsReview": bool(row["needs_review"]),
                }
            )

    with right:
        default_presentation = row["label_presentation"] or row["pred_presentation"] or "androgynous_or_unclear"
        presentation = st.selectbox(
            "Presentation",
            PRESENTATION_LABELS,
            index=list(PRESENTATION_LABELS).index(default_presentation)
            if default_presentation in PRESENTATION_LABELS
            else 2,
            key=f"presentation_{key}",
        )
        styles = st.multiselect(
            "Styles",
            STYLE_LABELS,
            default=label_styles or [item["label"] for item in pred_styles if item.get("label") in STYLE_LABELS],
            key=f"styles_{key}",
        )
        flags = {
            flag: st.checkbox(
                flag,
                value=bool(label_flags.get(flag, pred_flags.get(flag, False))),
                key=f"flag_{flag}_{key}",
            )
            for flag in FLAGS
        }
        reject_reason = st.text_input("Rejection reason", key=f"reject_{key}")
        buttons = st.columns(5)
        if buttons[0].button("Approve", key=f"approve_{key}"):
            set_review(conn, key, "approved", presentation, styles, flags)
            on_done()
        if buttons[1].button("Uncertain", key=f"uncertain_{key}"):
            set_review(conn, key, "uncertain", presentation, styles, flags)
            on_done()
        if buttons[2].button("Skip", key=f"skip_{key}"):
            set_review(conn, key, "skipped", presentation, styles, flags)
            on_done()
        if buttons[3].button("Reject", key=f"reject_button_{key}"):
            set_review(conn, key, "rejected", presentation, styles, flags, reject_reason or "manual")
            on_done()
        if buttons[4].button("Save", key=f"save_{key}"):
            set_review(conn, key, "corrected", presentation, styles, flags)
            on_done()


def advance(st, total: int) -> None:
    st.session_state.review_index = min(max(total - 2, 0), int(st.session_state.review_index))
    st.rerun()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".workspace")
    args, _unknown = parser.parse_known_args()
    run(args.workspace)


if __name__ == "__main__":
    main()
