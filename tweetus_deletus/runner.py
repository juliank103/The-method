"""Orchestration: fetch -> filter -> preview/review -> archive -> delete.

Modes
-----
preview : show exactly what matches; delete nothing (dry run).
review  : step through each matched item and confirm y/n/all/quit.
delete  : delete everything that matches (optionally with a confirm prompt).

Disappearing mode is just `delete` run on a loop with an age filter; see
cli.py `cmd_watch`.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Iterable

from .actions import Actions
from .archive import ArchiveLog, ArchiveReader
from .errors import ApiError
from .filters import FilterSet
from .models import Item


@dataclass
class RunStats:
    matched: int = 0
    deleted: int = 0
    protected: int = 0
    skipped: int = 0
    already_gone: int = 0
    errors: int = 0

    def line(self) -> str:
        return (f"matched={self.matched} deleted={self.deleted} "
                f"protected={self.protected} skipped={self.skipped} "
                f"already_gone={self.already_gone} errors={self.errors}")


class Runner:
    def __init__(self, actions: Actions, log: ArchiveLog | None = None,
                 out=sys.stdout):
        self.actions = actions
        self.log = log
        self.out = out

    def _p(self, msg=""):
        print(msg, file=self.out)

    def _source(self, category: str, filters: FilterSet,
                archive_path: str | None, max_items: int | None) -> Iterable[Item]:
        if archive_path:
            reader = ArchiveReader(archive_path)
            src = reader.items(category)
            if max_items is not None:
                src = _limit(src, max_items)
            return src
        return self.actions.fetch(category, max_items=max_items)

    def run(self, category: str, filters: FilterSet, *, mode: str = "preview",
            archive_path: str | None = None, max_items: int | None = None,
            assume_yes: bool = False) -> RunStats:
        stats = RunStats()

        # Resolve pinned tweet once, so protect_pinned works on live data.
        if filters.protect_pinned and filters.pinned_id is None \
                and category == "posts" and not archive_path:
            try:
                filters.pinned_id = self.actions.pinned_tweet_id()
            except ApiError:
                pass

        self._p(f"== {category} :: mode={mode} "
                f"{'(ARCHIVE IMPORT) ' if archive_path else ''}==")

        for item in self._source(category, filters, archive_path, max_items):
            if filters.protected(item):
                stats.protected += 1
                continue
            if not filters.should_delete(item):
                continue

            stats.matched += 1

            if mode == "preview":
                self._p("  WOULD DELETE  " + item.summary())
                if self.log:
                    self.log.record(item, "preview")
                continue

            if mode == "review":
                decision = self._ask(item, assume_yes)
                if decision == "quit":
                    self._p("  (stopping on user request)")
                    break
                if decision == "no":
                    stats.skipped += 1
                    continue
                if decision == "all":
                    assume_yes = True

            # mode == delete, or review decided yes/all
            self._delete_one(item, stats)

        self._p("-- " + stats.line())
        return stats

    def _delete_one(self, item: Item, stats: RunStats) -> None:
        try:
            result = self.actions.delete(item)
        except ApiError as exc:
            stats.errors += 1
            self._p(f"  ERROR deleting {item.kind} {item.id}: {exc}")
            if self.log:
                self.log.record(item, f"error:{exc}")
            return

        if result.get("_dry_run"):
            stats.deleted += 1  # counted for reporting; nothing really happened
            self._p("  (dry-run) " + item.summary())
            if self.log:
                self.log.record(item, "dry_run")
            return
        if result.get("_already_gone"):
            stats.already_gone += 1
            if self.log:
                self.log.record(item, "already_gone")
            return

        stats.deleted += 1
        self._p("  deleted  " + item.summary())
        if self.log:
            self.log.record(item, "deleted")

    def _ask(self, item: Item, assume_yes: bool) -> str:
        if assume_yes:
            return "yes"
        self._p("  " + item.summary())
        try:
            ans = input("    delete? [y]es/[n]o/[a]ll/[q]uit: ").strip().lower()
        except EOFError:
            return "quit"
        if ans in ("a", "all"):
            return "all"
        if ans in ("q", "quit"):
            return "quit"
        if ans in ("y", "yes"):
            return "yes"
        return "no"


def _limit(it, n):
    for i, x in enumerate(it):
        if i >= n:
            return
        yield x
