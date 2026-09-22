"""FastMCP server exposing READ-ONLY cluster + incident tools to the `aiops chat` agent.

Same pattern as ../docker-agent/mcp_server.py, but deliberately without any mutating tool:
fixes only ever go through `aiops approve`, which validates and records them.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from aiops import k8s
from aiops.config import Settings
from aiops.state import StateStore

mcp = FastMCP("AIops K8s MCP Server")


@mcp.tool()
def list_pods(namespace: str = "") -> str:
    """List pods with status, restarts and age. Leave namespace empty for all namespaces."""
    return k8s.run_kubectl(["get", "pods", "-o", "wide", *(["-n", namespace] if namespace else ["-A"])])


@mcp.tool()
def describe_pod(namespace: str, pod_name: str) -> str:
    """Full describe output (spec, status, conditions, events) for one pod."""
    return k8s.describe_pod(namespace, pod_name)


@mcp.tool()
def get_pod_logs(namespace: str, pod_name: str, previous: bool = False, tail: int = 50) -> str:
    """Recent logs of a pod. previous=true reads the last crashed container instance."""
    return k8s.pod_logs(namespace, pod_name, previous=previous, tail=tail)


@mcp.tool()
def get_pod_events(namespace: str, pod_name: str) -> str:
    """Kubernetes events for one pod, oldest first."""
    return k8s.pod_events(namespace, pod_name)


@mcp.tool()
def list_deployments(namespace: str = "") -> str:
    """List deployments with replica counts. Leave namespace empty for all namespaces."""
    return k8s.run_kubectl(["get", "deployments", "-o", "wide", *(["-n", namespace] if namespace else ["-A"])])


@mcp.tool()
def get_node_status() -> str:
    """Status, roles and versions of all nodes."""
    return k8s.run_kubectl(["get", "nodes", "-o", "wide"])


@mcp.tool()
def list_incidents() -> str:
    """AIops incidents found by the background daemon: id, status, category, workload, RCA summary."""
    incidents = StateStore(Settings().state_file).load()
    return json.dumps([
        {"id": i["id"], "status": i["status"], "category": i["category"],
         "workload": f"{i['namespace']}/{i['workload']}", "root_cause": i["rca"]["root_cause"],
         "proposed_fix": i["rca"]["proposed_fix"]["tool_name"]}
        for i in incidents.values()
    ], indent=1) or "[]"


if __name__ == "__main__":
    mcp.run()
