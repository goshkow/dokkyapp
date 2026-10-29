"""Стандартные навыки Докки: инструкции, которые подключаются к запросу как указание пользователя."""

from __future__ import annotations

from koddi.skills.catalog import (
    api_designer,
    bug_hunter,
    code_review,
    copywriting,
    db_schema,
    docs_research,
    docs_writer,
    engineering_quality,
    git_workflow,
    microservices,
    page_cro,
    perf_tuner,
    rag_architect,
    safe_refactor,
    security_audit,
    ui_verification,
    visual_design,
    writing_tests,
)

# версия растёт, когда меняется текст стандартных навыков: у пользователей он обновится при следующем запуске
VERSION = 3

BUILTIN = [
    module.SKILL
    for module in (
        code_review,
        bug_hunter,
        safe_refactor,
        writing_tests,
        engineering_quality,
        security_audit,
        ui_verification,
        docs_research,
        git_workflow,
        docs_writer,
        perf_tuner,
        db_schema,
        api_designer,
        microservices,
        rag_architect,
        copywriting,
        page_cro,
        visual_design,
    )
]

# когда навык нужен: слова задачи (основы для русских слов), вид работы, языки и инструменты, без которых он не работает
METADATA = {
    "code-review": {"triggers": ["ревью", "review", "провер", "pull request", "мерж", "diff"], "kinds": ["review"],
                    "capabilities": ["git.diff", "code.impact"]},
    "bug-hunter": {"triggers": ["баг", "bug", "ошибк", "падает", "сломал", "не работает", "crash", "exception",
                                "traceback", "поймай", "почини", "fix", "регресс"], "kinds": ["debug"],
                   "capabilities": ["shell.run", "code.references"]},
    "safe-refactor": {"triggers": ["рефактор", "refactor", "переименуй", "rename", "вынеси", "extract", "разнеси",
                                   "упрости", "дублир"], "kinds": ["refactor"],
                      "capabilities": ["refactor.rename", "refactor.extract", "code.impact"]},
    "test-writer": {"triggers": ["тест", "test", "покрыт", "coverage", "pytest", "jest", "vitest", "spec"],
                    "kinds": ["tests"], "capabilities": ["test.run"]},
    "engineering-quality": {"triggers": ["качеств", "quality", "crap", "мутац", "mutation", "tdd", "red green",
                                         "чист код", "clean code", "архитектур слоёв", "слои", "layer", "дубл",
                                         "технический долг", "tech debt"], "kinds": ["quality"],
                            "capabilities": ["quality.run"]},
    "security-audit": {"triggers": ["безопасн", "security", "уязвим", "vulnerab", "xss", "csrf", "sql injection",
                                    "инъекц", "секрет", "secret", "авториз", "auth", "права доступ", "owasp"],
                       "kinds": ["security"], "capabilities": ["secret.scan", "code.references"]},
    "ui-verification": {"triggers": ["интерфейс", "ui", "вёрстк", "верстк", "экран", "кнопк", "страниц", "компонент",
                                     "component", "адаптив", "responsive", "css", "layout", "отображ", "frontend",
                                     "фронтенд"], "kinds": ["ui"], "capabilities": ["browser.open", "ui.verify"],
                        "languages": ["typescript", "javascript", "vue", "svelte", "css", "html"]},
    "docs-version-research": {"triggers": ["документац", "docs", "версия", "version", "миграц на", "upgrade",
                                           "обнов библиотек", "deprecated", "устарел", "api библиотек", "sdk",
                                           "changelog"], "kinds": ["research"], "capabilities": ["docs.lookup"]},
    "git-workflow": {"triggers": ["коммит", "commit", "pull request", "ветк", "branch", "merge", "rebase",
                                  "changelog"], "kinds": ["git"], "capabilities": ["git.status"]},
    "docs-writer": {"triggers": ["readme", "документаци", "опиши", "инструкц", "docstring", "guide"], "kinds": ["docs"],
                    "capabilities": []},
    "perf-tuner": {"triggers": ["медленн", "тормоз", "ускор", "производительн", "performance", "профил", "profil",
                                "latency", "память", "memory leak", "n+1"], "kinds": ["performance"],
                   "capabilities": ["shell.run"]},
    "db-schema": {"triggers": ["миграц", "migration", "схем", "schema", "таблиц", "table", "индекс", "index", "sql",
                               "postgres", "sqlite", "orm"], "kinds": ["data"], "capabilities": []},
    "api-designer": {"triggers": ["api", "endpoint", "эндпоинт", "rest", "graphql", "контракт", "openapi"],
                     "kinds": ["api"], "capabilities": ["project.map"]},
    "microservices-architect": {"triggers": ["микросервис", "microservice", "очеред", "queue", "kafka", "rabbit",
                                             "распредел"], "kinds": ["architecture"], "capabilities": []},
    "rag-architect": {"triggers": ["rag", "embedding", "эмбеддинг", "векторн", "retrieval", "поиск по документ"],
                      "kinds": ["ai"], "capabilities": []},
    "copywriting": {"triggers": ["текст для", "копирайт", "слоган", "продающ"], "kinds": ["writing"], "capabilities": []},
    "page-cro": {"triggers": ["конверси", "лендинг", "landing", "cro"], "kinds": ["marketing"], "capabilities": []},
    "visual-design": {"triggers": ["постер", "баннер", "иллюстрац", "poster"], "kinds": ["design"], "capabilities": []},
}

