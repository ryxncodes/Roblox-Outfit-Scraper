from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Any

from collect_outfits import THUMBNAILS_BASE, RobloxClient


def thumbnail_filename(entry: dict[str, Any]) -> str:
    if entry.get("source") == "savedOutfit" and entry.get("outfitId"):
        return f"savedOutfit_{entry['outfitId']}.png"
    return f"currentAvatar_{entry['userId']}.png"


def thumbnail_request(client: RobloxClient, entry: dict[str, Any], size: str) -> dict[str, Any] | None:
    if entry.get("source") == "savedOutfit" and entry.get("outfitId"):
        payload = client.get_json(
            f"{THUMBNAILS_BASE}/v1/users/outfits",
            {
                "userOutfitIds": str(entry["outfitId"]),
                "size": size,
                "format": "Png",
                "isCircular": "false",
            },
        )
    else:
        payload = client.get_json(
            f"{THUMBNAILS_BASE}/v1/users/avatar",
            {
                "userIds": str(entry["userId"]),
                "size": size,
                "format": "Png",
                "isCircular": "false",
            },
        )
    data = payload.get("data", [])
    return data[0] if data else None


def cache_thumbnails(
    conn: Any,
    workspace: Path,
    client: RobloxClient,
    size: str = "420x420",
    refresh: bool = False,
    limit: int | None = None,
) -> int:
    import json
    import time

    rows = list(conn.execute("SELECT entry_id, raw_json FROM outfits WHERE rejected = 0 ORDER BY updated_at DESC"))
    if limit:
        rows = rows[:limit]
    thumb_dir = workspace / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for row in rows:
        entry = json.loads(row["raw_json"])
        path = thumb_dir / thumbnail_filename(entry)
        if path.exists() and not refresh:
            conn.execute(
                "INSERT OR REPLACE INTO thumbnails(entry_id, path, status, image_url, updated_at) VALUES(?, ?, ?, ?, ?)",
                (row["entry_id"], str(path), "cached", None, int(time.time())),
            )
            continue
        try:
            item = thumbnail_request(client, entry, size)
            state = str((item or {}).get("state", "missing")).lower()
            image_url = (item or {}).get("imageUrl")
            if state == "completed" and image_url:
                urllib.request.urlretrieve(image_url, path)
                status = "cached"
                count += 1
            else:
                status = state or "missing"
        except RuntimeError as exc:
            status = "rate_limited" if "HTTP 429" in str(exc) or "Too many requests" in str(exc) else "error"
            image_url = None
        conn.execute(
            "INSERT OR REPLACE INTO thumbnails(entry_id, path, status, image_url, updated_at) VALUES(?, ?, ?, ?, ?)",
            (row["entry_id"], str(path) if path.exists() else None, status, image_url, int(time.time())),
        )
    return count
