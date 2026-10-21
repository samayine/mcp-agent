"""Check the Gemini key works, then check the model can request a tool call.
Usage: python tests/test_gemini.py [model-name]
Never prints the key."""
import os
import sys

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
key = os.environ.get("GEMINI_API_KEY")
if not key:
    sys.exit("GEMINI_API_KEY not found. Check that .env is in the folder you run this from.")

client = genai.Client(api_key=key)

# 1) Which models can this key use for generateContent?
models = [m.name.removeprefix("models/") for m in client.models.list()
          if "generateContent" in (m.supported_actions or [])]
print("Models available to your key (first 15):")
for name in models[:15]:
    print("  ", name)

model = sys.argv[1] if len(sys.argv) > 1 else None
if not model:
    sys.exit("\nRe-run with a model name from the list, e.g.: python tests/test_gemini.py <model-name>")

# 2) Plain call
r = client.models.generate_content(model=model, contents="Reply with the single word: ready")
print("\nPlain call ->", r.text.strip())

# 3) Tool-call request (same shape our MCP tools will have)
tool = types.Tool(function_declarations=[types.FunctionDeclaration(
    name="currency_convert",
    description="Convert an amount between two currencies.",
    parameters_json_schema={
        "type": "object",
        "properties": {"amount": {"type": "number"}, "from_currency": {"type": "string"},
                       "to_currency": {"type": "string"}},
        "required": ["amount", "from_currency", "to_currency"],
    },
)])
r = client.models.generate_content(
    model=model, contents="How much is 20 USD in EUR?",
    config=types.GenerateContentConfig(tools=[tool],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)),
)
print("Tool call  ->", r.function_calls)
