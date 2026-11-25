"""Понедельничный PDF-выпуск «Агоры» за прошедшую неделю (пн–вс).

Запуск: python -m bot.weekly
  PUBLISH=yes — отправить PDF в канал; иначе только файл в out/.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from . import feeds, llm, prompts, telegram

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "archive"
OUT = ROOT / "out"
ATHENS = ZoneInfo("Europe/Athens")
MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()


# ---------- Схема выпуска ----------

class Article(BaseModel):
    kicker: str  # надзаголовок: «ВЫБОРЫ», «ТУРЦИЯ», «ДЕНЬГИ», «ОСТРЫЙ МОМЕНТ»
    headline: str
    paragraphs: list[str]
    source_ids: list[str]


class View(BaseModel):
    label: str  # «АФИНЫ», «БРЮССЕЛЬ», «КРИТИКИ»
    headline: str
    text: str
    source_ids: list[str]


class ThreeViews(BaseModel):
    headline: str
    intro: str
    views: list[View]  # ровно 3


class Brief(BaseModel):
    label: str
    text: str
    source_ids: list[str]


class PollRow(BaseModel):
    pollster: str
    note: str  # канал и дата: «Action 24, 16 ноября»
    values: list[str]  # по столбцам parties слева направо


class Polls(BaseModel):
    headline: str
    intro: str
    parties: list[str]  # заголовки столбцов: «ND», «ЭЛАС», «ПАСОК»
    rows: list[PollRow]
    after: list[str]  # абзацы после таблицы
    source_ids: list[str]


class Statement(BaseModel):
    who_when: str  # «Яннис Лавердос, 21 ноября»
    text: str
    source_ids: list[str]


class Analysis(BaseModel):
    headline: str
    paragraphs: list[str]  # 4–6 абзацев
    source_ids: list[str]


class Issue(BaseModel):
    main: Article
    analysis: Analysis  # «Анализ недели»: что стоит за событиями, расклад сил, что дальше
    number_value: str  # «11»
    number_text: str
    open_question: str
    open_question_text: str
    quote: str
    quote_attribution: str
    three_views: ThreeViews
    inside: list[Article]  # 2–3 статьи «Внутри страны»
    inside_brief: list[Brief]
    mood: list[Article]  # 1–2: митинги, забастовки
    polls: Polls | None
    world: list[Article]  # 2–3 «Греция и мир», включая Грецию и Россию, если есть
    statements: list[Statement]
    watch: list[Brief]


WEEKLY_TASK = """
Собери выпуск по материалам недели. Правила:
• main — главная ПОЛИТИЧЕСКАЯ тема недели (власть, партии, выборы, скандалы, внешняя политика), не митинг: \
3 абзаца, всего не больше 1100 знаков (иначе не поместится на первую страницу); суть, цифры, расхождения, что дальше.
• analysis — «Анализ недели»: 4–6 абзацев о том, что стоит за главными событиями, как меняется расклад \
сил (правительство, оппозиция, улица, соседи), чего ждать. Опирайся на материалы и на раздел «Взгляд извне» \
(что пишут мировые СМИ и аналитики); мнения обязательно приписывай их авторам. Источники из «Взгляда извне» \
указывай номерами w1, w2…
• number_value/number_text — «Цифра недели»: политическое число недели (депутаты, проценты опроса, суммы скандала, \
голоса), коротко — число или число со словом («11», «30,3%»), и 1–2 предложения, что оно значит. Только событие этой недели.
• open_question — главный открытый вопрос недели и 1–2 предложения, кто что ожидает.
• quote — «Цитата недели»: дословная цитата (в переводе) и кто, когда, где сказал.
• three_views — «Одна новость, три взгляда»: событие с тремя сторонами (страны, лагеря, институты), \
у каждой — label, заголовок, 2–3 предложения и источники.
• Каждая тема — в одном месте: «Коротко», mood и statements не повторяют главную тему, анализ и опросы.
• mood не повторяет главную тему: если главная тема — митинг, в mood другие акции, забастовки и опросы.
• inside — 2–3 статьи «Внутри страны» по 2 абзаца; inside_brief — 2–4 коротких пункта.
• mood — 1–2 статьи «Улица»: митинги, забастовки, протесты.
• polls — таблица опросов недели, если опросы были (иначе null): только цифры из материалов, \
пустые ячейки — «—», рядом поясни, что это за цифры (оценка голоса или намерения).
• world — 2–3 статьи «Греция и мир»; если неделя дала сюжет «Греция и Россия», одна из статей о нём.
• statements — 4–6 «Заявлений недели», одно предложение каждое.
• watch — 4–6 пунктов «Следим дальше» на следующую неделю.
• source_ids — номера из списка источников (вида n12 или w3), только реально использованные.
Приоритет — острая политика, улица, конфликты, трагедии и внешняя политика; бытовое (ярмарки, скидки, \
праздники, выплаты) не берём. Не повторяй одну тему в двух местах. Все факты только из материалов. \
Объём выпуска — 5 страниц A4, это максимум недели.
"""

WEEK_SEARCH_TASK = """
Ты — исследователь редакции. Ежедневных сводок за эту неделю нет, поэтому материал для выпуска \
собираешь сам поиском в интернете. Сделай до 15 поисков: греческие СМИ (Kathimerini/eKathimerini, \
To Vima, Proto Thema, Efsyn, Naftemporiki, ERT), агентства (Reuters, AP, AFP), Politico Europe, \
Balkan Insight, Cyprus Mail, турецкие СМИ, а также что Россия официально говорила о Греции.
Ищи именно события указанной недели: политика и парламент, скандалы, опросы, митинги и забастовки, \
трагедии и ЧП, Турция и Кипр, ЕС, оборона, миграция, энергетика, Греция и Россия. Бытовое не ищи.
Верни по-русски сюжеты в таком виде:
Сюжет: <название> [рубрика]
- факт с цифрами, датой, цитатой (издание) URL
- …
Расхождения: <если издания подают по-разному>
Даты впереди: <будущие события>
Для каждого факта — полный URL страницы, где он найден. Только найденное; без догадок. \
Отдельным блоком в конце: «Взгляд извне» — 4–8 оценок мировых СМИ и аналитиков с URL. \
Опросы недели — с компанией, каналом, датой и цифрами по партиям.
"""

RESEARCH_TASK = """
Ты — исследователь редакции. Найди в интернете, что за указанную неделю писали о Греции мировые СМИ \
и аналитики (Reuters, AP, AFP, Politico Europe, Financial Times, Bloomberg, Euractiv, Balkan Insight, \
турецкие и кипрские СМИ, ELIAMEP, ECFR и т. п.), и что Россия официально говорила о Греции. \
Сделай не больше 8 поисков. Ищи по главным сюжетам недели (список ниже) и шире: внешняя политика, \
Турция, ЕС, оборона, миграция, кризисы.
Верни по-русски список пунктов «Взгляд извне»: каждый пункт — кто (издание, автор), когда, что \
утверждает или как оценивает, и полный URL страницы. Только то, что реально нашёл; никаких догадок. \
Не повторяй факты из сюжетов ниже, если издание не добавляет оценки или нового факта.
"""


HOSTS = {
    "ekathimerini.com": "eKathimerini", "kathimerini.gr": "Kathimerini", "tovima.com": "To Vima",
    "tovima.gr": "To Vima", "protothema.gr": "Proto Thema", "en.protothema.gr": "Proto Thema",
    "efsyn.gr": "Efsyn", "naftemporiki.gr": "Naftemporiki", "ertnews.gr": "ERT", "reuters.com": "Reuters",
    "apnews.com": "AP", "politico.eu": "Politico Europe", "balkaninsight.com": "Balkan Insight",
    "cyprus-mail.com": "Cyprus Mail", "keeptalkinggreece.com": "Keep Talking Greece", "ft.com": "Financial Times",
    "bloomberg.com": "Bloomberg", "euractiv.com": "Euractiv", "aa.com.tr": "Anadolu", "dailysabah.com": "Daily Sabah",
    "hurriyetdailynews.com": "Hürriyet Daily News", "greekreporter.com": "Greek Reporter", "tass.ru": "ТАСС",
    "ria.ru": "РИА Новости", "mid.ru": "МИД России", "euronews.com": "Euronews", "dw.com": "DW",
    "greekcitytimes.com": "Greek City Times", "avgi.gr": "Avgi", "documentonews.gr": "Documento",
    "in.gr": "in.gr", "en.parapolitika.gr": "Parapolitika", "parapolitika.gr": "Parapolitika",
    "newsminimalist.com": "Newsit", "yusufkanli.substack.com": "Юсуф Канлы", "cyprusnews.substack.com": "RIK",
    "hellasjournal.com": "Hellas Journal", "en.rua.gr": "Athens News", "greekherald.com.au": "Greek Herald",
    "thenationalherald.com": "National Herald", "politis.com.cy": "Politis", "en.politis.com.cy": "Politis",
    "iefimerida.gr": "iefimerida", "neoskosmos.com": "Neos Kosmos", "kiprinform.com": "Kiprinform", "news247.gr": "News247", "skai.gr": "Skai", "megatv.com": "Mega", "cnn.gr": "CNN Greece",
}


def links_from_urls(text: str, links: dict) -> str:
    """Заменяет URL в тексте поиска на номера w1, w2… и запоминает ссылки."""
    import re
    for url in dict.fromkeys(re.findall(r"https?://[^\s)\]>»\"]+", text)):
        url = url.rstrip(".,;")
        host = re.sub(r"^www\.", "", url.split("/")[2])
        name = HOSTS.get(host) or HOSTS.get(host.split(".", 1)[-1]) or host
        k = f"w{len([x for x in links if x.startswith('w')]) + 1}"
        links[k] = {"name": name, "url": url}
        text = text.replace(url, f"[{k} = {name}]")
    return text


def week_range(today: dt.date) -> tuple[dt.date, dt.date]:
    end = today - dt.timedelta(days=today.weekday() + 1)  # прошедшее воскресенье
    return end - dt.timedelta(days=6), end


def issue_number(start: dt.date) -> int:
    # № 2 — неделя 16–22 ноября 2026; дальше по одному в неделю
    return 2 + (start - dt.date(2026, 11, 16)).days // 7


def collect_week(start: dt.date, end: dt.date) -> tuple[str, dict]:
    parts, links, k = [], {}, 0
    d = start
    while d <= end:
        f = ARCHIVE / f"{d}.json"
        if f.exists():
            data = json.loads(f.read_text("utf-8"))
            parts.append(f"===== {d.day} {MONTHS[d.month - 1]} =====")
            for b in data.get("stories", []):
                ids = []
                for s in b["sources"]:
                    k += 1
                    nid = f"n{k}"
                    links[nid] = s
                    ids.append(f"{nid} = {s['name']}")
                parts.append(
                    f"Сюжет: {b['title']} [{b['rubric']}, важность {b['importance']}]\n"
                    + "\n".join(f"- {x}" for x in b["facts"])
                    + (f"\nРасхождения: {b['disagreement']}" if b.get("disagreement") else "")
                    + (f"\nДаты впереди: {'; '.join(b.get('dates_ahead', []))}" if b.get("dates_ahead") else "")
                    + f"\nИсточники: {', '.join(ids)}"
                )
        d += dt.timedelta(days=1)
    return "\n\n".join(parts), links


# ---------- Вёрстка ----------

def _src(links: dict, ids: list[str], blocked: list[str], inline: bool = False) -> str:
    e, seen, out = html.escape, set(), []
    for i in ids:
        s = links.get(i)
        if not s or s["name"] in seen:
            continue
        seen.add(s["name"])
        if any(dom in s["url"] or dom in s.get("home", "") for dom in blocked):
            out.append(e(s["name"]))
        else:
            out.append(f'<a href="{e(s["url"], quote=True)}">{e(s["name"])}</a>')
    if not out:
        return ""
    tag = "span" if inline else "p"
    return f'<{tag} class="src">{" · ".join(out)}</{tag}>'


ACRONYMS = {"ЕС", "США", "ООН", "НАТО", "ПАСОК", "СИРИЗА", "ЭЛАС", "КПГ", "ККЕ", "ОПЕКЕПЕ", "ND", "GSI", "ADEDY", "ГСЭЭ"}


def kicker(t: str) -> str:
    """«ГРЕЦИЯ И КИПР» → «Греция и Кипр»: разреженный капс на кириллице читается хуже."""
    if not t.isupper():
        return t
    words = [w if w.strip("«»,.") in ACRONYMS else w.lower() for w in t.split()]
    out = " ".join(words)
    return out[:1].upper() + out[1:]


def render_html(issue: Issue, links: dict, start: dt.date, end: dt.date, today: dt.date) -> str:
    import re

    def e(x: str) -> str:  # убираем служебные номера источников вроде «(w10)», если модель их оставила
        return html.escape(re.sub(r"\s?\((?:[nw]\d+[,;\s]*)+\)", "", x))
    bl = feeds.no_link_domains()
    num = issue_number(start)
    if start.month == end.month:
        dates = f"{start.day}–{end.day} {MONTHS[end.month - 1]} {end.year}"
    else:
        dates = f"{start.day} {MONTHS[start.month - 1]} – {end.day} {MONTHS[end.month - 1]} {end.year}"

    def art(a: Article, cls: str = "") -> str:
        body = "".join(f"<p>{e(p)}</p>" for p in a.paragraphs)
        return (f'<article class="{cls}"><div class="kicker">{e(kicker(a.kicker))}</div>'
                f"<h3>{e(a.headline)}</h3>{body}{_src(links, a.source_ids, bl)}</article>")

    def brief(b: Brief) -> str:
        return f"<p><b>{e(b.label)}.</b> {e(b.text)} {_src(links, b.source_ids, bl, True)}</p>"

    tv = issue.three_views
    views = "".join(
        f'<div class="view"><div class="kicker">{e(kicker(v.label))}</div><h4>{e(v.headline)}</h4>'
        f"<p>{e(v.text)}</p>{_src(links, v.source_ids, bl)}</div>" for v in tv.views
    )
    polls = ""
    if issue.polls and issue.polls.rows:
        p = issue.polls
        head = "".join(f"<th>{e(x)}</th>" for x in p.parties)
        rows = "".join(
            f"<tr><td><b>{e(r.pollster)}</b><br><small>{e(r.note)}</small></td>"
            + "".join(f'<td class="n">{e(v)}</td>' for v in r.values) + "</tr>"
            for r in p.rows
        )
        polls = (f'<article><div class="kicker">Опросы</div><h3>{e(p.headline)}</h3><p>{e(p.intro)}</p>'
                 f'<table><tr><th class="l">Опрос</th>{head}</tr>{rows}</table>'
                 + "".join(f"<p>{e(x)}</p>" for x in p.after) + _src(links, p.source_ids, bl) + "</article>")
    statements = "".join(
        f"<p><b>{e(s.who_when)}.</b> {e(s.text)} {_src(links, s.source_ids, bl, True)}</p>" for s in issue.statements
    )
    watch = "".join(f"<p><b>{e(w.label)}.</b> {e(w.text)}</p>" for w in issue.watch)
    css = (ROOT / "templates" / "weekly.css").read_text("utf-8").replace("FONTS", (ROOT / "fonts").as_uri())

    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Агора № {num} — {dates}</title><style>{css}</style></head><body>
<section class="page first">
  <div class="top"><span>№ {num}</span><span>Неделя {dates}</span><span>Афины, {today.day} {MONTHS[today.month - 1]}</span></div>
  <header><div class="masthead"><img class="logo" src="{(ROOT / 'templates' / 'agora-mark.png').as_uri()}" alt="Агора">
  <h1>АГОРА</h1></div><div class="motto"><i>Мир за неделю.<br>Без информационного шума</i>
  <small>Греция внутри и снаружи</small></div></header>
  <div class="cols2">
    <div class="left">
      <article class="lead"><div class="kicker">Главная тема</div><h2>{e(issue.main.headline)}</h2>
      {"".join(f"<p>{e(p)}</p>" for p in issue.main.paragraphs)}{_src(links, issue.main.source_ids, bl)}</article>
      <div class="box number"><div class="big">{e(issue.number_value)}</div>
        <div><div class="kicker">Цифра недели</div><p>{e(issue.number_text)}</p></div></div>
      <div class="box"><div class="kicker">Открытый вопрос</div><p class="q">{e(issue.open_question)}</p>
        <p>{e(issue.open_question_text)}</p></div>
      <div class="box sand"><div class="kicker">Цитата недели</div><p class="q">«{e(issue.quote.strip('«»'))}»</p>
        <p>{e(issue.quote_attribution)}</p></div>
    </div>
    <div class="right"><div class="kicker">Одна новость, три взгляда</div><h3>{e(tv.headline)}</h3>
      <p>{e(tv.intro)}</p>{views}</div>
  </div>
</section>
<section class="page">
  <h2 class="sec">Анализ недели</h2>
  <article class="analysis"><h3>{e(issue.analysis.headline)}</h3>
  {"".join(f"<p>{e(p)}</p>" for p in issue.analysis.paragraphs)}{_src(links, issue.analysis.source_ids, bl)}</article>
  <div class="brief"><h4>Коротко</h4>{"".join(brief(b) for b in issue.inside_brief)}</div>
</section>
<section class="page"><h2 class="sec">Внутри страны</h2>
  <div class="cols3">{"".join(art(a) for a in issue.inside)}</div>
  <div class="cols2 bottom"><div class="brief"><h4>Заявления недели</h4>{statements}</div>
  <div class="brief"><h4>Следим дальше</h4>{watch}</div></div>
</section>
<section class="page"><h2 class="sec">Что думают греки</h2>
  <div class="cols2"><div>{"".join(art(a) for a in issue.mood)}</div><div>{polls}</div></div>
</section>
<section class="page"><h2 class="sec">Греция и мир</h2>
  <div class="cols3">{"".join(art(a) for a in issue.world)}</div>
</section>
</body></html>"""


