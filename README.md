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
