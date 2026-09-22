"""Fix validation and application.

The LLM only *proposes* a fix. Before it is ever stored or applied, `sanitize_fix` pins
every target argument (namespace, deployment, pod, container) to the anomaly that was
actually detected and rejects malformed values -- so a hallucinated or mistyped target can
never touch a different workload. Applying happens only via `apply_fix`, which the CLI
calls after a human runs `aiops approve`.
"""

from __future__ import annotations

import re

from aiops import k8s
from aiops.evidence import deployment_resources
from aiops.guardrails import MAX_REPLICAS, MIN_REPLICAS, audit, check_fix_policy
from aiops.models import Anomaly, ProposedFix

_CPU_RE = re.compile(r"^\d+(\.\d+)?m?$")
_MEM_RE = re.compile(r"^\d+(\.\d+)?(Ki|Mi|Gi|Ti|K|M|G|T)?$")
_MEM_UNITS = {"": 1, "K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12,
              "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}



def memory_bytes(q: str) -> int | None:
    m = re.match(r"^(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti|K|M|G|T)?$", q or "")
    return int(float(m.group(1)) * _MEM_UNITS[m.group(2) or ""]) if m else None


def _no_action(reason: str) -> ProposedFix:
    return ProposedFix(tool_name="no_action", args={}, rationale=reason)


def sanitize_fix(fix: ProposedFix, anomaly: Anomaly) -> ProposedFix:
    if fix.tool_name == "no_action":
        return ProposedFix(tool_name="no_action", args={}, rationale=fix.rationale)

    deployment = anomaly.workload_name if anomaly.workload_kind == "Deployment" else None
    needs_deployment = fix.tool_name in ("restart_deployment", "scale_deployment", "patch_resource_limits")
    if needs_deployment and not deployment:
        return _no_action(
            f"Model proposed {fix.tool_name}, but {anomaly.pod_name} is not owned by a "
            f"Deployment. Original rationale: {fix.rationale}"
        )

    args: dict = {"namespace": anomaly.namespace}
    if fix.tool_name == "delete_pod":
        args["pod_name"] = anomaly.pod_name
    elif fix.tool_name == "restart_deployment":
        args["deployment_name"] = deployment
    elif fix.tool_name == "scale_deployment":
        try:
            replicas = int(fix.args.get("replicas"))
        except (TypeError, ValueError):
            return _no_action(f"Model proposed scaling without a valid replica count. {fix.rationale}")
        if not MIN_REPLICAS <= replicas <= MAX_REPLICAS:
            return _no_action(f"Refusing to scale to {replicas} replicas (allowed "
                              f"{MIN_REPLICAS}-{MAX_REPLICAS}; scaling to 0 is an outage).")
        args.update(deployment_name=deployment, replicas=replicas)
    elif fix.tool_name == "patch_resource_limits":
        containers = list(deployment_resources(anomaly.namespace, deployment))
        container = anomaly.container_name
        if not container:
            # Pod-level anomalies (e.g. Pending) have no container: accept the model's
            # choice only if it really exists, else the sole container if there's one.
            asked = fix.args.get("container_name")
            container = asked if asked in containers else (containers[0] if len(containers) == 1 else "")
        args.update(deployment_name=deployment, container_name=container)
        if not args["container_name"]:
            return _no_action(f"Could not determine which container to patch. {fix.rationale}")
        for key, pattern in (("cpu_request", _CPU_RE), ("cpu_limit", _CPU_RE),
                             ("memory_request", _MEM_RE), ("memory_limit", _MEM_RE)):
            value = str(fix.args.get(key) or "").strip()
            if value:
                if not pattern.match(value):
                    return _no_action(f"Model proposed an invalid {key} '{value}'. {fix.rationale}")
                args[key] = value
        if not any(k in args for k in ("cpu_request", "cpu_limit", "memory_request", "memory_limit")):
            return _no_action(f"Model proposed patching resources but gave no values. {fix.rationale}")

    blocked = check_fix_policy(fix.tool_name, args)
    if blocked:
        audit("fix_blocked_by_policy", tool=fix.tool_name, args=args, reason=blocked)
        return _no_action(f"Blocked by guardrail: {blocked}. Original proposal: {fix.rationale}")
    return ProposedFix(tool_name=fix.tool_name, args=args, rationale=fix.rationale)


def apply_fix(fix: ProposedFix) -> dict:
    a = fix.args
    if fix.tool_name == "restart_deployment":
        return k8s.restart_deployment(a["namespace"], a["deployment_name"])
    if fix.tool_name == "delete_pod":
        return k8s.delete_pod(a["namespace"], a["pod_name"])
    if fix.tool_name == "scale_deployment":
        return k8s.scale_deployment(a["namespace"], a["deployment_name"], int(a["replicas"]))
    if fix.tool_name == "patch_resource_limits":
        return k8s.patch_resource_limits(
            a["namespace"], a["deployment_name"], a["container_name"],
            cpu_request=a.get("cpu_request"), cpu_limit=a.get("cpu_limit"),
            memory_request=a.get("memory_request"), memory_limit=a.get("memory_limit"),
        )
    return {"success": False, "command": "", "stdout": "", "stderr": "no_action: nothing to apply"}


def fix_as_kubectl(fix: ProposedFix) -> str:
    """Human-readable equivalent command, for SOPs and the approval prompt."""
    a = fix.args
    if fix.tool_name == "restart_deployment":
        return f"kubectl rollout restart deployment/{a['deployment_name']} -n {a['namespace']}"
    if fix.tool_name == "delete_pod":
        return f"kubectl delete pod {a['pod_name']} -n {a['namespace']}"
    if fix.tool_name == "scale_deployment":
        return f"kubectl scale deployment/{a['deployment_name']} -n {a['namespace']} --replicas={a['replicas']}"
    if fix.tool_name == "patch_resource_limits":
        limits = [f"{r}={a[f'{r}_limit']}" for r in ("cpu", "memory") if a.get(f"{r}_limit")]
        reqs = [f"{r}={a[f'{r}_request']}" for r in ("cpu", "memory") if a.get(f"{r}_request")]
        cmd = f"kubectl set resources deployment/{a['deployment_name']} -n {a['namespace']} -c {a['container_name']}"
        if limits:
            cmd += " --limits=" + ",".join(limits)
        if reqs:
            cmd += " --requests=" + ",".join(reqs)
        return cmd
    return "(no automated fix)"
