from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import collect_outfits
import pytest

from roblox_outfits.cli import build_parser
from roblox_outfits.classify import classify_entry, save_prediction
from roblox_outfits.config import ClassifierConfig
from roblox_outfits.dashboard import fetch_review_rows
from roblox_outfits.export import export_workspace
from roblox_outfits.features import entry_text
from roblox_outfits.heuristics import deterministic_flags, load_known_assets
from roblox_outfits.quality import is_low_information_saved_outfit
from roblox_outfits.workspace import Workspace, entry_key, is_scan_complete, mark_scan, set_review


def sample_entry() -> dict:
    return {
        "source": "currentAvatar",
        "outfitId": None,
        "userId": 123,
        "username": "Player",
        "groupId": 456,
        "rank": 200,
        "roleName": "Sinner",
        "name": "",
        "currentMatch": True,
        "avatarType": "R6",
        "tags": ["r6", "classic-clothing"],
        "assetIds": [134082579, 10, 11],
        "assets": [
            {"id": 134082579, "name": "Headless Head", "assetTypeName": "Head"},
            {"id": 10, "name": "Black Emo Hoodie", "assetTypeName": "Shirt"},
            {"id": 11, "name": "Ripped Jeans", "assetTypeName": "Pants"},
        ],
    }


def source_args(**overrides: object) -> SimpleNamespace:
    values = {
        "group_id": None,
        "rank": None,
        "role_name": None,
        "max_users_per_rank": None,
        "max_outfits_per_user": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_entry_key_is_stable_for_current_avatar_assets() -> None:
    first = sample_entry()
    second = sample_entry()
    second["assetIds"] = [11, 134082579, 10]
    assert entry_key(first) == entry_key(second)


def test_rank_names_alias_parses_as_role_names() -> None:
    sources = collect_outfits.parse_sources(
        {"sources": [{"groupId": 13353323, "rankNames": ["Charming"]}]},
        source_args(),
    )
    assert sources[0].role_names == ("Charming",)


def test_cli_caps_override_config_source_caps() -> None:
    sources = collect_outfits.parse_sources(
        {"sources": [{"groupId": 13353323, "rankNames": ["Charming"], "maxUsersPerRank": 25}]},
        source_args(max_users_per_rank=3, max_outfits_per_user=2),
    )
    assert sources[0].max_users_per_rank == 3
    assert sources[0].max_outfits_per_user == 2


def test_workspace_scrape_defaults_to_current_only() -> None:
    args = build_parser().parse_args(["scrape", "--group-id", "1", "--rank-name", "Role"])
    assert args.mode == "current-only"


def test_source_without_rank_or_role_has_clear_error() -> None:
    with pytest.raises(SystemExit, match="Use ranks, roleNames, or rankNames"):
        collect_outfits.parse_sources({"sources": [{"groupId": 13353323}]}, source_args())


def test_request_error_stat_detects_rate_limit() -> None:
    assert collect_outfits._request_error_stat(RuntimeError("HTTP 429: Too many requests")) == "rateLimitedSkipped"
    assert collect_outfits._request_error_stat(RuntimeError("HTTP 500")) == "requestErrorsSkipped"


def test_emit_collected_entry_uses_optional_callback() -> None:
    seen = []
    args = SimpleNamespace(entry_callback=seen.append)
    collect_outfits.emit_collected_entry(args, {"userId": 1})
    assert seen == [{"userId": 1}]


def test_low_information_saved_outfit_detects_head_mood_only() -> None:
    entry = {
        "source": "savedOutfit",
        "assets": [
            {"id": 1, "name": "Lin - Head", "assetTypeName": "DynamicHead"},
            {"id": 2, "name": "Lin Mood", "assetTypeName": "MoodAnimation"},
        ],
    }
    assert is_low_information_saved_outfit(entry) is True


def test_low_information_saved_outfit_keeps_real_clothing_outfit() -> None:
    entry = {
        "source": "savedOutfit",
        "assets": [
            {"id": 1, "name": "Hair", "assetTypeName": "HairAccessory"},
            {"id": 2, "name": "Shirt", "assetTypeName": "Shirt"},
            {"id": 3, "name": "Pants", "assetTypeName": "Pants"},
        ],
    }
    assert is_low_information_saved_outfit(entry) is False


def test_asset_text_extraction_includes_names_types_and_tags() -> None:
    text = entry_text(sample_entry())
    assert "black emo hoodie" in text
    assert "shirt" in text
    assert "classic-clothing" in text


def test_known_asset_config_and_flags() -> None:
    known = load_known_assets(Path("roblox_outfits/known_assets.json"))
    flags = deterministic_flags(sample_entry(), known)
    assert flags["headless"] is True
    assert flags["classic_clothing"] is True
    assert flags["r6"] is True


def test_workspace_insert_update_behavior(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / ".workspace")
    ws.init()
    with ws.connect() as conn:
        first_id = ws.upsert_entries([sample_entry()])
        second_id = ws.upsert_entries([sample_entry()])
        assert first_id == 1
        assert second_id == 1
        assert conn.execute("SELECT COUNT(*) FROM outfits").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 3


def test_scan_state_marks_completed_users(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / ".workspace")
    ws.init()
    with ws.connect() as conn:
        assert is_scan_complete(conn, 123, 456, 21, "Charming", "current") is False
        mark_scan(conn, 123, 456, 21, "Charming", "current", "complete")
        assert is_scan_complete(conn, 123, 456, 21, "Charming", "current") is True


def test_current_outfit_backfills_scan_skip(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / ".workspace")
    ws.init()
    ws.upsert_entries([sample_entry()])
    with ws.connect() as conn:
        assert is_scan_complete(conn, 123, 456, 200, "Sinner", "current") is True


def test_prediction_merge_and_workspace_export(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / ".workspace")
    ws.init()
    with ws.connect() as conn:
        ws.upsert_entries([sample_entry()])
        entry_id = conn.execute("SELECT entry_id FROM outfits").fetchone()[0]
        set_review(conn, entry_id, "approved", "androgynous_or_unclear", ["emo"], {"headless": True})
        result = classify_entry(
            sample_entry(),
            ClassifierConfig(workspace=ws.root, review_threshold=0.7, style_threshold=0.2),
            load_known_assets(Path("roblox_outfits/known_assets.json")),
            {"emo": ["emo", "ripped"]},
            None,
        )
        save_prediction(conn, entry_id, result)
        output = tmp_path / "OutfitSources.json"
        count = export_workspace(conn, output, "json", approved_only=True, include_classification=True)

    exported = json.loads(output.read_text(encoding="utf-8"))
    assert count == 1
    assert exported[0]["classification"]["flags"]["headless"] is True
    assert exported[0]["classification"]["styles"][0]["label"] in {"emo", "other_uncertain"}


def test_set_review_commits_immediately(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / ".workspace")
    ws.init()
    ws.upsert_entries([sample_entry()])
    with ws.connect() as conn:
        entry_id = conn.execute("SELECT entry_id FROM outfits").fetchone()[0]
        set_review(conn, entry_id, "rejected", "androgynous_or_unclear", ["other_uncertain"], {}, "default avatar")
    with ws.connect() as conn:
        row = conn.execute("SELECT review_status, rejected, rejection_reason FROM outfits").fetchone()
        assert row["review_status"] == "rejected"
        assert row["rejected"] == 1
        assert row["rejection_reason"] == "default avatar"


def test_dashboard_can_filter_to_cached_thumbnails(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / ".workspace")
    ws.init()
    ws.upsert_entries([sample_entry()])
    with ws.connect() as conn:
        entry_id = conn.execute("SELECT entry_id FROM outfits").fetchone()[0]
        assert fetch_review_rows(conn, ["unreviewed"], "all", "all", False, True, "", 10) == []
        conn.execute(
            "INSERT INTO thumbnails(entry_id, path, status, image_url, updated_at) VALUES(?, ?, ?, ?, ?)",
            (entry_id, str(tmp_path / "thumb.png"), "cached", None, 1),
        )
        rows = fetch_review_rows(conn, ["unreviewed"], "all", "all", False, True, "", 10)
        assert len(rows) == 1


def test_lua_serialization_tolerates_enriched_entries(tmp_path: Path) -> None:
    output = tmp_path / "OutfitSources.lua"
    collect_outfits.write_lua([sample_entry()], output, include_metadata=True)
    text = output.read_text(encoding="utf-8")
    assert 'source = "currentAvatar"' in text
    assert "assetIds = { 134082579, 10, 11 }" in text
