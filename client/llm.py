"""LLM adapters. The agent loop depends only on this neutral interface, so swapping
Gemini for Ollama (or anything else) never touches the loop or the MCP code.

Neutral history items (plain dicts):
  {"role": "user",      "text": str}
  {"role": "assistant", "text": str, "tool_calls": [ToolCall], "raw": provider-native or None}
  {"role": "tool",      "name": str, "content": str, "is_error": bool}
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("llm")


class LLMError(Exception):
    """Provider failure with a message safe to show the user (never contains secrets)."""


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None  # provider-native message, replayed as-is on the next call


def clean_schema(node: Any) -> Any:
    """Make an MCP/pydantic JSON Schema friendlier for LLM APIs: drop 'title' noise and
    collapse Optional (anyOf [X, null]) into X. Property NAMES are never touched."""
    if isinstance(node, list):
        return [clean_schema(x) for x in node]
    if not isinstance(node, dict):
        return node
    out: dict = {}
    for k, v in node.items():
        if k == "title":
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {name: clean_schema(s) for name, s in v.items()}
        else:
            out[k] = clean_schema(v)
    any_of = out.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2:
        non_null = [s for s in any_of if not (isinstance(s, dict) and s.get("type") == "null")]
        if len(non_null) == 1:
            out.pop("anyOf")
            out.update(non_null[0])
            if out.get("default", 0) is None:
                out.pop("default")
    return out


# ----------------------------------------------------------------------------- Gemini
class GeminiLLM:
    RETRY_CODES = (429, 500, 503)

    def __init__(self, api_key: str, model: str, max_retries: int = 4):
        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.max_retries = max_retries

    @staticmethod
    def _tools(tools) -> list:
        from google.genai import types
        decls = [types.FunctionDeclaration(
            name=t.name, description=t.description or "",
            parameters_json_schema=clean_schema(t.input_schema)) for t in tools]
        return [types.Tool(function_declarations=decls)]

    @staticmethod
    def _contents(history: list[dict]) -> list:
        from google.genai import types
        contents: list = []
        pending: list = []  # consecutive tool results are sent together in ONE user turn

        def flush():
            if pending:
                contents.append(types.Content(role="user", parts=list(pending)))
                pending.clear()

        for m in history:
            if m["role"] == "tool":
                payload = {"error": m["content"]} if m["is_error"] else {"result": m["content"]}
                pending.append(types.Part.from_function_response(name=m["name"], response=payload))
                continue
            flush()
            if m["role"] == "user":
                contents.append(types.Content(role="user", parts=[types.Part(text=m["text"])]))
            elif m.get("raw") is not None:
                contents.append(m["raw"])  # keeps Gemini's hidden "thought signatures" intact
            else:
                parts = []
                if m.get("text"):
                    parts.append(types.Part(text=m["text"]))
                for c in m.get("tool_calls", []):
                    parts.append(types.Part(function_call=types.FunctionCall(name=c.name, args=c.args)))
                contents.append(types.Content(role="model", parts=parts))
        flush()
        return contents

    @staticmethod
    def parse(resp) -> Reply:
        cand = resp.candidates[0] if resp.candidates else None
        if cand is None or cand.content is None or not cand.content.parts:
            return Reply(text="", raw=None)
        parts = cand.content.parts
        text = "".join(p.text for p in parts if p.text and not p.thought)
        calls = [ToolCall(p.function_call.name, dict(p.function_call.args or {}))
                 for p in parts if p.function_call]
        return Reply(text=text.strip(), tool_calls=calls, raw=cand.content)

    async def chat(self, system: str, history: list[dict], tools) -> Reply:
        from google.genai import errors, types
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=self._tools(tools) if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self.client.aio.models.generate_content(
                    model=self.model, contents=self._contents(history), config=config)
                return self.parse(resp)
            except (httpx.HTTPError, OSError):
                raise LLMError("Cannot reach Gemini (network problem). Check your internet connection, "
                               "or switch to the offline option: LLM_PROVIDER=ollama.") from None
            except errors.APIError as e:
                code = getattr(e, "code", None)
                if code in self.RETRY_CODES and attempt < self.max_retries:
                    delay = min(2 ** attempt * 2, 30) + random.random()
                    log.warning("Gemini %s, retrying in %.1fs (%d/%d)", code, delay, attempt + 1, self.max_retries)
                    await asyncio.sleep(delay)
                    continue
                if code == 429:
                    raise LLMError("Gemini rate limit reached. Wait a minute, or switch to a lighter model "
                                   "(GEMINI_MODEL) or to Ollama (LLM_PROVIDER=ollama).") from None
                raise LLMError(f"Gemini error {code}: {e.message}") from None
        raise LLMError("Gemini kept failing after retries.")


# ----------------------------------------------------------------------------- Ollama
class OllamaLLM:
    def __init__(self, model: str, host: str | None = None, think: bool | None = None):
        import ollama
        self.client = ollama.Client(host=host) if host else ollama.Client()
        self.model = model
        self.host = host or "http://localhost:11434"
        self.think = think  # None = don't send; set False for Qwen3-style thinking models

    @staticmethod
    def _tools(tools) -> list[dict]:
        return [{"type": "function", "function": {
            "name": t.name, "description": t.description or "",
            "parameters": clean_schema(t.input_schema)}} for t in tools]

    @staticmethod
    def _messages(system: str, history: list[dict]) -> list[dict]:
        msgs: list[dict] = [{"role": "system", "content": system}]
        for m in history:
            if m["role"] == "user":
                msgs.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                item: dict = {"role": "assistant", "content": m.get("text", "")}
                if m.get("tool_calls"):
                    item["tool_calls"] = [{"function": {"name": c.name, "arguments": c.args}}
                                          for c in m["tool_calls"]]
                msgs.append(item)
            else:
                body = ("ERROR: " + m["content"]) if m["is_error"] else m["content"]
                msgs.append({"role": "tool", "tool_name": m["name"], "content": body})
        return msgs

    async def chat(self, system: str, history: list[dict], tools) -> Reply:
        import ollama
        kwargs = {} if self.think is None else {"think": self.think}
        try:
            resp = await asyncio.to_thread(
                self.client.chat, model=self.model, messages=self._messages(system, history),
                tools=self._tools(tools) if tools else None, **kwargs)
        except ollama.ResponseError as e:
            raise LLMError(f"Ollama error ({e.status_code}): {e.error}. "
                           f"Is the model pulled? Try: ollama pull {self.model}") from None
        except ConnectionError:
            raise LLMError(f"Cannot reach Ollama at {self.host}. Check: systemctl status ollama") from None
        msg = resp.message
        calls = [ToolCall(tc.function.name, dict(tc.function.arguments or {})) for tc in (msg.tool_calls or [])]
        return Reply(text=(msg.content or "").strip(), tool_calls=calls)


# ----------------------------------------------------------------------------- factory
def make_llm():
    provider = os.environ.get("LLM_PROVIDER", "gemini").lower()
    if provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY is not set. Put it in .env (never in code).")
        return GeminiLLM(key, os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"))
    if provider == "ollama":
        think = os.environ.get("OLLAMA_THINK")
        think_val = None if think is None else think.lower() in ("1", "true", "yes")
        return OllamaLLM(os.environ.get("OLLAMA_MODEL", "qwen3:4b"),
                         os.environ.get("OLLAMA_HOST"), think_val)
    raise LLMError(f"Unknown LLM_PROVIDER '{provider}'. Use 'gemini' or 'ollama'.")
