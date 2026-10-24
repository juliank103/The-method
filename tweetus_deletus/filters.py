"""Filtering and protection rules.

A FilterSet decides, for a given Item, whether it should be deleted. It
combines positive match rules (keyword, date range, engagement, media,
structural type) with protection rules (keep pinned, keep specific ids, keep
anything matching a protect keyword). Protection always wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import Item


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class FilterSet:
    # -- positive match rules (all that are set must pass) --------------
    keywords: list[str] = field(default_factory=list)   # case-insensitive substr
    keywords_any: bool = True          # True: match ANY keyword; False: ALL
    regex: str | None = None
    hashtags: list[str] = field(default_factory=list)   # without '#'
    before: datetime | None = None     # only items created strictly before
    after: datetime | None = None      # only items created strictly after
    older_than_days: float | None = None   # only items older than N days
    newer_than_days: float | None = None   # only items newer than N days
    min_likes: int | None = None
    max_likes: int | None = None
    min_reposts: int | None = None
    max_reposts: int | None = None
    min_replies: int | None = None
    max_replies: int | None = None
    require_media: bool | None = None  # True: only w/ media, False: only w/o
    require_link: bool | None = None
    media_types: list[str] | None = None   # e.g. ["video"], ["photo"]
    # structural inclusion for the "posts" category
    include_posts: bool = True
    include_replies: bool = True
    include_reposts: bool = True
    include_quotes: bool = True

    # -- protection rules (any match protects the item) -----------------
    protect_ids: set[str] = field(default_factory=set)
    protect_pinned: bool = True
    pinned_id: str | None = None
    protect_keywords: list[str] = field(default_factory=list)
    protect_handles: set[str] = field(default_factory=set)   # lowercased, no @

    # -- evaluation -----------------------------------------------------

    def protected(self, item: Item) -> bool:
        if item.id in self.protect_ids:
            return True
        if self.protect_pinned and self.pinned_id and item.id == self.pinned_id:
            return True
        if self.protect_handles and item.handle:
            if item.handle.lstrip("@").lower() in self.protect_handles:
                return True
        if self.protect_keywords:
            low = item.text.lower()
            if any(k.lower() in low for k in self.protect_keywords):
                return True
        return False

    def _structural_ok(self, item: Item) -> bool:
        if item.kind == "repost":
            return self.include_reposts
        if item.kind == "quote":
            return self.include_quotes
        if item.kind == "reply":
            return self.include_replies
        if item.kind == "post":
            return self.include_posts
        return True  # non-tweet kinds aren't gated by these

    def _matches(self, item: Item) -> bool:
        if not self._structural_ok(item):
            return False

        if self.keywords:
            low = item.text.lower()
            hits = [k.lower() in low for k in self.keywords]
            if self.keywords_any and not any(hits):
                return False
            if not self.keywords_any and not all(hits):
                return False

        if self.regex and not re.search(self.regex, item.text, re.IGNORECASE):
            return False

        if self.hashtags:
            low = item.text.lower()
            tags = {h.lstrip("#").lower() for h in self.hashtags}
            if not any(("#" + t) in low for t in tags):
                return False

        created = _aware(item.created_at)
        if self.before and (created is None or created >= _aware(self.before)):
            return False
        if self.after and (created is None or created <= _aware(self.after)):
            return False

        age = item.age_days
        if self.older_than_days is not None:
            if age is None or age < self.older_than_days:
                return False
        if self.newer_than_days is not None:
            if age is None or age > self.newer_than_days:
                return False

        if not self._range_ok(item.likes, self.min_likes, self.max_likes):
            return False
        if not self._range_ok(item.reposts, self.min_reposts, self.max_reposts):
            return False
        if not self._range_ok(item.replies, self.min_replies, self.max_replies):
            return False

        if self.require_media is not None and item.has_media != self.require_media:
            return False
        if self.require_link is not None and item.has_link != self.require_link:
            return False
        if self.media_types:
            want = {m.lower() for m in self.media_types}
            if not (want & {m.lower() for m in item.media_types}):
                return False

        return True

    @staticmethod
    def _range_ok(value, lo, hi) -> bool:
        if lo is not None and value < lo:
            return False
        if hi is not None and value > hi:
            return False
        return True

    def should_delete(self, item: Item) -> bool:
        """The single decision function used by the runner."""
        if self.protected(item):
            return False
        return self._matches(item)


def parse_date(text: str) -> datetime:
    """Accept YYYY-MM-DD or full ISO-8601; return a UTC-aware datetime."""
    text = text.strip()
    try:
        if len(text) == 10:
            dt = datetime.strptime(text, "%Y-%m-%d")
        else:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Bad date {text!r}; use YYYY-MM-DD") from exc
    return _aware(dt)
