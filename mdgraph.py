#!/usr/bin/env python3
"""mdgraph — навигация по графу markdown-заметок (Obsidian-совместимый vault).

Зависимостей нет: только стандартная библиотека Python 3.9+.
Vault — обычная папка с .md-файлами, [[wikilinks]] и YAML frontmatter.
Любой CLI-агент (ZCode, Claude Code, Cline, Cursor) вызывает команды из шелла;
вывод — текст или JSON (--json), без привязки к конкретному агенту.

Команды:
  init [PATH]   создать скелет vault вместе с AGENTS.md
  graph         сводка графа: узлы, связи, хабы, orphan-заметки, битые ссылки
  query NOTE    заметка + соседи по ссылкам в пределах бюджета токенов
  find QUERY    поиск по имени файла, заголовку, тегам, контенту

Примеры:
  python mdgraph.py init myvault
  python mdgraph.py graph --vault myvault
  python mdgraph.py query index --depth 1 --max-tokens 4000 --vault myvault
  python mdgraph.py find auth --vault myvault
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from collections import deque
from pathlib import Path

CHARS_PER_TOKEN = 3  # консервативная оценка: кириллица «дороже» в токенах

WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]#|]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]")
TAG_RE = re.compile(r"(?<![\w/`])#([A-Za-zА-Яа-яЁё][\w-]*(?:/[\w-]+)*)")
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)

SKIP_DIRS = {".git", ".obsidian", ".trash", ".zcode", "node_modules"}
# верхнеуровневые инструкции — не часть графа знаний
SKIP_FILES = {"README.md", "AGENTS.md"}


def token_len(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def strip_code(text: str) -> str:
    text = CODE_FENCE_RE.sub(" ", text)
    return INLINE_CODE_RE.sub(" ", text)


def frontmatter_tags(text: str) -> list[str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return []
    tags: list[str] = []
    in_list = False
    for line in m.group(1).splitlines():
        s = line.strip()
        if s.startswith("tags:"):
            rest = s[5:].strip()
            if rest.startswith("["):
                rest = rest.strip("[]")
            if rest:
                tags += [t.strip().strip("'\"") for t in rest.split(",") if t.strip()]
                in_list = False
            else:
                in_list = True
        elif in_list and s.startswith("- "):
            tags.append(s[2:].strip().strip("'\""))
        elif in_list and s and not s.startswith("-"):
            in_list = False
    return tags


class Note:
    def __init__(self, root: Path, path: Path):
        self.path = path
        rel = path.relative_to(root).as_posix()
        self.key = rel[:-3] if rel.lower().endswith(".md") else rel
        self.text = path.read_text(encoding="utf-8-sig", errors="replace")
        plain = strip_code(self.text)  # wikilinks внутри кода — документация, не ссылки
        clean = WIKILINK_RE.sub(" ", plain)
        self.tags = sorted(set(frontmatter_tags(self.text)) | {t for t in TAG_RE.findall(clean)})
        self.links = [t.strip() for t in WIKILINK_RE.findall(plain)]
        m = H1_RE.search(clean)
        self.title = m.group(1).strip() if m else self.key.rsplit("/", 1)[-1]
        self.out: list[str] = []        # ключи заметок, на которые ссылается
        self.unresolved: list[str] = []  # битые [[ссылки]]
        self.back: list[str] = []       # ключи заметок, которые ссылаются сюда
        self.mtime = path.stat().st_mtime

    @property
    def tokens(self) -> int:
        return token_len(self.text)


class Vault:
    def __init__(self, root: Path):
        self.root = root
        self.notes: dict[str, Note] = {}
        self._scan()
        self._link()

    def _scan(self) -> None:
        if not self.root.is_dir():
            die(f"vault не найден: {self.root} (укажите путь через --vault)")
        for p in sorted(self.root.rglob("*.md")):
            rel = p.relative_to(self.root)
            if rel.as_posix() in SKIP_FILES:
                continue
            parts = rel.parts[:-1]
            if any(part in SKIP_DIRS or part.startswith(".") for part in parts):
                continue
            note = Note(self.root, p)
            self.notes[note.key] = note

    def resolve(self, raw: str) -> str | None:
        """[[ссылка]] → ключ заметки, либо None (битая ссылка)."""
        cand = raw.strip().replace("\\", "/").lstrip("/")
        if cand.lower().endswith(".md"):
            cand = cand[:-3]
        if not cand:
            return None
        if cand in self.notes:
            return cand
        base = cand.rsplit("/", 1)[-1].lower()
        matches = [k for k in self.notes if k.rsplit("/", 1)[-1].lower() == base]
        if matches:
            return sorted(matches, key=len)[0]  # при коллизии имён — кратчайший путь
        suffix = [k for k in self.notes if k.lower().endswith("/" + cand)]
        if len(suffix) == 1:
            return suffix[0]
        substr = [k for k in self.notes if base in k.rsplit("/", 1)[-1].lower()]
        if len(substr) == 1:  # уникальное частичное совпадение имени, напр. choose-db
            return substr[0]
        return None

    def lookup(self, raw: str) -> str:
        """Аргумент пользователя → ключ, с подсказкой при опечатке."""
        key = self.resolve(raw)
        if key:
            return key
        names = [k.rsplit("/", 1)[-1] for k in self.notes]
        close = difflib.get_close_matches(raw.rsplit("/", 1)[-1].lower(),
                                          [n.lower() for n in names], n=3, cutoff=0.5)
        hint = f"Похожие имена: {', '.join(close)}" if close else f"В vault {len(self.notes)} заметок; см. graph"
        die(f"заметка не найдена: {raw!r}. {hint}")

    def _link(self) -> None:
        for note in self.notes.values():
            for raw in note.links:
                key = self.resolve(raw)
                if key:
                    note.out.append(key)
                else:
                    note.unresolved.append(raw)
            note.out = sorted(set(note.out))
            note.unresolved = sorted(set(note.unresolved))
        for note in self.notes.values():
            for dst in note.out:
                self.notes[dst].back.append(note.key)
        for note in self.notes.values():
            note.back = sorted(set(note.back))


def clip(text: str, budget_tokens: int) -> tuple[str, int]:
    """Текст целиком, если влезает в бюджет; иначе по строкам + маркер обрезки."""
    total = token_len(text)
    if total <= budget_tokens:
        return text, total
    used, lines = 0, []
    for line in text.splitlines():
        t = token_len(line) + 1
        if used + t > budget_tokens:
            break
        lines.append(line)
        used += t
    lines += ["", f"[... обрезано: показано ~{used} из ~{total} токенов]"]
    return "\n".join(lines), used


def die(msg: str) -> None:
    print(f"mdgraph: {msg}", file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------- commands

def cmd_graph(args: argparse.Namespace) -> None:
    vault = Vault(Path(args.vault))
    edges = [{"from": src, "to": dst} for src, n in sorted(vault.notes.items()) for dst in n.out]
    unresolved = [{"from": src, "link": raw}
                  for src, n in sorted(vault.notes.items()) for raw in n.unresolved]
    orphans = [k for k, n in sorted(vault.notes.items()) if not n.out and not n.back]
    hubs = sorted(((k, len(n.back)) for k, n in vault.notes.items()),
                  key=lambda kv: (-kv[1], kv[0]))[:5]
    if args.json:
        print(json.dumps({
            "vault": str(vault.root.resolve()),
            "nodes": [{"key": k, "title": n.title, "tags": n.tags,
                       "out": n.out, "back": n.back, "tokens": n.tokens,
                       "mtime": n.mtime} for k, n in sorted(vault.notes.items())],
            "edges": edges,
            "unresolved": unresolved,
            "orphans": orphans,
        }, ensure_ascii=False, indent=2))
        return
    print(f"Vault: {vault.root.resolve()}")
    print(f"Узлов: {len(vault.notes)} | связей: {len(edges)} | "
          f"битых ссылок: {len(unresolved)} | orphan-заметок: {len(orphans)}")
    top = [f"{k} (←{d})" for k, d in hubs if d > 0]
    if top:
        print("\nХабы (больше всего входящих ссылок):")
        for line in top:
            print(f"  - {line}")
    if orphans:
        print("\nOrphan-заметки (ни ссылок, ни бэклинков):")
        for k in orphans:
            print(f"  - {k}.md")
    if unresolved:
        print("\nБитые ссылки:")
        for u in unresolved:
            print(f"  - {u['from']}.md → [[{u['link']}]]")


def cmd_query(args: argparse.Namespace) -> None:
    vault = Vault(Path(args.vault))
    start = vault.lookup(args.note)

    # BFS от стартовой заметки по ссылкам в обе стороны
    order: list[str] = []
    dist = {start: 0}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        order.append(cur)
        if dist[cur] >= args.depth:
            continue
        for nxt in vault.notes[cur].out + vault.notes[cur].back:
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                queue.append(nxt)

    print(f"Vault: {vault.root.resolve()} | глубина {args.depth} | "
          f"бюджет {args.max_tokens} ток. | узлов в выборке: {len(order)}")
    used, shown = 0, 0
    for key in order:
        note = vault.notes[key]
        remaining = args.max_tokens - used
        if remaining <= 0:
            print(f"\n--- {key}.md: пропущено, бюджет исчерпан ---")
            continue
        print(f"\n=== {key}.md (≈{note.tokens} ток.) "
              f"→{len(note.out)} ←{len(note.back)} ===")
        if note.out:
            print("→ " + ", ".join(note.out))
        if note.back:
            print("← " + ", ".join(note.back))
        print()
        body, spent = clip(note.text, remaining)
        print(body)
        used += spent
        shown += 1
    print(f"\n— показано {shown} из {len(order)} узлов, израсходовано ~{used} ток.")


def cmd_find(args: argparse.Namespace) -> None:
    vault = Vault(Path(args.vault))
    q = args.query.casefold()
    hits = []
    for key, note in sorted(vault.notes.items()):
        where = []
        if q in key.casefold() or q in note.title.casefold():
            where.append("имя/заголовок")
        if any(q in t.casefold() for t in note.tags):
            where.append("тег")
        count = strip_code(WIKILINK_RE.sub(" ", note.text)).casefold().count(q)
        if count:
            where.append(f"контент x{count}")
        if where:
            hits.append({"key": key, "title": note.title, "tags": note.tags,
                         "tokens": note.tokens, "where": where})
    if args.json:
        print(json.dumps({"vault": str(vault.root.resolve()), "query": args.query,
                          "hits": hits}, ensure_ascii=False, indent=2))
        return
    print(f"Поиск {args.query!r}: {len(hits)} совпадений из {len(vault.notes)} заметок")
    for h in hits:
        tags = f" {' '.join('#' + t for t in h['tags'])}" if h["tags"] else ""
        print(f"  - {h['key']}.md «{h['title']}» (≈{h['tokens']} ток.){tags} — "
              + ", ".join(h["where"]))


AGENTS_MD = """\
# Инструкция для AI-агентов: работа с этим vault

