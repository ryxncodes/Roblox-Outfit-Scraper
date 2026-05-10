# Roblox Outfit Collector

This is a small offline tool for building an avatar/outfit source pool for an infinite outfit game.

The best source strategy is to use Roblox groups that already curate avatar quality, then collect from specific trusted ranks. For outfit ranking groups, the strongest quality signal is usually what the ranked user is currently wearing, so the collector always supports saving current avatars.

## Setup

Requires Python 3.10 or newer. No third-party packages are needed.

Copy `outfit_sources.example.json` to your own config file and replace the group id and ranks:

```json
{
  "sources": [
    {
      "groupId": 123456,
      "ranks": [200, 250, 255],
      "roleNames": ["Sinner"],
      "maxUsersPerRank": 250,
      "maxOutfitsPerUser": 20
    }
  ],
  "filters": {
    "avatarTypes": ["R6"],
    "requireAssetTypes": ["Shirt", "Pants"],
    "excludeAssetTypes": ["JacketAccessory", "SweaterAccessory"],
    "includeKeywords": ["emo", "black"],
    "excludeKeywords": ["korblox", "headless"],
    "allowDefaultClothing": false
  }
}
```

Filters are optional. If `filters` is omitted, the collector keeps everything it finds.

Supported filters:

- `avatarTypes`: `["R6"]`, `["R15"]`, or both.
- `requireAssetTypes`: every listed asset type must be worn.
- `excludeAssetTypes`: entries with any listed asset type are skipped.
- `includeKeywords`: keeps entries where any asset name/type contains one of the words.
- `excludeKeywords`: skips entries where any asset name/type contains one of the words.
- `minAssets` / `maxAssets`: rough complexity limits.
- `allowDefaultClothing`: set to `false` to skip avatars using Roblox default shirt/pants.

Role names can be supplied with `roleNames` or `rankNames`. This is useful when you do not know Roblox's internal role id:

```json
{
  "sources": [
    {
      "groupId": 13353323,
      "rankNames": ["🥰🎀| 𝐂𝐡𝐚𝐫𝐦𝐢𝐧𝐠 |🎀🥰"],
      "maxUsersPerRank": 25,
      "maxOutfitsPerUser": 20
    }
  ],
  "filters": {}
}
```

Optional workspace, dashboard, and classifier dependencies are separate from the base scraper:

```powershell
python -m pip install -r .\requirements-classifier.txt
```

The old `collect_outfits.py` flow still works without these packages.

## Run

Estimate source size before a full run:

```powershell
python .\collect_outfits.py --estimate --config .\outfit_sources.json
```

Current avatar only:

```powershell
python .\collect_outfits.py --mode current-only --config .\outfit_sources.json --output .\OutfitSources.lua --metadata
```

Current avatar plus saved outfits:

```powershell
python .\collect_outfits.py --mode current-plus-saved --config .\outfit_sources.json --output .\OutfitSources.lua --metadata --validate-thumbnails
```

Or run a one-off source directly:

```powershell
python .\collect_outfits.py --mode current-plus-saved --group-id 123456 --rank 250 --rank 255 --output .\OutfitSources.lua --metadata --validate-thumbnails
```

You can also use a rank/role name instead of the numeric rank:

```powershell
python .\collect_outfits.py --mode current-only --group-id 35347855 --role-name Sinner --max-users-per-rank 10 --output .\OutfitSources.lua --metadata
```

For quick one-off filtering without editing JSON:

```powershell
python .\collect_outfits.py --mode current-only --group-id 123456 --rank 250 --avatar-type R6 --include-keyword emo --output .\OutfitSources.lua --metadata
```

Useful run with tuning files:

```powershell
python .\collect_outfits.py --config .\outfit_sources.json --output .\OutfitSources.lua --metadata --json-output .\OutfitSources.json --rejected-output .\RejectedOutfits.json --split-output-by tag
```

`--split-output-by` keeps the main output and also writes grouped files next to it. Supported groups are `source`, `avatar-type`, `group`, `rank`, and `tag`.

## Output

Use `--metadata` for this project. Current avatars do not have outfit ids, so metadata keeps the source clear:

```lua
return {
    {
        source = "savedOutfit",
        outfitId = 123,
        userId = 456,
        groupId = 789,
        rank = 250,
        currentMatch = true,
        avatarType = "R6",
        tags = { "classic-clothing", "current-match", "keyword-emo", "r6" },
        assetIds = { 111, 222, 333 },
        name = "Outfit",
        username = "Player",
        roleName = "Rank"
    },
    {
        source = "currentAvatar",
        outfitId = nil,
        userId = 999,
        groupId = 789,
        rank = 250,
        currentMatch = true,
        avatarType = "R6",
        tags = { "classic-clothing", "keyword-emo", "r6" },
        assetIds = { 444, 555, 666 },
        name = "",
        username = "OtherPlayer",
        roleName = "Rank"
    },
}
```

