"""SQLite layer. All SQL is parameterized: model-supplied text never becomes SQL."""
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime

CATEGORIES = ["food", "transport", "bills", "shopping", "health", "entertainment", "other"]
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DB_PATH = os.environ.get("EXPENSE_DB_PATH", os.path.join(os.path.dirname(__file__), "expenses.db"))


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amount REAL NOT NULL CHECK (amount > 0),
                category TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                spent_on TEXT NOT NULL
            )"""
        )


def validate_month(month: str) -> str:
    if not MONTH_RE.match(month):
        raise ValueError(f"Invalid month '{month}'. Use the format YYYY-MM, for example 2026-10.")
    return month


def add_expense(amount: float, category: str, note: str, spent_on: str | None) -> dict:
    if amount <= 0:
        raise ValueError("Amount must be greater than 0.")
    category = category.strip().lower()
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category '{category}'. Valid categories: {', '.join(CATEGORIES)}.")
    if spent_on is None:
        spent_on = date.today().isoformat()
    else:
        try:
            spent_on = datetime.strptime(spent_on, "%Y-%m-%d").date().isoformat()
        except ValueError:
            raise ValueError(f"Invalid date '{spent_on}'. Use the format YYYY-MM-DD.") from None
    note = note.strip()[:200]  # cap length so a model can't dump huge text into the DB
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO expenses (amount, category, note, spent_on) VALUES (?, ?, ?, ?)",
            (round(amount, 2), category, note, spent_on),
        )
        return {"id": cur.lastrowid, "amount": round(amount, 2), "category": category,
                "note": note, "spent_on": spent_on}


def list_expenses(month: str, category: str | None) -> list[dict]:
    validate_month(month)
    sql = "SELECT id, amount, category, note, spent_on FROM expenses WHERE substr(spent_on, 1, 7) = ?"
    params: list = [month]
    if category:
        category = category.strip().lower()
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category '{category}'. Valid categories: {', '.join(CATEGORIES)}.")
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY spent_on, id"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def summary_by_category(month: str) -> dict:
    validate_month(month)
    with connect() as conn:
        rows = conn.execute(
            "SELECT category, ROUND(SUM(amount), 2) AS total, COUNT(*) AS count "
            "FROM expenses WHERE substr(spent_on, 1, 7) = ? GROUP BY category ORDER BY total DESC",
            (month,),
        ).fetchall()
    cats = [dict(r) for r in rows]
    return {"month": month, "total": round(sum(c["total"] for c in cats), 2),
            "count": sum(c["count"] for c in cats), "by_category": cats}
