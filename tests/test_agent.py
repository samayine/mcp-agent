"""Tests the agent loop with a scripted LLM against the REAL server, plus adapter conversions (offline)."""
import asyncio, datetime, os, sys, tempfile

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "client"))

from mcp import Client, StdioServerParameters, stdio_client
from agent import run_turn
from llm import GeminiLLM, OllamaLLM, LLMError, Reply, ToolCall, clean_schema

MONTH = datetime.date.today().strftime("%Y-%m")
PASSED = []


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    PASSED.append(cond)


class ScriptedLLM:
    """Decides what to do from the history, like a (very simple) model would."""
    def __init__(self, fn): self.fn, self.calls = fn, 0
    async def chat(self, system, history, tools):
        self.calls += 1
        return self.fn(history)


def last_tool(history): return next((m for m in reversed(history) if m["role"] == "tool"), None)
def n_assistant(history): return sum(m["role"] == "assistant" for m in history)


def recovery_script(history):
    n, t = n_assistant(history), last_tool(history)
    if n == 0:
        return Reply("", [ToolCall("expense_add", {"amount": 500, "category": "pizza", "note": "lunch"})])
    if n == 1:  # the model SEES the error text and corrects itself
        assert t["is_error"] and "Valid categories" in t["content"]
        return Reply("", [ToolCall("expense_add", {"amount": 500, "category": "food", "note": "lunch"})])
    if n == 2:
        return Reply("", [ToolCall("expense_summary_by_category", {"month": MONTH})])
    return Reply("Logged 500 for food. Month total: 500.")