Vault — долгосрочная память проекта, независимая от конкретного CLI-агента.
Ниже `mdgraph` — это `python /путь/к/mdgraph.py` (подставьте свой путь).

## В начале сессии
1. `mdgraph query index --depth 1 --vault <путь>` — карта знаний и разделы.
2. По теме задачи: `mdgraph find <слово> --vault <путь>`, затем
   `mdgraph query <заметка> --depth 1 --max-tokens 4000 --vault <путь>`.

## После значимой работы
1. Обнови затронутые заметки: факты, решения, открытые вопросы.
   Пиши результат, а не транскрипт процесса.
2. Новое standalone-знание — новый файл + `[[wikilink]]` из родительской
   заметки (иначе он станет orphan и потеряется).
3. Устаревшее не удаляй молча — пометь в frontmatter `status: outdated` и объясни.

## Гигиена (периодически)
`mdgraph graph --vault <путь>` — почисти битые ссылки и разбери orphan-заметки
(влить в раздел или удалить, если пусто).

## Формат заметки
- Имя файла: `kebab-case.md`; решения: `YYYY-MM-DD-<slug>.md` в decisions/.
- YAML frontmatter: `tags`, `status`, `updated`.
- Заголовок `#` — человекочитаемое имя; `[[wikilinks]]` — на связанные заметки.
"""

INDEX_MD = """\
---
tags: [map]
---
# Карта знаний

