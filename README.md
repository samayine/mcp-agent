# Expense Tracker Assistant: an MCP server and a custom MCP client

A chat assistant that logs and analyses personal expenses from plain language, built end to end on the
[Model Context Protocol](https://modelcontextprotocol.io). I wrote **both sides**: an MCP server that exposes
the expense tools, and my own MCP client with an agent loop that connects an LLM (Gemini, or Ollama offline) to them.

**Use case.** For anyone who wants to track spending without filling in a spreadsheet:
*"I paid 20 USD for a taxi and 500 birr for lunch. Log both and tell me my food total this month."*

```
you> i spent 30 dollar at dinner with my friends
  -> currency_convert({'to_currency': 'ETB', 'amount': 30, 'from_currency': 'USD'})
  <- {"amount": 30.0, "from": "USD", "to": "ETB", "rate": 161.288291, "converted": 4838.65}
  -> expense_add({'category': 'food', 'note': 'dinner with my friends (30 USD)', 'amount': 4838.65})
  <- {"id": 6, "amount": 4838.65, "category": "food", ...}

I've recorded your 4838.65 ETB expense for food.
```

## How the pieces fit

```
 you ──► client/main.py ──► agent loop (client/agent.py) ──► LLM adapter (client/llm.py) ──► Gemini | Ollama
                                   │
                                   │  MCP over stdio or streamable HTTP (JSON-RPC)
                                   ▼
                          server/server.py ──► SQLite (expenses.db)
                                   │      └──► exchange-rate API (open.er-api.com)
                                   └─────────► exports/*.csv
```

The LLM never talks to the MCP server. It only *asks* for a tool call; my client runs it, so every call can be
checked, logged or limited before it executes.

## What the server exposes

| MCP primitive | Name | Controlled by | Purpose |
|---|---|---|---|
| Tool | `expense_add` | model | Record an expense |
| Tool | `expense_list` | model | List a month's entries, optionally by category |
| Tool | `expense_summary_by_category` | model | Totals per category and overall |
| Tool | `currency_convert` | model | Live exchange-rate conversion (external API) |
| Tool | `expense_export_csv` | model | Export a month to a CSV file |
| Resource | `expense://categories` | app | Valid category names; the client adds them to the system prompt |
| Prompt | `monthly_review(month)` | user | Template for a spending review; run it with `/review` in the chat |

Resources used: **a SQLite database**, **a web API** (exchange rates) and **local files** (CSV export).

## Tools and example calls

Every tool has a unique name, a description that says *when* to use it, typed inputs, and returns failures
inside the result (`is_error=true` with a readable message) so the model can fix its call and retry.

**`expense_add(amount: float, category: str, note: str = "", date: str | None)`**
```json
{"amount": 500, "category": "food", "note": "lunch"}
→ {"id": 1, "amount": 500.0, "category": "food", "note": "lunch", "spent_on": "2026-10-20"}
{"amount": 50, "category": "pizza"}
→ ERROR: Unknown category 'pizza'. Valid categories: food, transport, bills, shopping, health, entertainment, other.
```
Categories: `food, transport, bills, shopping, health, entertainment, other`.

**`expense_list(month: str, category: str | None)`**
```json
{"month": "2026-10", "category": "food"}
→ {"month": "2026-10", "count": 1, "expenses": [{"id": 1, "amount": 500.0, "category": "food", "note": "lunch", "spent_on": "2026-10-20"}]}
{"month": "10-2026"}
→ ERROR: Invalid month '10-2026'. Use the format YYYY-MM, for example 2026-10.
```

**`expense_summary_by_category(month: str)`**
```json
{"month": "2026-10"}
→ {"month": "2026-10", "total": 700.0, "count": 3, "by_category": [{"category": "food", "total": 580.0, "count": 2}, {"category": "transport", "total": 120.0, "count": 1}]}
```

**`currency_convert(amount: float, from_currency: str, to_currency: str)`**: needs internet; results cached for 1 hour.
```json
{"amount": 20, "from_currency": "USD", "to_currency": "ETB"}
→ {"amount": 20.0, "from": "USD", "to": "ETB", "rate": 161.808233, "converted": 3236.16}
{"amount": 20, "from_currency": "US", "to_currency": "ETB"}
→ ERROR: Invalid currency code 'US'. Use a 3-letter code such as USD, EUR or ETB.
```

**`expense_export_csv(month: str)`**: the server chooses the file name and folder; the model cannot pass a path.
```json
{"month": "2026-10"}
→ {"file": ".../exports/expenses-2026-10.csv", "rows": 3, "month": "2026-10"}
```

## Setup (Ubuntu / Linux)

Requires Python 3.10+ and, for the Inspector, Node.js.

```bash
git clone <repo-url> && cd mcp-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp env.example .env        # then edit .env and add your GEMINI_API_KEY
chmod 600 .env
```
Get a free Gemini key at <https://aistudio.google.com>. List the models your key can use with
`python scripts/check_gemini.py` and set `GEMINI_MODEL` in `.env`.

## Run

```bash
python client/main.py                                   # interactive chat
python client/main.py "I paid 20 USD for a taxi, log it"  # one-shot
```
Chat commands: `/review [YYYY-MM]` (uses the server's prompt), `/reset`, `/quit`.
The client prints each tool call (`->`) and result (`<-`) so you can see the agent loop working.
Server logs go to `server.log` (never to stdout, which is the protocol channel).

### Use Ollama instead of Gemini (offline)
```bash
ollama pull qwen3:4b
# in .env:
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen3:4b
OLLAMA_THINK=false
```
Choose a model with the **tools** tag. Only the currency tool needs internet; everything else, including the
LLM, works offline.

## Streamable HTTP transport

By default the client spawns the server as a subprocess over **stdio**. You can instead run the server standalone over **Streamable HTTP**:

```bash
# Terminal 1 — start the server on HTTP
python server/server.py --transport http --port 8000

# Terminal 2 — connect the client to it
MCP_TRANSPORT=http python client/main.py
```

### Bearer token authentication

Set `SERVER_API_KEY` on both sides to require a Bearer token:

```bash
# Terminal 1
SERVER_API_KEY=my-secret python server/server.py --transport http

# Terminal 2
MCP_TRANSPORT=http SERVER_API_KEY=my-secret python client/main.py
```

Unauthenticated requests receive `401 Unauthorized`.

## Test with the MCP Inspector
```bash
npx @modelcontextprotocol/inspector python server/server.py
```
Open the printed URL, then **Connect** and use the Tools, Resources and Prompts tabs. Try `expense_add` with
`category: pizza` to see an error returned to the model.

## Automated tests

| Script | Covers | Needs |
|---|---|---|
| `tests/test_server_smoke.py` | Server over stdio: list tools, success, and four error cases | nothing |
| `tests/test_tools.py` | Summary, CSV export (incl. path and formula-injection checks), amount limits, currency tool | internet for the live rate |
| `tests/test_resource_prompt.py` | Resource and prompt | nothing |
| `tests/test_agent.py` | Agent loop with a scripted LLM against the real server; Gemini and Ollama adapter conversion; retry and failure handling | nothing |
| `scripts/check_gemini.py` | *Manual, not a test:* checks your API key and that the model requests a tool call | `GEMINI_API_KEY`, internet |

```bash
for t in tests/test_*.py; do python $t; done
```

## Safety

- **Input validation:** categories come from a fixed list, months must match `YYYY-MM`, amounts must be positive, finite and below 1 billion, currency codes must be 3 letters, and notes are length-capped.
- **SQL injection:** all queries use `?` placeholders; model-supplied text never becomes SQL.
- **File safety:** the export tool takes no path, so the model cannot choose where to write. Spreadsheet formula injection is blocked by prefixing cells that start with `= + - @`.
- **Secrets:** API keys come from environment variables (`.env`, git-ignored). The server subprocess receives only `EXPENSE_DB_PATH` and `EXPENSE_EXPORT_DIR`, never the LLM key. No tool logs or returns secrets.
- **Protocol hygiene:** the stdio server never prints to stdout; logs go to stderr / `server.log`.
- **Agent loop limits:** unknown tool names are rejected, tool output is truncated, `max_steps=8` stops runaway loops, and rate-limit/network errors become clear messages.

## Project layout

```
server/   server.py (tools, resource, prompt)  db.py (SQLite + validation)  fx.py (exchange API)
client/   main.py (CLI)  agent.py (agent loop)  llm.py (Gemini and Ollama adapters)
tests/    see the table above
scripts/  check_gemini.py (manual API-key check)
.env.example  requirements.txt  README.md  SUMMARY.md  DEMO_SCRIPT.md
```

## Notes from building it

- Built on **MCP Python SDK 2.x**: the server class is `MCPServer` (formerly `FastMCP`) and the client is `Client(stdio_client(params))`.
- A plain `ValueError` inside a tool is masked from the model as "Error executing tool". Raise **`ToolError`** for failures the model should see; prompts need an `MCPError` with `INVALID_PARAMS`.
- Tool schemas generated from type hints contain `title` fields and `anyOf [X, null]` for optional arguments. `clean_schema` simplifies them defensively before they are sent to an LLM API (unit-tested, property names are never touched).
- For streamable HTTP transport, the SDK requires `AuthSettings` with `token_verifier` — even for a static key approach. The `StaticKeyVerifier` pattern wraps this cleanly.
