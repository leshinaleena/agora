"""Проверка лент без обращений к Claude (бесплатно). Запуск: python -m bot.check_feeds"""
import os

from . import feeds


def main() -> None:
    items, report = feeds.collect(hours=26)
    per = {}
    for i in items:
        per[i.source] = per.get(i.source, 0) + 1
    lines = ["| Издание | Статус | Свежих за сутки |", "|---|---|---|"]
    for name, status in report.items():
        lines.append(f"| {name} | {status} | {per.get(name, 0)} |")
    ok = sum(1 for s in report.values() if s.startswith("ok"))
    lines.append(f"\nРаботают {ok} из {len(report)} лент, свежих заметок всего: {len(items)}.")
    # Проверяем, открываются ли сами статьи (по одной на издание)
    lines += ["", "| Издание | Текст статьи |", "|---|---|"]
    done = set()
    for i in items:
        if i.source in done:
            continue
        done.add(i.source)
        n = len(feeds.read_article(i))
        lines.append(f"| {i.source} | {'открывается' if n > 600 else 'только анонс'} ({n} знаков) |")
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Проверка лент\n\n" + text + "\n")


if __name__ == "__main__":
    main()
