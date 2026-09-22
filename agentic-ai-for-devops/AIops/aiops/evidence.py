"""Deterministic evidence collection for one anomaly.

Gathering evidence in Python (instead of letting the LLM decide which tools to call)
turns RCA into a single LLM request. On a CPU-only machine running a small Qwen model
that is the difference between ~1 minute and ~10+ minutes per incident, and it makes the
result far more reliable since the model can't skip checking the logs.
"""

from __future__ import annotations

from aiops import k8s
from aiops.models import Anomaly

MAX_SECTION_CHARS = 2500


def _clip(text: str, limit: int = MAX_SECTION_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return "...(truncated)...\n" + text[-limit:]


def deployment_resources(namespace: str, deployment: str) -> dict[str, dict]:
    """container name -> resources block from the Deployment's pod template."""
    dep = k8s.get_deployment_json(namespace, deployment)
    if not isinstance(dep, dict):
        return {}
    containers = dep.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    return {c["name"]: c.get("resources", {}) for c in containers}


def node_allocatable() -> list[dict]:
    nodes = k8s.list_nodes_json()
    if not isinstance(nodes, dict):
        return []
    return [
        {"node": n["metadata"]["name"], "allocatable": n.get("status", {}).get("allocatable", {})}
        for n in nodes.get("items", [])
    ]


def collect(anomaly: Anomaly) -> dict[str, str]:
    """Return named evidence sections (already clipped to fit a small model's context)."""
    ev: dict[str, str] = {}

    if anomaly.category == "NodeNotReady":
        ev["node_describe"] = _clip(k8s.run_kubectl(["describe", "node", anomaly.pod_name]))
        return ev

    ns, pod = anomaly.namespace, anomaly.pod_name
    ev["describe_pod"] = _clip(k8s.describe_pod(ns, pod), 3500)
    ev["events"] = _clip(k8s.pod_events(ns, pod), 1500)

    if anomaly.category in ("CrashLoopBackOff", "OOMKilled", "HighRestartCount", "ProbeFailure"):
        ev["logs_previous_container"] = _clip(
            k8s.pod_logs(ns, pod, anomaly.container_name, previous=True), 2000
        )
        ev["logs_current_container"] = _clip(
            k8s.pod_logs(ns, pod, anomaly.container_name), 1000
        )

    if anomaly.workload_kind == "Deployment" and anomaly.workload_name:
        ev["deployment_container_resources"] = str(
            deployment_resources(ns, anomaly.workload_name)
        )

    if anomaly.category == "PendingUnschedulable":
        ev["node_allocatable"] = str(node_allocatable())

    return ev
