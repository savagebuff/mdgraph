# Vault базы знаний

Obsidian-совместимая папка заметок: `[[wikilinks]]` + YAML frontmatter.
Открывается в Obsidian как обычная папка (граф ссылок — бесплатно),
но не требует ничего кроме .md-файлов.

Шпаргалка (из родительской папки):
  python mdgraph.py graph --vault <эта папка>
  python mdgraph.py query index --depth 1 --vault <эта папка>
  python mdgraph.py find <слово> --vault <эта папка>

Правила ведения — в AGENTS.md (для агентов) и в index.md (для людей).
