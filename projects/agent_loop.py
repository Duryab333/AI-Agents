import os
from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_API_BASE"),
)

messages = [
    {"role": "system", "content": "You are a helpful assistant."}
]

# Replace the block below with the multi-turn loop
messages.append({"role": "user", "content": "What are three things an AI agent can do that a regular chatbot cannot?"})

questions = [
    "What is an agent?",
    "How is that different from a chatbot?",
    "Give me one example.",
]

for question in questions:
    messages.append({"role": "user", "content": question})
    while True:
        response = client.chat.completions.create(
            model="openai/gpt-4.1-mini",
            messages=messages,
        )
        finish_reason = response.choices[0].finish_reason
        if finish_reason == "stop":
            reply= response.choices[0].message.content
            print(f"Q:{question}")
            print(f"A:{reply}")
            print()
            messages.append({"role": "assistant", "content": reply})
            break
        else:
            break
