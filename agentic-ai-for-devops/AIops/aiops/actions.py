"""Incident actions shared by the CLI commands and the conversational agent.

`approve` takes a `confirm` callback that must return True before anything is applied.
Both callers wire it to a real terminal y/N prompt, so the human -- never the LLM --
makes the final call on every cluster change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from aiops import k8s, rca as rca_mod, sop
from aiops.config import Settings
from aiops.guardrails import audit, check_fix_policy
from aiops.models import Anomaly, ProposedFix
from aiops.remediation import apply_fix, fix_as_kubectl
from aiops.state import StateStore, find, now_iso

APPROVABLE = ("pending_fix", "fix_failed", "rejected")


class ActionError(Exception):
    pass


def get_incident(settings: Settings, incident_id: str) -> dict:
    inc = find(StateStore(settings.state_file).load(), incident_id.strip())
    if not inc:
        raise ActionError(f"No incident with id '{incident_id}'. List them with `aiops incidents`.")
    return inc


def describe_fix(inc: dict) -> str:
    fix = ProposedFix.model_validate(inc["rca"]["proposed_fix"])
    lines = [f"Incident {inc['id']}: {inc['category']} in {inc['namespace']}/{inc['workload']}",
             f"Root cause: {inc['rca']['root_cause']}"]
    if fix.tool_name == "no_action":
        lines.append("Fix:        none automated -- manual steps:")
        lines += [f"  - {s}" for s in inc["rca"].get("manual_fix_steps", [])]
    else:
        lines += [f"Fix:        {fix.tool_name} {fix.args}",
                  f"Equivalent: {fix_as_kubectl(fix)}"]
    lines.append(f"Rationale:  {fix.rationale}")
    return "\n".join(lines)


def approve(settings: Settings, incident_id: str, confirm: Callable[[], bool]) -> dict:
    """Apply an incident's proposed fix if `confirm()` returns True. Returns a result dict."""
    k8s.assert_kind_context()
    inc = get_incident(settings, incident_id)
    if inc["status"] not in APPROVABLE:
        raise ActionError(f"Incident {inc['id']} is '{inc['status']}' -- there is no pending fix to apply.")
    fix = ProposedFix.model_validate(inc["rca"]["proposed_fix"])
    if fix.tool_name == "no_action":
        raise ActionError(f"Incident {inc['id']} has no automated fix; a human must follow "
                          "the SOP's manual steps.")

    # Re-check at execution time: the stored fix could have been edited since it was proposed.
    blocked = check_fix_policy(fix.tool_name, fix.args)
    if blocked or fix.args.get("namespace") != inc["namespace"]:
        reason = blocked or "fix targets a different namespace than the incident"
        audit("fix_blocked_by_policy", incident=inc["id"], tool=fix.tool_name, args=fix.args, reason=reason)
        raise ActionError(f"Blocked by guardrail: {reason}.")

    print(describe_fix(inc))
    if not confirm():
        audit("fix_declined", incident=inc["id"], tool=fix.tool_name, args=fix.args)
        return {"applied": False, "message": "Operator declined; nothing was changed."}

    result = apply_fix(fix)
    audit("fix_applied" if result["success"] else "fix_failed", incident=inc["id"],
          tool=fix.tool_name, args=fix.args, command=result["command"],
          error=None if result["success"] else result["stderr"])
    store = StateStore(settings.state_file)
    with store.transaction() as incidents:
        cur = incidents[inc["key"]]
        cur["fix_result"] = result
        cur["status"] = "fix_applied" if result["success"] else "fix_failed"
        cur["history"].append({"time": now_iso(),
                               "event": f"Fix {'applied' if result['success'] else 'FAILED'} by operator: "
                                        f"`{result['command']}`"})
        path = sop.write_sop(cur, settings.sops_dir)
        sop.write_index(incidents, settings.sops_dir)
    return {"applied": result["success"],
            "message": (result["stdout"] if result["success"] else result["stderr"]),
            "sop": str(path)}


def reject(settings: Settings, incident_id: str) -> str:
    store = StateStore(settings.state_file)
    with store.transaction() as incidents:
        inc = find(incidents, incident_id.strip())
        if not inc:
            raise ActionError(f"No incident with id '{incident_id}'.")
        inc["status"] = "rejected"
        audit("fix_rejected", incident=inc["id"])
        inc["history"].append({"time": now_iso(), "event": "Proposed fix rejected by operator"})
        sop.write_sop(inc, settings.sops_dir)
        sop.write_index(incidents, settings.sops_dir)
    return f"Rejected fix for {inc['id']}; follow the SOP's manual steps."


def reanalyze(settings: Settings, incident_id: str) -> dict:
    """Re-run RCA (with the LLM) for an existing incident and refresh its SOP."""
    k8s.assert_kind_context()
    inc = get_incident(settings, incident_id)
    anomaly = Anomaly(
        namespace=inc["namespace"], pod_name=inc["pod_name"], container_name=inc.get("container_name"),
        category=inc["category"], severity=inc["severity"],
        detected_at=datetime.now(timezone.utc),
        signals=inc["signals"], related_events=inc.get("related_events", []),
        workload_kind=inc.get("workload_kind"), workload_name=inc["workload"],
    )
    result = rca_mod.analyze(anomaly, settings)
    store = StateStore(settings.state_file)
    with store.transaction() as incidents:
        cur = incidents[inc["key"]]
        cur["rca"] = result.model_dump()
        if cur["status"] in ("open", "pending_fix", "rejected"):
            cur["status"] = "open" if result.proposed_fix.tool_name == "no_action" else "pending_fix"
        cur["history"].append({"time": now_iso(), "event": f"Re-analyzed ({result.source}, "
                               f"{result.confidence}): proposed `{result.proposed_fix.tool_name}`"})
        path = sop.write_sop(cur, settings.sops_dir)
        sop.write_index(incidents, settings.sops_dir)
    return {"id": inc["id"], "source": result.source, "root_cause": result.root_cause,
            "proposed_fix": result.proposed_fix.tool_name, "summary": result.summary, "sop": str(path)}
