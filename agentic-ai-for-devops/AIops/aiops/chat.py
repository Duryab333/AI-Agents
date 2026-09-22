"""Interactive troubleshooting chat -- the docker-agent pattern (MCP tools + LangChain
agent + ChatOllama) pointed at the kind cluster and the AIops incident store.
"""

from __future__ import annotations

import os
import sys

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_ollama import ChatOllama

from aiops.config import PROJECT_DIR, Settings

SYSTEM = ("You are AIops, a Kubernetes SRE assistant for a local kind cluster. Use your "
          "read-only tools to answer with real data. You cannot change the cluster; to apply "
          "a proposed fix, tell the user to run `aiops approve <incident-id>`. Be concise.")


async def chat(settings: Settings) -> None:
    env = {**os.environ, "PYTHONPATH": str(PROJECT_DIR)}
    client = MultiServerMCPClient({
        "aiops-k8s": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "aiops.mcp_server"],
            "env": env,
        }
    })
    tools = await client.get_tools()
    llm = ChatOllama(model=settings.model, base_url=settings.ollama_host,
                     temperature=0.2, reasoning=False, num_ctx=settings.num_ctx)
    agent = create_agent(llm, tools, system_prompt=SYSTEM)

    print(f"AIops chat (model {settings.model}). Type 'exit' to quit.")
    history: list[dict] = []
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.lower() in ("exit", "quit"):
            break
        if not user_input:
            continue
        history.append({"role": "user", "content": user_input})
        response = await agent.ainvoke({"messages": history})
        answer = response["messages"][-1].content
        history.append({"role": "assistant", "content": answer})
        print("\nAIops:", answer)
