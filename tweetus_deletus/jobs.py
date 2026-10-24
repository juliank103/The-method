"""Background jobs for the web UI: scan (find matches) and delete.

Only one job runs at a time. Each job keeps progress counters, a short log,
and a cancel flag the UI can set. The X client is given `job.sleep` as its
sleeper, so even a long rate-limit wait stops promptly when you press Stop.
"""

from __future__ import annotations

import secrets
import threading
import time
import traceback
from collections import deque

from .archive import ArchiveLog, ArchiveReader
from .errors import ApiError, AuthError, TweetusError
from .filters import DATED_KINDS, FilterSet
from .models import Item
from .store import TWEET_KINDS, family

# What the UI calls things -> which fetch category holds them.
TYPE_CATEGORY = {
    "posts": "posts", "replies": "posts", "reposts": "posts",
    "likes": "likes", "bookmarks": "bookmarks", "dms": "dms",
    "following": "following", "mutes": "mutes", "blocks": "blocks",
    "lists": "lists",
}
CATEGORY_LABEL = {
    "posts": "posts", "likes": "likes", "bookmarks": "bookmarks",
    "dms": "messages", "following": "follows", "mutes": "mutes",
    "blocks": "blocks", "lists": "lists",
}
KIND_LABEL = {
    "post": "post", "reply": "reply", "quote": "quote post",
    "repost": "repost", "like": "like", "bookmark": "bookmark",
    "dm": "message", "follow": "follow", "mute": "mute", "block": "block",
    "list": "list",
}


class Cancelled(Exception):
    """Raised inside a job when the user presses Stop."""


def categories_for(types: list[str]) -> list[str]:
    out = []
    for t in types:
        cat = TYPE_CATEGORY.get(t)
        if cat is None:
            raise TweetusError(f"Unknown type: {t}")
        if cat not in out:
            out.append(cat)
    return out


def item_url(item: Item) -> str | None:
    if item.kind in TWEET_KINDS or item.kind in ("like", "bookmark"):
        return f"https://x.com/i/web/status/{item.id}"
    if item.kind in ("follow", "mute", "block"):
        return f"https://x.com/i/user/{item.id}"
    if item.kind == "list":
        return f"https://x.com/i/lists/{item.id}"
    return None


def item_json(item: Item) -> dict:
    text = " ".join((item.text or "").split())
    return {
        "id": item.id,
        "kind": item.kind,
        "date": item.created_at.isoformat() if item.created_at else None,
        "text": text[:500],
        "likes": item.likes,
        "reposts": item.reposts,
        "replies": item.replies,
        "media": item.media_types or (["media"] if item.has_media else []),
        "link": item.has_link,
        "handle": item.handle,
        "url": item_url(item),
    }


class Job:
    def __init__(self, kind: str, title: str):
        self.id = secrets.token_hex(6)
        self.kind = kind            # scan | delete | auto
        self.title = title
        self.phase = "scanning" if kind in ("scan", "auto") else "deleting"
        self.state = "running"      # running | done | error | cancelled
        self.error: str | None = None
        self.done = 0
        self.total: int | None = None
        self.note = ""
        self.log: deque = deque(maxlen=200)
        self.items: list[Item] = []
        self.stats: dict = {}
        self.started = time.time()
        self.finished: float | None = None
        self._cancel = threading.Event()

    # used by the work functions -----------------------------------------

    def say(self, msg: str) -> None:
        self.note = msg
        self.log.append({"t": time.time(), "msg": msg})

    def sleep(self, seconds: float) -> None:
        if self._cancel.wait(max(0.0, seconds)):
            raise Cancelled()

    def check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def cancel(self) -> None:
        self._cancel.set()

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "title": self.title,
            "phase": self.phase, "state": self.state, "error": self.error,
            "done": self.done, "total": self.total, "note": self.note,
            "log": list(self.log)[-40:], "stats": self.stats,
            "count": len(self.items), "started": self.started,
            "finished": self.finished,
        }


class JobManager:
    def __init__(self):
        self.current: Job | None = None
        self.last_scan: Job | None = None
        self._lock = threading.Lock()

    def busy(self) -> bool:
        return bool(self.current and self.current.state == "running")

    def start(self, kind: str, title: str, fn, total: int | None = None) -> Job:
        with self._lock:
            if self.busy():
                raise TweetusError(
                    "Something is already running. Wait for it to finish "
                    "or press Stop.")
            job = Job(kind, title)
            job.total = total
            self.current = job
            if kind == "scan":
                self.last_scan = job
        threading.Thread(target=self._run, args=(job, fn), daemon=True).start()
        return job

    @staticmethod
    def _run(job: Job, fn) -> None:
        try:
            fn(job)
            job.state = "done"
        except Cancelled:
            job.state = "cancelled"
            job.say("Stopped.")
        except TweetusError as exc:
            job.state = "error"
            job.error = str(exc)
        except Exception as exc:  # keep the server alive, show the user why
            job.state = "error"
            job.error = f"Unexpected error: {exc}"
            job.log.append({"t": time.time(), "msg": traceback.format_exc()})
        finally:
            job.finished = time.time()


# -- the work ------------------------------------------------------------

