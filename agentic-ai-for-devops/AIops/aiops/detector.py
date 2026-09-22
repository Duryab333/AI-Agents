"""Deterministic anomaly detection.

Detection is intentionally NOT LLM-driven: it parses structured kubectl JSON, so it is
fast, free on a healthy cluster (no LLM call at all), and testable. The LLM's job is RCA
over an anomaly that has already been identified.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiops import k8s
from aiops.models import Anomaly

SYSTEM_NAMESPACES = {"kube-system", "kube-node-lease", "kube-public", "local-path-storage"}

SEVERITY = {
    "NodeNotReady": "critical",
    "CrashLoopBackOff": "high",
    "ImagePullBackOff": "high",
    "OOMKilled": "high",
    "PendingUnschedulable": "high",
    "CreateContainerConfigError": "high",
    "HighRestartCount": "medium",
    "ContainerCreatingStuck": "medium",
    "ProbeFailure": "medium",
}

# Categories that already explain a rising restart count, so HighRestartCount would be noise.
_RESTART_EXPLAINED = {"CrashLoopBackOff", "OOMKilled", "ImagePullBackOff"}


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _index_events_by_pod(events_data: dict) -> dict[tuple[str, str], list[dict]]:
    by_pod: dict[tuple[str, str], list[dict]] = {}
    for event in events_data.get("items", []):
        involved = event.get("involvedObject", {})
        if involved.get("kind") != "Pod":
            continue
        key = (involved.get("namespace", ""), involved.get("name", ""))
        by_pod.setdefault(key, []).append(event)
    for events in by_pod.values():
        events.sort(key=lambda e: e.get("lastTimestamp") or e.get("eventTime") or "")
    return by_pod


def _event_messages(events: list[dict], limit: int = 8) -> list[str]:
    return [f"{e.get('reason', '')}: {e.get('message', '')}" for e in events[-limit:]]


def _detect_nodes(now: datetime) -> list[Anomaly]:
    nodes = k8s.list_nodes_json()
    if not isinstance(nodes, dict):
        return []
    anomalies = []
    for node in nodes.get("items", []):
        ready = next(
            (c for c in node.get("status", {}).get("conditions", []) if c.get("type") == "Ready"),
            None,
        )
        if ready and ready.get("status") != "True":
            anomalies.append(Anomaly(
                namespace="cluster",
                pod_name=node["metadata"]["name"],
                container_name=None,
                category="NodeNotReady",
                severity=SEVERITY["NodeNotReady"],
                detected_at=now,
                signals={"reason": ready.get("reason"), "message": ready.get("message")},
                workload_kind="Node",
                workload_name=node["metadata"]["name"],
            ))
    return anomalies


def detect_anomalies(
    namespace: str | None = None,
    restart_threshold: int = 3,
    stuck_threshold_seconds: int = 300,
    exclude_system_namespaces: bool = True,
) -> list[Anomaly]:
    pods_data = k8s.list_pods_json(namespace)
    if isinstance(pods_data, str):
        raise RuntimeError(f"failed to list pods: {pods_data}")

    events_data = k8s.list_events_json(namespace)
    events_by_pod = _index_events_by_pod(events_data) if isinstance(events_data, dict) else {}

    now = datetime.now(timezone.utc)
    anomalies: list[Anomaly] = [] if namespace else _detect_nodes(now)

    for pod in pods_data.get("items", []):
        ns = pod["metadata"]["namespace"]
        name = pod["metadata"]["name"]
        if exclude_system_namespaces and ns in SYSTEM_NAMESPACES:
            continue
        if pod["metadata"].get("deletionTimestamp"):
            continue  # already being torn down (e.g. mid-rollout) -- not an anomaly

        status = pod.get("status", {})
        phase = status.get("phase")
        if phase == "Succeeded":
            continue

        pod_events = events_by_pod.get((ns, name), [])
        pod_anomalies: list[Anomaly] = []

        def add(category: str, container_name: str | None, signals: dict) -> None:
            if any(a.category == category for a in pod_anomalies):
                return
            pod_anomalies.append(Anomaly(
                namespace=ns,
                pod_name=name,
                container_name=container_name,
                category=category,
                severity=SEVERITY.get(category, "medium"),
                detected_at=now,
                signals=signals,
                related_events=_event_messages(pod_events),
            ))

        for cs in status.get("containerStatuses") or []:
            cname = cs.get("name")
            restarts = cs.get("restartCount", 0)
            state = cs.get("state") or {}
            waiting = state.get("waiting") or {}
            terminated = state.get("terminated") or {}
            last_terminated = (cs.get("lastState") or {}).get("terminated") or {}
            wreason = waiting.get("reason")

            if wreason in ("ImagePullBackOff", "ErrImagePull", "InvalidImageName"):
                add("ImagePullBackOff", cname, {
                    "reason": wreason, "image": cs.get("image"), "message": waiting.get("message"),
                })

            if wreason in ("CreateContainerConfigError", "CreateContainerError"):
                add("CreateContainerConfigError", cname, {
                    "reason": wreason, "message": waiting.get("message"),
                })

            oom = terminated.get("reason") == "OOMKilled" or last_terminated.get("reason") == "OOMKilled"
            if oom:
                add("OOMKilled", cname, {"restart_count": restarts})

            # A crash-looping container flips between waiting/CrashLoopBackOff and
            # terminated/Error, so check the last termination too -- otherwise a scan
            # landing on the "terminated" half of the cycle misses it.
            crashed = (
                wreason == "CrashLoopBackOff"
                or (terminated.get("exitCode", 0) != 0 and restarts > 0)
                or (last_terminated.get("exitCode", 0) != 0 and restarts >= 2)
            )
            if crashed and not oom:
                last = last_terminated or terminated
                add("CrashLoopBackOff", cname, {
                    "restart_count": restarts,
                    "last_exit_code": last.get("exitCode"),
                    "last_reason": last.get("reason"),
                })

            if restarts >= restart_threshold and not ({a.category for a in pod_anomalies} & _RESTART_EXPLAINED):
                add("HighRestartCount", cname, {"restart_count": restarts})

            if wreason == "ContainerCreating":
                created = pod["metadata"].get("creationTimestamp")
                if created and (now - _parse_ts(created)).total_seconds() > stuck_threshold_seconds:
                    add("ContainerCreatingStuck", cname, {
                        "age_seconds": int((now - _parse_ts(created)).total_seconds()),
                    })

        if phase == "Pending" and not pod.get("spec", {}).get("nodeName"):
            failed = [e for e in pod_events if e.get("reason") == "FailedScheduling"]
            if failed:
                add("PendingUnschedulable", None, {"message": failed[-1].get("message")})

        if phase == "Running":
            unhealthy = [e for e in pod_events if e.get("reason") == "Unhealthy"]
            if unhealthy:
                add("ProbeFailure", None, {"message": unhealthy[-1].get("message")})

        if pod_anomalies:
            deployment = k8s.resolve_owning_deployment(ns, pod["metadata"].get("ownerReferences"))
            for a in pod_anomalies:
                a.workload_name = deployment
                a.workload_kind = "Deployment" if deployment else "Pod"
            anomalies.extend(pod_anomalies)

    # Several replicas of one Deployment failing the same way are one incident.
    unique: dict[str, Anomaly] = {}
    for a in anomalies:
        unique.setdefault(a.key, a)
    return list(unique.values())
