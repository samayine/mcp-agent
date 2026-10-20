"""Expense Tracker MCP server (stdio). NEVER print() here: stdout is the protocol channel."""
import csv
import json
import logging
import os
import sys

import db
import fx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS

# Logs go to stderr only.
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("expense-server")

EXPORT_DIR = os.environ.get(
    "EXPENSE_EXPORT_DIR", os.path.join(os.path.dirname(__file__), "..", "exports"))

mcp = MCPServer("expense-tracker")
db.init_db()


@mcp.tool()
def expense_add(amount: float, category: str, note: str = "", date: str | None = None) -> str:
    """Record one expense in the user's expense database.

    Use this whenever the user says they spent money. Amount must be positive and
    already in the user's main currency (call currency_convert first if it was
    paid in another currency).

    Args:
        amount: Amount spent, greater than 0.
        category: One of: food, transport, bills, shopping, health, entertainment, other.
        note: Short description, e.g. "lunch with team".
        date: Date spent as YYYY-MM-DD. Leave empty for today.
    """
    try:
        row = db.add_expense(amount, category, note, date)
    except ValueError as e:
        raise ToolError(str(e)) from e  # message is shown to the model so it can fix the call
    log.info("added expense id=%s category=%s", row["id"], row["category"])
    return json.dumps(row)


@mcp.tool()
def expense_list(month: str, category: str | None = None) -> str:
    """List recorded expenses for one month, optionally filtered by category.

    Use this when the user asks what they spent or wants to see individual entries.

    Args:
        month: Month as YYYY-MM, e.g. 2026-10.
        category: Optional filter: food, transport, bills, shopping, health, entertainment, other.
    """
    try:
        rows = db.list_expenses(month, category)
    except ValueError as e:
        raise ToolError(str(e)) from e
    if not rows:
        return json.dumps({"month": month, "count": 0, "expenses": [], "message": "No expenses found."})
    return json.dumps({"month": month, "count": len(rows), "expenses": rows})


@mcp.tool()
def expense_summary_by_category(month: str) -> str:
    """Total spending per category for one month, plus the overall total.

    Use this when the user asks how much they spent, "my food total", or wants
    a breakdown. Prefer it over expense_list when only totals are needed.

    Args:
        month: Month as YYYY-MM, e.g. 2026-10.
    """
    try:
        return json.dumps(db.summary_by_category(month))
    except ValueError as e:
        raise ToolError(str(e)) from e


@mcp.tool()
def currency_convert(amount: float, from_currency: str, to_currency: str) -> str:
    """Convert an amount between currencies using live exchange rates.

    Use this BEFORE expense_add when the user paid in a currency other than their
    main one. Uses 3-letter ISO codes (USD, EUR, ETB, ...). Needs internet.

    Args:
        amount: Amount to convert, greater than 0.
        from_currency: Source currency code, e.g. USD.
        to_currency: Target currency code, e.g. ETB.
    """
    try:
        return json.dumps(fx.convert(amount, from_currency, to_currency))
    except fx.FxError as e:
        raise ToolError(str(e)) from e


def _csv_safe(value) -> str:
    """Stop spreadsheet formula injection: cells starting with = + - @ get a leading quote."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


@mcp.tool()
def expense_export_csv(month: str) -> str:
    """Export one month of expenses to a CSV file the user can open in a spreadsheet.

    Use this when the user asks to export, download or save their expenses.
    The file name is chosen by the server, you cannot pick a path.

    Args:
        month: Month as YYYY-MM, e.g. 2026-10.
    """
    try:
        rows = db.list_expenses(month, None)
    except ValueError as e:
        raise ToolError(str(e)) from e
    if not rows:
        raise ToolError(f"No expenses found for {month}, so there is nothing to export.")
    os.makedirs(EXPORT_DIR, exist_ok=True)
    path = os.path.abspath(os.path.join(EXPORT_DIR, f"expenses-{month}.csv"))  # month already validated
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "date", "category", "amount", "note"])
        for r in rows:
            w.writerow([r["id"], r["spent_on"], r["category"], r["amount"], _csv_safe(r["note"])])
    log.info("exported %d rows for %s", len(rows), month)
    return json.dumps({"file": path, "rows": len(rows), "month": month})


@mcp.resource("expense://categories", name="expense-categories",
              description="The valid expense categories. Read this before choosing a category.",
              mime_type="application/json")
def categories_resource() -> str:
    """Read-only context the APP attaches (not the model): the allowed category names."""
    return json.dumps({"categories": db.CATEGORIES})


@mcp.prompt()
def monthly_review(month: str) -> str:
    """Review one month of spending: totals, biggest category, unusual entries and a tip.

    Args:
        month: Month as YYYY-MM, e.g. 2026-10.
    """
    try:
        db.validate_month(month)  # prompt arguments come from the user: validate them too
    except ValueError as e:
        raise MCPError(INVALID_PARAMS, str(e)) from e  # message stays visible (a plain ValueError is masked)
    return (
        f"Review my spending for {month}. First get the summary by category and the "
        "list of entries using your tools. Then tell me: the total, my biggest category, "
        "anything that looks unusually large, and one practical tip. Keep it under 120 words."
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
