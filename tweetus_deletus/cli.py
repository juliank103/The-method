"""Command-line interface for Tweetus Deletus.

    tweetus-deletus                     # open the app in your browser
    tweetus-deletus --demo              # ...with a pretend account
    tweetus-deletus login
    tweetus-deletus whoami
    tweetus-deletus preview  --what posts --keyword foo --before 2020-01-01
    tweetus-deletus review   --what likes
    tweetus-deletus delete   --what posts,likes,bookmarks --older-than 30 --yes
    tweetus-deletus wipe     --what all --yes          # delete EVERYTHING
    tweetus-deletus watch    --what posts --older-than 7 --interval 6h
    tweetus-deletus export-csv --log deleted.jsonl --csv deleted.csv

Run `tweetus-deletus <command> -h` for the full flag list.
"""

from __future__ import annotations

import argparse
import sys

from .actions import Actions
from .archive import ArchiveLog, jsonl_to_csv
from .auth import Authenticator, TokenStore
from .client import XClient
from .config import Config, DEFAULT_CONFIG_PATH
from .errors import TweetusError
from .filters import FilterSet, parse_date
from .runner import Runner

ALL_CATEGORIES = ["posts", "likes", "bookmarks", "following", "mutes",
                  "blocks", "lists", "dms"]
# Categories supported when importing from a data-export archive.
ARCHIVE_CATEGORIES = ["posts", "likes", "lists", "dms"]


# -- context wiring -----------------------------------------------------

def _authenticator(args) -> Authenticator:
    cfg = Config.load(args.config)
    cfg.require_app()
    store = TokenStore(cfg.token_path)
    return Authenticator(cfg.client_id, cfg.client_secret, cfg.redirect_uri,
                         store)


def _runner(args) -> tuple[Runner, Actions, ArchiveLog]:
    cfg = Config.load(args.config)
    cfg.require_app()
    store = TokenStore(cfg.token_path)
    auth = Authenticator(cfg.client_id, cfg.client_secret, cfg.redirect_uri,
                         store)
    client = XClient(auth, dry_run=getattr(args, "dry_run", False),
                     min_interval=cfg.min_interval)
    actions = Actions(client)
    log = ArchiveLog(getattr(args, "log", None))
    return Runner(actions, log=log), actions, log


# -- filter construction ------------------------------------------------

def _categories(args) -> list[str]:
    what = (args.what or "").strip().lower()
    if not what or what == "all":
        cats = list(ALL_CATEGORIES)
    else:
        cats = [c.strip() for c in what.split(",") if c.strip()]
        for c in cats:
            if c not in ALL_CATEGORIES:
                raise TweetusError(
                    f"Unknown category {c!r}. Choose from: "
                    f"{', '.join(ALL_CATEGORIES)} (or 'all').")
    if getattr(args, "archive", None):
        bad = [c for c in cats if c not in ARCHIVE_CATEGORIES]
        if bad:
            raise TweetusError(
                f"Archive import only supports {', '.join(ARCHIVE_CATEGORIES)};"
                f" got {', '.join(bad)}.")
    return cats


def _load_id_file(path: str) -> set[str]:
    out: set[str] = set()
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if line:
                out.add(line)
    return out


def _build_filters(args) -> FilterSet:
    fs = FilterSet()
    fs.keywords = list(args.keyword or [])
    fs.keywords_any = not args.keyword_all
    fs.regex = args.regex
    fs.hashtags = list(args.hashtag or [])
    if args.before:
        fs.before = parse_date(args.before)
    if args.after:
        fs.after = parse_date(args.after)
    fs.older_than_days = args.older_than
    fs.newer_than_days = args.newer_than
    fs.min_likes = args.min_likes
    fs.max_likes = args.max_likes
    fs.min_reposts = args.min_reposts
    fs.max_reposts = args.max_reposts
    fs.min_replies = args.min_replies
    fs.max_replies = args.max_replies
    if args.has_media:
        fs.require_media = True
    if args.no_media:
        fs.require_media = False
    if args.has_link:
        fs.require_link = True
    if args.media_type:
        fs.media_types = list(args.media_type)

    # structural toggles for the posts category
    if args.only_replies:
        fs.include_posts = fs.include_reposts = fs.include_quotes = False
    if args.only_reposts:
        fs.include_posts = fs.include_replies = fs.include_quotes = False
    if args.no_replies:
        fs.include_replies = False
    if args.no_reposts:
        fs.include_reposts = False

    # protection
    fs.protect_pinned = not args.delete_pinned
    if args.keep_id:
        fs.protect_ids.update(args.keep_id)
    if args.keep_ids_file:
        fs.protect_ids.update(_load_id_file(args.keep_ids_file))
    if args.keep_keyword:
        fs.protect_keywords = list(args.keep_keyword)
    if args.keep_handle:
        fs.protect_handles = {h.lstrip("@").lower() for h in args.keep_handle}
    return fs


