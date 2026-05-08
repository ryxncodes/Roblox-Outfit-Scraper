#!/usr/bin/env python3
"""
Collect Roblox current avatars / outfit ids from curated group ranks and export them as Lua.

The intended workflow is:
1. Pick avatar ranking / fashion groups that already curate high-quality users.
2. Add the group id and trusted rank numbers to a JSON config.
3. Run this script outside Roblox Studio to produce a Lua source table.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


GROUPS_BASE = "https://groups.roblox.com"
AVATAR_BASE = "https://avatar.roblox.com"
THUMBNAILS_BASE = "https://thumbnails.roblox.com"
SOURCE_CURRENT = "currentAvatar"
SOURCE_SAVED = "savedOutfit"


@dataclass(frozen=True)
class Source:
    group_id: int
    ranks: tuple[int, ...]
    max_users_per_rank: int | None = None
    max_outfits_per_user: int | None = None


class RobloxClient:
    def __init__(
        self,
        requests_per_minute: int,
        max_retries: int,
        user_agent: str,
        timeout: float,
    ) -> None:
        self.min_delay = 60.0 / max(1, requests_per_minute)
        self.max_retries = max_retries
        self.timeout = timeout
        self.user_agent = user_agent
        self.last_request_at = 0.0

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{query}"

        for attempt in range(self.max_retries + 1):
            self._pace()
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": self.user_agent,
                },
            )

            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < self.max_retries:
                    self._sleep_after_rate_limit(exc, attempt)
                    continue
                if 500 <= exc.code <= 599 and attempt < self.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"GET {url} failed with HTTP {exc.code}: {body}") from exc
            except (TimeoutError, urllib.error.URLError) as exc:
                if attempt < self.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise RuntimeError(f"GET {url} failed: {exc}") from exc

        raise RuntimeError(f"GET {url} failed after retries")

    def _pace(self) -> None:
        elapsed = time.monotonic() - self.last_request_at
        wait = self.min_delay - elapsed
        if wait > 0:
            time.sleep(wait)
        self.last_request_at = time.monotonic()

    def _sleep_after_rate_limit(self, exc: urllib.error.HTTPError, attempt: int) -> None:
        retry_after = exc.headers.get("retry-after")
        reset_after = exc.headers.get("x-ratelimit-reset")

        delay: float | None = None
        for value in (retry_after, reset_after):
            if value is None:
                continue
            try:
                delay = max(1.0, float(value))
                break
            except ValueError:
                continue

        if delay is None:
            delay = self._backoff_seconds(attempt)
        time.sleep(delay)

    def _sleep_backoff(self, attempt: int) -> None:
        time.sleep(self._backoff_seconds(attempt))

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        return min(60.0, (2**attempt) + random.uniform(0.0, 0.75))


def read_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_sources(config: dict[str, Any], args: argparse.Namespace) -> list[Source]:
    sources: list[Source] = []

    for raw in config.get("sources", []):
        group_id = int(raw["groupId"])
        ranks = tuple(int(rank) for rank in raw["ranks"])
        sources.append(
            Source(
                group_id=group_id,
                ranks=ranks,
                max_users_per_rank=_optional_int(raw.get("maxUsersPerRank")),
                max_outfits_per_user=_optional_int(raw.get("maxOutfitsPerUser")),
            )
        )

    if args.group_id:
        if not args.rank:
            raise SystemExit("--rank is required when using --group-id")
        sources.append(
            Source(
                group_id=args.group_id,
                ranks=tuple(args.rank),
                max_users_per_rank=args.max_users_per_rank,
                max_outfits_per_user=args.max_outfits_per_user,
            )
        )

    if not sources:
        raise SystemExit("No sources provided. Use --config or --group-id with --rank.")

    return sources


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def get_roles_by_rank(client: RobloxClient, group_id: int) -> dict[int, dict[str, Any]]:
    payload = client.get_json(f"{GROUPS_BASE}/v1/groups/{group_id}/roles")
    roles = payload.get("roles", [])
    return {int(role["rank"]): role for role in roles if "rank" in role and "id" in role}


def get_users_for_role(
    client: RobloxClient,
    group_id: int,
    role_id: int,
    max_users: int | None,
) -> list[dict[str, Any]]:
    users: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        payload = client.get_json(
            f"{GROUPS_BASE}/v1/groups/{group_id}/roles/{role_id}/users",
            {
                "sortOrder": "Asc",
                "limit": 100,
                "cursor": cursor,
            },
        )
        users.extend(payload.get("data", []))

        if max_users is not None and len(users) >= max_users:
            return users[:max_users]

        cursor = payload.get("nextPageCursor")
        if not cursor:
            return users


def get_user_outfits(
    client: RobloxClient,
    user_id: int,
    max_outfits: int | None,
    max_pages: int,
) -> list[dict[str, Any]]:
    outfits: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    for page in range(1, max_pages + 1):
        payload = client.get_json(
            f"{AVATAR_BASE}/v2/avatar/users/{user_id}/outfits",
            {
                "itemsPerPage": 100,
                "page": page,
                "isEditable": "false",
            },
        )
        data = payload.get("data", [])
        new_count = 0

        for outfit in data:
            outfit_id = outfit.get("id")
            if not isinstance(outfit_id, int) or outfit_id in seen_ids:
                continue
            seen_ids.add(outfit_id)
            outfits.append(outfit)
            new_count += 1
            if max_outfits is not None and len(outfits) >= max_outfits:
                return outfits

        if len(data) < 100 or new_count == 0:
            break

    return outfits


def get_current_avatar(client: RobloxClient, user_id: int) -> dict[str, Any]:
    return client.get_json(f"{AVATAR_BASE}/v2/avatar/users/{user_id}/avatar")


def get_outfit_details(client: RobloxClient, outfit_id: int) -> dict[str, Any]:
    return client.get_json(f"{AVATAR_BASE}/v3/outfits/{outfit_id}/details")


def avatar_signature(payload: dict[str, Any]) -> tuple[int, ...]:
    """A practical duplicate key: same worn asset ids means same obvious outfit."""
    asset_ids: set[int] = set()

    for asset in payload.get("assets", []):
        asset_id = asset.get("id")
        if isinstance(asset_id, int):
            asset_ids.add(asset_id)

    return tuple(sorted(asset_ids))


def asset_ids_from_signature(signature: tuple[int, ...]) -> list[int]:
    return list(signature)


def valid_thumbnail_outfit_ids(
    client: RobloxClient,
    outfit_ids: Iterable[int],
    batch_size: int,
) -> set[int]:
    valid: set[int] = set()
    pending = list(outfit_ids)

    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        payload = client.get_json(
            f"{THUMBNAILS_BASE}/v1/users/outfits",
            {
                "userOutfitIds": ",".join(str(outfit_id) for outfit_id in batch),
                "size": "420x420",
                "format": "Png",
                "isCircular": "false",
            },
        )

        for item in payload.get("data", []):
            outfit_id = item.get("targetId")
            state = str(item.get("state", "")).lower()
            image_url = item.get("imageUrl")
            if isinstance(outfit_id, int) and state == "completed" and image_url:
                valid.add(outfit_id)

    return valid


def collect(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = read_config(args.config) if args.config else {}
    sources = parse_sources(config, args)
    client = RobloxClient(
        requests_per_minute=args.requests_per_minute,
        max_retries=args.max_retries,
        user_agent=args.user_agent,
        timeout=args.timeout,
    )

    found: dict[tuple[int, ...], dict[str, Any]] = {}
    stats = {
        "sources": len(sources),
        "usersScanned": 0,
        "currentAvatarsFound": 0,
        "outfitsFoundBeforeValidation": 0,
        "savedOutfitDetailsScanned": 0,
        "savedOutfitsMatchingCurrent": 0,
        "duplicatesSkipped": 0,
        "outfitsWritten": 0,
        "missingRanks": [],
    }

    for source in sources:
        roles_by_rank = get_roles_by_rank(client, source.group_id)
        for rank in source.ranks:
            role = roles_by_rank.get(rank)
            if role is None:
                stats["missingRanks"].append({"groupId": source.group_id, "rank": rank})
                print(f"Skipping group {source.group_id} rank {rank}: rank not found", file=sys.stderr)
                continue

            users = get_users_for_role(
                client=client,
                group_id=source.group_id,
                role_id=int(role["id"]),
                max_users=source.max_users_per_rank,
            )
            stats["usersScanned"] += len(users)

            for user_entry in users:
                user = user_entry.get("user", user_entry)
                user_id = user.get("userId") or user.get("id")
                username = user.get("username") or user.get("name")
                if not isinstance(user_id, int):
                    continue

                current_avatar = get_current_avatar(client, user_id)
                current_signature = avatar_signature(current_avatar)
                if not current_signature:
                    continue

                current_entry = {
                    "source": SOURCE_CURRENT,
                    "id": None,
                    "outfitId": None,
                    "userId": user_id,
                    "username": username or "",
                    "groupId": source.group_id,
                    "rank": rank,
                    "roleName": role.get("name", ""),
                    "name": "",
                    "currentMatch": True,
                    "assetIds": asset_ids_from_signature(current_signature),
                }
                stats["currentAvatarsFound"] += 1

                if args.mode == "current-only":
                    if current_signature in found:
                        stats["duplicatesSkipped"] += 1
                    else:
                        found[current_signature] = current_entry
                    continue

                outfits = get_user_outfits(
                    client=client,
                    user_id=user_id,
                    max_outfits=source.max_outfits_per_user,
                    max_pages=args.max_outfit_pages,
                )
                stats["outfitsFoundBeforeValidation"] += len(outfits)

                best_current_entry = current_entry
                saved_entries: list[tuple[tuple[int, ...], dict[str, Any]]] = []

                for outfit in outfits:
                    outfit_id = outfit["id"]
                    details = get_outfit_details(client, outfit_id)
                    stats["savedOutfitDetailsScanned"] += 1
                    saved_signature = avatar_signature(details)
                    if not saved_signature:
                        continue

                    saved_entry = {
                        "source": SOURCE_SAVED,
                        "id": outfit_id,
                        "outfitId": outfit_id,
                        "userId": user_id,
                        "username": username or "",
                        "groupId": source.group_id,
                        "rank": rank,
                        "roleName": role.get("name", ""),
                        "name": outfit.get("name", ""),
                        "currentMatch": saved_signature == current_signature,
                        "assetIds": asset_ids_from_signature(saved_signature),
                    }

                    if saved_signature == current_signature and best_current_entry["source"] != SOURCE_SAVED:
                        best_current_entry = saved_entry
                        stats["savedOutfitsMatchingCurrent"] += 1
                    else:
                        saved_entries.append((saved_signature, saved_entry))

                if current_signature in found:
                    stats["duplicatesSkipped"] += 1
                else:
                    found[current_signature] = best_current_entry

                for saved_signature, saved_entry in saved_entries:
                    if saved_signature in found:
                        stats["duplicatesSkipped"] += 1
                        continue
                    found[saved_signature] = saved_entry

    if args.validate_thumbnails and found:
        saved_ids = [entry["outfitId"] for entry in found.values() if entry.get("source") == SOURCE_SAVED]
        valid_ids = valid_thumbnail_outfit_ids(client, saved_ids, args.thumbnail_batch_size)
        found = {
            signature: entry
            for signature, entry in found.items()
            if entry.get("source") != SOURCE_SAVED or entry.get("outfitId") in valid_ids
        }

    entries = sorted(
        found.values(),
        key=lambda entry: (
            entry["groupId"],
            entry["rank"],
            entry["userId"],
            0 if entry["source"] == SOURCE_CURRENT else 1,
            entry["outfitId"] or 0,
        ),
    )
    if args.limit:
        entries = entries[: args.limit]

    stats["outfitsWritten"] = len(entries)
    return entries, stats


def write_lua(entries: list[dict[str, Any]], path: Path, include_metadata: bool) -> None:
    lines = [
        "-- Generated by collect_outfits.py",
        "-- source = \"savedOutfit\" entries have outfitId values.",
        "-- source = \"currentAvatar\" entries should be loaded by userId/current avatar data.",
        "return {",
    ]

    for entry in entries:
        if include_metadata:
            lines.append(
                "    "
                + "{ "
                + f"source = {lua_string(entry['source'])}, "
                + f"outfitId = {lua_nil_or_number(entry['outfitId'])}, "
                + f"userId = {entry['userId']}, "
                + f"groupId = {entry['groupId']}, "
                + f"rank = {entry['rank']}, "
                + f"currentMatch = {lua_bool(entry['currentMatch'])}, "
                + f"assetIds = {lua_number_array(entry['assetIds'])}, "
                + f"name = {lua_string(entry['name'])}, "
                + f"username = {lua_string(entry['username'])}, "
                + f"roleName = {lua_string(entry['roleName'])} "
                + "},"
            )
        elif entry["source"] == SOURCE_SAVED:
            lines.append(f"    {entry['outfitId']},")
        else:
            lines.append(f"    {entry['userId']},")

    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json(entries: list[dict[str, Any]], path: Path) -> None:
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def lua_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def lua_nil_or_number(value: Any) -> str:
    return "nil" if value is None else str(int(value))


def lua_bool(value: Any) -> str:
    return "true" if value else "false"


def lua_number_array(values: Iterable[int]) -> str:
    return "{ " + ", ".join(str(value) for value in values) + " }"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect Roblox avatars/outfit ids from curated group ranks.")
    parser.add_argument(
        "--mode",
        choices=("current-only", "current-plus-saved"),
        default="current-plus-saved",
        help="current-only saves ranked users' current avatars; current-plus-saved also scans saved outfits.",
    )
    parser.add_argument("--config", type=Path, help="JSON config file with sources.")
    parser.add_argument("--group-id", type=int, help="Single group id source.")
    parser.add_argument("--rank", type=int, action="append", help="Rank to collect. Repeat for multiple ranks.")
    parser.add_argument("--output", type=Path, default=Path("OutfitSources.lua"), help="Lua output path.")
    parser.add_argument("--json-output", type=Path, help="Optional JSON metadata output path.")
    parser.add_argument("--metadata", action="store_true", help="Write Lua entries with source metadata.")
    parser.add_argument("--validate-thumbnails", action="store_true", help="Only keep outfits with completed thumbnails.")
    parser.add_argument("--limit", type=int, help="Maximum number of outfit ids to write.")
    parser.add_argument("--max-users-per-rank", type=int, help="CLI source max users per rank.")
    parser.add_argument("--max-outfits-per-user", type=int, help="CLI source max outfits per user.")
    parser.add_argument("--max-outfit-pages", type=int, default=3, help="Maximum outfit pages to fetch per user.")
    parser.add_argument("--thumbnail-batch-size", type=int, default=100, help="Thumbnail validation batch size.")
    parser.add_argument("--requests-per-minute", type=int, default=90, help="Soft request cap before 429 handling.")
    parser.add_argument("--max-retries", type=int, default=6, help="Retries for 429 and transient server errors.")
    parser.add_argument("--timeout", type=float, default=20.0, help="Request timeout in seconds.")
    parser.add_argument(
        "--user-agent",
        default="RobloxOutfitCollector/1.0 (+local development)",
        help="HTTP User-Agent value.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    entries, stats = collect(args)
    write_lua(entries, args.output, include_metadata=args.metadata)
    if args.json_output:
        write_json(entries, args.json_output)

    print(json.dumps(stats, indent=2))
    print(f"Wrote {len(entries)} outfit ids to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
