"""Twitter / X data-export import, and the local "archive before delete" log.

== Import ==
X's live timeline endpoint only reaches back ~3200 posts, so a true full wipe
needs the ids from your official data export. Request it at
Settings -> Your account -> Download an archive of your data. You get a .zip
whose `data/` folder holds JavaScript files such as:

    data/tweets.js           window.YTD.tweets.part0   = [ {"tweet": {...}} ]
    data/tweets-part1.js     window.YTD.tweets.part1   = [ ... ]
    data/like.js             window.YTD.like.part0     = [ {"like": {...}} ]
    data/direct-messages.js  window.YTD.direct_messages.part0 = [ ... ]
    data/lists-created.js    window.YTD.lists_created.part0   = [ ... ]

Each file is JSON preceded by a `window.YTD.<name>.partN = ` assignment. We
strip that prefix and json-load the rest, from either the .zip or an already
extracted folder, and turn the records into normalized Item objects so the
same filters apply.

== Archive log ==
Before (and as) items are deleted, we append them to a local JSONL file and
can emit a CSV. That's your "you always keep a local copy" safety net.
"""

from __future__ import annotations

import csv
import glob
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from typing import Iterator

from .models import Item, parse_time

_ASSIGN_RE = re.compile(r"^\s*window\.YTD\.[A-Za-z0-9_]+\.part\d+\s*=\s*",
                        re.DOTALL)


def _strip_assignment(text: str) -> str:
    return _ASSIGN_RE.sub("", text, count=1).strip()


def _twitter_time(value: str | None) -> datetime | None:
    if not value:
        return None
    # Export format: "Wed Oct 10 20:19:24 +0000 2018"
    try:
        return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return parse_time(value)


class ArchiveReader:
    """Reads records out of an export, whether a .zip or an extracted dir."""

    def __init__(self, path: str):
        self.path = path
        self.is_zip = zipfile.is_zipfile(path) if os.path.isfile(path) else False

    def _read_group(self, basename: str) -> list:
        """Return the merged list of records for an export group.

        `basename` is e.g. "tweets" or "like"; this merges tweets.js and any
        tweets-partN.js files.
        """
        records: list = []
        for raw in self._iter_group_text(basename):
            payload = _strip_assignment(raw)
            if not payload:
                continue
            records.extend(json.loads(payload))
        return records

    def _iter_group_text(self, basename: str) -> Iterator[str]:
        patterns = [f"data/{basename}.js", f"data/{basename}-part*.js",
                    f"{basename}.js", f"{basename}-part*.js"]
        if self.is_zip:
            with zipfile.ZipFile(self.path) as zf:
                names = zf.namelist()
                for pat in patterns:
                    rx = re.compile("^" + glob.fnmatch.translate(pat))
                    for name in names:
                        if rx.match(name):
                            yield zf.read(name).decode("utf-8", "replace")
        else:
            root = self.path
            seen = set()
            for pat in patterns:
                for fp in glob.glob(os.path.join(root, pat)):
                    if fp in seen:
                        continue
                    seen.add(fp)
                    with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                        yield fh.read()

    # -- typed extractors ----------------------------------------------

    def tweets(self) -> Iterator[Item]:
        for rec in self._read_group("tweets"):
            t = rec.get("tweet", rec)
            refs = t.get("referenced_tweets")  # not usually present in export
            is_reply = bool(t.get("in_reply_to_status_id_str")
                            or t.get("in_reply_to_user_id_str"))
            is_repost = t.get("full_text", "").startswith("RT @") or bool(
                t.get("retweeted_status"))
            entities = t.get("entities", {}) or {}
            ext = t.get("extended_entities", {}) or {}
            media = ext.get("media", entities.get("media", [])) or []
            media_types = [m.get("type") for m in media if m.get("type")]
            kind = "repost" if is_repost else "reply" if is_reply else "post"
            yield Item(
                kind=kind,
                id=str(t.get("id_str") or t.get("id")),
                created_at=_twitter_time(t.get("created_at")),
                text=t.get("full_text", t.get("text", "")),
                likes=int(t.get("favorite_count", 0) or 0),
                reposts=int(t.get("retweet_count", 0) or 0),
                is_reply=is_reply,
                is_repost=is_repost,
                has_media=bool(media),
                has_link=bool(entities.get("urls")),
                media_types=media_types,
            )

    def likes(self) -> Iterator[Item]:
        for rec in self._read_group("like"):
            like = rec.get("like", rec)
            tid = like.get("tweetId") or like.get("tweet_id")
            if not tid:
                continue
            yield Item(kind="like", id=str(tid),
                       text=like.get("fullText", ""))

    def lists_created(self) -> Iterator[Item]:
        for rec in self._read_group("lists-created"):
            lst = rec.get("userListInfo", rec)
            url = lst.get("listId") or lst.get("url", "")
            lid = str(url).rstrip("/").split("/")[-1]
            if lid:
                yield Item(kind="list", id=lid, text=lst.get("name", ""))

    def dms(self) -> Iterator[Item]:
        for rec in self._read_group("direct-messages"):
            conv = rec.get("dmConversation", {})
            for msg in conv.get("messages", []):
                create = msg.get("messageCreate")
                if not create:
                    continue
                yield Item(
                    kind="dm",
                    id=str(create.get("id")),
                    created_at=parse_time(create.get("createdAt")),
                    text=create.get("text", ""),
                    handle=str(create.get("senderId", "")),
                )

    GROUPS = {
        "posts": "tweets",
        "likes": "likes",
        "lists": "lists_created",
        "dms": "dms",
    }

    def items(self, category: str) -> Iterator[Item]:
        method = self.GROUPS.get(category)
        if method is None:
            raise ValueError(
                f"Archive import doesn't support category {category!r}. "
                f"Supported: {', '.join(self.GROUPS)}")
        return getattr(self, method)()


class ArchiveLog:
    """Appends deleted (or would-be-deleted) items to a JSONL file."""

    def __init__(self, path: str | None):
        self.path = os.path.expanduser(path) if path else None
        self._fh = None
        if self.path:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self._fh = open(self.path, "a", encoding="utf-8")

    def record(self, item: Item, outcome: str) -> None:
        if not self._fh:
            return
        row = item.to_dict()
        row["outcome"] = outcome
        row["logged_at"] = datetime.now(timezone.utc).isoformat()
        self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


def jsonl_to_csv(jsonl_path: str, csv_path: str) -> int:
    """Convert an archive log to CSV. Returns row count."""
    rows = []
    with open(os.path.expanduser(jsonl_path), "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        return 0
    fields = sorted({k for r in rows for k in r})
    with open(os.path.expanduser(csv_path), "w", newline="",
              encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    return len(rows)
