"""One detection -> RCA -> SOP cycle. Used by both `aiops scan` and the background daemon."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from aiops import detector, rca as rca_mod, sop
from aiops.config import Settings
from aiops.models import Anomaly
from aiops.state import ACTIVE_STATUSES, StateStore, now_iso

log = logging.getLogger("aiops.engine")

# An incident must be absent for this many consecutive scans before it's marked resolved,
# so a pod briefly flipping between states doesn't close + reopen (and re-diagnose) it.
RESOLVE_AFTER_MISSED_SCANS = 2


@dataclass
class CycleReport:
    detected: int = 0
    new: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    ongoing: int = 0


def _new_incident(anomaly: Anomaly, rca, existing: dict | None) -> dict:
    ts = now_iso()
    history = list(existing.get("history", [])) if existing else []
    history.append({"time": ts, "event": ("Recurred" if existing else "Detected")
                    + f": {anomaly.category} ({anomaly.signals})"[:300]})
    status = "open" if rca.proposed_fix.tool_name == "no_action" else "pending_fix"
    history.append({"time": ts, "event": f"RCA ({rca.source}, {rca.confidence}): "
                    f"proposed `{rca.proposed_fix.tool_name}`"})
    return {
        "id": anomaly.incident_id,
        "key": anomaly.key,
        "namespace": anomaly.namespace,
        "workload": anomaly.workload,
        "workload_kind": anomaly.workload_kind,
        "pod_name": anomaly.pod_name,
        "container_name": anomaly.container_name,
        "category": anomaly.category,
        "severity": anomaly.severity,
        "signals": anomaly.signals,
        "related_events": anomaly.related_events,
        "first_seen": ts,
        "last_seen": ts,
        "occurrences": 1,
        "missed_scans": 0,
        "status": status,
        "rca": rca.model_dump(),
        "fix_result": None,
        "history": history,
    }


def run_cycle(settings: Settings, store: StateStore, progress=print) -> CycleReport:
    anomalies = detector.detect_anomalies(
        namespace=settings.namespace,
        restart_threshold=settings.restart_threshold,
        stuck_threshold_seconds=settings.stuck_threshold_seconds,
        exclude_system_namespaces=not settings.include_system_namespaces,
    )
    report = CycleReport(detected=len(anomalies))
    seen_keys = {a.key for a in anomalies}

    # 1. Refresh already-known incidents (cheap, no LLM).
    to_analyze: list[Anomaly] = []
    with store.transaction() as incidents:
        for a in anomalies:
            inc = incidents.get(a.key)
            if inc and inc["status"] in ACTIVE_STATUSES:
                inc.update(last_seen=now_iso(), pod_name=a.pod_name, signals=a.signals,
                           related_events=a.related_events, missed_scans=0)
                inc["occurrences"] = inc.get("occurrences", 1) + 1
                report.ongoing += 1
            else:
                to_analyze.append(a)

    # 2. Diagnose new/recurring anomalies (slow LLM call -- done outside the lock).
    for i, a in enumerate(to_analyze, 1):
        progress(f"[{i}/{len(to_analyze)}] RCA for {a.category} in {a.namespace}/{a.workload} "
                 f"(model: {settings.model if settings.use_llm else 'rules only'}) ...")
        result = rca_mod.analyze(a, settings)
        with store.transaction() as incidents:
            inc = _new_incident(a, result, incidents.get(a.key))
            incidents[a.key] = inc
            path = sop.write_sop(inc, settings.sops_dir)
            sop.write_index(incidents, settings.sops_dir)
        report.new.append(inc["id"])
        progress(f"    -> {inc['id']}: {result.summary}")
        progress(f"    -> proposed: {result.proposed_fix.tool_name}  | SOP: {path}")

    # 3. Resolve incidents that are no longer detected.
    with store.transaction() as incidents:
        for key, inc in incidents.items():
            if inc["status"] not in ACTIVE_STATUSES or key in seen_keys:
                continue
            in_scope = (settings.namespace is None) or inc["namespace"] == settings.namespace
            if not in_scope:
                continue
            inc["missed_scans"] = inc.get("missed_scans", 0) + 1
            if inc["missed_scans"] >= RESOLVE_AFTER_MISSED_SCANS:
                inc["status"] = "resolved"
                inc["resolved_at"] = now_iso()
                inc["history"].append({"time": inc["resolved_at"],
                                       "event": "Resolved: anomaly no longer detected"})
                sop.write_sop(inc, settings.sops_dir)
                report.resolved.append(inc["id"])
                progress(f"Resolved {inc['id']} ({inc['category']} in {inc['namespace']}/{inc['workload']})")
        sop.write_index(incidents, settings.sops_dir)

    return report