# -- commands -----------------------------------------------------------

def cmd_login(args) -> int:
    auth = _authenticator(args)
    auth.login(open_browser=not args.no_browser)
    print("Authorized and tokens saved. Try: tweetus-deletus whoami")
    return 0


def cmd_whoami(args) -> int:
    auth = _authenticator(args)
    actions = Actions(XClient(auth))
    print(f"@{actions.my_handle}  (id {actions.my_id})")
    return 0


def _run_categories(args, mode: str) -> int:
    runner, actions, log = _runner(args)
    cats = _categories(args)
    try:
        total_deleted = 0
        for cat in cats:
            filters = _build_filters(args)
            stats = runner.run(
                cat, filters, mode=mode, archive_path=args.archive,
                max_items=args.max, assume_yes=args.yes)
            total_deleted += stats.deleted
        if mode != "preview":
            print(f"\nDone. {total_deleted} item(s) "
                  f"{'would be ' if args.dry_run else ''}deleted.")
        return 0
    finally:
        log.close()


def cmd_preview(args) -> int:
    return _run_categories(args, "preview")


def cmd_review(args) -> int:
    return _run_categories(args, "review")


def cmd_delete(args) -> int:
    if not args.yes and not args.dry_run:
        if not _confirm("This will permanently delete matching items."):
            print("Aborted.")
            return 1
    return _run_categories(args, "delete")


def cmd_wipe(args) -> int:
    # wipe = delete with no match filters (everything), extra confirmation.
    if not args.yes and not args.dry_run:
        who = "your selected categories"
        if not _confirm(
                f"FULL WIPE of {who}. This cannot be undone. Continue?",
                phrase="DELETE"):
            print("Aborted.")
            return 1
    return _run_categories(args, "delete")


def cmd_watch(args) -> int:
    """Disappearing mode: re-run deletion on an interval forever."""
    import time
    interval = _parse_interval(args.interval)
    runner, actions, log = _runner(args)
    cats = _categories(args)
    print(f"Disappearing mode: every {interval}s, deleting matching "
          f"{', '.join(cats)}. Ctrl-C to stop.")
    try:
        while True:
            for cat in cats:
                filters = _build_filters(args)
                runner.run(cat, filters, mode="delete",
                           archive_path=None, max_items=args.max,
                           assume_yes=True)
            print(f"  sleeping {interval}s ...")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    finally:
        log.close()


def cmd_export_csv(args) -> int:
    n = jsonl_to_csv(args.log, args.csv)
    print(f"Wrote {n} row(s) to {args.csv}")
    return 0


def cmd_ui(args) -> int:
    from .webui import serve
    demo = getattr(args, "demo", False) or getattr(args, "demo_ui", False)
    return serve(args.config, demo=demo,
                 port=getattr(args, "port", None),
                 open_browser=not getattr(args, "no_browser", False))


# -- helpers ------------------------------------------------------------

def _confirm(message: str, phrase: str = "yes") -> bool:
    print(message)
    try:
        ans = input(f"Type '{phrase}' to proceed: ").strip()
    except EOFError:
        return False
    return ans == phrase


def _parse_interval(text: str) -> int:
    text = text.strip().lower()
    mult = 1
    if text.endswith("s"):
        text, mult = text[:-1], 1
    elif text.endswith("m"):
        text, mult = text[:-1], 60
    elif text.endswith("h"):
        text, mult = text[:-1], 3600
    elif text.endswith("d"):
        text, mult = text[:-1], 86400
    return max(1, int(float(text) * mult))


# -- argument parser ----------------------------------------------------

