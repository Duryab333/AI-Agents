"""Incident store: a JSON file shared by the background daemon and CLI commands.

Guarded by an fcntl lock so `aiops approve` and the daemon never clobber each other.
Incidents are keyed by namespace/workload/category, which is what lets the daemon
diagnose a problem once instead of on every scan cycle.

Incident lifecycle:
    open           -- detected, RCA done, no automated fix available (manual action)
    pending_fix    -- RCA proposed a fix, awaiting `aiops approve` / `aiops reject`
    fix_applied    -- fix applied; waiting for the anomaly to disappear
    fix_failed     -- apply attempted and kubectl returned an error
    rejected       -- human rejected the proposed fix
    resolved       -- anomaly no longer detected
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ACTIVE_STATUSES = {"open", "pending_fix", "fix_applied", "fix_failed", "rejected"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_suffix(".lock")
        path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def transaction(self):
        """Yield the mutable incidents dict under an exclusive lock; saved on exit."""
        with open(self.lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = self._read()
            yield data["incidents"]
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, default=str))
            os.replace(tmp, self.path)

    def load(self) -> dict:
        with open(self.lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            return self._read()["incidents"]

    def _read(self) -> dict:
        if not self.path.exists():
            return {"incidents": {}}
        try:
            return json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return {"incidents": {}}


def find(incidents: dict, incident_id: str) -> dict | None:
    return next((i for i in incidents.values() if i["id"] == incident_id), None)
