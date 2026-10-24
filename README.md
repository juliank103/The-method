# Tweetus Deletus

A personal command-line tool to mass-delete your own X / Twitter content.
It does everything the Redact app does for X, through X's **official API v2**,
acting only on the account you log in as. Nothing is sent anywhere except to
`api.x.com` with your own OAuth token.

> It's just for me, so there's no account system, no server, no telemetry — it
> runs on your machine against your account and that's it.

## What it can delete

| Category     | What happens                         | Endpoint used                               |
|--------------|--------------------------------------|---------------------------------------------|
| `posts`      | delete posts / replies / quotes      | `DELETE /2/tweets/:id`                       |
| (reposts)    | un-repost (within `posts`)           | `DELETE /2/users/:id/retweets/:source_id`   |
| `likes`      | un-like                              | `DELETE /2/users/:id/likes/:tweet_id`       |
| `bookmarks`  | remove bookmark                      | `DELETE /2/users/:id/bookmarks/:tweet_id`   |
| `following`  | unfollow (mass)                      | `DELETE /2/users/:id/following/:target`     |
| `mutes`      | unmute                               | `DELETE /2/users/:id/muting/:target`        |
| `blocks`     | unblock                              | `DELETE /2/users/:id/blocking/:target`      |
| `lists`      | delete owned lists                   | `DELETE /2/lists/:id`                        |
| `dms`        | delete your DM messages              | `DELETE /2/dm_events/:event_id`             |

Reposts, replies and quotes all live inside `posts`; use the structural flags
(`--only-reposts`, `--no-replies`, …) to narrow them.

## Features (the Redact feature set)

- **Every content type** above, individually or all at once (`--what all`).
- **Filters**: keyword (`--keyword`, repeatable; `--keyword-all` for AND),
  regex (`--regex`), hashtag (`--hashtag`), date range (`--before`/`--after`),
  age (`--older-than`/`--newer-than` days), engagement thresholds
  (`--min-likes`/`--max-likes`, reposts, replies), media
  (`--has-media`/`--no-media`/`--media-type photo|video|animated_gif`), links
  (`--has-link`), and structure (`--only-replies`, `--only-reposts`,
  `--no-replies`, `--no-reposts`).
- **Preview mode** — `preview` shows exactly what would go, deletes nothing.
- **Review mode** — `review` steps through each item: `y`/`n`/`all`/`quit`.
- **Protect / whitelist** — `--keep-id`, `--keep-ids-file`, `--keep-keyword`,
  `--keep-handle`, and your pinned post is protected by default
  (`--delete-pinned` to override). Protection always beats a match.
- **Archive import** — `--archive export.zip` reads ids from your official X
  data export so you can delete your *entire* history, not just the ~3200
  posts the live timeline exposes.
- **Local archive before delete** — `--log deleted.jsonl` keeps a copy of
  everything processed; `export-csv` turns it into a spreadsheet.
- **Disappearing mode** — `watch --older-than 7 --interval 6h` keeps deleting
  anything past an age, forever.
- **Rate-limit aware** — reads `x-rate-limit-*` headers and backs off on 429s,
  so long wipes run unattended.
- **Dry run** — `--dry-run` does everything except the final DELETE call.

## Setup

1. **Install** (Python 3.11+):
   ```bash
   pip install -r requirements.txt      # just: requests
   pip install -e .                     # optional: gives the `tweetus-deletus` command
   ```
   Without `pip install -e .` you can always run `python3 -m tweetus_deletus …`.

2. **Create an X API app** at <https://developer.x.com> (the free tier can
   delete your own content). In the app's *User authentication settings*:
   - App permissions: **Read and write and Direct message**
   - Type of App: **Native App** (public client, PKCE — no secret), or
     **Web App** if you prefer a confidential client with a secret.
   - Callback URI / Redirect URL: `http://127.0.0.1:8721/callback`

3. **Configure**: copy `config.example.toml` to
   `~/.config/tweetus_deletus/config.toml` and paste your Client ID
   (and Client Secret for a Web App). Or set `TWEETUS_CLIENT_ID` /
   `TWEETUS_CLIENT_SECRET` in your environment.

4. **Log in once** (opens your browser, stores a refresh token):
   ```bash
   tweetus-deletus login
   tweetus-deletus whoami        # confirms the account
   ```

## Usage

```bash
# See what would be deleted — always start here.
tweetus-deletus preview --what posts --before 2021-01-01

# Decide item by item.
tweetus-deletus review --what likes --older-than 365

# Delete old low-engagement posts, keeping a local copy.
tweetus-deletus delete --what posts --older-than 180 --max-likes 5 \
    --log ~/td-archive.jsonl --yes

# Nuke a whole category (extra confirmation; type DELETE).
tweetus-deletus wipe --what bookmarks

# Full-history wipe using your data export.
tweetus-deletus delete --what posts,likes --archive ~/twitter-export.zip \
    --log ~/td-archive.jsonl --yes

# Disappearing mode: keep only the last 7 days of posts, re-checking every 6h.
tweetus-deletus watch --what posts --older-than 7 --interval 6h

# Turn the archive log into a spreadsheet.
tweetus-deletus export-csv --log ~/td-archive.jsonl --csv ~/td-archive.csv
```

Protect specific things from any run:

```bash
tweetus-deletus delete --what posts --older-than 365 \
    --keep-keyword "announcement" --keep-id 1234567890 \
    --keep-ids-file ~/keep.txt
```

## Notes & limits

- **Deletions are permanent.** `--dry-run` and `preview` exist so you can be
  sure first; `--log` keeps your own copy.
- The **live timeline** only reaches ~3200 recent posts. For everything older,
  request your archive (Settings → Your account → Download an archive of your
  data) and pass it with `--archive`. Archive import covers
  `posts`, `likes`, `lists`, `dms`.
- **DMs**: X only lets you delete messages *you sent*, and only from your own
  side of the conversation.
- **Blocks/unblocks and some endpoints** may require a specific API access
  tier on your developer account; if a call returns 403, that's X's tier
  gate, not a bug here.
- Tokens live in `~/.config/tweetus_deletus/tokens.json` (chmod 600) and are
  git-ignored. So are your config, exports, and logs.

## Development

```bash
python3 tests/test_smoke.py        # offline tests, no network
# or: python3 -m pytest tests/ -q
```

The code is small and split by concern: `auth.py` (OAuth2 PKCE),
`client.py` (HTTP + rate limits), `actions.py` (fetch/delete per type),
`filters.py` (matching + protection), `archive.py` (export import + logging),
`runner.py` (orchestration), `cli.py` (commands).
