"""Per-content-type fetchers and delete operations against the X API v2.

Each content type gets:
  * a fetch_* generator that yields normalized Item objects, and
  * a delete(item) method that performs the right DELETE call.

The endpoints used (all user-context OAuth 2.0):
  post / reply / quote   DELETE /2/tweets/:id
  repost                 DELETE /2/users/:id/retweets/:source_tweet_id
  like                   DELETE /2/users/:id/likes/:tweet_id
  bookmark               DELETE /2/users/:id/bookmarks/:tweet_id
  follow                 DELETE /2/users/:source_id/following/:target_id
  mute                   DELETE /2/users/:source_id/muting/:target_id
  block                  DELETE /2/users/:source_id/blocking/:target_id
  list                   DELETE /2/lists/:id
  dm                     DELETE /2/dm_events/:event_id
"""

from __future__ import annotations

from typing import Iterator

from .client import XClient
from .errors import ApiError
from .models import Item, parse_time

TWEET_FIELDS = (
    "created_at,public_metrics,referenced_tweets,attachments,entities,"
    "in_reply_to_user_id,text"
)
MEDIA_FIELDS = "type"
USER_FIELDS = "username,name"


class Actions:
    """Owns the authenticated user id and all fetch/delete logic."""

    def __init__(self, client: XClient):
        self.client = client
        self._me = None

    # -- identity -------------------------------------------------------

    @property
    def me(self) -> dict:
        if self._me is None:
            self._me = self.client.get("/2/users/me",
                                       {"user.fields": "username"})["data"]
        return self._me

    @property
    def my_id(self) -> str:
        return self.me["id"]

    @property
    def my_handle(self) -> str:
        return self.me.get("username", "")

    def pinned_tweet_id(self) -> str | None:
        data = self.client.get(
            "/2/users/me", {"user.fields": "pinned_tweet_id"})["data"]
        return data.get("pinned_tweet_id")

    # -- fetchers -------------------------------------------------------

    def _tweet_to_item(self, raw: dict) -> Item:
        pm = raw.get("public_metrics", {}) or {}
        refs = raw.get("referenced_tweets", []) or []
        ref_types = {r.get("type") for r in refs}
        is_reply = "replied_to" in ref_types or bool(
            raw.get("in_reply_to_user_id"))
        is_repost = "retweeted" in ref_types
        is_quote = "quoted" in ref_types

        includes = raw.get("_includes", {}) or {}
        media_lookup = {m.get("media_key"): m.get("type")
                        for m in includes.get("media", []) or []}
        attach = raw.get("attachments", {}) or {}
        media_keys = attach.get("media_keys", []) or []
        media_types = [media_lookup.get(k) for k in media_keys
                       if media_lookup.get(k)]
        entities = raw.get("entities", {}) or {}
        has_link = bool(entities.get("urls"))

        kind = "repost" if is_repost else "quote" if is_quote else \
            "reply" if is_reply else "post"
        return Item(
            kind=kind,
            id=str(raw["id"]),
            created_at=parse_time(raw.get("created_at")),
            text=raw.get("text", ""),
            likes=pm.get("like_count", 0),
            reposts=pm.get("retweet_count", 0),
            replies=pm.get("reply_count", 0),
            quotes=pm.get("quote_count", 0),
            is_reply=is_reply,
            is_repost=is_repost,
            is_quote=is_quote,
            has_media=bool(media_keys),
            has_link=has_link,
            media_types=media_types,
            extra={"referenced_tweets": refs},
        )

    def fetch_posts(self, max_items=None) -> Iterator[Item]:
        """All of your own posts/replies/reposts/quotes from the timeline.

        NOTE: X's timeline endpoint only reaches back ~3200 posts. For a full
        history use archive import (see archive.py) to supply older ids.
        """
        params = {
            "max_results": 100,
            "tweet.fields": TWEET_FIELDS,
            "media.fields": MEDIA_FIELDS,
            "expansions": "attachments.media_keys",
        }
        for raw in self.client.paginate(
                f"/2/users/{self.my_id}/tweets", params, max_items=max_items):
            yield self._tweet_to_item(raw)

    def fetch_likes(self, max_items=None) -> Iterator[Item]:
        params = {
            "max_results": 100,
            "tweet.fields": TWEET_FIELDS,
            "media.fields": MEDIA_FIELDS,
            "expansions": "attachments.media_keys",
        }
        for raw in self.client.paginate(
                f"/2/users/{self.my_id}/liked_tweets", params,
                max_items=max_items):
            item = self._tweet_to_item(raw)
            item.kind = "like"
            yield item

    def fetch_bookmarks(self, max_items=None) -> Iterator[Item]:
        params = {
            "max_results": 100,
            "tweet.fields": TWEET_FIELDS,
            "media.fields": MEDIA_FIELDS,
            "expansions": "attachments.media_keys",
        }
        for raw in self.client.paginate(
                f"/2/users/{self.my_id}/bookmarks", params,
                max_items=max_items):
            item = self._tweet_to_item(raw)
            item.kind = "bookmark"
            yield item

    def _user_to_item(self, raw: dict, kind: str) -> Item:
        return Item(
            kind=kind,
            id=str(raw["id"]),
            handle="@" + raw.get("username", "") if raw.get("username") else "",
            text=raw.get("name", ""),
        )

    def fetch_following(self, max_items=None) -> Iterator[Item]:
        params = {"max_results": 1000, "user.fields": USER_FIELDS}
        for raw in self.client.paginate(
                f"/2/users/{self.my_id}/following", params,
                max_items=max_items):
            yield self._user_to_item(raw, "follow")

    def fetch_muting(self, max_items=None) -> Iterator[Item]:
        params = {"max_results": 1000, "user.fields": USER_FIELDS}
        for raw in self.client.paginate(
                f"/2/users/{self.my_id}/muting", params, max_items=max_items):
            yield self._user_to_item(raw, "mute")

    def fetch_blocking(self, max_items=None) -> Iterator[Item]:
        params = {"max_results": 1000, "user.fields": USER_FIELDS}
        for raw in self.client.paginate(
                f"/2/users/{self.my_id}/blocking", params,
                max_items=max_items):
            yield self._user_to_item(raw, "block")

    def fetch_lists(self, max_items=None) -> Iterator[Item]:
        params = {"max_results": 100,
                  "list.fields": "created_at,member_count,description"}
        for raw in self.client.paginate(
                f"/2/users/{self.my_id}/owned_lists", params,
                max_items=max_items):
            yield Item(
                kind="list",
                id=str(raw["id"]),
                text=raw.get("name", ""),
                created_at=parse_time(raw.get("created_at")),
            )

    def fetch_dms(self, max_items=None) -> Iterator[Item]:
        params = {
            "max_results": 100,
            "dm_event.fields": "created_at,text,sender_id,event_type",
        }
        for raw in self.client.paginate(
                "/2/dm_events", params, max_items=max_items):
            # Only MessageCreate events (not participants_join, etc.) and only
            # ones we sent can be deleted.
            if raw.get("event_type") not in (None, "MessageCreate"):
                continue
            sender = str(raw.get("sender_id", ""))
            if sender and sender != self.my_id:
                # Can't delete a message you didn't send.
                continue
            yield Item(
                kind="dm",
                id=str(raw["id"]),
                created_at=parse_time(raw.get("created_at")),
                text=raw.get("text", ""),
                handle=sender,
            )

    FETCHERS = {
        "posts": "fetch_posts",
        "likes": "fetch_likes",
        "bookmarks": "fetch_bookmarks",
        "following": "fetch_following",
        "mutes": "fetch_muting",
        "blocks": "fetch_blocking",
        "lists": "fetch_lists",
        "dms": "fetch_dms",
    }

    def fetch(self, category: str, max_items=None) -> Iterator[Item]:
        name = self.FETCHERS.get(category)
        if not name:
            raise ApiError(f"Unknown category: {category}")
        return getattr(self, name)(max_items=max_items)

    # -- deletion -------------------------------------------------------

    def _repost_source(self, item: Item) -> str | None:
        """The id of the post that `item` reposts.

        Live timeline items carry it in referenced_tweets. Archive items
        don't, so look the repost up once.
        """
        refs = item.extra.get("referenced_tweets")
        if refs is None:
            try:
                data = self.client.get(
                    f"/2/tweets/{item.id}",
                    {"tweet.fields": "referenced_tweets"}).get("data") or {}
            except ApiError:
                return None
            refs = data.get("referenced_tweets") or []
        for ref in refs:
            if ref.get("type") == "retweeted":
                return str(ref.get("id"))
        return None

    def delete(self, item: Item) -> dict:
        uid = self.my_id
        if item.kind in ("post", "reply", "quote"):
            return self.client.delete(f"/2/tweets/{item.id}")
        if item.kind == "repost":
            # Un-reposting takes the ORIGINAL post's id, not the repost's.
            source = self._repost_source(item)
            if source:
                return self.client.delete(f"/2/users/{uid}/retweets/{source}")
            # Couldn't resolve the original (e.g. it was deleted); deleting
            # the repost by its own id is the remaining option.
            return self.client.delete(f"/2/tweets/{item.id}")
        if item.kind == "like":
            return self.client.delete(f"/2/users/{uid}/likes/{item.id}")
        if item.kind == "bookmark":
            return self.client.delete(f"/2/users/{uid}/bookmarks/{item.id}")
        if item.kind == "follow":
            return self.client.delete(f"/2/users/{uid}/following/{item.id}")
        if item.kind == "mute":
            return self.client.delete(f"/2/users/{uid}/muting/{item.id}")
        if item.kind == "block":
            return self.client.delete(f"/2/users/{uid}/blocking/{item.id}")
        if item.kind == "list":
            return self.client.delete(f"/2/lists/{item.id}")
        if item.kind == "dm":
            return self.client.delete(f"/2/dm_events/{item.id}")
        raise ApiError(f"Don't know how to delete a {item.kind}")
