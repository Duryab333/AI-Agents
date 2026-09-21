
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent
from langchain_ollama import ChatOllama
import asyncio
from pathlib import Path

async def main():
    base_dir = Path(__file__).resolve().parent
    mcp_server = base_dir / "mcp_server.py"

    client = MultiServerMCPClient(
        {
            "mcp-docker": {
                "transport": "stdio",
                "command": <Path to Python env execution >/python.exe",
                "args": [str(mcp_server)]
            }
        }
    )

    tools = await client.get_tools()

    llm = ChatOllama(
        model="gemma4:26b",
        temperature=0.7
    )

    agent = create_agent(llm, tools)

    while True:
        user_input = input("\nYou: ")

        if user_input.lower() in ["exit", "quit"]:
            break

        response = await agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": user_input
                    }
                ]
            }
        )

        print("\nAgent:", response["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
