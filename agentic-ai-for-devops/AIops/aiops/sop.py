"""Ready-to-use SOP (standard operating procedure) Markdown docs.

One SOP per incident, re-rendered from the incident record whenever its status changes
(detected -> fix pending -> applied -> resolved), plus a sops/README.md index that is
regenerated from the full incident store (so it never accumulates duplicates).
"""

from __future__ import annotations

import re
from pathlib import Path

from aiops.models import ProposedFix
from aiops.remediation import fix_as_kubectl

STATUS_LABEL = {
    "open": "🟠 Open -- manual action required",
    "pending_fix": "🟡 Fix proposed -- awaiting approval",
    "fix_applied": "🔵 Fix applied -- verifying",
    "fix_failed": "🔴 Fix failed",
    "rejected": "⚪ Fix rejected -- manual action required",
    "resolved": "🟢 Resolved",
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown"


def sop_filename(incident: dict) -> str:
    return (f"{_slug(incident['category'])}_{_slug(incident['namespace'])}_"
            f"{_slug(incident['workload'])}_{incident['id']}.md")


def _bullets(items: list[str], empty: str = "(none)") -> str:
    return "\n".join(f"- {i}" for i in items) if items else f"- {empty}"


def _code_block(cmds: list[str]) -> str:
    return "```bash\n" + "\n".join(cmds) + "\n```"


def _verification(incident: dict) -> list[str]:
    ns, wl, kind = incident["namespace"], incident["workload"], incident.get("workload_kind")
    if incident["category"] == "NodeNotReady":
        return [f"kubectl get node {wl}", "# Expect STATUS=Ready"]
    cmds = []
    if kind == "Deployment":
        cmds.append(f"kubectl rollout status deployment/{wl} -n {ns} --timeout=120s")
    cmds += [f"kubectl get pods -n {ns} | grep {wl}",
             "# Expect STATUS=Running, READY=1/1 and RESTARTS not increasing",
             "aiops scan --no-llm   # expect this incident to no longer be reported"]
    return cmds


def _rollback(fix: ProposedFix) -> list[str]:
    a = fix.args
    if fix.tool_name == "patch_resource_limits":
        return [f"kubectl rollout undo deployment/{a['deployment_name']} -n {a['namespace']}"]
    if fix.tool_name == "scale_deployment":
        return [f"kubectl scale deployment/{a['deployment_name']} -n {a['namespace']} --replicas=<previous-count>"]
    if fix.tool_name in ("restart_deployment", "delete_pod"):
        return ["# Not needed: this action does not change the workload spec."]
    return ["# Not applicable: no automated change was made."]


def render(incident: dict) -> str:
    rca = incident["rca"]
    fix = ProposedFix.model_validate(rca["proposed_fix"])
    ns, wl, iid = incident["namespace"], incident["workload"], incident["id"]
    automated = fix.tool_name != "no_action"

    if automated:
        fix_section = f"""**Automated fix (whitelisted, validated):** `{fix.tool_name}`

- Arguments: `{fix.args}`
- Why: {fix.rationale}

Apply it with AIops (recommended -- records the result in this SOP):

{_code_block([f"aiops approve {iid}"])}

Or apply the equivalent manually:

{_code_block([fix_as_kubectl(fix)])}
"""
    else:
        fix_section = f"""**No automated fix is safe for this failure** -- a human change is required.

- Why: {fix.rationale}
"""

    result = incident.get("fix_result")
    result_section = ""
    if result:
        result_section = f"""
### Fix execution result

- Command: `{result.get('command', '')}`
- Success: {result.get('success')}
- Output: `{(result.get('stdout') or result.get('stderr') or '').strip()[:500]}`
"""

    timeline = "\n".join(f"| {h['time']} | {h['event']} |" for h in incident.get("history", []))
    events = _bullets(incident.get("related_events") or [], "(none captured)")

    return f"""# SOP: {incident['category']} in `{ns}/{wl}`

| Field | Value |
|---|---|
| Incident ID | `{iid}` |
| Status | {STATUS_LABEL.get(incident['status'], incident['status'])} |
| Severity | {incident['severity']} |
| Category | {incident['category']} |
| Namespace / Workload | `{ns}` / {incident.get('workload_kind') or ''} `{wl}` |
| Pod / Container | `{incident['pod_name']}` / `{incident.get('container_name') or '(pod-level)'}` |
| First seen / Last seen | {incident['first_seen']} / {incident['last_seen']} |
| Detections | {incident.get('occurrences', 1)} |
| RCA source / confidence | {rca.get('source', 'llm')} / {rca['confidence']} |

## 1. Summary

{rca['summary']}

## 2. Symptoms

- Detection signals: `{incident['signals']}`
- Kubernetes events:
{events}

## 3. Root Cause Analysis

{rca['root_cause']}

### Evidence

{_bullets(rca.get('evidence', []))}

## 4. Diagnose (run these first)

{_code_block([
    f"kubectl describe pod {incident['pod_name']} -n {ns}" if ns != "cluster" else f"kubectl describe node {wl}",
    f"kubectl get events -n {ns} --field-selector involvedObject.name={incident['pod_name']} --sort-by=.lastTimestamp" if ns != "cluster" else "kubectl get nodes -o wide",
    f"kubectl logs {incident['pod_name']} -n {ns} --previous --tail=50" if ns != "cluster" else "docker ps --filter name=control-plane",
])}

## 5. Resolution

{fix_section}
### Manual remediation steps

{_bullets(rca.get('manual_fix_steps', []), "Follow the automated fix above.")}
{result_section}
## 6. Verify

{_code_block(_verification(incident))}

## 7. Rollback

{_code_block(_rollback(fix))}

## 8. Prevention

{_bullets(rca.get('prevention', []))}

## 9. Timeline

| Time (UTC) | Event |
|---|---|
{timeline}

---
*Generated by AIops (`{rca.get('source', 'llm')}` analysis). Review before acting in any non-local environment.*
"""


def write_sop(incident: dict, sops_dir: Path) -> Path:
    sops_dir.mkdir(parents=True, exist_ok=True)
    path = sops_dir / sop_filename(incident)
    path.write_text(render(incident))
    return path


def write_index(incidents: dict, sops_dir: Path) -> Path:
    sops_dir.mkdir(parents=True, exist_ok=True)
    rows = sorted(incidents.values(), key=lambda i: i["first_seen"], reverse=True)
    active = sum(1 for i in rows if i["status"] != "resolved")
    pending = [i for i in rows if i["status"] == "pending_fix"]

    table = "\n".join(
        f"| `{i['id']}` | {STATUS_LABEL.get(i['status'], i['status'])} | {i['severity']} "
        f"| {i['category']} | `{i['namespace']}/{i['workload']}` | {i['first_seen']} "
        f"| [SOP](./{sop_filename(i)}) |"
        for i in rows
    ) or "| - | - | - | - | - | - | - |"

    pending_block = "\n".join(
        f"- `aiops approve {i['id']}` -- {i['category']} in `{i['namespace']}/{i['workload']}`"
        for i in pending
    ) or "- (none)"

    content = f"""# AIops Incident SOPs

Auto-generated by AIops. **{len(rows)}** incidents total, **{active}** active, \
**{len(pending)}** awaiting approval.

## Fixes awaiting approval

{pending_block}

## All incidents

| ID | Status | Severity | Category | Workload | First seen (UTC) | SOP |
|---|---|---|---|---|---|---|
{table}
"""
    path = sops_dir / "README.md"
    path.write_text(content)
    return path
