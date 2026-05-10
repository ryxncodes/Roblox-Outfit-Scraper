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
import hashlib
import json
import random
import shutil
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
    ranks: tuple[int, ...] = ()
    role_names: tuple[str, ...] = ()
    max_users_per_rank: int | None = None
    max_outfits_per_user: int | None = None


@dataclass(frozen=True)
class Filters:
    avatar_types: tuple[str, ...] = ()
    require_asset_types: tuple[str, ...] = ()
    exclude_asset_types: tuple[str, ...] = ()
    include_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    min_assets: int | None = None
    max_assets: int | None = None
    allow_default_clothing: bool = True


class RobloxClient:
    def __init__(
        self,
        requests_per_minute: int,
        max_retries: int,
        user_agent: str,
        timeout: float,
        cache_dir: Path | None,
    ) -> None:
        self.min_delay = 60.0 / max(1, requests_per_minute)
        self.max_retries = max_retries
        self.timeout = timeout
        self.user_agent = user_agent
        self.last_request_at = 0.0
        self.cache_dir = cache_dir
        self.cache_hits = 0
        self.cache_misses = 0
        self.rate_limit_hits = 0
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{query}"

        cached = self._read_cache(url)
        if cached is not None:
            self.cache_hits += 1
            return cached
        self.cache_misses += 1

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
                    payload = json.loads(response.read().decode("utf-8"))
                    self._write_cache(url, payload)
                    return payload
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < self.max_retries:
                    self.rate_limit_hits += 1
                    self._sleep_after_rate_limit(exc, attempt, url)
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

    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, url: str) -> dict[str, Any] | None:
        path = self._cache_path(url)
        if not path or not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                wrapper = json.load(handle)
            if wrapper.get("url") != url:
                return None
            payload = wrapper.get("payload")
            return payload if isinstance(payload, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, url: str, payload: dict[str, Any]) -> None:
        path = self._cache_path(url)
        if not path:
            return
        wrapper = {"url": url, "cachedAt": int(time.time()), "payload": payload}
        path.write_text(json.dumps(wrapper, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")

    def _pace(self) -> None:
        elapsed = time.monotonic() - self.last_request_at
        wait = self.min_delay - elapsed
        if wait > 0:
            time.sleep(wait)
        self.last_request_at = time.monotonic()

    def _sleep_after_rate_limit(self, exc: urllib.error.HTTPError, attempt: int, url: str) -> None:
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
            delay = self._rate_limit_backoff_seconds(attempt)
            if "/v2/avatar/users/" in url and "/outfits" in url:
                delay *= 1.5
        self._adapt_after_rate_limit(delay)
        print(f"Rate limited by Roblox; waiting {delay:.1f}s and slowing future requests.", file=sys.stderr)
        time.sleep(delay)

    def _sleep_backoff(self, attempt: int) -> None:
        time.sleep(self._backoff_seconds(attempt))

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        return min(60.0, (2**attempt) + random.uniform(0.0, 0.75))

    @staticmethod
    def _rate_limit_backoff_seconds(attempt: int) -> float:
        return min(300.0, 15.0 * (2**attempt) + random.uniform(0.0, 3.0))

    def _adapt_after_rate_limit(self, delay: float) -> None:
        self.min_delay = min(max(self.min_delay * 1.75, delay / 4.0, 1.0), 15.0)


def read_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_sources(config: dict[str, Any], args: argparse.Namespace) -> list[Source]:
    sources: list[Source] = []

    for raw in config.get("sources", []):
        group_id = int(raw["groupId"])
        raw_ranks = raw.get("ranks", [])
        ranks = tuple(int(rank) for rank in raw_ranks if isinstance(rank, int) or str(rank).isdigit())
        role_names = tuple(str(rank) for rank in raw_ranks if not (isinstance(rank, int) or str(rank).isdigit()))
        role_names = (
            role_names
            + _tuple_of_strings(raw.get("roleNames"))
            + _tuple_of_strings(raw.get("rankNames"))
            + _tuple_of_strings(raw.get("roleName"))
            + _tuple_of_strings(raw.get("rankName"))
        )
        if not ranks and not role_names:
            raise SystemExit(
                f"Source for group {group_id} has no ranks or role names. "
                "Use ranks, roleNames, or rankNames."
            )
        sources.append(
            Source(
                group_id=group_id,
                ranks=ranks,
                role_names=role_names,
                max_users_per_rank=args.max_users_per_rank
                if args.max_users_per_rank is not None
                else _optional_int(raw.get("maxUsersPerRank")),
                max_outfits_per_user=args.max_outfits_per_user
                if args.max_outfits_per_user is not None
                else _optional_int(raw.get("maxOutfitsPerUser")),
            )
        )

    if args.group_id:
        if not args.rank and not args.role_name:
            raise SystemExit("--rank or --role-name is required when using --group-id")
        sources.append(
            Source(
                group_id=args.group_id,
                ranks=tuple(args.rank or ()),
                role_names=tuple(args.role_name or ()),
                max_users_per_rank=args.max_users_per_rank,
                max_outfits_per_user=args.max_outfits_per_user,
            )
        )

    if not sources:
        raise SystemExit("No sources provided. Use --config or --group-id with --rank.")

    return sources


def parse_filters(config: dict[str, Any], args: argparse.Namespace) -> Filters:
    raw = config.get("filters", {})
    avatar_types = _tuple_of_strings(raw.get("avatarTypes"))
    require_asset_types = _tuple_of_strings(raw.get("requireAssetTypes"))
    exclude_asset_types = _tuple_of_strings(raw.get("excludeAssetTypes"))
    include_keywords = _tuple_of_strings(raw.get("includeKeywords") or raw.get("nameKeywords"))
    exclude_keywords = _tuple_of_strings(raw.get("excludeKeywords"))

    if args.avatar_type:
        avatar_types = tuple(value.upper() for value in args.avatar_type)
    if args.include_keyword:
        include_keywords = include_keywords + tuple(args.include_keyword)
    if args.exclude_keyword:
        exclude_keywords = exclude_keywords + tuple(args.exclude_keyword)

    return Filters(
        avatar_types=tuple(value.upper() for value in avatar_types),
        require_asset_types=tuple(value.lower() for value in require_asset_types),
        exclude_asset_types=tuple(value.lower() for value in exclude_asset_types),
        include_keywords=tuple(value.lower() for value in include_keywords),
        exclude_keywords=tuple(value.lower() for value in exclude_keywords),
        min_assets=_optional_int(raw.get("minAssets")),
        max_assets=_optional_int(raw.get("maxAssets")),
        allow_default_clothing=bool(raw.get("allowDefaultClothing", True)),
    )


def _tuple_of_strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def get_roles_by_rank(client: RobloxClient, group_id: int) -> dict[int, dict[str, Any]]:
    payload = client.get_json(f"{GROUPS_BASE}/v1/groups/{group_id}/roles")
    roles = payload.get("roles", [])
    return {int(role["rank"]): role for role in roles if "rank" in role and "id" in role}


def get_roles_by_name(client: RobloxClient, group_id: int) -> dict[str, dict[str, Any]]:
    payload = client.get_json(f"{GROUPS_BASE}/v1/groups/{group_id}/roles")
    roles = payload.get("roles", [])
    return {normalize_role_name(role["name"]): role for role in roles if "name" in role and "id" in role}


def normalize_role_name(value: str) -> str:
    return str(value).strip().lower()


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


def filter_rejection_reason(payload: dict[str, Any], filters: Filters) -> str | None:
    assets = payload.get("assets", [])
    asset_count = len(assets)
    avatar_type = str(payload.get("playerAvatarType", "")).upper()

    if filters.avatar_types and avatar_type not in filters.avatar_types:
        return f"avatarType:{avatar_type or 'unknown'}"
    if filters.min_assets is not None and asset_count < filters.min_assets:
        return f"minAssets:{asset_count}"
    if filters.max_assets is not None and asset_count > filters.max_assets:
        return f"maxAssets:{asset_count}"
    if not filters.allow_default_clothing and (
        payload.get("defaultShirtApplied") or payload.get("defaultPantsApplied")
    ):
        return "defaultClothing"

    asset_types = {asset_type_name(asset).lower() for asset in assets}
    if filters.require_asset_types and not set(filters.require_asset_types).issubset(asset_types):
        missing = sorted(set(filters.require_asset_types).difference(asset_types))
        return "missingAssetTypes:" + ",".join(missing)
    if filters.exclude_asset_types and set(filters.exclude_asset_types).intersection(asset_types):
        blocked = sorted(set(filters.exclude_asset_types).intersection(asset_types))
        return "excludedAssetTypes:" + ",".join(blocked)

    searchable = " ".join(
        part.lower()
        for asset in assets
        for part in (str(asset.get("name", "")), asset_type_name(asset))
    )
    if filters.include_keywords and not any(keyword in searchable for keyword in filters.include_keywords):
        return "missingIncludeKeyword"
    if filters.exclude_keywords and any(keyword in searchable for keyword in filters.exclude_keywords):
        matched = [keyword for keyword in filters.exclude_keywords if keyword in searchable]
        return "excludedKeyword:" + ",".join(matched)

    return None


def avatar_passes_filters(payload: dict[str, Any], filters: Filters) -> bool:
    return filter_rejection_reason(payload, filters) is None


def asset_type_name(asset: dict[str, Any]) -> str:
    asset_type = asset.get("assetType", {})
    if isinstance(asset_type, dict):
        return str(asset_type.get("name", ""))
    return ""


def normalized_assets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for asset in payload.get("assets", []):
        asset_id = asset.get("id")
        if not isinstance(asset_id, int):
            continue
        assets.append(
            {
                "id": asset_id,
                "name": str(asset.get("name", "")),
                "assetTypeName": asset_type_name(asset),
            }
        )
    return assets


def avatar_type(payload: dict[str, Any]) -> str:
    value = str(payload.get("playerAvatarType", "")).upper()
    return value if value else "unknown"


def deterministic_tags(entry: dict[str, Any], payload: dict[str, Any], filters: Filters) -> list[str]:
    tags = [avatar_type(payload).lower()]
    asset_types = {asset_type_name(asset).lower() for asset in payload.get("assets", [])}
    if "shirt" in asset_types and "pants" in asset_types:
        tags.append("classic-clothing")
    if any(asset_type in asset_types for asset_type in ("jacketaccessory", "sweateraccessory", "dressskirtaccessory")):
        tags.append("layered-clothing")
    if entry["source"] == SOURCE_SAVED and entry.get("currentMatch"):
        tags.append("current-match")
    for keyword in filters.include_keywords:
        tags.append(f"keyword-{safe_tag(keyword)}")
    return sorted(set(tag for tag in tags if tag))


def safe_tag(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")


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


def collect(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    config = read_config(args.config) if args.config else {}
    sources = parse_sources(config, args)
    filters = parse_filters(config, args)
    cache_dir = None if args.no_cache else args.cache_dir
    client = RobloxClient(
        requests_per_minute=args.requests_per_minute,
        max_retries=args.max_retries,
        user_agent=args.user_agent,
        timeout=args.timeout,
        cache_dir=cache_dir,
    )

    found: dict[tuple[int, ...], dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    stats = {
        "sources": len(sources),
        "usersScanned": 0,
        "currentAvatarsFound": 0,
        "outfitsFoundBeforeValidation": 0,
        "savedOutfitDetailsScanned": 0,
        "savedOutfitsMatchingCurrent": 0,
        "duplicatesSkipped": 0,
        "filteredOut": 0,
        "rateLimitedSkipped": 0,
        "requestErrorsSkipped": 0,
        "alreadyScannedSkipped": 0,
        "outfitsWritten": 0,
        "cacheHits": 0,
        "cacheMisses": 0,
        "rateLimitHits": 0,
        "missingRanks": [],
    }

    for source in sources:
        roles_by_rank = get_roles_by_rank(client, source.group_id)
        roles_by_name = get_roles_by_name(client, source.group_id) if source.role_names else {}
        rank_roles = [(rank, roles_by_rank.get(rank)) for rank in source.ranks]
        name_roles = [
            (_optional_int(role.get("rank")) if role else None, role)
            for role_name in source.role_names
            for role in [roles_by_name.get(normalize_role_name(role_name))]
        ]

        for rank, role in rank_roles + name_roles:
            if role is None:
                stats["missingRanks"].append({"groupId": source.group_id, "rank": rank})
                print(f"Skipping group {source.group_id} rank {rank}: rank not found", file=sys.stderr)
                continue
            if rank is None:
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
                if should_skip_user(args, user_id, source.group_id, rank, role.get("name", ""), args.mode):
                    stats["alreadyScannedSkipped"] += 1
                    continue

                try:
                    current_avatar = get_current_avatar(client, user_id)
                except RuntimeError as exc:
                    if args.continue_on_request_error:
                        stats[_request_error_stat(exc)] += 1
                        print(f"Skipping current avatar for user {user_id}: {exc}", file=sys.stderr)
                        continue
                    raise
                current_signature = avatar_signature(current_avatar)
                if not current_signature:
                    continue
                reason = filter_rejection_reason(current_avatar, filters)
                if reason:
                    stats["filteredOut"] += 1
                    rejected.append(
                        rejected_entry(SOURCE_CURRENT, reason, user_id, username, source.group_id, rank, role, None, current_avatar)
                    )
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
                    "avatarType": avatar_type(current_avatar),
                    "assetIds": asset_ids_from_signature(current_signature),
                    "assets": normalized_assets(current_avatar),
                }
                current_entry["tags"] = deterministic_tags(current_entry, current_avatar, filters)
                emit_collected_entry(args, current_entry)
                stats["currentAvatarsFound"] += 1

                if args.mode == "current-only":
                    if current_signature in found:
                        stats["duplicatesSkipped"] += 1
                    else:
                        found[current_signature] = current_entry
                    emit_user_scan(args, user_id, source.group_id, rank, role.get("name", ""), "current", "complete")
                    continue

                saved_scan_status = "complete"
                saved_scan_message = None
                try:
                    outfits = get_user_outfits(
                        client=client,
                        user_id=user_id,
                        max_outfits=source.max_outfits_per_user,
                        max_pages=args.max_outfit_pages,
                    )
                except RuntimeError as exc:
                    if args.continue_on_request_error:
                        stats[_request_error_stat(exc)] += 1
                        emit_user_scan(
                            args,
                            user_id,
                            source.group_id,
                            rank,
                            role.get("name", ""),
                            "saved",
                            "partial",
                            str(exc),
                        )
                        saved_scan_status = "partial"
                        saved_scan_message = str(exc)
                        print(
                            f"Skipping saved outfits for user {user_id}; keeping current avatar: {exc}",
                            file=sys.stderr,
                        )
                        outfits = []
                    else:
                        raise
                stats["outfitsFoundBeforeValidation"] += len(outfits)

                best_current_entry = current_entry
                saved_entries: list[tuple[tuple[int, ...], dict[str, Any]]] = []

                for outfit in outfits:
                    outfit_id = outfit["id"]
                    try:
                        details = get_outfit_details(client, outfit_id)
                    except RuntimeError as exc:
                        if args.continue_on_request_error:
                            stats[_request_error_stat(exc)] += 1
                            print(f"Skipping saved outfit {outfit_id}: {exc}", file=sys.stderr)
                            continue
                        raise
                    stats["savedOutfitDetailsScanned"] += 1
                    saved_signature = avatar_signature(details)
                    if not saved_signature:
                        continue
                    reason = filter_rejection_reason(details, filters)
                    if reason:
                        stats["filteredOut"] += 1
                        rejected.append(
                            rejected_entry(
                                SOURCE_SAVED,
                                reason,
                                user_id,
                                username,
                                source.group_id,
                                rank,
                                role,
                                outfit_id,
                                details,
                                outfit.get("name", ""),
                            )
                        )
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
                        "avatarType": avatar_type(details),
                        "assetIds": asset_ids_from_signature(saved_signature),
                        "assets": normalized_assets(details),
                    }
                    saved_entry["tags"] = deterministic_tags(saved_entry, details, filters)
                    emit_collected_entry(args, saved_entry)

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
                emit_user_scan(
                    args,
                    user_id,
                    source.group_id,
                    rank,
                    role.get("name", ""),
                    "saved",
                    saved_scan_status,
                    saved_scan_message,
                )

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
    stats["cacheHits"] = client.cache_hits
    stats["cacheMisses"] = client.cache_misses
    stats["rateLimitHits"] = client.rate_limit_hits
    return entries, stats, rejected


def rejected_entry(
    source_name: str,
    reason: str,
    user_id: int,
    username: str | None,
    group_id: int,
    rank: int,
    role: dict[str, Any],
    outfit_id: int | None,
    payload: dict[str, Any],
    outfit_name: str = "",
) -> dict[str, Any]:
    signature = avatar_signature(payload)
    return {
        "source": source_name,
        "reason": reason,
        "outfitId": outfit_id,
        "userId": user_id,
        "username": username or "",
        "groupId": group_id,
        "rank": rank,
        "roleName": role.get("name", ""),
        "name": outfit_name,
        "avatarType": avatar_type(payload),
        "assetIds": asset_ids_from_signature(signature),
        "assets": normalized_assets(payload),
    }


def _request_error_stat(exc: RuntimeError) -> str:
    message = str(exc)
    if "HTTP 429" in message or "Too many requests" in message:
        return "rateLimitedSkipped"
    return "requestErrorsSkipped"


def emit_collected_entry(args: argparse.Namespace, entry: dict[str, Any]) -> None:
    callback = getattr(args, "entry_callback", None)
    if callable(callback):
        callback(entry)


def should_skip_user(
    args: argparse.Namespace,
    user_id: int,
    group_id: int,
    rank: int,
    role_name: str,
    mode: str,
) -> bool:
    callback = getattr(args, "skip_user_callback", None)
    if callable(callback):
        return bool(callback(user_id, group_id, rank, role_name, "current" if mode == "current-only" else "saved"))
    return False


def emit_user_scan(
    args: argparse.Namespace,
    user_id: int,
    group_id: int,
    rank: int,
    role_name: str,
    scan_type: str,
    status: str,
    message: str | None = None,
) -> None:
    callback = getattr(args, "user_scan_callback", None)
    if callable(callback):
        callback(user_id, group_id, rank, role_name, scan_type, status, message)


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
                + f"avatarType = {lua_string(entry['avatarType'])}, "
                + f"tags = {lua_string_array(entry['tags'])}, "
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


def write_split_outputs(entries: list[dict[str, Any]], output: Path, split_by: str, include_metadata: bool) -> list[Path]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        for key in split_keys(entry, split_by):
            grouped.setdefault(key, []).append(entry)

    written: list[Path] = []
    stem = output.stem
    suffix = output.suffix or ".lua"
    for key, group_entries in sorted(grouped.items()):
        path = output.with_name(f"{stem}_{key}{suffix}")
        write_lua(group_entries, path, include_metadata)
        written.append(path)
    return written


def split_keys(entry: dict[str, Any], split_by: str) -> list[str]:
    if split_by == "source":
        return [safe_tag(entry["source"])]
    if split_by == "avatar-type":
        return [safe_tag(entry.get("avatarType", "unknown"))]
    if split_by == "group":
        return [f"group-{entry['groupId']}"]
    if split_by == "rank":
        return [f"group-{entry['groupId']}-rank-{entry['rank']}"]
    if split_by == "tag":
        return [safe_tag(tag) for tag in entry.get("tags", [])] or ["untagged"]
    return ["all"]


def lua_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def lua_nil_or_number(value: Any) -> str:
    return "nil" if value is None else str(int(value))


def lua_bool(value: Any) -> str:
    return "true" if value else "false"


def lua_number_array(values: Iterable[int]) -> str:
    return "{ " + ", ".join(str(value) for value in values) + " }"


def lua_string_array(values: Iterable[str]) -> str:
    return "{ " + ", ".join(lua_string(value) for value in values) + " }"


def clear_cache(cache_dir: Path) -> None:
    resolved = cache_dir.resolve()
    cwd = Path.cwd().resolve()
    if resolved == cwd or cwd not in resolved.parents:
        raise SystemExit(f"Refusing to clear cache outside this workspace: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def estimate(args: argparse.Namespace) -> dict[str, Any]:
    config = read_config(args.config) if args.config else {}
    sources = parse_sources(config, args)
    cache_dir = None if args.no_cache else args.cache_dir
    client = RobloxClient(
        requests_per_minute=args.requests_per_minute,
        max_retries=args.max_retries,
        user_agent=args.user_agent,
        timeout=args.timeout,
        cache_dir=cache_dir,
    )

    groups: list[dict[str, Any]] = []
    total_users = 0
    missing_ranks: list[dict[str, Any]] = []

    for source in sources:
        roles_by_rank = get_roles_by_rank(client, source.group_id)
        roles_by_name = get_roles_by_name(client, source.group_id) if source.role_names else {}
        rank_roles = [(rank, roles_by_rank.get(rank)) for rank in source.ranks]
        name_roles = [
            (_optional_int(role.get("rank")) if role else None, role)
            for role_name in source.role_names
            for role in [roles_by_name.get(normalize_role_name(role_name))]
        ]

        for rank, role in rank_roles + name_roles:
            if role is None:
                missing_ranks.append({"groupId": source.group_id, "rank": rank})
                continue
            if rank is None:
                continue
            role_members = _optional_int(role.get("memberCount"))
            planned_users = role_members if role_members is not None else source.max_users_per_rank
            if planned_users is not None and source.max_users_per_rank is not None:
                planned_users = min(planned_users, source.max_users_per_rank)
            total_users += planned_users or 0
            groups.append(
                {
                    "groupId": source.group_id,
                    "rank": rank,
                    "roleName": role.get("name", ""),
                    "roleMembers": role_members,
                    "plannedUsers": planned_users,
                    "maxOutfitsPerUser": source.max_outfits_per_user,
                }
            )

    saved_outfit_upper_bound = None
    if args.mode == "current-plus-saved":
        per_user = max((source.max_outfits_per_user or args.max_outfit_pages * 100) for source in sources)
        saved_outfit_upper_bound = total_users * per_user

    return {
        "mode": args.mode,
        "sources": len(sources),
        "plannedUsers": total_users,
        "savedOutfitUpperBound": saved_outfit_upper_bound,
        "notes": [
            "Estimate uses group role member counts and configured caps.",
            "Saved outfit count is an upper bound unless maxOutfitsPerUser is set.",
            "Filters and duplicate removal are not estimated without scanning avatars.",
        ],
        "groups": groups,
        "missingRanks": missing_ranks,
        "cacheHits": client.cache_hits,
        "cacheMisses": client.cache_misses,
    }


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
    parser.add_argument("--role-name", action="append", help="Role/rank name to collect. Repeat for multiple names.")
    parser.add_argument("--avatar-type", action="append", choices=("R6", "R15", "r6", "r15"), help="Quick filter. Repeat for multiple avatar types.")
    parser.add_argument("--include-keyword", action="append", help="Quick asset-name keyword filter.")
    parser.add_argument("--exclude-keyword", action="append", help="Quick asset-name keyword exclusion.")
    parser.add_argument("--output", type=Path, default=Path("OutfitSources.lua"), help="Lua output path.")
    parser.add_argument(
        "--split-output-by",
        choices=("source", "avatar-type", "group", "rank", "tag"),
        help="Also write grouped Lua output files next to --output.",
    )
    parser.add_argument("--json-output", type=Path, help="Optional JSON metadata output path.")
    parser.add_argument("--rejected-output", type=Path, help="Optional JSON file for filtered-out entries.")
    parser.add_argument("--metadata", action="store_true", help="Write Lua entries with source metadata.")
    parser.add_argument("--estimate", action="store_true", help="Print a cheap source-size estimate and exit.")
    parser.add_argument("--validate-thumbnails", action="store_true", help="Only keep outfits with completed thumbnails.")
    parser.add_argument("--limit", type=int, help="Maximum number of outfit ids to write.")
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/roblox-outfits"), help="Local API response cache directory.")
    parser.add_argument("--no-cache", action="store_true", help="Disable cache reads and writes.")
    parser.add_argument("--clear-cache", action="store_true", help="Clear the cache before running.")
    parser.add_argument("--clear-cache-only", action="store_true", help="Clear the cache and exit.")
    parser.add_argument("--max-users-per-rank", type=int, help="CLI source max users per rank.")
    parser.add_argument("--max-outfits-per-user", type=int, help="CLI source max outfits per user.")
    parser.add_argument("--max-outfit-pages", type=int, default=3, help="Maximum outfit pages to fetch per user.")
    parser.add_argument("--thumbnail-batch-size", type=int, default=100, help="Thumbnail validation batch size.")
    parser.add_argument("--requests-per-minute", type=int, default=90, help="Soft request cap before 429 handling.")
    parser.add_argument("--max-retries", type=int, default=6, help="Retries for 429 and transient server errors.")
    parser.add_argument(
        "--continue-on-request-error",
        action="store_true",
        help="Skip users/outfits that still fail after retries instead of aborting the whole run.",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="Request timeout in seconds.")
    parser.add_argument(
        "--user-agent",
        default="RobloxOutfitCollector/1.0 (+local development)",
        help="HTTP User-Agent value.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.clear_cache or args.clear_cache_only:
        clear_cache(args.cache_dir)
        print(f"Cleared cache: {args.cache_dir}")
        if args.clear_cache_only:
            return 0

    if args.estimate:
        print(json.dumps(estimate(args), indent=2))
        return 0

    entries, stats, rejected = collect(args)
    write_lua(entries, args.output, include_metadata=args.metadata)
    split_paths: list[Path] = []
    if args.split_output_by:
        split_paths = write_split_outputs(entries, args.output, args.split_output_by, args.metadata)
    if args.json_output:
        write_json(entries, args.json_output)
    if args.rejected_output:
        write_json(rejected, args.rejected_output)

    print(json.dumps(stats, indent=2))
    print(f"Wrote {len(entries)} entries to {args.output}")
    if split_paths:
        print(f"Wrote {len(split_paths)} split output files")
    if args.rejected_output:
        print(f"Wrote {len(rejected)} rejected entries to {args.rejected_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
