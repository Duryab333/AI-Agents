"""Conversational AIops agent: type plain English, the Qwen agent picks the tools.

Same pattern as ../docker-agent (MCP tools + LangChain create_agent + ChatOllama), with:
- read-only cluster tools served by aiops/mcp_server.py over MCP, and
- local AIops action tools (scan, explain, re-analyze, apply/reject fixes, daemon control).

Safety: `apply_fix` always stops and asks the human in the terminal ("Apply this fix?
[y/N]") before touching the cluster. That prompt is code, not the model, so the LLM can
never approve a change on its own -- whatever it says.
"""

from __future__ import annotations

import json
import os
import re
import sys

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_ollama import ChatOllama

from aiops import actions, daemon, engine, sop
from aiops.config import PROJECT_DIR, Settings
from aiops.guardrails import PROMPT_RULES, audit, redact
from aiops.state import StateStore

SYSTEM = """You are AIops, an SRE agent for a local kind Kubernetes cluster. The user talks \
in plain English; you act by calling tools, then answer briefly (a few lines) with real data.

Tool guide:
- "check / scan / anything broken?" -> scan_cluster (use deep_analysis=true only if the user \
asks for AI/deep analysis; it takes minutes).
- "what's wrong / list problems / incidents" -> list_incidents.
- "why / explain / details of <incident>" -> explain_incident.
- "fix it / apply / approve" -> apply_fix with the incident id. The human confirms in the \
terminal; report exactly what the tool returned. Never claim a fix was applied unless the \
tool result says applied=true.
- "don't fix / reject" -> reject_fix.
- Pod details, logs, events, deployments, nodes -> the kubectl read tools.
- Background monitoring -> aiops_daemon(action="status"|"start"|"stop").

Incident ids look like inc-1a2b3c; get them from list_incidents or scan_cluster, never invent \
one. If an incident has no automated fix, give the user its manual steps.

You have NO tool for Secrets, databases, shell commands, deleting namespaces/deployments/\
volumes, or editing arbitrary YAML -- if asked, refuse and explain the safe manual approach.
""" + PROMPT_RULES


def _short(inc: dict) -> dict:
    return {"id": inc["id"], "status": inc["status"], "category": inc["category"],
            "workload": f"{inc['namespace']}/{inc['workload']}",
            "root_cause": inc["rca"]["root_cause"][:300],
            "automated_fix": inc["rca"]["proposed_fix"]["tool_name"]}


def build_action_tools(settings: Settings) -> list:
    store = StateStore(settings.state_file)

    @tool
    def scan_cluster(deep_analysis: bool = False) -> str:
        """Scan the cluster for anomalies, run root-cause analysis on new ones and write SOPs.
        deep_analysis=false uses fast built-in rules (seconds); true uses the Qwen model (minutes)."""
        s = Settings(**{**settings.__dict__, "use_llm": deep_analysis})
        try:
            report = engine.run_cycle(s, store, progress=lambda m: print(f"   {m}"))
        except Exception as exc:
            return f"Scan failed: {exc}"
        active = [_short(i) for i in store.load().values() if i["status"] != "resolved"]
        return json.dumps({"anomalies_detected": report.detected, "new_incidents": report.new,
                           "resolved_now": report.resolved, "active_incidents": active})

    @tool
    def list_incidents(include_resolved: bool = False) -> str:
        """List AIops incidents (id, status, category, workload, root cause, automated fix)."""
        incs = [_short(i) for i in store.load().values()
                if include_resolved or i["status"] != "resolved"]
        return json.dumps(incs) if incs else "No active incidents."

    @tool
    def explain_incident(incident_id: str) -> str:
        """Full details of one incident: root cause, evidence, proposed fix, manual steps, SOP path."""
        try:
            inc = actions.get_incident(settings, incident_id)
        except actions.ActionError as exc:
            return str(exc)
        rca = inc["rca"]
        return redact(json.dumps({
            **_short(inc), "summary": rca["summary"], "evidence": rca.get("evidence", [])[:6],
            "proposed_fix": rca["proposed_fix"], "manual_fix_steps": rca.get("manual_fix_steps", []),
            "prevention": rca.get("prevention", []), "analysis_source": rca.get("source"),
            "sop_file": str(settings.sops_dir / sop.sop_filename(inc)),
        }))

    @tool
    def analyze_incident_with_ai(incident_id: str) -> str:
        """Re-run a deep root-cause analysis of one incident with the Qwen model (takes minutes)."""
        try:
            return json.dumps(actions.reanalyze(settings, incident_id))
        except actions.ActionError as exc:
            return str(exc)

    @tool
    def apply_fix(incident_id: str) -> str:
        """Apply an incident's proposed automated fix. The human is asked to confirm y/N in the
        terminal first; nothing changes without their yes."""
        def confirm() -> bool:
            print()
            answer = input("   >>> Apply this fix? [y/N]: ").strip().lower()
            return answer == "y"
        try:
            return json.dumps(actions.approve(settings, incident_id, confirm))
        except actions.ActionError as exc:
            return str(exc)
        except Exception as exc:
            return f"Could not apply fix: {exc}"

    @tool
    def reject_fix(incident_id: str) -> str:
        """Reject an incident's proposed fix (the human will fix it manually)."""
        try:
            return actions.reject(settings, incident_id)
        except actions.ActionError as exc:
            return str(exc)

    @tool
    def aiops_daemon(action: str = "status") -> str:
        """Control background monitoring. action: 'status', 'start' or 'stop'."""
        action = action.lower().strip()
        try:
            if action == "start":
                from aiops.cli import run_args_for_daemon
                pid = daemon.start(settings, run_args_for_daemon(settings, None))
                return f"Background daemon started (PID {pid}), scanning every {settings.interval_seconds}s."
            if action == "stop":
                pid = daemon.stop(settings)
                return f"Daemon stopped (PID {pid})." if pid else "Daemon was not running."
        except RuntimeError as exc:
            return str(exc)
        pid = daemon.read_pid(settings)
        return f"Daemon is {'running (PID ' + str(pid) + ')' if pid else 'stopped'}."

    return [scan_cluster, list_incidents, explain_incident, analyze_incident_with_ai,
            apply_fix, reject_fix, aiops_daemon]


