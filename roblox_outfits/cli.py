from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import collect_outfits

from .classify import classify_workspace
from .config import load_classifier_config
from .export import export_workspace
from .quality import is_low_information_saved_outfit
from .thumbnails import cache_thumbnails
from .train import train_metadata_model
from .workspace import Workspace, entry_key, is_scan_complete, mark_scan, upsert_entry


def saved_outfit_entry(
    user_id: int,
    username: str,
    outfit: dict,
    details: dict,
    current_signature: tuple[int, ...],
) -> dict:
    signature = collect_outfits.avatar_signature(details)
    outfit_id = int(outfit["id"])
    entry = {
        "source": "savedOutfit",
        "id": outfit_id,
        "outfitId": outfit_id,
        "userId": user_id,
        "username": username,
        "groupId": None,
        "rank": None,
        "roleName": "",
        "name": outfit.get("name", ""),
        "currentMatch": signature == current_signature,
        "avatarType": collect_outfits.avatar_type(details),
        "assetIds": collect_outfits.asset_ids_from_signature(signature),
        "assets": collect_outfits.normalized_assets(details),
        "tags": [],
    }
    entry["tags"] = collect_outfits.deterministic_tags(
        entry,
        details,
        collect_outfits.Filters(),
    )
    return entry


def scrape_one_users_saved_outfits(args: argparse.Namespace, ws: Workspace) -> dict:
    client = collect_outfits.RobloxClient(
        requests_per_minute=30,
        max_retries=10,
        user_agent="RobloxOutfitCollector/1.0 (+local development)",
        timeout=20.0,
        cache_dir=Path(".cache/roblox-outfits"),
    )
    username = args.username
    current_signature: tuple[int, ...] = ()
    try:
        current_avatar = collect_outfits.get_current_avatar(client, args.user_id)
        current_signature = collect_outfits.avatar_signature(current_avatar)
    except RuntimeError:
        current_avatar = {}
    outfits = collect_outfits.get_user_outfits(client, args.user_id, args.max_outfits, args.max_pages)
    stats = {"seen": 0, "stored": 0, "lowInformationSkipped": 0, "errorsSkipped": 0}
    with ws.connect() as conn:
        for outfit in outfits:
            stats["seen"] += 1
            try:
                details = collect_outfits.get_outfit_details(client, int(outfit["id"]))
            except RuntimeError:
                stats["errorsSkipped"] += 1
                continue
            entry = saved_outfit_entry(args.user_id, username, outfit, details, current_signature)
            if is_low_information_saved_outfit(entry) and not args.include_low_info:
                stats["lowInformationSkipped"] += 1
                continue
            existed = conn.execute("SELECT 1 FROM outfits WHERE entry_id = ?", (entry_key(entry),)).fetchone()
            upsert_entry(conn, entry)
            if not existed:
                stats["stored"] += 1
        mark_scan(conn, args.user_id, None, None, None, "saved", "complete")
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Roblox outfit workspace and classifier workflow.")
    parser.add_argument("--workspace", type=Path, default=Path(".workspace"), help="Workspace folder.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_workspace_arg(command: argparse.ArgumentParser) -> None:
        command.add_argument("--workspace", type=Path, default=argparse.SUPPRESS, help="Workspace folder.")

    init = sub.add_parser("init", help="Create the local SQLite workspace.")
    add_workspace_arg(init)

    scrape = sub.add_parser("scrape", help="Scrape outfits and store them in the workspace.")
    add_workspace_arg(scrape)
    scrape.add_argument("--config", type=Path, help="Optional JSON config file with sources.")
    scrape.add_argument("--group-id", type=int, help="Single group id source.")
    scrape.add_argument("--rank", type=int, action="append", help="Numeric rank. Repeat for multiple ranks.")
    scrape.add_argument("--role-name", action="append", help="Role/rank name. Repeat for multiple names.")
    scrape.add_argument("--rank-name", action="append", help="Alias for --role-name.")
    scrape.add_argument("--mode", choices=("current-only", "current-plus-saved"), default="current-only")
    scrape.add_argument("--download-thumbnails", action="store_true")
    scrape.add_argument("--limit", type=int)
    scrape.add_argument("--requests-per-minute", type=int, default=45, help="Workspace scrape request cap.")
    scrape.add_argument("--max-retries", type=int, default=10, help="Retries for 429 and transient server errors.")
    scrape.add_argument("--timeout", type=float, default=20.0, help="Request timeout in seconds.")
    scrape.add_argument("--max-outfit-pages", type=int, default=3, help="Maximum saved outfit pages per user.")
    scrape.add_argument("--max-users-per-rank", type=int, help="Override config max users per rank for smoke runs.")
    scrape.add_argument("--max-outfits-per-user", type=int, help="Override config max saved outfits per user.")
    scrape.add_argument("--cache-dir", type=Path, default=Path(".cache/roblox-outfits"), help="Roblox API cache.")
    scrape.add_argument("--no-cache", action="store_true", help="Disable cache reads and writes.")
    scrape.add_argument("--current-only", action="store_true", help="Shortcut for --mode current-only.")

    thumbs = sub.add_parser("thumbnails", help="Download/cache thumbnails for workspace outfits.")
    add_workspace_arg(thumbs)
    thumbs.add_argument("--refresh", action="store_true")
    thumbs.add_argument("--limit", type=int)

    user_outfits = sub.add_parser("user-outfits", help="Pull saved outfits for one specific user.")
    add_workspace_arg(user_outfits)
    user_outfits.add_argument("--user-id", type=int, required=True)
    user_outfits.add_argument("--username", default="")
    user_outfits.add_argument("--max-outfits", type=int, default=50)
    user_outfits.add_argument("--max-pages", type=int, default=2)
    user_outfits.add_argument("--download-thumbnails", action="store_true")
    user_outfits.add_argument("--include-low-info", action="store_true")

    dash = sub.add_parser("dashboard", help="Open the local Streamlit review dashboard.")
    add_workspace_arg(dash)
    dash.add_argument("--port", type=int, default=8501)

    train = sub.add_parser("train", help="Train metadata/text classifiers from reviewed labels.")
    add_workspace_arg(train)
    train.add_argument("--config", type=Path, help="Classifier config JSON.")

    classify = sub.add_parser("classify", help="Classify unreviewed outfits and save predictions.")
    add_workspace_arg(classify)
    classify.add_argument("--config", type=Path, help="Classifier config JSON.")
    classify.add_argument("--limit", type=int)

    export = sub.add_parser("export", help="Export approved workspace outfits.")
    add_workspace_arg(export)
    export.add_argument("--format", choices=("lua", "json"), default="lua")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--include-classification", action=argparse.BooleanOptionalAction, default=True)
    export.add_argument("--approved-only", action=argparse.BooleanOptionalAction, default=True)
    export.add_argument("--style")
    export.add_argument("--presentation")

    stats = sub.add_parser("stats", help="Print workspace counts.")
    add_workspace_arg(stats)
    cleanup = sub.add_parser("cleanup", help="Hide or delete low-information saved outfits from the review queue.")
    add_workspace_arg(cleanup)
    cleanup.add_argument("--delete", action="store_true", help="Delete junk saved outfits instead of marking rejected.")
    cleanup.add_argument(
        "--thumbnail-failures",
        action="store_true",
        help="Also reject non-rejected outfits with blocked/error/missing thumbnails.",
    )
    go = sub.add_parser("go", help="One-command scrape setup for a group/rank source.")
    add_workspace_arg(go)
    go.add_argument("--group-id", type=int, required=True)
    go.add_argument("--rank-name", action="append", required=True, help="Role/rank name. Repeat for multiple names.")
    go.add_argument("--download-thumbnails", action="store_true")
    go.add_argument("--saved-outfits", action="store_true", help="Also scan saved outfits for every ranked user.")
    go.add_argument("--open-dashboard", action="store_true", help="Open dashboard after scraping.")
    go.add_argument("--max-users-per-rank", type=int)
    go.add_argument("--max-outfits-per-user", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ws = Workspace(args.workspace)

    if args.command == "init":
        ws.init()
        print(f"Initialized workspace: {ws.root}")
        return 0

    if args.command == "scrape":
        ws.init()
        role_names = (args.role_name or []) + (args.rank_name or [])
        if not args.config and not args.group_id:
            raise SystemExit("Use --config, or pass --group-id with --rank-name/--role-name/--rank.")
        collect_args = collect_outfits.build_parser().parse_args(
            (["--config", str(args.config)] if args.config else [])
            + (
                ["--group-id", str(args.group_id)]
                + [item for rank in (args.rank or []) for item in ("--rank", str(rank))]
                + [item for name in role_names for item in ("--role-name", str(name))]
                if args.group_id
                else []
            )
            + [
                "--mode",
                "current-only" if args.current_only else args.mode,
                "--metadata",
                "--requests-per-minute",
                str(args.requests_per_minute),
                "--max-retries",
                str(args.max_retries),
                "--timeout",
                str(args.timeout),
                "--max-outfit-pages",
                str(args.max_outfit_pages),
                "--cache-dir",
                str(args.cache_dir),
                "--continue-on-request-error",
            ]
            + (["--limit", str(args.limit)] if args.limit else [])
            + (["--max-users-per-rank", str(args.max_users_per_rank)] if args.max_users_per_rank else [])
            + (["--max-outfits-per-user", str(args.max_outfits_per_user)] if args.max_outfits_per_user else [])
            + (["--no-cache"] if args.no_cache else [])
        )
        progress = {"stored": 0, "seen": 0, "duplicates": 0}
        workspace_conn = ws.connect()

        def store_progress(entry: dict) -> None:
            progress["seen"] += 1
            if is_low_information_saved_outfit(entry):
                return
            before = workspace_conn.execute("SELECT 1 FROM outfits WHERE entry_id = ?", (entry_key(entry),)).fetchone()
            upsert_entry(workspace_conn, entry)
            workspace_conn.commit()
            if before:
                progress["duplicates"] += 1
            else:
                progress["stored"] += 1
                if progress["stored"] % 25 == 0:
                    print(f"Stored {progress['stored']} new outfits so far...")

        def skip_user(user_id: int, group_id: int, rank: int, role_name: str, scan_type: str) -> bool:
            return is_scan_complete(workspace_conn, user_id, group_id, rank, role_name, scan_type)

        def record_scan(
            user_id: int,
            group_id: int,
            rank: int,
            role_name: str,
            scan_type: str,
            status: str,
            message: str | None = None,
        ) -> None:
            mark_scan(workspace_conn, user_id, group_id, rank, role_name, scan_type, status, message)
            workspace_conn.commit()

        collect_args.entry_callback = store_progress
        collect_args.skip_user_callback = skip_user
        collect_args.user_scan_callback = record_scan
        try:
            entries, stats, _rejected = collect_outfits.collect(collect_args)
        finally:
            workspace_conn.close()
        before_quality = len(entries)
        entries = [entry for entry in entries if not is_low_information_saved_outfit(entry)]
        count = ws.upsert_entries(entries)
        if args.download_thumbnails:
            client = collect_outfits.RobloxClient(
                requests_per_minute=collect_args.requests_per_minute,
                max_retries=collect_args.max_retries,
                user_agent=collect_args.user_agent,
                timeout=collect_args.timeout,
                cache_dir=collect_args.cache_dir,
            )
            with ws.connect() as conn:
                cache_thumbnails(conn, ws.root, client)
        print(
            json.dumps(
                {
                    "stored": count,
                    "newStoredDuringRun": progress["stored"],
                    "candidatesSeenDuringRun": progress["seen"],
                    "duplicatesUpdatedDuringRun": progress["duplicates"],
                    "lowInformationSavedOutfitsSkipped": before_quality - len(entries),
                    "scrapeStats": stats,
                },
                indent=2,
            )
        )
        return 0

    if args.command == "go":
        argv = [
            "--workspace",
            str(ws.root),
            "scrape",
            "--group-id",
            str(args.group_id),
        ]
        for rank_name in args.rank_name:
            argv.extend(["--rank-name", rank_name])
        if args.download_thumbnails:
            argv.append("--download-thumbnails")
        if args.saved_outfits:
            argv.extend(["--mode", "current-plus-saved"])
        else:
            argv.append("--current-only")
        if args.max_users_per_rank:
            argv.extend(["--max-users-per-rank", str(args.max_users_per_rank)])
        if args.max_outfits_per_user:
            argv.extend(["--max-outfits-per-user", str(args.max_outfits_per_user)])
        result = main(argv)
        if result != 0 or not args.open_dashboard:
            return result
        return main(["--workspace", str(ws.root), "dashboard"])

    if args.command == "user-outfits":
        ws.init()
        stored = scrape_one_users_saved_outfits(args, ws)
        if args.download_thumbnails:
            client = collect_outfits.RobloxClient(45, 10, "RobloxOutfitCollector/1.0 (+local development)", 20.0, Path(".cache/roblox-outfits"))
            with ws.connect() as conn:
                cache_thumbnails(conn, ws.root, client)
        print(json.dumps(stored, indent=2))
        return 0

    if args.command == "thumbnails":
        ws.init()
        client = collect_outfits.RobloxClient(90, 6, "RobloxOutfitCollector/1.0 (+local development)", 20.0, Path(".cache/roblox-outfits"))
        with ws.connect() as conn:
            count = cache_thumbnails(conn, ws.root, client, refresh=args.refresh, limit=args.limit)
        print(f"Cached {count} thumbnails")
        return 0

    if args.command == "dashboard":
        ws.init()
        if importlib.util.find_spec("streamlit") is None:
            raise SystemExit(
                "Dashboard dependencies are missing. Install them with: "
                "python -m pip install -r requirements-classifier.txt"
            )
        cmd = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(Path(__file__).with_name("dashboard.py")),
            "--server.port",
            str(args.port),
            "--",
            "--workspace",
            str(ws.root),
        ]
        print(f"Opening dashboard at http://localhost:{args.port}")
        return subprocess.call(cmd)

    if args.command == "train":
        ws.init()
        cfg = load_classifier_config(args.config, ws.root)
        with ws.connect() as conn:
            result = train_metadata_model(conn, cfg.resolved_model_path)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "classify":
        ws.init()
        cfg = load_classifier_config(args.config, ws.root)
        with ws.connect() as conn:
            count = classify_workspace(conn, cfg, limit=args.limit)
        print(f"Classified {count} outfits")
        return 0

    if args.command == "export":
        ws.init()
        with ws.connect() as conn:
            count = export_workspace(
                conn,
                args.output,
                args.format,
                approved_only=args.approved_only,
                include_classification=args.include_classification,
                style=args.style,
                presentation=args.presentation,
            )
        print(f"Wrote {count} entries to {args.output}")
        return 0

    if args.command == "stats":
        ws.init()
        with ws.connect() as conn:
            stats = {
                "outfits": conn.execute("SELECT COUNT(*) FROM outfits").fetchone()[0],
                "reviewQueue": conn.execute(
                    "SELECT COUNT(*) FROM outfits WHERE rejected=0 AND review_status IN ('unreviewed','uncertain')"
                ).fetchone()[0],
                "approved": conn.execute("SELECT COUNT(*) FROM outfits WHERE review_status IN ('approved','corrected')").fetchone()[0],
                "rejected": conn.execute("SELECT COUNT(*) FROM outfits WHERE rejected = 1").fetchone()[0],
                "predictions": conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0],
                "labels": conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0],
                "thumbnails": conn.execute("SELECT COUNT(*) FROM thumbnails WHERE path IS NOT NULL").fetchone()[0],
                "currentUsersStored": conn.execute(
                    "SELECT COUNT(DISTINCT user_id) FROM outfits WHERE source='currentAvatar'"
                ).fetchone()[0],
                "completedCurrentUserScans": conn.execute(
                    "SELECT COUNT(*) FROM scan_state WHERE scan_type='current' AND status='complete'"
                ).fetchone()[0],
                "completedSavedUserScans": conn.execute(
                    "SELECT COUNT(*) FROM scan_state WHERE scan_type='saved' AND status='complete'"
                ).fetchone()[0],
            }
        print(json.dumps(stats, indent=2))
        return 0

    if args.command == "cleanup":
        ws.init()
        import json as _json
        import time as _time

        with ws.connect() as conn:
            rows = list(conn.execute("SELECT entry_id, raw_json FROM outfits WHERE source = 'savedOutfit'"))
            junk_ids = [
                row["entry_id"]
                for row in rows
                if is_low_information_saved_outfit(_json.loads(row["raw_json"] or "{}"))
            ]
            if args.delete:
                conn.executemany("DELETE FROM outfits WHERE entry_id = ?", [(entry_id,) for entry_id in junk_ids])
            else:
                conn.executemany(
                    """
                    UPDATE outfits
                    SET review_status='rejected',
                        rejected=1,
                        rejection_reason='low_information_saved_outfit',
                        updated_at=?
                    WHERE entry_id = ?
                    """,
                    [(_time.time(), entry_id) for entry_id in junk_ids],
                )
            thumb_ids: list[str] = []
            if args.thumbnail_failures:
                thumb_ids = [
                    row["entry_id"]
                    for row in conn.execute(
                        """
                        SELECT o.entry_id
                        FROM outfits o
                        LEFT JOIN thumbnails t ON t.entry_id = o.entry_id
                        WHERE o.rejected = 0
                          AND (t.entry_id IS NULL OR t.path IS NULL)
                          AND COALESCE(t.status, 'missing') IN ('missing', 'blocked', 'error', 'rate_limited')
                        """
                    )
                ]
                if args.delete:
                    conn.executemany("DELETE FROM outfits WHERE entry_id = ?", [(entry_id,) for entry_id in thumb_ids])
                else:
                    conn.executemany(
                        """
                        UPDATE outfits
                        SET review_status='rejected',
                            rejected=1,
                            rejection_reason='thumbnail_unavailable',
                            updated_at=?
                        WHERE entry_id = ?
                        """,
                        [(_time.time(), entry_id) for entry_id in thumb_ids],
                    )
        action = "Deleted" if args.delete else "Marked rejected"
        if args.thumbnail_failures:
            print(f"{action} {len(junk_ids)} low-information saved outfits and {len(thumb_ids)} thumbnail failures")
        else:
            print(f"{action} {len(junk_ids)} low-information saved outfits")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
