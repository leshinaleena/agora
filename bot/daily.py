"""Ежедневная сводка «Агоры».

Запуск: python -m bot.daily
Переменные окружения:
  ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN — секреты (в GitHub: Settings → Secrets).
  PUBLISH=yes   — отправить в канал; иначе только черновик в out/ и archive/.
  POST_AT=05:30 — не публиковать раньше этого времени UTC (05:30 UTC = 8:30 по Москве).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import time
from pathlib import Path

from . import feeds, llm, prompts, telegram

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "archive"
OUT = ROOT / "out"
MSK = dt.timezone(dt.timedelta(hours=3))

RUBRICS = {
    "politics": "Политика",
    "society": "Общество",
    "economy": "Экономика",
    "world": "Греция и мир",
    "mood": "Настроения",
    "russia": "Греция и Россия",
}
ORDER = ["politics", "world", "economy", "society", "mood", "russia"]
MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
WEEKDAYS = "понедельник вторник среда четверг пятница суббота воскресенье".split()


def ru_date(d: dt.date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}, {WEEKDAYS[d.weekday()]}"


def previous_titles(today: dt.date) -> list[str]:
    for back in range(1, 4):
        f = ARCHIVE / f"{today - dt.timedelta(days=back)}.json"
        if f.exists():
            data = json.loads(f.read_text("utf-8"))
            return [i["headline"] for i in data["digest"]["items"]]
    return []


def build(today: dt.date) -> dict:
    items, report = feeds.collect()
    by_id = {i.id: i for i in items}
    print(f"[daily] лент: {len(report)}, свежих заметок: {len(items)}")
    for name, status in report.items():
        print(f"   {name}: {status}")
    if len(items) < 15:
        raise SystemExit("Слишком мало свежих заметок — похоже, ленты не открываются. Сводка не собрана.")

    yesterday = previous_titles(today)
    yest_txt = "\n".join(f"- {t}" for t in yesterday) or "(нет)"

    # 1. Haiku отбирает сюжеты по заголовкам
    listing = "\n".join(f"{i.id} | {i.source} | {i.title} | {i.summary[:150]}" for i in items)
    sel = llm.ask(
        llm.HAIKU,
        prompts.SELECT_SYSTEM,
        f"Сюжеты вчерашней сводки:\n{yest_txt}\n\nЗаметки за сутки:\n{listing}",
        llm.Selection,
        effort="medium",
    )
    if not sel or not sel.stories:
        raise SystemExit("Отбор сюжетов не удался.")
    stories = sorted(sel.stories, key=lambda s: -s.importance)[:10]

    # 2. Haiku читает статьи по каждому сюжету
    notes_blocks = []
    for n, st in enumerate(stories, 1):
        chosen, sources_seen = [], set()
        for iid in st.item_ids:
            it = by_id.get(iid)
            if it and it.source not in sources_seen:
                chosen.append(it)
                sources_seen.add(it.source)
            if len(chosen) == 3:
                break
        if not chosen:
            continue
        texts = "\n\n".join(
            f"=== {it.id} | {it.source} | {it.title}\n{feeds.read_article(it)}" for it in chosen
        )
        notes = llm.ask(
            llm.HAIKU,
            prompts.NOTES_SYSTEM,
            f"Вчерашняя сводка:\n{yest_txt}\n\nСюжет: {st.working_title}\n\n{texts}",
            llm.Notes,
            effort="low",
        )
        if not notes or (not notes.is_new and st.importance < 4):
            continue
        src = ", ".join(f"{it.id} = {it.source}" for it in chosen)
        notes_blocks.append(
            {
                "n": n,
                "title": st.working_title,
                "rubric": st.rubric,
                "importance": st.importance,
                "facts": notes.facts,
                "disagreement": notes.disagreement,
                "sources": [{"id": it.id, "name": it.source, "url": it.link, "title": it.title} for it in chosen],
                "src_line": src,
            }
        )

    # 3. Sonnet пишет сводку
    material = "\n\n".join(
        f"Сюжет {b['n']}: {b['title']} [рубрика {b['rubric']}, важность {b['importance']}]\n"
        + "\n".join(f"- {f}" for f in b["facts"])
        + (f"\nРасхождения: {b['disagreement']}" if b["disagreement"] else "")
        + f"\nИсточники: {b['src_line']}"
        for b in notes_blocks
    )
    digest = llm.ask(
        llm.SONNET,
        prompts.WRITE_SYSTEM,
        f"Дата выпуска: {ru_date(today)}.\n\n{material}",
        llm.Digest,
        effort="medium",
    )
    if not digest:
        raise SystemExit("Сонет не написал сводку.")

    links = {s["id"]: s for b in notes_blocks for s in b["sources"]}
    return {
        "date": str(today),
        "digest": digest.model_dump(),
        "stories": [{k: v for k, v in b.items() if k != "src_line"} for b in notes_blocks],
        "links": links,
        "feeds": report,
        "cost_usd": round(llm.spent["usd"], 4),
        "calls": llm.spent["calls"],
    }


def render(data: dict) -> str:
    d = dt.date.fromisoformat(data["date"])
    e = html.escape
    out = [f"<b>Агора · {ru_date(d)}</b>", f"<i>{e(data['digest']['lead'])}</i>"]
    items = data["digest"]["items"]
    for rub in ORDER:
        group = [i for i in items if i["rubric"] == rub]
        if not group:
            continue
        lines = [f"<b>{RUBRICS[rub].upper()}</b>"]
        for it in group:
            seen, refs = set(), []
            for sid in it["source_ids"]:
                s = data["links"].get(sid)
                if s and s["name"] not in seen:
                    seen.add(s["name"])
                    refs.append(f'<a href="{e(s["url"], quote=True)}">{e(s["name"])}</a>')
            tail = f" ({', '.join(refs)})" if refs else ""
            lines.append(f"▪️ <b>{e(it['headline'])}.</b> {e(it['body'])}{tail}")
        out.append("\n\n".join(lines))
    return "\n\n".join(out)


def wait_until(post_at: str) -> None:
    hh, mm = map(int, post_at.split(":"))
    now = dt.datetime.now(dt.timezone.utc)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    delay = (target - now).total_seconds()
    if 0 < delay < 3600:
        print(f"[daily] жду {int(delay)} с до {post_at} UTC")
        time.sleep(delay)


def main() -> None:
    today = dt.datetime.now(MSK).date()
    data = build(today)
    text = render(data)
    ARCHIVE.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    (ARCHIVE / f"{today}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    (OUT / "digest.html").write_text(text, "utf-8")
    print("\n" + text + "\n")
    print(f"[daily] знаков: {len(text)}, запросов к Claude: {data['calls']}, расход: ${data['cost_usd']}")
    if os.environ.get("PUBLISH") == "yes":
        wait_until(os.environ.get("POST_AT", "05:30"))
        telegram.send_html(text)
        print(f"[daily] опубликовано в {telegram.chat_id()}")
    else:
        print("[daily] PUBLISH не включён — в канал ничего не отправлено")


if __name__ == "__main__":
    main()
