# mdgraph — плагин Claude Code

Долговременная память для AI-агентов: SessionStart-хук автоматически кладёт
карту знаний vault (`mdgraph query index`) в контекст каждой сессии, Stop-хук
напоминает, если заметки давно не обновлялись, скилл `vault-save` фиксирует
итоги работы по правилам AGENTS.md.

## Настройка

Единственная переменная — `MDGRAPH_VAULT` (абсолютный путь к vault).
Без неё хуки молча пропускают работу (плагин не мешает проектам без vault).

Пример для `~/.claude/settings.json`:

```json
{
  "env": { "MDGRAPH_VAULT": "/абсолютный/путь/к/vault" },
  "extraKnownMarketplaces": {
    "mdgraph": {
      "source": { "source": "directory", "path": "/путь/к/клону/mdgraph" }
    }
  },
  "enabledPlugins": { "mdgraph@mdgraph": true }
}
```

## Состав

- `mdgraph.py` — копия одноимённого скрипта из корня репозитория.
  **При изменении корневого скрипта скопируйте его сюда** — плагин
  самодостаточен и не ссылается на файлы снаружи.
- `hooks/hooks.json` — SessionStart (карта знаний в контекст) и Stop
  (напоминание, если в vault нет .md новее 2 часов; проверка на python3 —
  не зависит от того, чем в системе представлен `find`).
- `skills/vault-save/SKILL.md` — процедура сохранения итогов сессии.

## Проверка

    claude plugin validate ./plugin
    claude plugin validate .
