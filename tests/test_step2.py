"""Step 2 checks: new tools, validation, CSV safety. Live FX call is reported, not required."""
import asyncio, csv, datetime, json, os, sys, tempfile

from mcp import Client, StdioServerParameters, stdio_client

SERVER = os.path.join(os.path.dirname(__file__), "..", "server", "server.py")
MONTH = datetime.date.today().strftime("%Y-%m")


def show(label, r):
    print(f"{label:<14} is_error={r.is_error!s:<5} | {r.content[0].text[:150]}")


async def main():
    tmp = tempfile.mkdtemp()
    env = {**os.environ, "EXPENSE_DB_PATH": os.path.join(tmp, "t.db"), "EXPENSE_EXPORT_DIR": os.path.join(tmp, "out")}
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)
    async with Client(stdio_client(params)) as c:
        print("TOOLS:", [t.name for t in (await c.list_tools()).tools])
        for amt, cat, note in [(500, "food", "lunch"), (120, "transport", "taxi"), (80, "food", "=HYPERLINK(\"evil\")")]:
            await c.call_tool("expense_add", {"amount": amt, "category": cat, "note": note})
        show("SUMMARY", await c.call_tool("expense_summary_by_category", {"month": MONTH}))
        show("SUMMARY empty", await c.call_tool("expense_summary_by_category", {"month": "1999-01"}))
        show("SUMMARY bad", await c.call_tool("expense_summary_by_category", {"month": "oct"}))
        r = await c.call_tool("expense_export_csv", {"month": MONTH}); show("EXPORT", r)
        path = json.loads(r.content[0].text)["file"]
        print("CSV CONTENT:"); print(open(path).read())
        show("EXPORT empty", await c.call_tool("expense_export_csv", {"month": "1999-01"}))
        show("EXPORT trav", await c.call_tool("expense_export_csv", {"month": "../../etc/passwd"}))
        show("FX bad code", await c.call_tool("currency_convert", {"amount": 10, "from_currency": "US", "to_currency": "ETB"}))
        show("FX negative", await c.call_tool("currency_convert", {"amount": -1, "from_currency": "USD", "to_currency": "ETB"}))
        show("FX same", await c.call_tool("currency_convert", {"amount": 10, "from_currency": "usd", "to_currency": "USD"}))
        show("FX LIVE", await c.call_tool("currency_convert", {"amount": 20, "from_currency": "USD", "to_currency": "ETB"}))
        show("FX bad target", await c.call_tool("currency_convert", {"amount": 20, "from_currency": "USD", "to_currency": "ZZZ"}))

asyncio.run(main())
