"""Сообщения Лене в личку, если сводка или газета не вышли.

python -m bot.alert whoami  — ответить всем, кто недавно нажал Start у бота, их номером чата
                              (номер не печатается в журнал: репозиторий публичный).
python -m bot.alert fail <что> <файл журнала> — сообщить о сбое в чат из секрета ALERT_CHAT_ID.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import telegram

REASONS = [
    ("credit balance", "закончились деньги на ключе Claude API (лимит $5). Пополните баланс в консоли Anthropic."),
    ("rate_limit", "Claude API временно ограничил запросы. Обычно проходит само к следующему запуску."),
    ("authentication", "ключ Claude API не принят. Проверьте секрет ANTHROPIC_API_KEY."),
    ("Слишком мало свежих заметок", "почти не открылись ленты новостей (сайты недоступны)."),
    ("Unauthorized", "Telegram не принял токен бота. Проверьте секрет TELEGRAM_BOT_TOKEN."),
    ("chat not found", "бот не может писать в канал. Проверьте, что он администратор канала."),
]


def whoami() -> None:
    updates = telegram._call("getUpdates", json={"timeout": 0})
    done = set()
    for u in updates:
        chat = (u.get("message") or {}).get("chat") or {}
        if chat.get("type") == "private" and chat["id"] not in done:
            done.add(chat["id"])
            telegram._call("sendMessage", json={
                "chat_id": chat["id"],
                "text": f"Ваш номер чата для уведомлений «Агоры»: {chat['id']}\n"
                        "Добавьте его в GitHub как секрет ALERT_CHAT_ID.",
            })
    print(f"[alert] ответил в {len(done)} личных чатов")


def fail(what: str, log_path: str) -> None:
    chat = os.environ.get("ALERT_CHAT_ID")
    if not chat:
        print("[alert] ALERT_CHAT_ID не задан, сообщать некому")
        return
    log = Path(log_path).read_text("utf-8", errors="ignore") if Path(log_path).exists() else ""
    reason = next((r for key, r in REASONS if key.lower() in log.lower()), None)
    if not reason:
        tail = [line for line in log.strip().splitlines() if line.strip()][-1:] or ["причина не ясна"]
        reason = f"ошибка в программе: {tail[0][:300]}"
    run = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY', '')}" \
          f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    telegram._call("sendMessage", json={
        "chat_id": chat,
        "text": f"⚠️ «Агора»: {what} не вышла.\nПричина: {reason}\nПодробности: {run}",
        "link_preview_options": {"is_disabled": True},
    })
    print("[alert] сообщение о сбое отправлено")


def say(text: str) -> None:
    chat = os.environ.get("ALERT_CHAT_ID")
    if chat:
        telegram._call("sendMessage", json={"chat_id": chat, "text": text,
                                            "link_preview_options": {"is_disabled": True}})


if __name__ == "__main__":
    if sys.argv[1] == "whoami":
        whoami()
    elif sys.argv[1] == "say":
        say(sys.argv[2])
    else:
        fail(sys.argv[2], sys.argv[3])
