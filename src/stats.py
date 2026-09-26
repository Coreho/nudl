"""How much nudl has done — counted on this machine, and nowhere else.

The tray tooltip and About show it: "412 trackers removed from 230 links". A tool that
works by making things quietly disappear is otherwise very easy to forget is doing
anything at all.

This is not telemetry. It is two numbers in `%AppData%\\nudl\\stats.json`, which nudl
never sends anywhere, because nudl never sends anything anywhere. An undo takes its
clean back off the count: the trackers are on the link again.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass

from . import config

logger = logging.getLogger(__name__)


@dataclass
class Stats:
    trackers: int = 0
    links: int = 0
    #: When counting started, as YYYY-MM-DD.
    since: str | None = None

    def describe(self) -> str:
        if not self.links:
            return "nothing removed yet"
        trackers = f"{self.trackers} tracker{'s' if self.trackers != 1 else ''}"
        links = f"{self.links} link{'s' if self.links != 1 else ''}"
        return f"{trackers} removed from {links}"


class Counter:
    """The running count. Thread-safe: cleans come from the pump, undos from the UI too."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stats = _load()

    def add(self, trackers: int, links: int) -> Stats:
        with self._lock:
            self.stats.trackers += trackers
            self.stats.links += links
            self.stats.since = self.stats.since or time.strftime("%Y-%m-%d")
            _save(self.stats)
            return Stats(**asdict(self.stats))

    def take_back(self, trackers: int, links: int) -> Stats:
        with self._lock:
            self.stats.trackers = max(0, self.stats.trackers - trackers)
            self.stats.links = max(0, self.stats.links - links)
            _save(self.stats)
            return Stats(**asdict(self.stats))


def _load() -> Stats:
    """Whatever is on disk, or zero. A mangled file costs the count, never a crash."""
    try:
        with open(config.stats_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        trackers, links, since = data.get("trackers"), data.get("links"), data.get("since")
        if isinstance(trackers, int) and isinstance(links, int) and trackers >= 0 <= links:
            return Stats(trackers, links, since if isinstance(since, str) else None)
    except (OSError, ValueError, AttributeError):
        pass
    return Stats()


def _save(stats: Stats) -> None:
    path = config.stats_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(asdict(stats)), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logger.warning("could not save %s", path, exc_info=True)
