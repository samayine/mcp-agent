"""Quick end-to-end check: spawn the server over stdio, list tools, call them."""
import asyncio
import os
import sys
import tempfile

from mcp import Client, StdioServerParameters, stdio_client

SERVER = os.path.join(os.path.dirname(__file__), "..", "server", "server.py")


async def main():
    tmp = tempfile.mkdtemp()
    params = StdioServerParameters(
        command=sys.executable, args=[SERVER],
        env={**os.environ, "EXPENSE_DB_PATH": os.path.join(tmp, "test.db")},
    )
    async with Client(stdio_client(params)) as c:
        tools = (await c.list_tools()).tools
        print("TOOLS:", [t.name for t in tools])
        print("SCHEMA:", tools[0].input_schema)

        r = await c.call_tool("expense_add", {"amount": 500, "category": "food", "note": "lunch"})
        print("ADD ok  -> is_error:", r.is_error, "|", r.content[0].text)

        r = await c.call_tool("expense_add", {"amount": 50, "category": "pizza"})
        print("ADD bad -> is_error:", r.is_error, "|", r.content[0].text)

        r = await c.call_tool("expense_add", {"amount": -5, "category": "food"})
        print("NEG     -> is_error:", r.is_error, "|", r.content[0].text)

        r = await c.call_tool("expense_add", {"amount": "abc", "category": "food"})
        print("TYPE    -> is_error:", r.is_error, "|", r.content[0].text[:120])

        import datetime
        month = datetime.date.today().strftime("%Y-%m")
        r = await c.call_tool("expense_list", {"month": month})
        print("LIST    -> is_error:", r.is_error, "|", r.content[0].text)

        r = await c.call_tool("expense_list", {"month": "10-2026"})
        print("BADMONTH-> is_error:", r.is_error, "|", r.content[0].text)

asyncio.run(main())