Точка входа в vault: отсюда начинается навигация (людьми и агентами).

## Разделы
- [[projects/index|Проекты]] — активная работа: цели, состояние, вопросы
- [[decisions/index|Решения]] — принятые решения и их причины
- [[sessions/index|Сессии]] — что сделано, что осталось

## Правила
- Новая заметка получает хотя бы одну ссылку `[[wikilink]]` из родительского раздела.
- Имена файлов в kebab-case, даты — в frontmatter.
"""

PROJECTS_INDEX_MD = """\
---
tags: [map]
---
# Проекты

Одна заметка на проект: цель, текущее состояние, открытые вопросы, ссылки на решения.

- (добавьте первый проект и сослались на него отсюда)
"""

DECISIONS_INDEX_MD = """\
---
tags: [map]
---
# Решения (ADR)

Каждое решение — файл `YYYY-MM-DD-<slug>.md`: контекст → варианты → выбор → последствия.

- (пока пусто)
"""

SESSIONS_INDEX_MD = """\
---
tags: [map]
---
# Сессии

Короткие логи рабочих сессий: что сделано, что осталось. Старые — архивируем.

- (пока пусто)
"""

VAULT_README_MD = """\
# Vault базы знаний

Obsidian-совместимая папка заметок: `[[wikilinks]]` + YAML frontmatter.
Открывается в Obsidian как обычная папка (граф ссылок — бесплатно),
но не требует ничего кроме .md-файлов.