async def _mcp_read_tools() -> list:
    client = MultiServerMCPClient({
        "aiops-k8s": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "aiops.mcp_server"],
            "env": {**os.environ, "PYTHONPATH": str(PROJECT_DIR)},
        }
    })
    tools = await client.get_tools()
    return [t for t in tools if t.name != "list_incidents"]  # local tool covers incidents


def _clean(text: str) -> str:
    """Drop Qwen3's reasoning, which it sometimes emits after tool calls even with
    reasoning disabled (a bare '...</think>' prefix or a full <think> block)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text.strip() or "(no answer)"


def _print_step(update: dict) -> None:
    for node, data in update.items():
        for msg in (data or {}).get("messages", []):
            for call in getattr(msg, "tool_calls", None) or []:
                args = ", ".join(f"{k}={v!r}" for k, v in call["args"].items())
                print(f"   ⚙  {call['name']}({args})")


async def chat(settings: Settings) -> None:
    print("Loading tools and model...")
    tools = await _mcp_read_tools() + build_action_tools(settings)
    llm = ChatOllama(model=settings.model, base_url=settings.ollama_host, temperature=0.1,
                     reasoning=False, num_ctx=settings.num_ctx)
    agent = create_agent(llm, tools, system_prompt=SYSTEM)

    print(f"\nAIops agent ready (model {settings.model}). Talk to it in plain English, e.g.:\n"
          "  - is anything broken in my cluster?\n"
          "  - why is oom-app failing?\n"
          "  - fix it\n"
          "  - show me the logs of crashloop-app\n"
          "  - start background monitoring\n"
          "Type 'exit' to quit.")

    history: list = []
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.lower() in ("exit", "quit", "bye"):
            break
        if not user_input:
            continue

        history.append({"role": "user", "content": user_input})
        audit("chat_request", text=user_input[:500])
        print("   (thinking...)")
        final = None
        try:
            async for update in agent.astream({"messages": history}, stream_mode="updates"):
                _print_step(update)
                for data in update.values():
                    if data and data.get("messages"):
                        final = data["messages"][-1]
        except Exception as exc:
            print(f"\nAIops: Sorry, something went wrong talking to the model: {exc}")
            history.pop()
            continue

        answer = _clean(final.content) if final is not None else "(no answer)"
        # Keep only user/assistant text in history so the small model's context stays small.
        history.append({"role": "assistant", "content": answer})
        history = history[-12:]
        print(f"\nAIops: {answer}")
