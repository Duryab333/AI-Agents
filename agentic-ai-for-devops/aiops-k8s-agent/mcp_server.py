"""FastMCP server exposing Kubernetes tools to the RCA agent.

Tools are split into two auditable groups:
- read-only inspection tools, freely usable by the diagnosis-phase agent (see agent.py)
- remediation tools, only ever invoked directly by the CLI's apply_fix step after
  explicit human confirmation -- never called by the diagnosis agent itself.

There is deliberately no generic "run any kubectl command" tool.
"""

import json

from mcp.server.fastmcp import FastMCP

import k8s_client

mcp = FastMCP("K8s AIOps MCP Server")


# ---- Read-only inspection tools ----


@mcp.tool()
def list_pods(namespace: str = "") -> str:
    """List pods with status, restarts, and age. Leave namespace empty for all namespaces."""
    args = ["get", "pods", "-o", "wide"]
    args += ["-n", namespace] if namespace else ["-A"]
    return k8s_client.run_kubectl(args)


@mcp.tool()
def describe_pod(namespace: str, pod_name: str) -> str:
    """Show full describe output (spec, status, conditions, recent events) for a pod."""
    return k8s_client.run_kubectl(["describe", "pod", pod_name, "-n", namespace])


@mcp.tool()
def get_pod_logs(
    namespace: str, pod_name: str, container: str = "", previous: bool = False, tail: int = 100
) -> str:
    """Get recent logs for a pod's container. Set previous=true to read logs from the
    last crashed instance of the container -- essential for diagnosing CrashLoopBackOff
    or OOMKilled, since the current instance may not have logged anything yet."""
    args = ["logs", pod_name, "-n", namespace, "--tail", str(tail)]
    if container:
        args += ["-c", container]
    if previous:
        args.append("--previous")
    return k8s_client.run_kubectl(args)


@mcp.tool()
def get_pod_events(namespace: str, pod_name: str) -> str:
    """Get Kubernetes events for a specific pod, sorted chronologically."""
    return k8s_client.run_kubectl(
        [
            "get", "events", "-n", namespace,
            "--field-selector", f"involvedObject.name={pod_name}",
            "--sort-by=.lastTimestamp",
        ]
    )


@mcp.tool()
def get_node_status() -> str:
    """Show status, roles, and capacity of all nodes in the cluster."""
    return k8s_client.run_kubectl(["get", "nodes", "-o", "wide"])


@mcp.tool()
def list_deployments(namespace: str = "") -> str:
    """List deployments with replica counts. Leave namespace empty for all namespaces."""
    args = ["get", "deployments", "-o", "wide"]
    args += ["-n", namespace] if namespace else ["-A"]
    return k8s_client.run_kubectl(args)


# ---- Remediation tools (whitelisted) ----
# Only ever invoked by the CLI's apply_fix step after explicit human confirmation.


@mcp.tool()
def restart_deployment(namespace: str, deployment_name: str) -> str:
    """Trigger a rolling restart of a deployment -- fixes issues resolved by getting a
    fresh set of pods without changing the deployment's spec."""
    return json.dumps(k8s_client.restart_deployment(namespace, deployment_name))


@mcp.tool()
def delete_pod(namespace: str, pod_name: str) -> str:
    """Delete a single pod, forcing its controller to recreate it. Use once the
    underlying cause of a stuck/crash-looping pod is understood (or already patched)."""
    return json.dumps(k8s_client.delete_pod(namespace, pod_name))


@mcp.tool()
def scale_deployment(namespace: str, deployment_name: str, replicas: int) -> str:
    """Scale a deployment to a specific replica count."""
    return json.dumps(k8s_client.scale_deployment(namespace, deployment_name, replicas))


@mcp.tool()
def patch_resource_limits(
    namespace: str,
    deployment_name: str,
    container_name: str,
    memory_limit: str = "",
    cpu_limit: str = "",
    memory_request: str = "",
    cpu_request: str = "",
) -> str:
    """Patch a container's CPU/memory requests and/or limits on a deployment. Use to fix
    OOMKilled (raise memory_limit) or Pending/unschedulable pods caused by oversized
    requests (lower cpu_request/memory_request). Leave any field empty to leave it
    unchanged."""
    return json.dumps(
        k8s_client.patch_resource_limits(
            namespace,
            deployment_name,
            container_name,
            memory_limit=memory_limit or None,
            cpu_limit=cpu_limit or None,
            memory_request=memory_request or None,
            cpu_request=cpu_request or None,
        )
    )


if __name__ == "__main__":
    mcp.run()
