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


def fetch_feed(feed: dict) -> tuple[list[dict], str]:
    """Возвращает (записи, статус). Статус — для отчёта о лентах."""
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


def collect(hours: float = 26) -> tuple[list[Item], dict[str, str]]:
    """Свежие записи из всех лент за последние `hours` часов."""
    since = time.time() - hours * 3600
    items: list[Item] = []
    report: dict[str, str] = {}
    seen_links: set[str] = set()
    seen_titles: set[str] = set()
    for feed in load_feeds():
        entries, status = fetch_feed(feed)
        report[feed["name"]] = status
        for e in entries:
            pub = _published(e)
            if pub is None or pub < since:
                continue
            link = e.get("link", "")
            link_key = link.split("?")[0]
            title = clean(e.get("title", ""))
            source = feed["name"]
            if feed.get("aggregator"):
                # В Google News издание указано в <source>, а в конце заголовка « - Издание»
                src = (e.get("source") or {}).get("title") or ""
                if src and title.endswith(" - " + src):
                    title = title[: -len(src) - 3]
                source = src or source
            key = title.lower()[:80]
            if not link or not title or link_key in seen_links or key in seen_titles:
                continue
            seen_links.add(link_key)
            seen_titles.add(key)
            text = ""
            if feed.get("full"):
                content = e.get("content") or []
                text = clean(content[0].get("value", "")) if content else ""
                if len(text) < 600:
                    text = clean(e.get("summary", "")) if len(clean(e.get("summary", ""))) > 600 else ""
            items.append(
                Item(
                    id=f"n{len(items) + 1}",
                    source=source,
                    lang=feed.get("lang", "el"),
                    title=title,
                    summary=clean(e.get("summary", ""))[:220],
                    link=link,
                    published=pub,
                    text=text,
                    via=feed["name"] if feed.get("aggregator") else "",
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