def main() -> None:
    today = dt.datetime.now(ATHENS).date()
    start, end = week_range(today)
    if os.environ.get("WEEK_START"):  # выпуск за прошлую неделю по запросу: WEEK_START=2026-11-16
        start = dt.date.fromisoformat(os.environ["WEEK_START"])
        end = start + dt.timedelta(days=6)
        today = end + dt.timedelta(days=1)  # дата выхода — понедельник после недели
    saved_issue = ARCHIVE / f"weekly-{start}.json"
    if os.environ.get("FROM_ARCHIVE") == "yes" and saved_issue.exists():
        # уже проверенный выпуск: только свёрстать и отправить, без новых запросов к Claude
        d = json.loads(saved_issue.read_text("utf-8"))
        publish(Issue.model_validate(d["issue"]), d["links"], start, end, today)
        return
    material, links = collect_week(start, end)
    outside = ""
    if not material:
        # Сводок за неделю нет (например, бот тогда ещё не работал): собираем материал поиском.
        print(f"[weekly] сводок за {start}–{end} нет, собираю материал поиском в интернете")
        saved = ARCHIVE / f"weekly-research-{start}.md"
        if saved.exists():  # поиск уже был — второй раз не платим
            found = saved.read_text("utf-8")
        else:
            # Haiku читает найденные страницы в 20 раз дешевле Sonnet; пишет выпуск всё равно Sonnet.
            found = llm.research(prompts.EDITORIAL + WEEK_SEARCH_TASK, f"Неделя {start} — {end}.",
                                 max_searches=12, model=llm.HAIKU)
            ARCHIVE.mkdir(exist_ok=True)
            saved.write_text(found, "utf-8")
        material = links_from_urls(found, links)
        if not material.strip():
            raise SystemExit(f"Нет материала за {start}–{end}: выпуск не из чего собирать.")
    else:
        titles = "\n".join(line for line in material.splitlines() if line.startswith("Сюжет: "))[:6000]
        try:
            outside = llm.research(prompts.EDITORIAL + RESEARCH_TASK, f"Неделя {start} — {end}.\n\n{titles}")
        except Exception as e:  # поиск не должен ломать выпуск
            print(f"[weekly] поиск не удался: {e}")
        outside = links_from_urls(outside, links)
    issue = llm.ask(
        llm.SONNET, prompts.WEEKLY_SYSTEM + WEEKLY_TASK,
        f"Неделя {start} — {end}.\n\n{material}\n\n===== Взгляд извне (поиск в интернете) =====\n{outside or '(нет)'}",
        Issue, effort="medium", max_tokens=48000, strict=False,
    )
    if not issue:
        raise SystemExit("Выпуск не написан.")
    (ARCHIVE / f"weekly-{start}.json").write_text(
        json.dumps({"issue": issue.model_dump(), "links": links}, ensure_ascii=False, indent=1), "utf-8")
    publish(issue, links, start, end, today)


