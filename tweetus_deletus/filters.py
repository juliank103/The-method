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


# Kinds that carry a date / engagement counts / media at all. A filter on an
# attribute a kind never has (e.g. "older than 30 days" on a follow) doesn't
# apply to it. An item of a kind that SHOULD have the attribute but doesn't
# (e.g. an archived like, which has no date) fails the filter: when in doubt,
# don't delete.
DATED_KINDS = {"post", "reply", "quote", "repost", "like", "bookmark", "dm",
               "list"}
TWEETLIKE_KINDS = {"post", "reply", "quote", "repost", "like", "bookmark"}


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def contains(text: str, phrase: str) -> bool:
    """Case-insensitive whole-word match: "cat" matches "my cat!" but not
    "concatenate". Phrases ("good morning"), #hashtags and @mentions work."""
    phrase = phrase.strip()
    if not phrase:
        return False
    pattern = re.escape(phrase)
    if phrase[0].isalnum() or phrase[0] == "_":
        pattern = r"(?<!\w)" + pattern
    if phrase[-1].isalnum() or phrase[-1] == "_":
        pattern = pattern + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


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
            if any(contains(item.text, k) for k in self.protect_keywords):
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
            hits = [contains(item.text, k) for k in self.keywords]
            if self.keywords_any and not any(hits):
                return False
            if not self.keywords_any and not all(hits):
                return False

        if self.regex and not re.search(self.regex, item.text, re.IGNORECASE):
            return False

        if self.hashtags:
            tags = {h.lstrip("#") for h in self.hashtags}
            if not any(contains(item.text, "#" + t) for t in tags):
                return False

        if item.kind in DATED_KINDS and not self._dates_ok(item):
            return False

        if item.kind in TWEETLIKE_KINDS:
            if not self._range_ok(item.likes, self.min_likes, self.max_likes):
                return False
            if not self._range_ok(item.reposts, self.min_reposts,
                                  self.max_reposts):
                return False
            if not self._range_ok(item.replies, self.min_replies,
                                  self.max_replies):
                return False
            if self.require_media is not None \
                    and item.has_media != self.require_media:
                return False
            if self.require_link is not None \
                    and item.has_link != self.require_link:
                return False
            if self.media_types:
                want = {m.lower() for m in self.media_types}
                if not (want & {m.lower() for m in item.media_types}):
                    return False

        return True

    @property
    def has_date_rule(self) -> bool:
        return any(v is not None for v in (self.before, self.after,
                                           self.older_than_days,
                                           self.newer_than_days))

    def _dates_ok(self, item: Item) -> bool:
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
        return True

    @staticmethod
    def _range_ok(value, lo, hi) -> bool:
        if lo is not None and value < lo:
            return False
        if hi is not None and value > hi:
            return False
        return True

    def matches(self, item: Item) -> bool:
        """True if the selection rules match, ignoring protection."""
        return self._matches(item)

    def should_delete(self, item: Item) -> bool:
        """The single decision function used by the runner."""
        if self.protected(item):
            return False
        return self._matches(item)


_ID_IN_URL = re.compile(r"/(?:status|statuses|lists|user)/(\d+)")


def extract_id(text: str) -> str | None:
    """Accept a bare id or a pasted x.com / twitter.com link; return the id."""
    text = text.strip()
    if text.isdigit():
        return text
    m = _ID_IN_URL.search(text)
    return m.group(1) if m else None


def _num(d: dict, key: str, cast=int):
    value = d.get(key)
    if value in (None, ""):
        return None
    try:
        value = cast(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key.replace('_', ' ')} must be a number") from None
    if value < 0:
        raise ValueError(f"{key.replace('_', ' ')} can't be negative")
    return value


def _words(d: dict, key: str) -> list[str]:
    value = d.get(key) or []
    if isinstance(value, str):
        value = value.split(",")
    return [w.strip() for w in value if str(w).strip()]


def filters_from_dict(d: dict) -> FilterSet:
    """Build a FilterSet from the web UI's JSON. Raises ValueError with a
    human-readable message on bad input."""
    fs = FilterSet()
    fs.keywords = _words(d, "keywords")
    fs.keywords_any = not d.get("keywords_all")
    regex = (d.get("regex") or "").strip()
    if regex:
        try:
            re.compile(regex)
        except re.error as exc:
            raise ValueError(f"That pattern isn't valid: {exc}") from None
        fs.regex = regex
    fs.hashtags = _words(d, "hashtags")
    if d.get("before"):
        fs.before = parse_date(d["before"])
    if d.get("after"):
        fs.after = parse_date(d["after"])
    fs.older_than_days = _num(d, "older_than_days", float)
    fs.newer_than_days = _num(d, "newer_than_days", float)
    for key in ("min_likes", "max_likes", "min_reposts", "max_reposts",
                "min_replies", "max_replies"):
        setattr(fs, key, _num(d, key))

    media = d.get("media", "any")
    if media == "with":
        fs.require_media = True
    elif media == "without":
        fs.require_media = False
    if d.get("media_types"):
        fs.media_types = list(d["media_types"])
    if d.get("links_only"):
        fs.require_link = True

    fs.protect_pinned = d.get("keep_pinned", True)
    fs.protect_keywords = _words(d, "keep_keywords")
    for raw in d.get("keep_ids") or []:
        found = extract_id(str(raw))
        if found:
            fs.protect_ids.add(found)
    fs.protect_handles = {h.lstrip("@").lower()
                          for h in _words(d, "keep_handles")}
    return fs


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