In `current-plus-saved` mode, the collector:

- Pulls the user's current avatar.
- Pulls the user's saved outfits.
- Compares saved outfit assets against the current avatar assets.
- Saves the saved outfit id when it matches the current avatar.
- Saves the current avatar by user id when no saved outfit matches.
- Saves non-duplicate saved outfits too.

Duplicate detection is intentionally practical: if two entries have the same worn asset ids, only one is kept. This ignores body type differences, which is what we want for this game.

Without `--metadata`, the output is a simple array. Saved outfit entries write `outfitId`; current avatar entries write `userId`. Because that can be ambiguous, metadata output is recommended.

`--rejected-output` writes filtered-out entries and rejection reasons so you can tune filters without guessing.

## Cache

The script caches Roblox API responses in:

```powershell
.\.cache\roblox-outfits
```

This makes repeated test runs much faster and gentler on rate limits.

Clear the cache and keep going:

```powershell
python .\collect_outfits.py --clear-cache --config .\outfit_sources.json --metadata
```

Clear the cache only:

```powershell
python .\collect_outfits.py --clear-cache-only
```

Use a different cache directory:

```powershell
python .\collect_outfits.py --cache-dir .\.cache\r6-emo --config .\outfit_sources.json --metadata
```

Disable caching for one run:

```powershell
python .\collect_outfits.py --no-cache --config .\outfit_sources.json --metadata
```

## Rate Limits

The script uses a soft request cap with `--requests-per-minute` and also handles Roblox `429` responses. When Roblox sends `retry-after` or `x-ratelimit-reset`, the script waits that long. If those headers are missing, it retries with exponential backoff.

The default is intentionally conservative:

```powershell
--requests-per-minute 90 --max-retries 6
```

For large groups, start with `maxUsersPerRank` and `maxOutfitsPerUser` limits so you can test the source quality before doing a long run.

## Workspace Workflow

The package workflow stores scraped outfits, asset metadata, thumbnails, labels, predictions, review state, and exports in a local SQLite workspace:

```powershell
python -m roblox_outfits init --workspace .\.workspace
python -m roblox_outfits go --group-id 13353323 --rank-name "Exact Role Name" --workspace .\.workspace --download-thumbnails
python -m roblox_outfits thumbnails --workspace .\.workspace
python -m roblox_outfits dashboard --workspace .\.workspace
python -m roblox_outfits train --workspace .\.workspace
python -m roblox_outfits classify --workspace .\.workspace
python -m roblox_outfits export --workspace .\.workspace --format lua --output .\OutfitSources.lua
```

The wrapper form is also available:

```powershell
python .\roblox_outfits.py init --workspace .\.workspace
python .\roblox_outfits.py go --group-id 13353323 --rank-name "Exact Role Name" --workspace .\.workspace
python .\roblox_outfits.py dashboard --workspace .\.workspace
python .\roblox_outfits.py train --workspace .\.workspace
python .\roblox_outfits.py classify --workspace .\.workspace
python .\roblox_outfits.py export --workspace .\.workspace --format json --output .\OutfitSources.json
```

The workspace layout is:

```text
.workspace/
  roblox_outfits.sqlite
  thumbnails/
  models/
  exports/
  logs/
```

`scrape` reuses the existing collection logic and stores entries in SQLite instead of making you move CSVs or JSON files between scripts. Add `--download-thumbnails` during scrape, or run `thumbnails` separately.

For the simplest start, skip the JSON config and tell the tool the group plus rank name directly:

```powershell
python -m roblox_outfits go --group-id 13353323 --rank-name "Exact Role Name" --workspace .\.workspace --download-thumbnails --open-dashboard
```

For PowerShell quoting-sensitive role names with emojis or styled Unicode, a JSON config is still useful, but it is no longer required for a single source.

The workspace workflow defaults to ranked users' current avatars only. This is faster and usually cleaner than saved outfits. Saved outfits are noisy: many are head-only, animation-only, shoe-only, or tiny changes that are not useful for style classification.

If you intentionally want saved outfits for a specific user:

```powershell
python -m roblox_outfits user-outfits --user-id 123456789 --workspace .\.workspace --max-outfits 50 --download-thumbnails
```

If you intentionally want saved outfits for every ranked user, opt in explicitly:

```powershell
python -m roblox_outfits go --group-id 13353323 --rank-name "Exact Role Name" --workspace .\.workspace --saved-outfits
```

Workspace scrape writes discovered entries to SQLite as it goes. Reruns skip already scanned current-avatar users. If Roblox rate-limits a saved-outfit page after retries, the scraper skips that user or outfit, keeps already stored current avatars, and reports `rateLimitedSkipped` in the final stats.

