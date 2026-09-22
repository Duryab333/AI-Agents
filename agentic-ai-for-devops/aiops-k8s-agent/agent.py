"""LangChain/Ollama agent: root-cause analysis and fix application.

Uses a two-phase tool split as the core safety mechanism: the RCA phase is only ever
given read-only tools, so it is structurally incapable of mutating the cluster. Applying
a fix is a separate, deterministic step (apply_fix, called from cli.py) gated on
explicit human confirmation -- never decided by the LLM.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_ollama import ChatOllama

import prompts
from models import Anomaly, ProposedFix, RCAResult

READ_TOOL_NAMES = {
    "list_pods",
    "get_pod_events",
    "get_pod_logs",
    "describe_pod",
    "get_node_status",
    "list_deployments",
}

REMEDIATION_TOOL_NAMES = {
    "restart_deployment",
    "delete_pod",
    "scale_deployment",
    "patch_resource_limits",
}


async def build_mcp_tools():
    base_dir = Path(__file__).resolve().parent
    mcp_server_path = base_dir / "mcp_server.py"

    client = MultiServerMCPClient(
        {
            "k8s": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(mcp_server_path)],
            }
        }
    )
    tools = await client.get_tools()
    read_tools = [t for t in tools if t.name in READ_TOOL_NAMES]
    remediation_tools = [t for t in tools if t.name in REMEDIATION_TOOL_NAMES]
    return read_tools, remediation_tools


def _extract_json_block(text: str) -> str:
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match:
        return match.group(1)
    raise ValueError("no JSON object found in the model's response")


async def run_rca(anomaly: Anomaly, read_tools, model: str) -> RCAResult:
    llm = ChatOllama(model=model, temperature=0.2)
    agent = create_agent(llm, read_tools)

    messages = [
        {"role": "system", "content": prompts.RCA_SYSTEM_PROMPT},
        {"role": "user", "content": prompts.build_rca_prompt(anomaly)},
    ]

    last_error: Exception | None = None
    for _ in range(2):
        response = await agent.ainvoke({"messages": messages})
        content = response["messages"][-1].content
        try:
            raw_json = _extract_json_block(content)
            return RCAResult.model_validate_json(raw_json)
        except Exception as exc:  # malformed/unparseable model output
            last_error = exc
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your previous response could not be parsed ({exc}). "
                        "Respond again with ONLY the corrected JSON code block."
                    ),
                }
            )

    return RCAResult(
        root_cause=f"RCA agent failed to produce valid structured output: {last_error}",
        evidence=[],
        prevention=[],
        proposed_fix=ProposedFix(
            tool_name="no_action",
            args={},
            rationale="Automated RCA failed; manual investigation required.",
        ),
        confidence="low",
        summary="Automated diagnosis failed after retrying -- manual review needed.",
    )


async def apply_fix(remediation_tools, proposed_fix: ProposedFix) -> dict:
    if proposed_fix.tool_name == "no_action":
        return {"success": False, "message": "no_action: nothing to apply"}

    tool = next((t for t in remediation_tools if t.name == proposed_fix.tool_name), None)
    if tool is None:
        return {
            "success": False,
            "message": f"'{proposed_fix.tool_name}' is not a whitelisted remediation tool",
        }

    raw = await tool.ainvoke(proposed_fix.args)
    return {"success": True, "raw_result": raw}
