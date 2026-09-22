"""kubectl wrapper shared by the detector, evidence collector, remediation and MCP server.

Every mutating action is a distinct, named function -- there is deliberately no generic
"run this kubectl command" escape hatch, so neither the LLM nor the daemon can do anything
outside this whitelist.
"""

from __future__ import annotations

import json
import subprocess

KUBECTL_TIMEOUT = 30


class NotKindClusterError(RuntimeError):
    pass


def run_kubectl(args: list[str], parse_json: bool = False):
    """Run kubectl; return stdout (or parsed JSON), or an 'Error: ...' string."""
    try:
        result = subprocess.run(
            ["kubectl", *args], capture_output=True, text=True, timeout=KUBECTL_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return f"Error: kubectl {' '.join(args)} timed out after {KUBECTL_TIMEOUT}s"
    except FileNotFoundError:
        return "Error: kubectl not found on PATH"

    if result.returncode != 0:
        return f"Error: {result.stderr.strip()}"

    output = result.stdout.strip()
    if parse_json:
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            return f"Error: could not parse kubectl JSON output ({exc})"
    return output


def current_context() -> str:
    result = subprocess.run(
        ["kubectl", "config", "current-context"], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def assert_kind_context() -> str:
    """Refuse to run against anything that isn't a local kind cluster."""
    context = current_context()
    if not context.startswith("kind-"):
        raise NotKindClusterError(
            f"current kubectl context is '{context or '(none)'}', not a kind cluster "
            "(expected 'kind-*'). Create one with `kind create cluster` or run "
            "`kubectl config use-context kind-<name>`."
        )
    return context


# ---- Read-only ----


def _scope(namespace: str | None) -> list[str]:
    return ["-n", namespace] if namespace else ["-A"]


def list_pods_json(namespace: str | None = None):
    return run_kubectl(["get", "pods", "-o", "json", *_scope(namespace)], parse_json=True)


def list_events_json(namespace: str | None = None):
    return run_kubectl(["get", "events", "-o", "json", *_scope(namespace)], parse_json=True)


def list_nodes_json():
    return run_kubectl(["get", "nodes", "-o", "json"], parse_json=True)


def get_deployment_json(namespace: str, name: str):
    return run_kubectl(["get", "deployment", name, "-n", namespace, "-o", "json"], parse_json=True)


def describe_pod(namespace: str, pod_name: str) -> str:
    return run_kubectl(["describe", "pod", pod_name, "-n", namespace])


def pod_logs(namespace: str, pod_name: str, container: str | None = None,
             previous: bool = False, tail: int = 40) -> str:
    args = ["logs", pod_name, "-n", namespace, "--tail", str(tail)]
    if container:
        args += ["-c", container]
    if previous:
        args.append("--previous")
    return run_kubectl(args)


def pod_events(namespace: str, pod_name: str) -> str:
    return run_kubectl([
        "get", "events", "-n", namespace,
        "--field-selector", f"involvedObject.name={pod_name}",
        "--sort-by=.lastTimestamp",
    ])


def resolve_owning_deployment(namespace: str, owner_references: list[dict] | None) -> str | None:
    """Walk ownerReferences (Pod -> ReplicaSet -> Deployment) to find the Deployment name."""
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
    result = subprocess.run(args, capture_output=True, text=True, timeout=KUBECTL_TIMEOUT)
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
    return _run_and_report(["kubectl", "delete", "pod", pod_name, "-n", namespace, "--wait=false"])


def scale_deployment(namespace: str, deployment_name: str, replicas: int) -> dict:
    return _run_and_report([
        "kubectl", "scale", f"deployment/{deployment_name}",
        "-n", namespace, f"--replicas={replicas}",
    ])


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
        return {"success": False, "command": "", "stdout": "",
                "stderr": "No resource values provided; nothing to patch."}

    patch = {"spec": {"template": {"spec": {
        "containers": [{"name": container_name, "resources": resources}]
    }}}}
    return _run_and_report([
        "kubectl", "patch", "deployment", deployment_name,
        "-n", namespace, "--type", "strategic", "-p", json.dumps(patch),
    ])