def _add_filter_args(p):
    g = p.add_argument_group("selection filters")
    g.add_argument("--what", default="all",
                   help="comma list of categories or 'all': "
                        + ", ".join(ALL_CATEGORIES))
    g.add_argument("--keyword", action="append",
                   help="only items whose text contains this (repeatable)")
    g.add_argument("--keyword-all", action="store_true",
                   help="require ALL --keyword values (default: any)")
    g.add_argument("--regex", help="only items whose text matches this regex")
    g.add_argument("--hashtag", action="append",
                   help="only items containing this hashtag (repeatable)")
    g.add_argument("--before", help="only items created before YYYY-MM-DD")
    g.add_argument("--after", help="only items created after YYYY-MM-DD")
    g.add_argument("--older-than", type=float, metavar="DAYS",
                   help="only items older than N days")
    g.add_argument("--newer-than", type=float, metavar="DAYS",
                   help="only items newer than N days")
    g.add_argument("--min-likes", type=int)
    g.add_argument("--max-likes", type=int)
    g.add_argument("--min-reposts", type=int)
    g.add_argument("--max-reposts", type=int)
    g.add_argument("--min-replies", type=int)
    g.add_argument("--max-replies", type=int)
    g.add_argument("--has-media", action="store_true",
                   help="only items with media")
    g.add_argument("--no-media", action="store_true",
                   help="only items without media")
    g.add_argument("--has-link", action="store_true",
                   help="only items containing a link")
    g.add_argument("--media-type", action="append",
                   help="photo/video/animated_gif (repeatable)")
    g.add_argument("--only-replies", action="store_true")
    g.add_argument("--only-reposts", action="store_true")
    g.add_argument("--no-replies", action="store_true")
    g.add_argument("--no-reposts", action="store_true")

    pr = p.add_argument_group("protection (always wins)")
    pr.add_argument("--delete-pinned", action="store_true",
                    help="allow deleting your pinned post (default: protected)")
    pr.add_argument("--keep-id", action="append",
                    help="never delete this id (repeatable)")
    pr.add_argument("--keep-ids-file",
                    help="file of ids to protect, one per line")
    pr.add_argument("--keep-keyword", action="append",
                    help="protect items containing this text (repeatable)")
    pr.add_argument("--keep-handle", action="append",
                    help="for follows/mutes/blocks/dms: protect this handle")

    src = p.add_argument_group("source / safety")
    src.add_argument("--archive", metavar="PATH",
                     help="import ids from an X data export (.zip or folder) "
                          "instead of the live timeline")
    src.add_argument("--max", type=int, help="stop after N items per category")
    src.add_argument("--log", metavar="FILE",
                     help="append every processed item to this JSONL archive")
    src.add_argument("--dry-run", action="store_true",
                     help="do everything except the actual DELETE calls")
    src.add_argument("--yes", "-y", action="store_true",
                     help="skip the confirmation prompt")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tweetus-deletus",
        description="Mass-delete your own X/Twitter content via the official "
                    "API. Operates only on the account you log in as. "
                    "Run with no command to open the app in your browser.")
    p.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                   help=f"config file (default {DEFAULT_CONFIG_PATH})")
    p.add_argument("--demo", action="store_true",
                   help="open the app with a pretend account (nothing real "
                        "is deleted)")
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("ui", help="open the app in your browser (default)")
    sp.add_argument("--demo", dest="demo_ui", action="store_true",
                    help="use a pretend account; nothing real is deleted")
    sp.add_argument("--port", type=int,
                    help="port to listen on (default: from your callback "
                         "URL, 8721; demo uses 8722)")
    sp.add_argument("--no-browser", action="store_true",
                    help="don't open a browser tab automatically")
    sp.set_defaults(func=cmd_ui)

    sp = sub.add_parser("login", help="authorize once in your browser")
    sp.add_argument("--no-browser", action="store_true")
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("whoami", help="show the authorized account")
    sp.set_defaults(func=cmd_whoami)

    sp = sub.add_parser("preview", help="show what matches; delete nothing")
    _add_filter_args(sp)
    sp.set_defaults(func=cmd_preview)

    sp = sub.add_parser("review", help="confirm each item interactively")
    _add_filter_args(sp)
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser("delete", help="delete everything that matches")
    _add_filter_args(sp)
    sp.set_defaults(func=cmd_delete)

    sp = sub.add_parser("wipe", help="delete ALL of the chosen categories")
    _add_filter_args(sp)
    sp.set_defaults(func=cmd_wipe)

    sp = sub.add_parser("watch",
                        help="disappearing mode: delete on an interval forever")
    _add_filter_args(sp)
    sp.add_argument("--interval", default="6h",
                    help="how often to run (e.g. 30m, 6h, 1d; default 6h)")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("export-csv", help="convert an archive log to CSV")
    sp.add_argument("--log", required=True, help="input JSONL archive")
    sp.add_argument("--csv", required=True, help="output CSV path")
    sp.set_defaults(func=cmd_export_csv)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args.func = cmd_ui  # no command: open the app
    try:
        return args.func(args)
    except TweetusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
