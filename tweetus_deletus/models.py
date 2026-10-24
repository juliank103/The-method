"""Normalized record for anything Tweetus Deletus can delete.

Every content type (post, reply, repost, like, bookmark, DM, list, follow,
mute, block) is flattened into an `Item` so filtering, previewing, archiving
and deletion can treat them uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class Item:
    kind: str                 # post, reply, repost, quote, like, bookmark,
                              # dm, list, follow, mute, block
    id: str                   # the id used to delete it (tweet id / list id /
                              # target user id / dm event id)
    created_at: datetime | None = None
    text: str = ""
    # engagement counts (posts only; 0 when unknown)
    likes: int = 0
    reposts: int = 0
    replies: int = 0
    quotes: int = 0
    # structural flags
    is_reply: bool = False
    is_repost: bool = False
    is_quote: bool = False
    has_media: bool = False
    has_link: bool = False
    media_types: list[str] = field(default_factory=list)
    # for follows/mutes/blocks and DMs
    handle: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def age_days(self) -> float | None:
        if self.created_at is None:
            return None
        now = datetime.now(timezone.utc)
        return (now - self.created_at).total_seconds() / 86400.0

    def summary(self, width: int = 80) -> str:
        when = (self.created_at.date().isoformat()
                if self.created_at else "????-??-??")
        label = self.handle or self.text or f"({self.kind} {self.id})"
        label = " ".join(label.split())  # collapse whitespace/newlines
        if len(label) > width:
            label = label[: width - 1] + "…"
        meta = ""
        if self.kind in ("post", "reply", "quote", "repost"):
            meta = f"  ♥{self.likes} ↻{self.reposts} 💬{self.replies}"
        return f"[{self.kind:8}] {when} {self.id:>20}  {label}{meta}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_at"] = (self.created_at.isoformat()
                           if self.created_at else None)
        d.pop("extra", None)  # keep the archive tidy; _includes is bulky
        return d


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # X returns ISO-8601 with Z.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
