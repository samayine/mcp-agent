# Summary note: Expense Tracker Assistant on MCP

## What I built
A chat assistant that records and analyses personal expenses from natural language, built end to end on the
Model Context Protocol. I wrote **the MCP server** (tools, a resource and a prompt) and **my own MCP client** with an
agent loop, instead of using a built-in host like Claude Desktop. The assistant runs on Gemini by default and on a
local Ollama model with a one-line config change.

## How the pieces fit
1. **Server** (`server/`). Exposes five tools: add, list and summarise expenses in SQLite, convert currency through a
   live exchange-rate API, and export a month to CSV. It also exposes a resource (`expense://categories`) and a prompt
   (`monthly_review`). It runs as a subprocess over stdio, or standalone over streamable HTTP with optional bearer
   token authentication.
2. **Client** (`client/`). Starts the server (or connects to it over HTTP), lists its tools, converts their JSON Schemas
   to the LLM's tool format, and runs the agent loop: send the conversation to the LLM; if it requests a tool, run it
   through MCP and return the result; repeat until it gives a final answer. The LLM never talks to the server; my code
   sits between them.
3. **LLM adapters** (`client/llm.py`). The loop depends on one small interface, so Gemini and Ollama are
   interchangeable adapters. This is the same idea as MCP itself: it turns M × N integrations into M + N.

A typical request, *"I paid 30 USD for shopping"*, becomes `currency_convert` (USD to ETB) followed by `expense_add`
with the converted amount, then a short natural-language reply.

## Design decisions
- **Tools return errors instead of crashing.** Failures come back as `is_error` results with a clear message
  ("Unknown category 'pizza'. Valid categories: ..."), so the model can correct its own call. I found that a plain
  exception is hidden from the model on SDK 2.x, so I raise `ToolError` deliberately.
- **Descriptions steer behaviour.** Each docstring says when to use the tool (for example, convert *before* adding).
- **Control model of the primitives.** The model picks tools, my app attaches the resource, and the user triggers the prompt (`/review`).
- **Safety.** Strict input validation, parameterised SQL, no model-controlled file paths, CSV formula-injection
  protection, secrets only in environment variables (the LLM key is never passed to the server process), logs kept off stdout,
  and loop limits (`max_steps`, output truncation, retries with backoff on rate limits).
- **Stretch goals implemented.** Streamable HTTP transport and bearer token authentication are supported via the
  `--transport http` flag and `SERVER_API_KEY` environment variable respectively.

## Verification
The server was checked with the MCP Inspector and with automated scripts. The agent loop is tested with a scripted
LLM against the real server (error recovery, unknown tools, runaway loops, rollback on failure), and the Gemini and
Ollama adapters are tested offline. The full flow was also run live with Gemini on multi-tool requests.

