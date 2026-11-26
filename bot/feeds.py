"""Сбор новостей из RSS-лент и чтение текста статей."""
from __future__ import annotations

import calendar
import html
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import feedparser
import requests
import trafilatura
import yaml

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
ROOT = Path(__file__).resolve().parent.parent
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


@dataclass
class Item:
    id: str
    source: str
    lang: str
    title: str
    summary: str
    link: str
    published: float  # unix time, UTC
    text: str = ""  # полный текст, если есть
    extra: dict = field(default_factory=dict)
    via: str = ""  # через какую ленту пришло (для Google News)


def clean(raw: str) -> str:
    return SPACE_RE.sub(" ", html.unescape(TAG_RE.sub(" ", raw or ""))).strip()


def _config() -> dict:
    return yaml.safe_load((ROOT / "feeds.yaml").read_text("utf-8"))


def load_feeds() -> list[dict]:
    return _config()["feeds"]


def no_link_domains() -> list[str]:
    return _config().get("no_link_domains", [])


def fetch_telegram(channel: str) -> tuple[list[dict], str]:
    """Публичный Telegram-канал через его веб-страницу t.me/s/<канал> (без ключей и входа)."""
    try:
        r = requests.get(f"https://t.me/s/{channel}", headers={"User-Agent": UA}, timeout=25)
    except requests.RequestException as e:
        return [], f"ошибка сети: {type(e).__name__}"
    if r.status_code != 200:
        return [], f"ответ {r.status_code}"
    entries = []
    for block in r.text.split('class="tgme_widget_message_wrap')[1:]:
        post = re.search(r'data-post="([^"]+)"', block)
        when = re.search(r'<time[^>]+datetime="([^"]+)"', block)
        body = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        if not (post and when and body):
            continue
        text = clean(body.group(1).replace("<br/>", " ").replace("<br>", " "))
        t = time.strptime(when.group(1)[:19], "%Y-%m-%dT%H:%M:%S")
        entries.append({
            "title": text[:140], "summary": text, "link": f"https://t.me/{post.group(1)}",
            "published_parsed": t, "content": [{"value": text}],
        })
    if not entries:
        return [], "200, но сообщений нет (канал закрыт или без веб-версии)"
    return entries, f"ok, {len(entries)} сообщений"


def fetch_feed(feed: dict) -> tuple[list[dict], str]:
    """Возвращает (записи, статус). Статус — для отчёта о лентах."""
    if feed.get("telegram"):
        return fetch_telegram(feed["telegram"])
    try:
        r = requests.get(feed["url"], headers={"User-Agent": UA}, timeout=25)
    except requests.RequestException as e:
        return [], f"ошибка сети: {type(e).__name__}"
    if r.status_code != 200:
        return [], f"ответ {r.status_code}"
    parsed = feedparser.parse(r.content)
    if not parsed.entries:
        return [], "200, но записей нет"
    return parsed.entries, f"ok, {len(parsed.entries)} записей"


def _published(entry) -> float | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return calendar.timegm(t)
    return None


CACHE = ROOT / "cache" / "items.jsonl"  # заметки, собранные днём (snapshot.py), чтобы утром видеть все сутки


def _records(feed: dict, entries: list) -> list[dict]:
    """Записи ленты в простом виде: их же сохраняет дневной сбор."""
    out = []
    for e in entries[:100]:
        pub = _published(e)
        link = e.get("link", "")
        title = clean(e.get("title", ""))
        if pub is None or not link or not title:
            continue
        source, home = feed["name"], ""
        if feed.get("aggregator"):
            # В Google News издание указано в <source>, а в конце заголовка « - Издание»
            src = (e.get("source") or {}).get("title") or ""
            if src and title.endswith(" - " + src):
                title = title[: -len(src) - 3]
            source = src or source
            home = (e.get("source") or {}).get("href") or ""
        text = ""
        if feed.get("full"):
            content = e.get("content") or []
            text = clean(content[0].get("value", "")) if content else ""
            if len(text) < 600:
                summ = clean(e.get("summary", ""))
                text = summ if len(summ) > 600 else ""
        out.append({
            "feed": feed["name"], "source": source, "lang": feed.get("lang", "el"), "title": title,
            "summary": clean(e.get("summary", ""))[:220], "link": link, "published": pub, "text": text[:5000],
            "via": feed["name"] if feed.get("aggregator") else "", "home": home, "signal": bool(feed.get("signal")),
        })
    return out


def load_cache() -> list[dict]:
    if not CACHE.exists():
        return []
    import json
    recs = []
    for line in CACHE.read_text("utf-8").splitlines():
        try:
            recs.append(json.loads(line))
        except ValueError:
            pass
    return recs


def fetch_all() -> tuple[list[dict], dict[str, str]]:
    recs, report = [], {}
    for feed in load_feeds():
        entries, status = fetch_feed(feed)
        report[feed["name"]] = status
        recs += _records(feed, entries)
    return recs, report


def collect(hours: float = 26) -> tuple[list[Item], dict[str, str]]:
    """Свежие записи из всех лент за последние `hours` часов: то, что в лентах сейчас,
    плюс собранное днём (иначе к утру вечерние заметки из лент уже выпадают)."""
    since = time.time() - hours * 3600
    live, report = fetch_all()
    cached = load_cache()
    if cached:
        report["(собрано днём)"] = f"{len(cached)} заметок в запасе"
    items: list[Item] = []
    seen_links: set[str] = set()
    seen_titles: set[str] = set()
    for r in live + cached:
        if r["published"] < since:
            continue
        link_key = r["link"].split("?")[0]
        key = r["title"].lower()[:80]
        if link_key in seen_links or key in seen_titles:
            continue
        seen_links.add(link_key)
        seen_titles.add(key)
        items.append(
            Item(
                id=f"n{len(items) + 1}", source=r["source"], lang=r["lang"], title=r["title"],
                summary=r["summary"], link=r["link"], published=r["published"], text=r["text"], via=r["via"],
                extra={**({"home": r["home"]} if r["home"] else {}), **({"signal": True} if r["signal"] else {})},
            )
        )
    return items, report


def read_article(item: Item, max_chars: int = 5000) -> str:
    """Текст статьи: из ленты, если он там полный, иначе со страницы."""
    if len(item.text) >= 600:
        return item.text[:max_chars]
    try:
        r = requests.get(item.link, headers={"User-Agent": UA}, timeout=25)
        if r.status_code == 200:
            text = trafilatura.extract(r.text, include_comments=False, include_tables=False) or ""
            if len(text) > 300:
                return SPACE_RE.sub(" ", text)[:max_chars]
    except requests.RequestException:
        pass
    return item.summary  # не удалось открыть — хотя бы анонс