async def main():
    tmp = tempfile.mkdtemp()
    env = {"EXPENSE_DB_PATH": os.path.join(tmp, "t.db"), "EXPENSE_EXPORT_DIR": os.path.join(tmp, "out")}
    params = StdioServerParameters(command=sys.executable, args=[os.path.join(ROOT, "server", "server.py")], env=env)
    events = []
    with open(os.devnull, "w") as devnull:
        async with Client(stdio_client(params, errlog=devnull)) as c:
            tools = (await c.list_tools()).tools

            # 1) error recovery through the real server
            h = []
            ans = await run_turn(c, ScriptedLLM(recovery_script), tools, h, "lunch 500", "sys", on_event=events.append)
            check("loop runs list->call->error->retry->summary->answer", ans.startswith("Logged 500"))
            check("error from server reached the model (is_error=True in history)",
                  any(m["role"] == "tool" and m["is_error"] for m in h))
            check("summary tool result contains the stored amount",
                  any(m["role"] == "tool" and not m["is_error"] and '"total": 500.0' in m["content"] for m in h))

            # 2) unknown tool name is handled, not crashed
            def unknown(history):
                return Reply("", [ToolCall("drop_database", {})]) if n_assistant(history) == 0 else Reply("ok")
            h = []
            await run_turn(c, ScriptedLLM(unknown), tools, h, "x", "sys")
            check("unknown tool -> error result with list of real tools",
                  "Unknown tool" in last_tool(h)["content"] and last_tool(h)["is_error"])

            # 3) infinite tool-calling is stopped
            looping = ScriptedLLM(lambda hist: Reply("", [ToolCall("expense_list", {"month": MONTH})]))
            h = []
            ans = await run_turn(c, looping, tools, h, "x", "sys", max_steps=3)
            check("max_steps stops a runaway loop", "stopped after 3 steps" in ans and looping.calls == 3)
            check("history stays well-formed after stopping (ends with assistant)", h[-1]["role"] == "assistant")

            # 4) LLM failure rolls the turn back
            class Boom:
                async def chat(self, *a): raise LLMError("rate limit")
            h = [{"role": "user", "text": "earlier"}, {"role": "assistant", "text": "hi", "tool_calls": [], "raw": None}]
            try:
                await run_turn(c, Boom(), tools, h, "new", "sys")
            except LLMError:
                pass
            check("LLMError rolls history back to before the turn", len(h) == 2)

            # 5) schema cleaning on the REAL tool schemas
            add = next(t for t in tools if t.name == "expense_add")
            cleaned = clean_schema(add.input_schema)
            check("clean_schema drops titles", "title" not in str(cleaned))
            check("clean_schema collapses Optional date to plain string",
                  cleaned["properties"]["date"] == {"type": "string"})
            check("clean_schema keeps required list", cleaned["required"] == ["amount", "category"])
            check("clean_schema never deletes a property named 'title'",
                  "title" in clean_schema({"type": "object", "properties": {"title": {"type": "string", "title": "T"}}})["properties"])

    # 6) Gemini adapter conversions, offline
    hist = [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "", "tool_calls": [ToolCall("a", {"x": 1}), ToolCall("b", {})], "raw": None},
        {"role": "tool", "name": "a", "content": "ra", "is_error": False},
        {"role": "tool", "name": "b", "content": "boom", "is_error": True},
        {"role": "assistant", "text": "done", "tool_calls": [], "raw": None},
    ]
    contents = GeminiLLM._contents(hist)
    check("gemini: roles are user/model/user/model", [c.role for c in contents] == ["user", "model", "user", "model"])
    check("gemini: two tool results grouped in ONE user turn", len(contents[2].parts) == 2)
    check("gemini: error result sent under 'error' key", contents[2].parts[1].function_response.response == {"error": "boom"})
    tools_obj = GeminiLLM._tools(tools)
    check("gemini: declares all 5 tools", len(tools_obj[0].function_declarations) == 5)

    from google.genai import types
    fake = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[
        types.Part(text="thinking...", thought=True),
        types.Part(function_call=types.FunctionCall(name="expense_add", args={"amount": 5, "category": "food"}))]))])
    r = GeminiLLM.parse(fake)
    check("gemini: parse extracts tool call and ignores thought text",
          r.text == "" and r.tool_calls[0].name == "expense_add" and r.tool_calls[0].args["amount"] == 5)
    check("gemini: parse handles an empty/blocked response", GeminiLLM.parse(types.GenerateContentResponse()).text == "")

    # 7) Ollama adapter conversions, offline
    msgs = OllamaLLM._messages("SYS", hist)
    check("ollama: system first, tool role uses tool_name", msgs[0]["role"] == "system" and msgs[3]["tool_name"] == "a")
    check("ollama: error results are prefixed", msgs[4]["content"].startswith("ERROR: "))
    check("ollama: assistant tool_calls shape", msgs[2]["tool_calls"][0]["function"] == {"name": "a", "arguments": {"x": 1}})
    ot = OllamaLLM._tools(tools)
    check("ollama: tool format", ot[0]["type"] == "function" and "parameters" in ot[0]["function"])

    # 8) Gemini retry / failure handling (fake client, no network)
    import types as _t, httpx, llm as llm_mod
    from google.genai import errors as gerrors
    ok_resp = types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text="hello")]))])

    def make(behaviours):
        g = GeminiLLM("fake-key", "m", max_retries=2)
        seq = list(behaviours)
        async def gen(**kw):
            b = seq.pop(0)
            if isinstance(b, Exception): raise b
            return b
        g.client = _t.SimpleNamespace(aio=_t.SimpleNamespace(models=_t.SimpleNamespace(generate_content=gen)))
        return g, seq

    async def nosleep(_): pass
    llm_mod.asyncio.sleep = nosleep   # make retries instant for the test
    e429 = gerrors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})

    g, left = make([e429, e429, ok_resp])
    r = await g.chat("s", [{"role": "user", "text": "x"}], tools)
    check("gemini: retries 429 twice then succeeds", r.text == "hello" and not left)

    g, _ = make([e429, e429, e429])
    try: await g.chat("s", [{"role": "user", "text": "x"}], tools); msg = ""
    except LLMError as e: msg = str(e)
    check("gemini: persistent 429 -> friendly rate-limit message", "rate limit" in msg.lower())

    g, _ = make([httpx.ConnectError("down")])
    try: await g.chat("s", [{"role": "user", "text": "x"}], tools); msg = ""
    except LLMError as e: msg = str(e)
    check("gemini: network failure -> friendly message, no traceback, no key", "network" in msg.lower() and "fake-key" not in msg)

    e400 = gerrors.ClientError(400, {"error": {"message": "bad request", "status": "INVALID_ARGUMENT"}})
    g, left = make([e400, ok_resp])
    try: await g.chat("s", [{"role": "user", "text": "x"}], tools); msg = ""
    except LLMError as e: msg = str(e)
    check("gemini: 400 is NOT retried", "400" in msg and len(left) == 1)

    print(f"\n{sum(PASSED)}/{len(PASSED)} checks passed")
    sys.exit(0 if all(PASSED) else 1)

asyncio.run(main())
