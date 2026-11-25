"""Учёт расходов на Claude API: каждый платный запуск дописывает строку в archive/costs.csv.

Записывается автоматически при выходе из программы (даже если она упала после платных запросов),
поэтому в журнале видно всё, что ушло с ключа через бота.
"""
from __future__ import annotations

import atexit
import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

LEDGER = Path(__file__).resolve().parent.parent / "archive" / "costs.csv"
ATHENS = ZoneInfo("Europe/Athens")


def record(kind: str, usd: float, note: str = "", day: dt.date | None = None) -> None:
    if usd <= 0:
        return
    LEDGER.parent.mkdir(exist_ok=True)
    new = not LEDGER.exists()
    with LEDGER.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "kind", "usd", "note"])
        w.writerow([day or dt.datetime.now(ATHENS).date(), kind, f"{usd:.4f}", note])


def rows() -> list[dict]:
    if not LEDGER.exists():
        return []
    with LEDGER.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def total(since: dt.date, until: dt.date) -> float:
    return sum(float(r["usd"]) for r in rows() if since <= dt.date.fromisoformat(r["date"]) <= until)


def summary(today: dt.date | None = None, current: float = 0.0) -> str:
    """Строка для понедельничного сообщения: расход за 7 дней и с начала месяца."""
    today = today or dt.datetime.now(ATHENS).date()
    # current — расход текущего запуска: в журнал он попадёт только при выходе
    week = total(today - dt.timedelta(days=6), today) + current
    month = total(today.replace(day=1), today) + current
    return f"Расход на Claude: за неделю ${week:.2f}, с 1-го числа ${month:.2f} (лимит $5 в месяц)."


def track(kind: str) -> None:
    """Вызвать в начале программы: при выходе записать, сколько потрачено за этот запуск."""
    from . import llm

    atexit.register(lambda: record(kind, llm.spent["usd"]))


if __name__ == "__main__":
    print(summary())