def run_scan(job: Job, actions, types: list[str], filters: FilterSet, *,
             archive_path: str | None = None, keep_ids=(), done_keys=(),
             check_pinned: bool = True) -> None:
    cats = categories_for(types)
    if not cats:
        raise TweetusError("Pick at least one thing to delete.")
    filters.include_posts = filters.include_quotes = "posts" in types
    filters.include_replies = "replies" in types
    filters.include_reposts = "reposts" in types
    filters.protect_ids |= set(keep_ids)
    done_keys = set(done_keys)
    reader = ArchiveReader(archive_path) if archive_path else None

    if "posts" in cats and filters.protect_pinned and check_pinned:
        job.say("Checking which post is pinned…")
        try:
            filters.pinned_id = actions.pinned_tweet_id()
        except (ApiError, AuthError) as exc:
            job.say(f"Couldn't check your pinned post ({_short(exc)}). "
                    "Uncheck it in the results if it shows up.")

    stats = {"scanned": 0, "matched": 0, "protected": 0, "already": 0,
             "by_kind": {}, "sources": {}}
    job.stats = stats
    for cat in cats:
        from_archive = reader is not None and cat in ArchiveReader.GROUPS
        stats["sources"][cat] = "archive" if from_archive else "live"
        job.say(f"Reading your {CATEGORY_LABEL[cat]} from "
                f"{'your archive' if from_archive else 'X'}…")
        try:
            source = reader.items(cat) if from_archive else actions.fetch(cat)
            for item in source:
                job.check()
                stats["scanned"] += 1
                job.done = stats["scanned"]
                if from_archive and (family(item.kind), item.id) in done_keys:
                    stats["already"] += 1
                    continue
                if not filters.matches(item):
                    if item.created_at is None and filters.has_date_rule \
                            and item.kind in DATED_KINDS:
                        stats["undated"] = stats.get("undated", 0) + 1
                    continue
                if filters.protected(item):
                    stats["protected"] += 1
                    continue
                job.items.append(item)
                stats["matched"] += 1
                stats["by_kind"][item.kind] = stats["by_kind"].get(item.kind, 0) + 1
        except AuthError as exc:
            raise TweetusError(f"{exc} (Settings → Connect).") from exc
        except ApiError as exc:
            raise TweetusError(
                f"X wouldn't list your {CATEGORY_LABEL[cat]}: {_short(exc)}"
                f"{_hint(exc)}") from exc

    # Newest first; undated things (follows, mutes, blocks) go last.
    job.items.sort(key=lambda i: i.created_at.timestamp() if i.created_at
                   else float("-inf"), reverse=True)
    job.say(f"Found {stats['matched']:,} item(s) to delete.")


def run_delete(job: Job, actions, items: list[Item], history: ArchiveLog) -> None:
    job.phase = "deleting"
    job.total = len(items)
    job.done = 0
    stats = {"deleted": 0, "already_gone": 0, "errors": 0, "by_kind": {}}
    job.stats = stats
    streak = 0
    for item in items:
        job.check()
        when = item.created_at.date().isoformat() if item.created_at else ""
        job.note = f"Deleting {KIND_LABEL.get(item.kind, item.kind)} {when}".strip()
        try:
            result = actions.delete(item)
        except AuthError as exc:
            raise TweetusError(f"{exc} (Settings → Connect).") from exc
        except ApiError as exc:
            stats["errors"] += 1
            streak += 1
            job.done += 1
            history.record(item, f"error: {_short(exc)}")
            job.say(f"Couldn't delete {KIND_LABEL.get(item.kind, item.kind)} "
                    f"{item.id}: {_short(exc)}")
            if streak >= 5:
                raise TweetusError(
                    f"Stopped after 5 failures in a row. X said: "
                    f"{_short(exc)}{_hint(exc)}") from exc
            continue
        streak = 0
        outcome = "already_gone" if result.get("_already_gone") else "deleted"
        history.record(item, outcome)
        stats[outcome] += 1
        if outcome == "deleted":
            stats["by_kind"][item.kind] = stats["by_kind"].get(item.kind, 0) + 1
        job.done += 1

    parts = [f"{stats['deleted']:,} deleted"]
    if stats["already_gone"]:
        parts.append(f"{stats['already_gone']:,} were already gone")
    if stats["errors"]:
        parts.append(f"{stats['errors']:,} failed")
    job.say("Done: " + ", ".join(parts) + ".")


def _short(exc: Exception) -> str:
    payload = getattr(exc, "payload", None) or {}
    if isinstance(payload, dict):
        for key in ("detail", "title", "error_description", "error"):
            if isinstance(payload.get(key), str):
                return payload[key]
        errs = payload.get("errors")
        if isinstance(errs, list) and errs and isinstance(errs[0], dict):
            msg = errs[0].get("message") or errs[0].get("detail")
            if msg:
                return str(msg)
    text = str(exc)
    return text if len(text) <= 200 else text[:200] + "…"


def _hint(exc: Exception) -> str:
    status = getattr(exc, "status", None)
    if status == 401:
        return " — reconnect your X account in Settings."
    if status == 402:
        return " — your X developer account may be out of credits."
    if status == 403:
        return (" — your X app may be missing permission (it needs “Read and"
                " write and Direct message”) or your API plan doesn't include"
                " this. Reconnect after changing app permissions.")
    return ""
