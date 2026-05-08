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
      "maxUsersPerRank": 250,
      "maxOutfitsPerUser": 20
    }
  ]
}
```

## Run

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

## Rate Limits

The script uses a soft request cap with `--requests-per-minute` and also handles Roblox `429` responses. When Roblox sends `retry-after` or `x-ratelimit-reset`, the script waits that long. If those headers are missing, it retries with exponential backoff.

The default is intentionally conservative:

```powershell
--requests-per-minute 90 --max-retries 6
```

For large groups, start with `maxUsersPerRank` and `maxOutfitsPerUser` limits so you can test the source quality before doing a long run.
