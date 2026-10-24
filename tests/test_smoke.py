"""Offline smoke tests — no network. Run: python3 -m pytest tests/ -q
or simply: python3 tests/test_smoke.py
"""

import io
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tweetus_deletus.actions import Actions            # noqa: E402
from tweetus_deletus.archive import ArchiveReader, ArchiveLog, jsonl_to_csv  # noqa: E402
from tweetus_deletus.cli import build_parser, _build_filters, _parse_interval  # noqa: E402
from tweetus_deletus.filters import FilterSet, parse_date   # noqa: E402
from tweetus_deletus.models import Item                 # noqa: E402
from tweetus_deletus.runner import Runner               # noqa: E402

UTC = timezone.utc


def _post(**kw):
    base = dict(kind="post", id="1", created_at=datetime(2019, 1, 1, tzinfo=UTC),
                text="hello world")
    base.update(kw)
    return Item(**base)


def test_keyword_and_date_filters():
    fs = FilterSet(keywords=["crypto"])
    assert fs.should_delete(_post(text="buy crypto now"))
    assert not fs.should_delete(_post(text="nothing here"))

    fs = FilterSet(before=datetime(2020, 1, 1, tzinfo=UTC))
    assert fs.should_delete(_post(created_at=datetime(2019, 6, 1, tzinfo=UTC)))
    assert not fs.should_delete(_post(created_at=datetime(2021, 6, 1, tzinfo=UTC)))


def test_engagement_and_media_filters():
    fs = FilterSet(max_likes=5)
    assert fs.should_delete(_post(likes=2))
    assert not fs.should_delete(_post(likes=99))

    fs = FilterSet(require_media=True)
    assert fs.should_delete(_post(has_media=True, media_types=["video"]))
    assert not fs.should_delete(_post(has_media=False))

    fs = FilterSet(media_types=["video"])
    assert fs.should_delete(_post(has_media=True, media_types=["video"]))
    assert not fs.should_delete(_post(has_media=True, media_types=["photo"]))


def test_older_than():
    old = datetime.now(UTC) - timedelta(days=40)
    recent = datetime.now(UTC) - timedelta(days=2)
    fs = FilterSet(older_than_days=30)
    assert fs.should_delete(_post(created_at=old))
    assert not fs.should_delete(_post(created_at=recent))


def test_protection_wins():
    fs = FilterSet(keywords=["crypto"], protect_ids={"1"})
    assert not fs.should_delete(_post(id="1", text="crypto crypto"))
    fs = FilterSet(keywords=["crypto"], protect_keywords=["keepme"])
    assert not fs.should_delete(_post(text="crypto keepme"))
    fs = FilterSet(protect_pinned=True, pinned_id="42")
    assert not fs.should_delete(_post(id="42", text="anything"))


def test_structural_toggles():
    fs = FilterSet(include_replies=False)
    assert not fs.should_delete(_post(kind="reply", is_reply=True))
    assert fs.should_delete(_post(kind="post"))


def test_archive_parsing_zip_and_dir():
    import zipfile
    tweets_js = ('window.YTD.tweets.part0 = [\n'
                 '  {"tweet": {"id_str": "111", "full_text": "old crypto take",'
                 '   "created_at": "Wed Oct 10 20:19:24 +0000 2018",'
                 '   "favorite_count": "3", "retweet_count": "0",'
                 '   "entities": {"urls": [{"url": "http://x"}]}}},\n'
                 '  {"tweet": {"id_str": "112", "full_text": "RT @a: hi",'
                 '   "created_at": "Wed Oct 10 20:19:24 +0000 2018"}}\n]')
    like_js = ('window.YTD.like.part0 = [\n'
               '  {"like": {"tweetId": "999", "fullText": "liked"}}\n]')

    with tempfile.TemporaryDirectory() as d:
        data = os.path.join(d, "data")
        os.makedirs(data)
        with open(os.path.join(data, "tweets.js"), "w") as fh:
            fh.write(tweets_js)
        with open(os.path.join(data, "like.js"), "w") as fh:
            fh.write(like_js)

        r = ArchiveReader(d)
        posts = list(r.items("posts"))
        assert {p.id for p in posts} == {"111", "112"}
        assert any(p.is_repost for p in posts)        # the RT
        assert any(p.has_link for p in posts)         # 111 has a url
        likes = list(r.items("likes"))
        assert likes[0].id == "999" and likes[0].kind == "like"

        # Now the same as a zip.
        zpath = os.path.join(d, "export.zip")
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("data/tweets.js", tweets_js)
            zf.writestr("data/like.js", like_js)
        rz = ArchiveReader(zpath)
        assert {p.id for p in rz.items("posts")} == {"111", "112"}


