"""Demo mode: a pretend X account so you can try the whole app safely.

Nothing here touches the network. "Deleting" just removes items from this
in-memory account (they come back when you restart the demo).
"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timedelta, timezone

from .actions import Actions
from .models import Item

_POSTS = [
    "Just had the best coffee of my life ☕",
    "Hot take: pineapple absolutely belongs on pizza 🍍🍕",
    "Can't believe it's already {month} again",
    "Working on something new, more soon #buildinpublic",
    "lol",
    "Monday. Again.",
    "Who else is watching the game tonight?",
    "My cat just knocked my phone off the table and looked me dead in the eye",
    "Unpopular opinion: meetings could all be emails",
    "Finally finished that book everyone recommended. It was fine.",
    "New blog post is up! https://example.com/post",
    "Learning to cook. Set off the smoke alarm twice. Progress.",
    "Thread 🧵 on why I switched editors (1/7)",
    "Is it just me or is the internet extra weird today",
    "Happy birthday to my favourite person!! 🎉",
    "If you know, you know",
    "Trying to drink more water. Day 3. Going great.",
    "Concert last night was unreal 🎸",
    "Never tweeting before coffee again",
    "Big announcement coming Friday 👀",
    "honestly crypto is going to the moon this time 🚀 #crypto",
    "Road trip photos 📸",
    "Why does every app need an account now",
    "Rainy day = soup day",
]
_REPLIES = [
    "@sam haha exactly", "@jordan this is so true", "@alex no way!!",
    "@taylor congrats!", "@riley I disagree but respectfully",
    "@casey link?", "@morgan same here", "@jamie 😂😂",
]
_OTHERS = ["@sam", "@jordan", "@alex", "@taylor", "@riley", "@casey",
           "@morgan", "@jamie", "@newsdaily", "@techcrunchy", "@chefmaria",
           "@spacefacts", "@dogsofx", "@bandcamp_fan"]
_MONTHS = ["January", "March", "June", "September", "December"]


class DemoActions(Actions):
    """Same interface as Actions, backed by generated data."""

    def __init__(self, seed: int = 7):
        self.client = None
        self._me = {"id": "1000000001", "username": "you_demo"}
        self._lock = threading.Lock()
        self._data = _generate(random.Random(seed))
        self._pinned = max((p for p in self._data["posts"] if p.kind == "post"),
                           key=lambda p: p.likes).id

    def pinned_tweet_id(self) -> str | None:
        return self._pinned

    def fetch(self, category: str, max_items=None):
        with self._lock:
            items = list(self._data.get(category, []))
        for n, item in enumerate(items):
            if max_items is not None and n >= max_items:
                return
            # pretend to page through X
            if n and n % 100 == 0:
                time.sleep(0.05)
            yield item

    def delete(self, item: Item) -> dict:
        time.sleep(0.03)
        with self._lock:
            for category, items in self._data.items():
                for i, existing in enumerate(items):
                    if existing.id == item.id and existing.kind == item.kind:
                        del items[i]
                        return {"deleted": True}
        return {"_already_gone": True}


def _generate(rng: random.Random) -> dict:
    now = datetime.now(timezone.utc)
    start = datetime(2013, 3, 1, tzinfo=timezone.utc)
    span = (now - start).total_seconds()
    next_id = [1500000000000000000]

    def new_id():
        next_id[0] += rng.randint(1000, 999999)
        return str(next_id[0])

    def when():
        # skew towards recent years like a real account
        frac = rng.random() ** 0.6
        return start + timedelta(seconds=span * frac)

    def engagement():
        r = rng.random()
        if r < 0.7:
            return rng.randint(0, 6), rng.randint(0, 1), rng.randint(0, 2)
        if r < 0.95:
            return rng.randint(5, 80), rng.randint(0, 15), rng.randint(0, 10)
        return rng.randint(200, 4000), rng.randint(30, 900), rng.randint(10, 300)

    posts = []
    for _ in range(420):
        roll = rng.random()
        likes, reposts, replies = engagement()
        if roll < 0.6:
            text = rng.choice(_POSTS).format(month=rng.choice(_MONTHS))
            media = rng.random() < 0.25
            posts.append(Item(
                kind="post", id=new_id(), created_at=when(), text=text,
                likes=likes, reposts=reposts, replies=replies,
                has_media=media, has_link="http" in text,
                media_types=[rng.choice(["photo", "photo", "video",
                                         "animated_gif"])] if media else []))
        elif roll < 0.85:
            posts.append(Item(
                kind="reply", id=new_id(), created_at=when(),
                text=rng.choice(_REPLIES), likes=likes // 4,
                reposts=0, replies=replies // 4, is_reply=True))
        elif roll < 0.95:
            who = rng.choice(_OTHERS)
            posts.append(Item(
                kind="repost", id=new_id(), created_at=when(),
                text=f"RT {who}: {rng.choice(_POSTS).format(month='May')}",
                is_repost=True,
                extra={"referenced_tweets": [
                    {"type": "retweeted", "id": new_id()}]}))
        else:
            posts.append(Item(
                kind="quote", id=new_id(), created_at=when(),
                text=rng.choice(["This 👇", "Couldn't agree more",
                                 "Wild if true", "Saving this"]),
                likes=likes, reposts=reposts, replies=replies,
                is_quote=True))
    posts.sort(key=lambda i: i.created_at, reverse=True)

    def other_post(kind):
        who = rng.choice(_OTHERS)
        likes, reposts, replies = engagement()
        return Item(kind=kind, id=new_id(), created_at=when(),
                    text=f"{who}: {rng.choice(_POSTS).format(month='July')}",
                    likes=likes * 10, reposts=reposts * 5, replies=replies)

    likes = sorted((other_post("like") for _ in range(260)),
                   key=lambda i: i.created_at, reverse=True)
    bookmarks = sorted((other_post("bookmark") for _ in range(35)),
                       key=lambda i: i.created_at, reverse=True)

    def users(kind, n):
        out = []
        for k in range(n):
            handle = rng.choice(_OTHERS) + (str(k) if k >= len(_OTHERS) else "")
            out.append(Item(kind=kind, id=new_id(), handle=handle,
                            text=handle.lstrip("@").replace("_", " ").title()))
        return out

    dms = sorted((Item(kind="dm", id=new_id(), created_at=when(),
                       text=rng.choice(["hey!", "did you see this?",
                                        "running 5 min late", "lol yes",
                                        "send me that link", "thanks!!"]),
                       handle="1000000001")
                  for _ in range(60)), key=lambda i: i.created_at, reverse=True)

    return {
        "posts": posts,
        "likes": likes,
        "bookmarks": bookmarks,
        "following": users("follow", 48),
        "mutes": users("mute", 7),
        "blocks": users("block", 4),
        "lists": [Item(kind="list", id=new_id(), created_at=when(), text=name)
                  for name in ("Tech people", "Recipes", "Friends")],
        "dms": dms,
    }