Шпаргалка (из родительской папки):
  python mdgraph.py graph --vault <эта папка>
  python mdgraph.py query index --depth 1 --vault <эта папка>
  python mdgraph.py find <слово> --vault <эта папка>

Правила ведения — в AGENTS.md (для агентов) и в index.md (для людей).
"""


def cmd_init(args: argparse.Namespace) -> None:
    root = Path(args.path)
    if root.exists() and any(root.rglob("*.md")):
        die(f"{root} уже содержит .md-файлы — init ничего не перезаписывает")
    (root / "inbox").mkdir(parents=True, exist_ok=True)
    files = {
        "index.md": INDEX_MD,
        "projects/index.md": PROJECTS_INDEX_MD,
        "decisions/index.md": DECISIONS_INDEX_MD,
        "sessions/index.md": SESSIONS_INDEX_MD,
        "AGENTS.md": AGENTS_MD,
        "README.md": VAULT_README_MD,
    }
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with open(path, "w", encoding="utf-8", newline="\n") as f:  # newline= в write_text — только 3.10+
                f.write(content)
    print(f"Vault создан: {root.resolve()}")
    print(f"Дальше: python mdgraph.py query index --depth 1 --vault {root.as_posix()}")


# ---------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    default_vault = os.environ.get("MDGRAPH_VAULT", "vault")
    parser = argparse.ArgumentParser(prog="mdgraph", formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="создать скелет vault")
    p_init.add_argument("path", nargs="?", default="vault", help="папка vault (по умолчанию ./vault)")
    p_init.set_defaults(func=cmd_init)

    def add(name: str, help_: str):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--vault", default=default_vault,
                       help=f"папка vault (по умолчанию {default_vault!r}; env MDGRAPH_VAULT)")
        p.add_argument("--json", action="store_true", help="машинный вывод JSON")
        return p

    p_graph = add("graph", "сводка графа: узлы, связи, orphan, битые ссылки")
    p_graph.set_defaults(func=cmd_graph)

    p_query = add("query", "заметка + соседи по ссылкам в пределах бюджета токенов")
    p_query.add_argument("note", help="имя заметки или путь (можно без .md)")
    p_query.add_argument("--depth", type=int, default=1, help="глубина обхода по ссылкам (по умолчанию 1)")
    p_query.add_argument("--max-tokens", type=int, default=6000, help="бюджет вывода в токенах (по умолчанию 6000)")
    p_query.set_defaults(func=cmd_query, json=False)

    p_find = add("find", "поиск по имени, заголовку, тегам, контенту")
    p_find.add_argument("query", help="подстрока (регистр не важен)")
    p_find.set_defaults(func=cmd_find)

    return parser


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
