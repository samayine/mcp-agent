"""The agent loop: ask the LLM -> run the tool calls it requests through MCP -> feed results back -> repeat."""
from __future__ import annotations

from collections.abc import Callable

from llm import LLMError, ToolCall  # noqa: F401  (ToolCall re-exported for convenience)

MAX_TOOL_OUTPUT_CHARS = 6000


def result_text(result) -> str:
    """Join the text blocks of an MCP CallToolResult."""
    parts = [getattr(block, "text", "") for block in (result.content or [])]
    return "\n".join(p for p in parts if p) or "(empty result)"


async def run_turn(mcp_client, llm, tools, history: list[dict], user_text: str, system: str,
                   max_steps: int = 8, on_event: Callable[[str], None] = lambda s: None) -> str:
    """Handle one user message. Mutates `history`. Always leaves history well-formed."""
    start = len(history)
    history.append({"role": "user", "text": user_text})
    known = {t.name for t in tools}
    try:
        for _ in range(max_steps):
            reply = await llm.chat(system, history, tools)
            history.append({"role": "assistant", "text": reply.text,
                            "tool_calls": reply.tool_calls, "raw": reply.raw})
            if not reply.tool_calls:
                return reply.text or "(The model returned an empty answer.)"

            for call in reply.tool_calls:
                on_event(f"-> {call.name}({call.args})")
                if call.name not in known:
                    content, is_error = (f"Unknown tool '{call.name}'. "
                                         f"Available tools: {', '.join(sorted(known))}."), True
                else:
                    try:
                        result = await mcp_client.call_tool(call.name, call.args)
                        content, is_error = result_text(result), bool(result.is_error)
                    except Exception as e:  # protocol/transport problem, not a tool-level error
                        content, is_error = f"Tool call failed: {e}", True
                if len(content) > MAX_TOOL_OUTPUT_CHARS:
                    content = content[:MAX_TOOL_OUTPUT_CHARS] + "\n...(truncated)"
                on_event(f"<- {'ERROR ' if is_error else ''}{content[:160]}")
                history.append({"role": "tool", "name": call.name, "content": content, "is_error": is_error})

        msg = f"I stopped after {max_steps} steps without reaching a final answer."
        history.append({"role": "assistant", "text": msg, "tool_calls": [], "raw": None})
        return msg
    except LLMError:
        del history[start:]  # roll back this turn so the next one starts clean
        raise


async def load_categories(mcp_client) -> str | None:
    """Resource = context the APP chooses to attach. Returns None if the server has no such resource."""
    try:
        res = await mcp_client.read_resource("expense://categories")
        return "\n".join(getattr(c, "text", "") for c in res.contents).strip() or None
    except Exception:
        return None


async def get_prompt_text(mcp_client, name: str, arguments: dict[str, str]) -> str:
    """Prompt = a template the USER picks. Returns the text of its messages."""
    res = await mcp_client.get_prompt(name, arguments)
    return "\n".join(getattr(m.content, "text", "") for m in res.messages).strip()