# ---- fake client to exercise Actions + Runner without network ----

class FakeClient:
    def __init__(self):
        self.deleted = []
        self.dry_run = False

    def get(self, path, params=None):
        if path == "/2/users/me":
            return {"data": {"id": "me123", "username": "tester",
                             "pinned_tweet_id": None}}
        raise AssertionError("unexpected GET " + path)

    def delete(self, path):
        self.deleted.append(path)
        return {"deleted": True}


def test_runner_delete_from_archive():
    tweets_js = ('window.YTD.tweets.part0 = ['
                 '{"tweet": {"id_str": "111", "full_text": "crypto",'
                 ' "created_at": "Wed Oct 10 20:19:24 +0000 2018"}},'
                 '{"tweet": {"id_str": "222", "full_text": "keep this",'
                 ' "created_at": "Wed Oct 10 20:19:24 +0000 2018"}}]')
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "data"))
        with open(os.path.join(d, "data", "tweets.js"), "w") as fh:
            fh.write(tweets_js)

        client = FakeClient()
        actions = Actions(client)
        out = io.StringIO()
        runner = Runner(actions, out=out)
        fs = FilterSet(keywords=["crypto"])
        stats = runner.run("posts", fs, mode="delete", archive_path=d)

        assert stats.matched == 1
        assert stats.deleted == 1
        assert client.deleted == ["/2/tweets/111"]  # 222 did not match


def test_runner_preview_deletes_nothing():
    client = FakeClient()
    actions = Actions(client)

    def fake_fetch(category, max_items=None):
        yield _post(id="7", text="delete me")

    actions.fetch = fake_fetch
    out = io.StringIO()
    runner = Runner(actions, out=out)
    stats = runner.run("posts", FilterSet(), mode="preview")
    assert stats.matched == 1 and stats.deleted == 0
    assert client.deleted == []
    assert "WOULD DELETE" in out.getvalue()


def test_archive_log_and_csv():
    with tempfile.TemporaryDirectory() as d:
        jl = os.path.join(d, "log.jsonl")
        log = ArchiveLog(jl)
        log.record(_post(id="1"), "deleted")
        log.record(_post(id="2", kind="like"), "deleted")
        log.close()
        csvp = os.path.join(d, "out.csv")
        assert jsonl_to_csv(jl, csvp) == 2
        assert os.path.getsize(csvp) > 0


def test_cli_filter_building_and_interval():
    p = build_parser()
    args = p.parse_args(["delete", "--what", "posts", "--keyword", "a",
                         "--keyword", "b", "--before", "2020-01-01",
                         "--min-likes", "0", "--max-likes", "10",
                         "--keep-id", "555", "--older-than", "30"])
    fs = _build_filters(args)
    assert fs.keywords == ["a", "b"]
    assert fs.before == datetime(2020, 1, 1, tzinfo=UTC)
    assert fs.max_likes == 10 and fs.older_than_days == 30
    assert "555" in fs.protect_ids

    assert _parse_interval("30m") == 1800
    assert _parse_interval("6h") == 21600
    assert _parse_interval("1d") == 86400
    assert parse_date("2021-05-06") == datetime(2021, 5, 6, tzinfo=UTC)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok", fn.__name__)
    print(f"\n{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
