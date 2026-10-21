"""Step 4: resource and prompt, via the real server and the client helpers."""
import asyncio, datetime, os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "client"))
from mcp import Client, StdioServerParameters, stdio_client
from agent import get_prompt_text, load_categories

MONTH = datetime.date.today().strftime("%Y-%m")
ok = []
def check(n, c): print(("PASS  " if c else "FAIL  ") + n); ok.append(c)

async def main():
    tmp = tempfile.mkdtemp()
    env = {"EXPENSE_DB_PATH": os.path.join(tmp, "t.db"), "EXPENSE_EXPORT_DIR": os.path.join(tmp, "o")}
    params = StdioServerParameters(command=sys.executable, args=[os.path.join(ROOT, "server", "server.py")], env=env)
    with open(os.devnull, "w") as dn:
        async with Client(stdio_client(params, errlog=dn)) as c:
            res = (await c.list_resources()).resources
            check("resource listed: expense://categories", [str(r.uri) for r in res] == ["expense://categories"])
            prompts = (await c.list_prompts()).prompts
            check("prompt listed: monthly_review", [p.name for p in prompts] == ["monthly_review"])
            check("prompt declares its 'month' argument", [a.name for a in prompts[0].arguments] == ["month"])

            cats = await load_categories(c)
            check("client reads the resource", cats is not None and "food" in cats and "transport" in cats)

            text = await get_prompt_text(c, "monthly_review", {"month": MONTH})
            check("prompt expands with the month filled in", MONTH in text and "summary by category" in text)

            try:
                await get_prompt_text(c, "monthly_review", {"month": "oct"}); bad = False
            except Exception as e:
                bad = "YYYY-MM" in str(e) or "month" in str(e).lower()
                print("      (bad month ->", str(e)[:100], ")")
            check("prompt rejects an invalid month", bad)
            check("tools still all there", len((await c.list_tools()).tools) == 5)
    print(f"\n{sum(ok)}/{len(ok)} checks passed"); sys.exit(0 if all(ok) else 1)
asyncio.run(main())
