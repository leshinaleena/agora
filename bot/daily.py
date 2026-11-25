"""Ежедневная сводка «Агоры».

Запуск: python -m bot.daily
Переменные окружения:
  ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN — секреты (в GitHub: Settings → Secrets).
  PUBLISH=yes    — отправить в канал; иначе только черновик в out/ и archive/.
  POST_AT=08:30  — не публиковать раньше этого времени по Афинам.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import feeds, llm, prompts, telegram

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "archive"
OUT = ROOT / "out"
ATHENS = ZoneInfo("Europe/Athens")
MOSCOW = ZoneInfo("Europe/Moscow")

MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
WEEKDAYS = "понедельник вторник среда четверг пятница суббота воскресенье".split()
BRIEF_ORDER = [
    "Политика", "Турция, Кипр и соседи", "Европа и мир", "Миграция", "Безопасность и море",
    "Экономика", "Общество", "Россия и Греция, русские в Греции",
]
WMO = {  # коды погоды Open-Meteo
    0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "облачно", 45: "туман", 48: "туман",
    51: "морось", 53: "морось", 55: "морось", 61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
    80: "ливни", 81: "ливни", 82: "сильные ливни", 95: "гроза", 96: "гроза с градом", 99: "гроза с градом",
    71: "снег", 73: "снег", 75: "сильный снег",
}


def ru_date(d: dt.date) -> str:
    return f"{WEEKDAYS[d.weekday()].capitalize()}, {d.day} {MONTHS[d.month - 1]} {d.year}"


def weather() -> str:
    """Погода в Афинах (Open-Meteo, бесплатно, без ключа)."""
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": 37.98, "longitude": 23.73, "timezone": "Europe/Athens", "forecast_days": 2,
                "current": "temperature_2m,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            },
            timeout=20,
        ).json()
        cur, d = r["current"], r["daily"]
        rain = d["precipitation_probability_max"][0] or 0
        rain_txt = "дождь маловероятен" if rain < 30 else "возможен дождь" if rain < 60 else "ожидается дождь"
        return (
            f"Афины сегодня: {WMO.get(d['weather_code'][0], 'без осадков')}, сейчас {round(cur['temperature_2m']):+d}, "
            f"днём до {round(d['temperature_2m_max'][0]):+d}, {rain_txt} ({rain}%). "
            f"Завтра до {round(d['temperature_2m_max'][1]):+d}."
        )
    except Exception as e:  # погода не должна ломать сводку
        print(f"[daily] погода недоступна: {e}")
        return ""


def previous(today: dt.date) -> dict:
    for back in range(1, 4):
        f = ARCHIVE / f"{today - dt.timedelta(days=back)}.json"
        if f.exists():
            return json.loads(f.read_text("utf-8"))
    return {}


def headlines_of(data: dict) -> list[str]:
    d = data.get("digest") or {}
    out = [m["headline"] for m in d.get("main", [])]
    for sec in d.get("brief", []):
        out += [f"{b['label']}: {b['text'][:80]}" for b in sec["bullets"] if b["label"]]
    out += [f"{b['label']}: {b['text'][:80]}" for b in d.get("mood", [])]
    return out


def build(today: dt.date) -> dict:
    items, report = feeds.collect()
    by_id = {i.id: i for i in items}
    print(f"[daily] лент: {len(report)}, свежих заметок: {len(items)}")
    for name, status in report.items():
        print(f"   {name}: {status}")
    if len(items) < 15:
        raise SystemExit("Слишком мало свежих заметок — похоже, ленты не открываются. Сводка не собрана.")

    prev = previous(today)
    yest_txt = "\n".join(f"- {t}" for t in headlines_of(prev)) or "(нет)"
    prev_watch = "\n".join(f"- {w}" for w in (prev.get("digest") or {}).get("watch", [])) or "(нет)"

    # 1. Haiku отбирает сюжеты по заголовкам
    listing = "\n".join(f"{i.id} | {i.source} | {i.title} | {i.summary[:150]}" for i in items)
    sel = llm.ask(
        llm.HAIKU, prompts.SELECT_SYSTEM,
        f"Сюжеты вчерашней сводки:\n{yest_txt}\n\nЗаметки за сутки:\n{listing}",
        llm.Selection, effort="medium",
    )
    if not sel or not sel.stories:
        raise SystemExit("Отбор сюжетов не удался.")
    stories = sorted(sel.stories, key=lambda s: -s.importance)[:18]

    # 2. Haiku читает статьи по каждому сюжету
    blocks = []
    for n, st in enumerate(stories, 1):
        chosen, seen = [], set()
        for iid in st.item_ids:
            it = by_id.get(iid)
            if it and it.source not in seen:
                chosen.append(it)
                seen.add(it.source)
            if len(chosen) == 3:
                break
        if not chosen:
            continue
        texts = "\n\n".join(f"=== {it.id} | {it.source} | {it.title}\n{feeds.read_article(it)}" for it in chosen)
        notes = llm.ask(
            llm.HAIKU, prompts.NOTES_SYSTEM,
            f"Вчерашняя сводка:\n{yest_txt}\n\nСюжет: {st.working_title}\n\n{texts}",
            llm.Notes, effort="low",
        )
        if not notes or (not notes.is_new and st.importance < 4):
            continue
        blocks.append({
            "n": n, "title": st.working_title, "rubric": st.rubric, "importance": st.importance,
            "facts": notes.facts, "disagreement": notes.disagreement, "dates_ahead": notes.dates_ahead,
            "sources": [
                {"id": it.id, "name": it.source, "url": it.link, "title": it.title, "home": it.extra.get("home", "")}
                for it in chosen
            ],
        })

    # 3. Sonnet пишет сводку
    material = "\n\n".join(
        f"Сюжет {b['n']}: {b['title']} [рубрика {b['rubric']}, важность {b['importance']}]\n"
        + "\n".join(f"- {f}" for f in b["facts"])
        + (f"\nРасхождения: {b['disagreement']}" if b["disagreement"] else "")
        + (f"\nДаты впереди: {'; '.join(b['dates_ahead'])}" if b["dates_ahead"] else "")
        + "\nИсточники: " + ", ".join(f"{s['id']} = {s['name']}" for s in b["sources"])
        for b in blocks
    )
    # Пакетом (вдвое дешевле). Срок — 08:15 по Афинам при публикации по расписанию, иначе 25 минут.
    deadline = None
    if os.environ.get("POST_AT", "00:00") != "00:00":
        hh, mm = map(int, os.environ["POST_AT"].split(":"))
        deadline = dt.datetime.now(ATHENS).replace(hour=hh, minute=mm, second=0).timestamp() - 15 * 60
    digest = llm.ask_batch(
        llm.SONNET, prompts.WRITE_SYSTEM,
        f"Дата выпуска: {ru_date(today)}.\n\nВчерашний список «Следим»:\n{prev_watch}\n\n{material}",
        llm.Digest, effort="medium", max_tokens=32000, deadline=deadline,
    )
    if not digest:
        raise SystemExit("Сводка не написана.")

    return {
        "date": str(today),
        "weather": weather(),
        "digest": digest.model_dump(),
        "stories": blocks,
        "links": {s["id"]: s for b in blocks for s in b["sources"]},
        "feeds": report,
        "cost_usd": round(llm.spent["usd"], 4),
        "calls": llm.spent["calls"],
    }


# ---------- Оформление для Telegram ----------

def _refs(data: dict, ids: list[str], blocked: list[str]) -> list[str]:
    e, seen, out = html.escape, set(), []
    for sid in ids:
        s = data["links"].get(sid)
        if not s or s["name"] in seen:
            continue
        seen.add(s["name"])
        if any(dom in s["url"] or dom in s.get("home", "") for dom in blocked):
            out.append(e(s["name"]))
        else:
            out.append(f'<a href="{e(s["url"], quote=True)}">{e(s["name"])}</a>')
    return out


def _bullet(data: dict, b: dict, blocked: list[str]) -> str:
    e = html.escape
    refs = _refs(data, b["source_ids"], blocked)
    label = f"<b>{e(b['label'])}.</b> " if b["label"] else ""
    tail = f" ({', '.join(refs)})" if refs else ""
    return f"• {label}{e(b['text'])}{tail}"


def render(data: dict) -> list[str]:
    """Возвращает список сообщений (каждое до 4000 знаков)."""
    e = html.escape
    blocked = feeds.no_link_domains()
    d = data["digest"]
    day = dt.date.fromisoformat(data["date"])
    now_a = dt.datetime.combine(day, dt.time(8, 30), ATHENS)
    head = [
        "<b>АГОРА · УТРЕННЯЯ СВОДКА</b>",
        f"{ru_date(day)} · Афины 8:30 · Москва {now_a.astimezone(MOSCOW):%-H:%M}",
    ]
    if data.get("weather"):
        head.append(data["weather"])

    parts = ["\n".join(head), "<b>ГЛАВНОЕ</b>"]
    for i, m in enumerate(d["main"], 1):
        refs = _refs(data, m["source_ids"], blocked)
        src = f"\n<i>Источники: {', '.join(refs)}</i>" if refs else ""
        parts.append(
            f"<b>{i}. {e(m['headline'])}</b>\n{e(m['lead'])}\n"
            f"<blockquote expandable>{e(m['details'])}</blockquote>{src}"
        )
    msg1 = "\n\n".join(parts)

    parts = []
    if d["mood"]:
        parts.append("<b>ЧТО ДУМАЮТ ГРЕКИ</b>\n\n" + "\n".join(_bullet(data, b, blocked) for b in d["mood"]))
    if d["agenda"]:
        parts.append("<b>СЕГОДНЯ В ПОВЕСТКЕ</b>\n\n" + "\n".join(_bullet(data, b, blocked) for b in d["agenda"]))
    secs = sorted(d["brief"], key=lambda s: BRIEF_ORDER.index(s["title"]))
    if secs:
        parts.append("<b>КОРОТКО</b>\n\n" + "\n\n".join(
            f"<b>{e(s['title'])}</b>\n" + "\n".join(_bullet(data, b, blocked) for b in s["bullets"]) for s in secs
        ))
    if d["athens"]:
        parts.append("<b>АФИНЫ СЕГОДНЯ</b>\n\n" + "\n".join(_bullet(data, b, blocked) for b in d["athens"]))
    if d["watch"]:
        parts.append("<b>СЛЕДИМ</b>\n\n" + "\n".join(f"• {e(w)}" for w in d["watch"]))

    messages: list[str] = []
    for block in [msg1] + parts:  # складываем разделы в сообщения по видимой длине
        for piece in telegram.split(block):
            if messages and telegram.visible_len(messages[-1]) + telegram.visible_len(piece) + 2 <= telegram.LIMIT:
                messages[-1] += "\n\n" + piece
            else:
                messages.append(piece)
    return messages


def wait_until(post_at: str) -> None:
    hh, mm = map(int, post_at.split(":"))
    now = dt.datetime.now(ATHENS)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    delay = (target - now).total_seconds()
    if 0 < delay < 3 * 3600:
        print(f"[daily] жду {int(delay)} с до {post_at} по Афинам")
        time.sleep(delay)


def preview_html(messages: list[str]) -> str:
    bubbles = "".join(
        f'<div class="b"><div class="t">{m}</div><div class="n">часть {i} из {len(messages)} · {len(m)} знаков</div></div>'
        for i, m in enumerate(messages, 1)
    )
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1"><title>Агора: превью</title><style>'
        "body{margin:0;background:#dfe7ee;font:16px/1.4 -apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#111}"
        ".w{max-width:560px;margin:0 auto;padding:12px}.b{background:#fff;border-radius:14px 14px 14px 4px;"
        "padding:10px 12px 6px;margin:0 0 10px;box-shadow:0 1px 1px rgba(0,0,0,.12)}"
        ".t{white-space:pre-wrap;overflow-wrap:anywhere}.t a{color:#2a7fd0;text-decoration:none}.t i{color:#5b6773}"
        ".t blockquote{margin:4px 0;padding:2px 0 2px 10px;border-left:3px solid #2a7fd0;background:#f1f6fb}"
        ".n{font-size:11px;color:#8a97a3;text-align:right}</style></head><body><div class='w'>"
        f"{bubbles}</div></body></html>"
    )


def main() -> None:
    today = dt.datetime.now(ATHENS).date()
    data = build(today)
    messages = render(data)
    ARCHIVE.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    (ARCHIVE / f"{today}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    (OUT / "digest.html").write_text("\n\n<hr>\n\n".join(messages), "utf-8")
    (OUT / "preview.html").write_text(preview_html(messages), "utf-8")
    for m in messages:
        print("\n" + m)
    total = sum(len(m) for m in messages)
    print(f"\n[daily] сообщений: {len(messages)}, знаков: {total}, запросов к Claude: {data['calls']}, "
          f"расход: ${data['cost_usd']}")
    if os.environ.get("PUBLISH") == "yes":
        wait_until(os.environ.get("POST_AT", "08:30"))
        for m in messages:
            telegram.send_html(m)
            time.sleep(1)
        print(f"[daily] опубликовано в {telegram.chat_id()}")
    else:
        print("[daily] PUBLISH не включён — в канал ничего не отправлено")


if __name__ == "__main__":
    main()
