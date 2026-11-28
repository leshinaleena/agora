"""Отправка в Telegram через бота."""
from __future__ import annotations

import os
import re
from pathlib import Path

import requests

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4000  # у Telegram предел 4096 символов


def _call(method: str, **kwargs) -> dict:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    r = requests.post(API.format(token=token, method=method), timeout=60, **kwargs)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data.get('description')}")
    return data["result"]


def chat_id() -> str:
    return os.environ.get("TELEGRAM_CHAT", "@agora_greece")


def visible_len(text: str) -> int:
    """Длина так, как её считает Telegram: без HTML-тегов и адресов ссылок."""
    return len(re.sub(r"<[^>]+>", "", text).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))


def split(text: str) -> list[str]:
    """Делит длинный текст по пустым строкам, чтобы не порвать ссылку."""
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if cur and visible_len(cur) + visible_len(block) + 2 > LIMIT:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        parts.append(cur)
    return parts


def delete(message_ids: list[int]) -> None:
    """Удалить свои прошлые сообщения в канале (Telegram разрешает в течение 48 часов)."""
    for mid in message_ids:
        try:
            _call("deleteMessage", json={"chat_id": chat_id(), "message_id": mid})
        except RuntimeError as e:
            print(f"[telegram] не удалось удалить {mid}: {e}")


def send_html(text: str) -> list[int]:
    ids = []
    for part in split(text):
        res = _call(
            "sendMessage",
            json={
                "chat_id": chat_id(),
                "text": part,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            },
        )
        ids.append(res["message_id"])
    return ids


def edit_html(message_ids: list[int], text: str) -> None:
    """Заменить текст уже опубликованных сообщений (те же части, в том же порядке)."""
    parts = split(text)
    if len(parts) != len(message_ids):
        raise RuntimeError(f"частей {len(parts)}, а сообщений {len(message_ids)}: правка невозможна")
    for mid, part in zip(message_ids, parts):
        _call("editMessageText", json={
            "chat_id": chat_id(), "message_id": mid, "text": part,
            "parse_mode": "HTML", "link_preview_options": {"is_disabled": True},
        })


def send_pdf(path: Path, caption_html: str) -> None:
    with path.open("rb") as f:
        _call(
            "sendDocument",
            data={"chat_id": chat_id(), "caption": caption_html, "parse_mode": "HTML"},
            files={"document": (path.name, f, "application/pdf")},
        )
