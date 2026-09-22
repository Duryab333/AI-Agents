"""Deterministic anomaly detection for a Kubernetes cluster.

Detection is intentionally NOT LLM-driven: it parses structured `kubectl` JSON output
so it stays fast, cheap (no LLM call on a healthy cluster), and testable. The LLM's job
(see agent.py) is root-cause analysis over an already-identified anomaly, not deciding
what counts as broken.
"""

from __future__ import annotations

from datetime import datetime, timezone

import k8s_client
from models import Anomaly

SYSTEM_NAMESPACES = {"kube-system", "kube-node-lease", "kube-public", "local-path-storage"}

_SEVERITY = {
    "CrashLoopBackOff": "high",
    "ImagePullBackOff": "high",
    "OOMKilled": "high",
    "PendingUnschedulable": "high",
    "HighRestartCount": "medium",
    "ContainerCreatingStuck": "medium",
    "ProbeFailure": "medium",
}


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


def _event_messages(events: list[dict]) -> list[str]:
    return [f"{e.get('reason', '')}: {e.get('message', '')}" for e in events]


def detect_anomalies(
    namespace: str | None = None,
    restart_threshold: int = 5,
    stuck_threshold_seconds: int = 300,
    exclude_system_namespaces: bool = True,
) -> list[Anomaly]:
    pods_data = k8s_client.list_pods_json(namespace)
    if isinstance(pods_data, str):
        raise RuntimeError(f"failed to list pods: {pods_data}")

    events_data = k8s_client.list_events_json(namespace)
    events_by_pod = _index_events_by_pod(events_data) if isinstance(events_data, dict) else {}

    now = datetime.now(timezone.utc)
    anomalies: list[Anomaly] = []

    for pod in pods_data.get("items", []):
        ns = pod["metadata"]["namespace"]
        name = pod["metadata"]["name"]

        if exclude_system_namespaces and ns in SYSTEM_NAMESPACES:
            continue

        status = pod.get("status", {})
        phase = status.get("phase")
        if phase == "Succeeded":
            continue

        pod_events = events_by_pod.get((ns, name), [])
        container_statuses = status.get("containerStatuses") or []
        categories_found: set[str] = set()

        def add(category: str, container_name: str | None, signals: dict) -> None:
            categories_found.add(category)
            anomalies.append(
                Anomaly(
                    id=f"{ns}/{name}/{category}",
                    namespace=ns,
                    pod_name=name,
                    container_name=container_name,
                    category=category,
                    severity=_SEVERITY.get(category, "medium"),
                    detected_at=now,
                    signals=signals,
                    related_events=_event_messages(pod_events),
                )
            )

        for cs in container_statuses:
            cname = cs.get("name")
            restart_count = cs.get("restartCount", 0)
            waiting = (cs.get("state") or {}).get("waiting")
            terminated = (cs.get("state") or {}).get("terminated")
            last_terminated = (cs.get("lastState") or {}).get("terminated")

            if waiting and waiting.get("reason") == "CrashLoopBackOff":
                add("CrashLoopBackOff", cname, {
                    "restart_count": restart_count,
                    "message": waiting.get("message"),
                })

            if waiting and waiting.get("reason") in ("ImagePullBackOff", "ErrImagePull"):
                add("ImagePullBackOff", cname, {
                    "reason": waiting.get("reason"),
                    "message": waiting.get("message"),
                })

            if (terminated and terminated.get("reason") == "OOMKilled") or (
                last_terminated and last_terminated.get("reason") == "OOMKilled"
            ):
                add("OOMKilled", cname, {"restart_count": restart_count})

            restart_explained = categories_found & {"CrashLoopBackOff", "OOMKilled", "ImagePullBackOff"}
            if restart_count >= restart_threshold and not restart_explained:
                add("HighRestartCount", cname, {"restart_count": restart_count})

            if waiting and waiting.get("reason") == "ContainerCreating":
                created = pod["metadata"].get("creationTimestamp")
                if created:
                    age_seconds = (now - _parse_ts(created)).total_seconds()
                    if age_seconds > stuck_threshold_seconds:
                        add("ContainerCreatingStuck", cname, {"age_seconds": age_seconds})

        if phase == "Pending" and not pod.get("spec", {}).get("nodeName"):
            # Only a genuinely unscheduled pod counts here -- a pod that already has a
            # node assigned but is still Pending (e.g. stuck pulling its image) is
            # covered by the other categories, and may carry a stale FailedScheduling
            # event from before it was scheduled.
            failed_sched = [e for e in pod_events if e.get("reason") == "FailedScheduling"]
            if failed_sched:
                add("PendingUnschedulable", None, {"message": failed_sched[-1].get("message")})

        if phase == "Running":
            unhealthy = [e for e in pod_events if e.get("reason") == "Unhealthy"]
            if unhealthy:
                add("ProbeFailure", None, {"message": unhealthy[-1].get("message")})

        if categories_found:
            workload_name = k8s_client.resolve_owning_deployment(
                ns, pod["metadata"].get("ownerReferences")
            )
            for a in anomalies:
                if a.namespace == ns and a.pod_name == name and a.workload_name is None:
                    a.workload_name = workload_name
                    a.workload_kind = "Deployment" if workload_name else None

    return anomalies
