"""Everything Tweetus Deletus remembers between runs, in one folder.

    ~/.config/tweetus_deletus/
        config.toml     your X app's client id (written by the setup screen)
        tokens.json     your X sign-in (refreshed automatically)
        archive.zip     the X data export you uploaded, if any
        archive.json    cached summary of that export
        keep.json       items you chose to always keep
        auto.json       the auto-delete (disappearing mode) rule
        history.jsonl   a copy of everything that was deleted
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

TWEET_KINDS = {"post", "reply", "quote", "repost"}

AUTO_DEFAULTS = {
    "enabled": False,
    "types": ["posts", "replies", "reposts"],
    "older_than_days": 30,
    "interval_hours": 6,
    "keep_pinned": True,
    "keep_keywords": [],
    "max_likes": None,
    "last_run": None,
    "last_result": None,
}


def family(kind: str) -> str:
    """Posts, replies, quotes and reposts share one id space."""
    return "tweet" if kind in TWEET_KINDS else kind


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DataDir:
    def __init__(self, root: str):
        self.root = os.path.expanduser(root)
        self._lock = threading.Lock()

    def _p(self, name: str) -> str:
        return os.path.join(self.root, name)

    @property
    def archive_path(self) -> str:
        return self._p("archive.zip")

    @property
    def history_path(self) -> str:
        return self._p("history.jsonl")

    # -- small JSON files -------------------------------------------------

    def _read_json(self, name: str, default):
        try:
            with open(self._p(name), "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default

    def _write_json(self, name: str, data) -> None:
        os.makedirs(self.root, exist_ok=True)
        tmp = self._p(name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self._p(name))

    # -- archive summary --------------------------------------------------

    def archive_summary(self) -> dict | None:
        if not os.path.exists(self.archive_path):
            return None
        return self._read_json("archive.json", None)

    def save_archive_summary(self, summary: dict) -> None:
        self._write_json("archive.json", summary)

    def remove_archive(self) -> None:
        for name in ("archive.zip", "archive.json"):
            try:
                os.remove(self._p(name))
            except FileNotFoundError:
                pass

    # -- always-keep list -------------------------------------------------

    def keep_list(self) -> list[dict]:
        return self._read_json("keep.json", [])

    def keep_ids(self) -> set[str]:
        return {str(k["id"]) for k in self.keep_list()}

    def add_keep(self, entries: list[dict]) -> int:
        """entries: [{"id", "kind", "text"}]. Returns how many were new."""
        with self._lock:
            current = self.keep_list()
            have = {str(k["id"]) for k in current}
            added = 0
            for e in entries:
                if str(e["id"]) in have:
                    continue
                current.append({"id": str(e["id"]),
                                "kind": e.get("kind", ""),
                                "text": (e.get("text") or "")[:200],
                                "added": _now()})
                have.add(str(e["id"]))
                added += 1
            self._write_json("keep.json", current)
            return added

    def remove_keep(self, ids: list[str]) -> None:
        with self._lock:
            drop = {str(i) for i in ids}
            self._write_json("keep.json", [k for k in self.keep_list()
                                           if str(k["id"]) not in drop])

    # -- auto-delete rule -------------------------------------------------

    def auto_rule(self) -> dict:
        rule = dict(AUTO_DEFAULTS)
        rule.update(self._read_json("auto.json", {}))
        return rule

    def save_auto_rule(self, rule: dict) -> dict:
        with self._lock:
            merged = self.auto_rule()
            merged.update({k: v for k, v in rule.items() if k in AUTO_DEFAULTS})
            self._write_json("auto.json", merged)
            return merged

    # -- history ----------------------------------------------------------

    def _history_rows(self) -> list[dict]:
        rows = []
        try:
            with open(self.history_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue  # a half-written line from a crash
        except FileNotFoundError:
            pass
        return rows

    def history(self, offset: int = 0, limit: int = 50,
                query: str = "") -> tuple[list[dict], int]:
        """Newest first. Returns (page, total matching)."""
        rows = [r for r in self._history_rows()
                if r.get("outcome") in ("deleted", "already_gone")]
        rows.reverse()
        if query:
            q = query.lower()
            rows = [r for r in rows if q in (r.get("text") or "").lower()
                    or q in (r.get("handle") or "").lower()
                    or q in str(r.get("id"))]
        return rows[offset:offset + limit], len(rows)

    def all_history(self) -> list[dict]:
        return [r for r in self._history_rows()
                if r.get("outcome") in ("deleted", "already_gone")]

    def done_keys(self) -> set[tuple[str, str]]:
        """(family, id) of everything already deleted, so re-running an
        archive import skips it instead of paying for the request again."""
        return {(family(r.get("kind", "")), str(r.get("id")))
                for r in self.all_history()}
