"""Обращения к Claude: Haiku отбирает и читает, Sonnet пишет."""
from __future__ import annotations

import os
from typing import Literal, TypeVar

import anthropic
from pydantic import BaseModel

HAIKU = os.environ.get("AGORA_HAIKU", "claude-haiku-5-5")
SONNET = os.environ.get("AGORA_SONNET", "claude-sonnet-5-5")
SONNET_FALLBACK = "claude-sonnet-5"

# Цены за 1 млн токенов (ввод, вывод), долл. Нужны только для отчёта о расходе.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
}

T = TypeVar("T", bound=BaseModel)
_client: anthropic.Anthropic | None = None
spent = {"usd": 0.0, "calls": 0}


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=4)
    return _client


def _account(model: str, usage) -> None:
    pin, pout = PRICES.get(model, (2.0, 10.0))
    cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
    cost = (
        usage.input_tokens * pin + cache_w * pin * 1.25 + cache_r * pin * 0.1 + usage.output_tokens * pout
    ) / 1e6
    spent["usd"] += cost
    spent["calls"] += 1


def ask(
    model: str,
    system: str,
    user: str,
    schema: type[T],
    effort: Literal["low", "medium", "high"] = "medium",
    max_tokens: int = 16000,
) -> T | None:
    """Один запрос со структурированным ответом. None — если модель отказалась."""
    models = [model] + ([SONNET_FALLBACK] if model == SONNET else [])
    for m in models:
        with client().messages.stream(
            model=m,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort},
            output_format=schema,
        ) as stream:
            resp = stream.get_final_message()
        _account(m, resp.usage)
        print(f"[llm] {m}: ввод {resp.usage.input_tokens}, вывод {resp.usage.output_tokens}, "
              f"итого ${spent['usd']:.4f}", flush=True)
        if resp.stop_reason == "refusal":
            print(f"[llm] {m} отказался отвечать, пробую запасную модель")
            continue
        if resp.stop_reason == "max_tokens":
            print(f"[llm] {m}: ответ обрезан по max_tokens")
            return None
        return resp.parsed_output
    return None


# ---------- Схемы ответов ----------

Rubric = Literal[
    "politics", "neighbors", "eu_world", "migration", "security", "economy", "society", "mood", "russia", "athens"
]


class Story(BaseModel):
    working_title: str  # рабочее название сюжета по-русски
    rubric: Rubric
    importance: int  # 1–5
    item_ids: list[str]  # номера заметок из списка, лучшие первыми


class Selection(BaseModel):
    stories: list[Story]


class Notes(BaseModel):
    facts: list[str]  # факты, цифры, цитаты по-русски, каждый со ссылкой на издание в скобках
    disagreement: str  # чем расходятся издания, если расходятся; иначе пустая строка
    dates_ahead: list[str]  # будущие даты и события из текстов: «25 ноября: забастовка ADEDY»
    is_new: bool  # есть ли в сюжете новое по сравнению со вчерашней сводкой


class MainStory(BaseModel):
    headline: str
    lead: str  # 1–2 предложения: суть
    details: str  # подробности, 4–8 предложений, уходят под «ещё»
    source_ids: list[str]


class Bullet(BaseModel):
    label: str  # короткая жирная метка: «Опросы», «Кабель GSI»; может быть пустой
    text: str
    source_ids: list[str]


class BriefSection(BaseModel):
    title: Literal[
        "Политика", "Турция, Кипр и соседи", "Европа и мир", "Миграция", "Безопасность и море",
        "Экономика", "Общество", "Россия и Греция, русские в Греции",
    ]
    bullets: list[Bullet]


class Digest(BaseModel):
    main: list[MainStory]  # ровно 3 главные истории
    mood: list[Bullet]  # улица, забастовки, цены, опросы
    agenda: list[Bullet]  # что происходит сегодня
    brief: list[BriefSection]
    athens: list[Bullet]  # практическое для живущих в Афинах: транспорт, забастовки, город
    watch: list[str]  # «19 ноября: …» — за чем следим в ближайшие дни
