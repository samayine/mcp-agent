"""Expense assistant: your own MCP client + agent loop.

Interactive:   python client/main.py
One-shot:      python client/main.py "I paid 20 USD for a taxi, log it"
Provider:      LLM_PROVIDER=gemini (default) | ollama   (see .env.example)
"""
import asyncio
import datetime
import os
import sys

from dotenv import load_dotenv

from mcp import Client, StdioServerParameters, stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from agent import get_prompt_text, load_categories, run_turn
from llm import LLMError, make_llm

SERVER_PATH = os.path.join(HERE, "..", "server", "server.py")


def build_system_prompt() -> str:
    today = datetime.date.today()
    currency = os.environ.get("DEFAULT_CURRENCY", "ETB").upper()
    return (
        f"You are an expense-tracking assistant. Today's date is {today.isoformat()}. "
        f"The user's main currency is {currency}.\n"
        "Rules:\n"
        "- Use the tools for every fact about expenses or exchange rates. Never invent numbers.\n"
        f"- Expenses are stored in {currency}. If the user paid in another currency, call "
        "currency_convert first, then record the converted amount with expense_add and put the "
        "original amount in the note.\n"
        f"- 'this month' means {today.strftime('%Y-%m')}. Use YYYY-MM for months and YYYY-MM-DD for dates.\n"
        "- If a tool returns an error, read it, fix your arguments and retry once. If it still fails, "
        "explain the problem plainly.\n"
        "- If a needed detail (like the amount) is missing, ask the user. Keep answers short."
    )


async def amain() -> None:
    load_dotenv()
    try:
        llm = make_llm()
    except LLMError as e:
        sys.exit(f"Setup problem: {e}")

    # The server subprocess gets ONLY what it needs. Your LLM API key is never passed to it.
    server_env = {k: os.environ[k] for k in ("EXPENSE_DB_PATH", "EXPENSE_EXPORT_DIR") if k in os.environ}
    params = StdioServerParameters(command=sys.executable, args=[SERVER_PATH], env=server_env or None)
    log_path = os.path.join(HERE, "..", "server.log")  # server logs go to a file, keeping the chat clean

    with open(log_path, "a", encoding="utf-8") as server_log:
        async with Client(stdio_client(params, errlog=server_log)) as mcp_client:
            tools = (await mcp_client.list_tools()).tools
            print(f"Connected. LLM: {type(llm).__name__} | tools: {', '.join(t.name for t in tools)}")
            system, history = build_system_prompt(), []
            cats = await load_categories(mcp_client)  # RESOURCE: the app attaches this context
            if cats:
                system += f"\nValid expense categories (from the server): {cats}"

            async def ask(text: str) -> None:
                try:
                    answer = await run_turn(mcp_client, llm, tools, history, text, system,
                                            on_event=lambda s: print("  " + s))
                    print(f"\n{answer}\n")
                except LLMError as e:
                    print(f"\n[LLM problem] {e}\n")

            if len(sys.argv) > 1:
                await ask(" ".join(sys.argv[1:]))
                return
            print("Type a message. Commands: /review [YYYY-MM]  /reset  /quit")
            while True:
                try:
                    text = (await asyncio.to_thread(input, "you> ")).strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not text:
                    continue
                if text in ("/quit", "/exit"):
                    break
                if text.startswith("/review"):  # PROMPT: a template the user picks
                    month = (text.split() + [datetime.date.today().strftime("%Y-%m")])[1]
                    try:
                        text = await get_prompt_text(mcp_client, "monthly_review", {"month": month})
                    except Exception as e:
                        print(f"Could not use that prompt: {e}")
                        continue
                if text == "/reset":
                    history.clear()
                    print("History cleared.")
                    continue
                await ask(text)


if __name__ == "__main__":
    asyncio.run(amain())
