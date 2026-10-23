"""Стандартные навыки Докки: инструкции, которые подключаются к запросу как указание пользователя."""

from __future__ import annotations

from koddi.skills.catalog import (
    api_designer,
    bug_hunter,
    code_review,
    copywriting,
    db_schema,
    docs_writer,
    git_workflow,
    microservices,
    page_cro,
    perf_tuner,
    rag_architect,
    safe_refactor,
    security_audit,
    visual_design,
    writing_tests,
)

# версия растёт, когда меняется текст стандартных навыков: у пользователей он обновится при следующем запуске
VERSION = 1

BUILTIN = [
    module.SKILL
    for module in (
        code_review,
        bug_hunter,
        safe_refactor,
        writing_tests,
        security_audit,
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
