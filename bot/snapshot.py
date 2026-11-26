"""Дневной сбор заметок (12:00, 16:00, 20:00, 0:00 по Афинам), без обращений к Claude.

Многие издания держат в ленте только 30–100 последних заметок, и к утру вчерашние дневные
и вечерние новости из ленты выпадают. Этот сбор складывает их в cache/items.jsonl,
а утренняя сводка берёт и их.
"""
from __future__ import annotations

import json
import time

from . import feeds

KEEP_HOURS = 36


def main() -> None:
    old = feeds.load_cache()
    live, report = feeds.fetch_all()
    since = time.time() - KEEP_HOURS * 3600
    by_link: dict[str, dict] = {}
    for r in old + live:  # свежая версия записи заменяет старую
        if r["published"] >= since:
            by_link[r["link"].split("?")[0]] = r
    feeds.CACHE.parent.mkdir(exist_ok=True)
    feeds.CACHE.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in by_link.values()), "utf-8")
    bad = [n for n, s in report.items() if not s.startswith("ok")]
    print(f"[snapshot] было {len(old)}, в лентах сейчас {len(live)}, в запасе {len(by_link)} заметок за {KEEP_HOURS} ч")
    if bad:
        print("[snapshot] не открылись: " + ", ".join(bad))


if __name__ == "__main__":
    main()
