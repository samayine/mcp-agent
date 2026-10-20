# Expense Tracker — MCP Server & AI Agent Client

An expense-tracking assistant built on the **Model Context Protocol (MCP)**. The project has two sides:

- **MCP Server** — exposes expense CRUD, category summaries, CSV export, and live currency conversion as MCP tools, resources, and prompts over stdio.
- **Agent Client** — connects to the server, discovers its tools at runtime, and drives an LLM (Gemini or Ollama) in an agentic tool-use loop to answer natural-language queries.

## Architecture

```
┌────────────────────┐  stdio (JSON-RPC)  ┌─────────────────────┐
│   Agent Client     │◄──────────────────►│    MCP Server        │
│                    │                     │                      │
│  main.py           │                     │  server.py           │
│  agent.py (loop)   │                     │  db.py (SQLite)      │
│  llm.py (adapters) │                     │  fx.py (exchange API)│
└────────────────────┘                     └──────────────────────┘
         │
         ▼
   Gemini API / Ollama
```

**How a turn works:**
1. User types a message (e.g. *"I spent 20 USD on a taxi"*).
2. The agent sends the message + tool definitions to the LLM.
3. The LLM returns one or more tool calls (`currency_convert`, `expense_add`).
4. The agent executes each call via MCP and feeds results back to the LLM.
5. Steps 3–4 repeat until the LLM produces a final text answer.

## MCP Features Used

| Feature      | Implementation |
|:-------------|:---------------|
| **Tools**    | `expense_add`, `expense_list`, `expense_summary_by_category`, `currency_convert`, `expense_export_csv` |
| **Resources**| `expense://categories` — valid category list attached as context |
| **Prompts**  | `monthly_review` — guided spending review template |

## Setup

**Prerequisites:** Python 3.11+, pip

```bash
# 1. Clone and enter the project
git clone <repo-url> && cd <repo-name>

# 2. Create a virtual environment
python -m venv venv && source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp env.example .env
# Edit .env and add your GEMINI_API_KEY
```

### LLM Provider Options

| Provider | Config in `.env` |
|:---------|:-----------------|
| **Gemini** (default) | `LLM_PROVIDER=gemini` + set `GEMINI_API_KEY` |
| **Ollama** (offline)  | `LLM_PROVIDER=ollama` + `OLLAMA_MODEL=qwen3:4b` (run `ollama pull qwen3:4b` first) |

## Usage

```bash
# Interactive chat
python client/main.py

# One-shot query
python client/main.py "How much did I spend on food this month?"
```

### Chat Commands

| Command | Description |
|:--------|:------------|
| `/review [YYYY-MM]` | Trigger the monthly spending review prompt |
| `/reset` | Clear conversation history |
| `/quit` | Exit |

### Example Conversation

```
you> I paid 50 USD for groceries yesterday
  -> currency_convert({"amount": 50, "from_currency": "USD", "to_currency": "ETB"})
  <- {"converted": 2875.50, "rate": 57.51, ...}
  -> expense_add({"amount": 2875.50, "category": "food", "note": "groceries (50 USD)", "date": "2026-10-19"})
  <- {"id": 1, "amount": 2875.50, "category": "food", ...}

Logged ✓ — 50 USD → 2,875.50 ETB under food.

you> /review 2026-10
  -> expense_summary_by_category({"month": "2026-10"})
  ...
```

## Inspecting the Server

The server can be tested standalone using the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector python server/server.py
```

## Running Tests

```bash
python -m pytest tests/ -v
```

## Project Structure

```
├── client/
│   ├── main.py          # Entry point, chat loop, server lifecycle
│   ├── agent.py         # Tool-use agent loop (provider-agnostic)
│   └── llm.py           # LLM adapters (Gemini, Ollama) + factory
├── server/
│   ├── server.py        # MCP server: tools, resources, prompts
│   ├── db.py            # SQLite operations (parameterized queries)
│   └── fx.py            # Currency conversion (open.er-api.com)
├── tests/               # Unit and integration tests
├── env.example          # Template for .env
├── requirements.txt     # Python dependencies
└── .gitignore
```
