"""Shared kubectl subprocess wrapper layer, used by both the MCP server (agent-facing
tools) and the deterministic detector. Every remediation action lives here as a
distinct, named function -- there is deliberately no generic "run this kubectl command"
escape hatch, so an LLM can only ever trigger one of these specific, auditable actions.
"""

from __future__ import annotations

import json
import subprocess
import sys


def run_kubectl(args: list[str], parse_json: bool = False):
    """Run a kubectl command and return its stdout (or parsed JSON), or an 'Error: ...' string."""
    result = subprocess.run(["kubectl", *args], capture_output=True, text=True)

    if result.returncode != 0:
        return f"Error: {result.stderr.strip()}"

    output = result.stdout.strip()
    if parse_json:
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            return f"Error: could not parse kubectl JSON output ({exc})"
    return output


def assert_kind_context(allow_override: bool = False) -> None:
    """Refuse to run against anything that isn't a local kind cluster, unless overridden."""
    if allow_override:
        return
    result = subprocess.run(
        ["kubectl", "config", "current-context"], capture_output=True, text=True
    )
    context = result.stdout.strip()
    if result.returncode != 0 or not context.startswith("kind-"):
        print(
            f"Refusing to run: current kubectl context is '{context or '(none)'}', "
            "not a kind cluster (expected a context starting with 'kind-').\n"
            "Create one with `kind create cluster` or switch context with "
            "`kubectl config use-context kind-<name>`.",
            file=sys.stderr,
        )
        sys.exit(1)


# ---- Read-only inspection ----


def list_pods_json(namespace: str | None = None):
    args = ["get", "pods", "-o", "json"]
    args += ["-n", namespace] if namespace else ["-A"]
    return run_kubectl(args, parse_json=True)


def list_events_json(namespace: str | None = None):
    args = ["get", "events", "-o", "json"]
    args += ["-n", namespace] if namespace else ["-A"]
    return run_kubectl(args, parse_json=True)


def list_deployments_json(namespace: str | None = None):
    args = ["get", "deployments", "-o", "json"]
    args += ["-n", namespace] if namespace else ["-A"]
    return run_kubectl(args, parse_json=True)


def resolve_owning_deployment(namespace: str, owner_references: list[dict] | None) -> str | None:
    """Walk ownerReferences (Pod -> ReplicaSet -> Deployment) to find the owning Deployment name."""
    for owner in owner_references or []:
        if owner.get("kind") == "Deployment":
            return owner.get("name")
        if owner.get("kind") == "ReplicaSet":
            rs = run_kubectl(
                ["get", "replicaset", owner["name"], "-n", namespace, "-o", "json"],
                parse_json=True,
            )
            if isinstance(rs, dict):
                for rs_owner in rs.get("metadata", {}).get("ownerReferences", []):
                    if rs_owner.get("kind") == "Deployment":
                        return rs_owner.get("name")
    return None


# ---- Remediation (whitelisted) ----


def _run_and_report(args: list[str]) -> dict:
    result = subprocess.run(args, capture_output=True, text=True)
    return {
        "success": result.returncode == 0,
        "command": " ".join(args),
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def restart_deployment(namespace: str, deployment_name: str) -> dict:
    return _run_and_report(
        ["kubectl", "rollout", "restart", f"deployment/{deployment_name}", "-n", namespace]
    )


def delete_pod(namespace: str, pod_name: str) -> dict:
    return _run_and_report(["kubectl", "delete", "pod", pod_name, "-n", namespace])


def scale_deployment(namespace: str, deployment_name: str, replicas: int) -> dict:
    return _run_and_report(
        [
            "kubectl", "scale", f"deployment/{deployment_name}",
            "-n", namespace, f"--replicas={replicas}",
        ]
    )


def patch_resource_limits(
    namespace: str,
    deployment_name: str,
    container_name: str,
    cpu_request: str | None = None,
    cpu_limit: str | None = None,
    memory_request: str | None = None,
    memory_limit: str | None = None,
) -> dict:
    limits = {k: v for k, v in {"cpu": cpu_limit, "memory": memory_limit}.items() if v}
    requests = {k: v for k, v in {"cpu": cpu_request, "memory": memory_request}.items() if v}
    resources = {}
    if limits:
        resources["limits"] = limits
    if requests:
        resources["requests"] = requests

    if not resources:
        return {
            "success": False,
            "command": "",
            "stdout": "",
            "stderr": "No resource values provided; nothing to patch.",
        }

    patch = {
        "spec": {
            "template": {
                "spec": {"containers": [{"name": container_name, "resources": resources}]}
            }
        }
    }
    args = [
        "kubectl", "patch", "deployment", deployment_name,
        "-n", namespace, "--type", "strategic", "-p", json.dumps(patch),
    ]
    return _run_and_report(args)