def _first_sentences(text: str, n: int) -> str:
    import re
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(parts[:n])


def post_text(issue: Issue, start: dt.date, end: dt.date) -> str:
    """Текстовый пост к PDF: главное за неделю. Собирается из выпуска, без новых запросов к Claude."""
    e = html.escape
    num = issue_number(start)
    lines = [f"<b>АГОРА № {num} · неделя {start.day}–{end.day} {MONTHS[end.month - 1]}</b>", "",
             f"<b>{e(issue.main.headline)}</b>", e(_first_sentences(issue.main.paragraphs[0], 2)) if issue.main.paragraphs else "", "",
             f"<b>Цифра недели: {e(issue.number_value)}.</b> {e(issue.number_text)}", "",
             "<b>Ещё за неделю</b>"]
    for a in issue.inside + issue.mood + issue.world:
        lines.append(f"• {e(a.headline)}")
    lines += ["", f"<b>Открытый вопрос.</b> {e(issue.open_question)}", "", "Весь выпуск — в PDF ниже."]
    return "\n".join(lines)


def publish(issue: Issue, links: dict, start: dt.date, end: dt.date, today: dt.date) -> None:
    from weasyprint import HTML  # импорт здесь: ежедневной сводке WeasyPrint не нужен

    OUT.mkdir(exist_ok=True)
    num = issue_number(start)
    page = render_html(issue, links, start, end, today)
    (OUT / "weekly.html").write_text(page, "utf-8")
    pdf = OUT / f"agora_news_{today:%Y-%m-%d}.pdf"  # дата выхода (понедельник)
    HTML(string=page, base_url=str(ROOT)).write_pdf(pdf)
    text = post_text(issue, start, end)
    (OUT / "weekly_post.html").write_text(text, "utf-8")
    print(f"[weekly] № {num}: {pdf.name}, расход ${llm.spent['usd']:.3f}\n\n{text}")
    if os.environ.get("PUBLISH") == "yes":
        telegram.send_html(text)  # сначала текст с главным, затем файл
        telegram.send_pdf(pdf, f"<b>Агора № {num}</b> · неделя {start.day}–{end.day} {MONTHS[end.month - 1]}\n"
                               f"<i>{html.escape(issue.main.headline)}</i>")
        print(f"[weekly] опубликовано в {telegram.chat_id()}")


if __name__ == "__main__":
    main()
