"""Persistent item blacklist for the bazaar flipper.

Stored as a JSON array of product IDs in a file (default: ``blacklist.json`` in
the project root, next to ``app.py``). The list is small enough to keep fully in
memory; every mutation rewrites the file so blacklisted items survive restarts.

Product IDs are normalised to upper case to match the Hypixel bazaar IDs
(e.g. ``ENCHANTED_LAPIS_LAZULI``).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "blacklist.json"


class Blacklist:
    """A small, file-backed set of blacklisted product IDs."""

    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._ids: set[str] = self._load()

    def _load(self) -> set[str]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return set()
        if isinstance(raw, list):
            return {str(x).strip().upper() for x in raw if str(x).strip()}
        return set()

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(sorted(self._ids), indent=2) + "\n", encoding="utf-8"
        )

    @staticmethod
    def _norm(product_id: str) -> str:
        return product_id.strip().upper()

    def all(self) -> list[str]:
        """Return the blacklisted IDs, sorted, for display."""
        return sorted(self._ids)

    def as_set(self) -> set[str]:
        """Return a copy of the blacklist as a set, for fast filtering."""
        return set(self._ids)

    def __contains__(self, product_id: str) -> bool:
        return self._norm(product_id) in self._ids

    def add(self, product_id: str) -> bool:
        """Add an item. Returns True if it was newly added."""
        pid = self._norm(product_id)
        if not pid:
            return False
        with self._lock:
            if pid in self._ids:
                return False
            self._ids.add(pid)
            self._save()
            return True

    def remove(self, product_id: str) -> bool:
        """Remove an item. Returns True if it was present and removed."""
        pid = self._norm(product_id)
        with self._lock:
            if pid not in self._ids:
                return False
            self._ids.discard(pid)
            self._save()
            return True