The normal path should not require manually choosing a speed. The scraper adapts when Roblox returns `429 Too Many Requests` by waiting and slowing future requests. The speed flags are only there for unusual troubleshooting.

## Dashboard Review

Open the local review UI with:

```powershell
python -m roblox_outfits dashboard --workspace .\.workspace
```

The dashboard shows one review card at a time so the browser does not get buried under hundreds of widgets. It includes the thumbnail when cached, username, user id, outfit id, source, group, rank, role name, avatar type, worn asset names/types, deterministic flags, predictions, confidence scores, review status, and rejection state.

Supported review actions:

- approve prediction
- edit presentation label
- add/remove style labels
- add/remove flags
- mark uncertain
- skip
- reject from export
- save corrected labels
- filter by source, avatar type, review status, and prediction review need

The dashboard writes directly to `roblox_outfits.sqlite`; normal labeling does not require CSV export/import.

## Classification

Classification is optional and intentionally hybrid. Roblox aesthetics such as preppy, emo, goth, y2k, softie, slender, kawaii, and streetwear overlap too much for pure zero-shot CLIP to be the main classifier.

The current MVP uses:

- deterministic Roblox-specific flags from asset IDs, asset names, asset types, and avatar metadata
- metadata/text features from worn asset names, asset types, outfit name, tags, and avatar type
- supervised scikit-learn classifiers trained from dashboard-reviewed labels
- configurable keyword/style hint boosts
- a low-confidence review queue

Image embeddings are reserved for an optional later layer. If image dependencies are not installed, metadata-only classification still works.

Label definitions live in `roblox_outfits/labels.py`. Known Roblox asset IDs live in `roblox_outfits/known_assets.json`, and style hint keywords live in `roblox_outfits/style_hints.json`, so you can tune IDs and hints without editing classifier code.

Presentation labels:

- `feminine_presenting`
- `masculine_presenting`
- `androgynous_or_unclear`

Style labels:

- `angel`, `baddie`, `classic_blocky`, `clean_minimal`, `coquette`, `cosplay_anime`
- `cottagecore`, `cutesy`, `cyber`, `demon`, `e_girl_e_boy`, `emo`, `evade`
- `fairycore`, `fantasy`, `gamer`, `goth`, `grunge`, `hood`, `horror`, `kawaii`
- `kidcore`, `maid_butler`, `military_tactical`, `preppy`, `rich_flex`, `royale_high`
- `scene`, `school_uniform`, `skater`, `slender`, `softie`, `sports`, `streetwear`
- `techwear`, `troll_meme`, `vampire`, `y2k`, `other_uncertain`

Flags:

- `headless`, `korblox`, `layered_clothing`, `classic_clothing`, `r6`, `r15`, `default_clothing`

Train after you have reviewed some examples:

```powershell
python -m roblox_outfits train --workspace .\.workspace
```

Then classify unreviewed outfits:

```powershell
python -m roblox_outfits classify --workspace .\.workspace
```

The workflow is meant to improve through active learning:

1. Scrape group/rank outfits.
2. Label obvious examples in the dashboard.
3. Train.
4. Classify unreviewed outfits.
5. Review uncertain or wrong cases.
6. Retrain and repeat.

You do not need thousands of labels for the loop to become useful. Start with roughly 30-100 clean examples per major style and improve from there.

## Workspace Export

Workspace exports exclude rejected outfits. By default, exports include approved/corrected entries only:

```powershell
python -m roblox_outfits export --workspace .\.workspace --format lua --output .\OutfitSources.lua
python -m roblox_outfits export --workspace .\.workspace --format json --output .\OutfitSources.json
python -m roblox_outfits export --workspace .\.workspace --style emo --format lua --output .\OutfitSources_emo.lua
```

Lua classification metadata is flattened for Roblox:

```lua
presentation = "feminine_presenting"
presentationConfidence = 0.87
styles = { "preppy", "y2k" }
styleScores = { preppy = 0.81, y2k = 0.43 }
classificationFlags = { "korblox", "classic_clothing" }
needsReview = false
```

JSON exports include a nested `classification` object with presentation, style scores, flags, method, and `needsReview`.

## Troubleshooting

- Missing dashboard/classifier packages: run `python -m pip install -r .\requirements-classifier.txt`.
- Too few labels: approve or correct more dashboard examples before `train`.
- Low confidence: review uncertain predictions, save corrections, and retrain.
- Missing thumbnails: run `python -m roblox_outfits thumbnails --workspace .\.workspace`; Roblox may return pending/missing states, and metadata-only classification can continue.
- Roblox rate limits: reduce source caps, rely on `.cache\roblox-outfits`, try `--current-only`, or slow the workspace scrape with `--requests-per-minute 20 --max-retries 12`.
