"""
audit.py — lightweight, thread-safe audit trail.

Banks (and GDPR) expect an audit trail of what was processed and when.
This shared helper writes JSON-Lines (append-only) and can export a CSV
report. It intentionally stores NO raw PII — only metadata + the already
redacted output — so the audit log itself is safe to keep.

Used by:
  * the anonymizer (every clipboard redaction is logged)
  * the analyzer (every analysis run is logged)
"""

from __future__ import annotations

import csv
import json
import threading
import time
from pathlib import Path


class AuditLogger:
    """Append-only JSONL audit log with a CSV export helper."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- write
    def log(self, **fields) -> None:
        """Append one event. Timestamps are ISO-8601 UTC."""
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **fields}
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    # ----------------------------------------------------------------- read
    def read(self) -> list[dict]:
        """Read all events back as a list of dicts (oldest first)."""
        if not self.path.exists():
            return []
        with self._lock:
            with open(self.path, encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]

    def count(self) -> int:
        return len(self.read())

    # ---------------------------------------------------------------- export
    def export_csv(self, out_path: str | Path, columns: list[str] | None = None) -> Path:
        """Write a CSV report of all events.

        ``columns`` selects/flattens the fields to include (default: all).
        Nested dicts are JSON-serialised into a single cell so the export is
        always a flat, spreadsheet-friendly table.
        """
        events = self.read()
        if not events:
            raise ValueError("audit log is empty; nothing to export")
        if columns is None:
            columns = sorted({k for e in events for k in e})
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for event in events:
                row = {}
                for col in columns:
                    val = event.get(col, "")
                    if isinstance(val, dict):
                        val = json.dumps(val, ensure_ascii=False, default=str)
                    row[col] = val
                writer.writerow(row)
        return out