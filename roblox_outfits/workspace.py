from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 2


def entry_key(entry: dict[str, Any]) -> str:
    source = entry.get("source", "")
    outfit_id = entry.get("outfitId")
    if outfit_id:
        return f"savedOutfit:{outfit_id}"
    asset_ids = ",".join(str(value) for value in sorted(entry.get("assetIds", [])))
    digest = hashlib.sha1(asset_ids.encode("utf-8")).hexdigest()[:16]
    return f"currentAvatar:{entry.get('userId')}:{digest}"


def now_ts() -> int:
    return int(time.time())


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.db_path = self.root / "roblox_outfits.sqlite"

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for child in ("thumbnails", "models", "exports", "logs"):
            (self.root / child).mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            apply_schema(conn)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def upsert_entries(self, entries: Iterable[dict[str, Any]]) -> int:
        self.init()
        count = 0
        with self.connect() as conn:
            for entry in entries:
                upsert_entry(conn, entry)
                count += 1
        return count


def apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            rank INTEGER,
            role_name TEXT,
            UNIQUE(group_id, rank, role_name)
        );

        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS outfits (
            entry_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            outfit_id INTEGER,
            user_id INTEGER NOT NULL,
            username TEXT,
            group_id INTEGER,
            rank INTEGER,
            role_name TEXT,
            name TEXT,
            current_match INTEGER NOT NULL DEFAULT 0,
            avatar_type TEXT,
            tags_json TEXT NOT NULL DEFAULT '[]',
            raw_json TEXT NOT NULL DEFAULT '{}',
            review_status TEXT NOT NULL DEFAULT 'unreviewed',
            rejected INTEGER NOT NULL DEFAULT 0,
            rejection_reason TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS assets (
            asset_id INTEGER PRIMARY KEY,
            name TEXT,
            asset_type TEXT,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS outfit_assets (
            entry_id TEXT NOT NULL REFERENCES outfits(entry_id) ON DELETE CASCADE,
            asset_id INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(entry_id, asset_id)
        );

        CREATE TABLE IF NOT EXISTS thumbnails (
            entry_id TEXT PRIMARY KEY REFERENCES outfits(entry_id) ON DELETE CASCADE,
            path TEXT,
            status TEXT NOT NULL,
            image_url TEXT,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS labels (
            entry_id TEXT PRIMARY KEY REFERENCES outfits(entry_id) ON DELETE CASCADE,
            presentation_label TEXT,
            style_labels_json TEXT NOT NULL DEFAULT '[]',
            flags_json TEXT NOT NULL DEFAULT '{}',
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS predictions (
            entry_id TEXT PRIMARY KEY REFERENCES outfits(entry_id) ON DELETE CASCADE,
            presentation_label TEXT,
            presentation_confidence REAL,
            styles_json TEXT NOT NULL DEFAULT '[]',
            flags_json TEXT NOT NULL DEFAULT '{}',
            method TEXT,
            needs_review INTEGER NOT NULL DEFAULT 1,
            model_run_id TEXT,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS review_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL REFERENCES outfits(entry_id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS model_runs (
            model_run_id TEXT PRIMARY KEY,
            model_type TEXT NOT NULL,
            path TEXT,
            metrics_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scan_state (
            scan_key TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            group_id INTEGER,
            rank INTEGER,
            role_name TEXT,
            scan_type TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT,
            scanned_at INTEGER NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )


def scan_key(user_id: int, group_id: int | None, rank: int | None, role_name: str | None, scan_type: str) -> str:
    return f"{scan_type}:{group_id or 0}:{rank or 0}:{role_name or ''}:{user_id}"


def is_scan_complete(
    conn: sqlite3.Connection,
    user_id: int,
    group_id: int | None,
    rank: int | None,
    role_name: str | None,
    scan_type: str,
) -> bool:
    row = conn.execute(
        "SELECT status FROM scan_state WHERE scan_key = ?",
        (scan_key(user_id, group_id, rank, role_name, scan_type),),
    ).fetchone()
    if row:
        return row["status"] == "complete"
    if scan_type == "current":
        existing = conn.execute(
            """
            SELECT 1 FROM outfits
            WHERE source='currentAvatar'
              AND user_id=?
              AND COALESCE(group_id, 0)=COALESCE(?, 0)
              AND COALESCE(rank, 0)=COALESCE(?, 0)
              AND COALESCE(role_name, '')=COALESCE(?, '')
            LIMIT 1
            """,
            (user_id, group_id, rank, role_name),
        ).fetchone()
        return existing is not None
    return False


def mark_scan(
    conn: sqlite3.Connection,
    user_id: int,
    group_id: int | None,
    rank: int | None,
    role_name: str | None,
    scan_type: str,
    status: str = "complete",
    message: str | None = None,
) -> None:
    ts = now_ts()
    conn.execute(
        """
        INSERT INTO scan_state(scan_key, user_id, group_id, rank, role_name, scan_type, status, message, scanned_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scan_key) DO UPDATE SET
            status=excluded.status,
            message=excluded.message,
            scanned_at=excluded.scanned_at
        """,
        (scan_key(user_id, group_id, rank, role_name, scan_type), user_id, group_id, rank, role_name, scan_type, status, message, ts),
    )


def upsert_entry(conn: sqlite3.Connection, entry: dict[str, Any]) -> str:
    ts = now_ts()
    eid = entry_key(entry)
    conn.execute(
        """
        INSERT INTO users(user_id, username, updated_at)
        VALUES(?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, updated_at=excluded.updated_at
        """,
        (entry.get("userId"), entry.get("username", ""), ts),
    )
    if entry.get("groupId") is not None:
        conn.execute(
            """
            INSERT INTO sources(group_id, rank, role_name)
            VALUES(?, ?, ?)
            ON CONFLICT(group_id, rank, role_name) DO NOTHING
            """,
            (entry.get("groupId"), entry.get("rank"), entry.get("roleName", "")),
        )
    conn.execute(
        """
        INSERT INTO outfits(
            entry_id, source, outfit_id, user_id, username, group_id, rank, role_name, name,
            current_match, avatar_type, tags_json, raw_json, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(entry_id) DO UPDATE SET
            source=excluded.source,
            outfit_id=excluded.outfit_id,
            user_id=excluded.user_id,
            username=excluded.username,
            group_id=excluded.group_id,
            rank=excluded.rank,
            role_name=excluded.role_name,
            name=excluded.name,
            current_match=excluded.current_match,
            avatar_type=excluded.avatar_type,
            tags_json=excluded.tags_json,
            raw_json=excluded.raw_json,
            updated_at=excluded.updated_at
        """,
        (
            eid,
            entry.get("source"),
            entry.get("outfitId"),
            entry.get("userId"),
            entry.get("username", ""),
            entry.get("groupId"),
            entry.get("rank"),
            entry.get("roleName", ""),
            entry.get("name", ""),
            1 if entry.get("currentMatch") else 0,
            entry.get("avatarType", "unknown"),
            json.dumps(entry.get("tags", []), ensure_ascii=False),
            json.dumps(entry, ensure_ascii=False),
            ts,
            ts,
        ),
    )
    conn.execute("DELETE FROM outfit_assets WHERE entry_id = ?", (eid,))
    for index, asset in enumerate(_entry_assets(entry)):
        asset_id = asset.get("id")
        if not isinstance(asset_id, int):
            continue
        conn.execute(
            """
            INSERT INTO assets(asset_id, name, asset_type, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                name=excluded.name,
                asset_type=excluded.asset_type,
                updated_at=excluded.updated_at
            """,
            (asset_id, asset.get("name", ""), asset.get("assetTypeName", ""), ts),
        )
        conn.execute(
            "INSERT OR REPLACE INTO outfit_assets(entry_id, asset_id, position) VALUES(?, ?, ?)",
            (eid, asset_id, index),
        )
    return eid


def _entry_assets(entry: dict[str, Any]) -> list[dict[str, Any]]:
    raw_assets = entry.get("assets")
    if isinstance(raw_assets, list) and raw_assets:
        return [asset for asset in raw_assets if isinstance(asset, dict)]
    return [
        {"id": asset_id, "name": "", "assetTypeName": ""}
        for asset_id in entry.get("assetIds", [])
        if isinstance(asset_id, int)
    ]


def rows_to_entries(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for row in rows:
        raw = json.loads(row["raw_json"] or "{}")
        raw["reviewStatus"] = row["review_status"]
        raw["rejected"] = bool(row["rejected"])
        raw["rejectionReason"] = row["rejection_reason"]
        entries.append(raw)
    return entries


def fetch_outfits(conn: sqlite3.Connection, where: str = "", params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    sql = "SELECT * FROM outfits"
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY updated_at DESC, entry_id"
    return list(conn.execute(sql, params))


def set_review(
    conn: sqlite3.Connection,
    entry_id: str,
    status: str,
    presentation: str | None,
    styles: list[str],
    flags: dict[str, bool],
    rejection_reason: str | None = None,
) -> None:
    ts = now_ts()
    rejected = 1 if status == "rejected" else 0
    conn.execute(
        "UPDATE outfits SET review_status=?, rejected=?, rejection_reason=?, updated_at=? WHERE entry_id=?",
        (status, rejected, rejection_reason, ts, entry_id),
    )
    conn.execute(
        """
        INSERT INTO labels(entry_id, presentation_label, style_labels_json, flags_json, updated_at)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(entry_id) DO UPDATE SET
            presentation_label=excluded.presentation_label,
            style_labels_json=excluded.style_labels_json,
            flags_json=excluded.flags_json,
            updated_at=excluded.updated_at
        """,
        (entry_id, presentation, json.dumps(styles), json.dumps(flags), ts),
    )
    conn.execute(
        "INSERT INTO review_events(entry_id, action, payload_json, created_at) VALUES(?, ?, ?, ?)",
        (entry_id, status, json.dumps({"presentation": presentation, "styles": styles, "flags": flags}), ts),
    )
    conn.commit()
