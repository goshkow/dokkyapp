from __future__ import annotations

import re
import os

from koddi.storage.db import db


def retrieve_memory(project_id: int | None, query: str = "", limit: int = 12) -> list[dict]:
    """Память изолирована по владельцу и проекту; глобальные заметки принадлежат тому же владельцу."""
    limit = max(1, min(50, limit))
    terms = list(dict.fromkeys(re.findall(r"[^\W_]{3,}", query.casefold())))[:12]
    from koddi.policy.account import local_owner

    owner = local_owner()
    legacy = "" if os.getenv("KODDI_INTERNAL_TEST") == "1" else owner
    with db() as conn:
        # сведения о пользователе и проекте идут в контекст целиком (memory_context), поиск - только по заметкам
        scope = "(m.project_id=? OR m.project_id IS NULL) AND m.owner_id IN (?,?) AND m.kind NOT IN ('user','project')"
        rows = []
        if terms:
            match = " OR ".join('"' + term + '"' for term in terms)
            rows = conn.execute(
                "SELECT m.* FROM memory_search JOIN memory_summaries m ON m.id=memory_search.rowid "
                f"WHERE memory_search MATCH ? AND {scope} ORDER BY bm25(memory_search),m.id DESC LIMIT ?",
                (match, project_id, owner, legacy, limit),
            ).fetchall()
        if not rows:
            rows = conn.execute(
                f"SELECT m.* FROM memory_summaries m WHERE {scope} ORDER BY m.id DESC LIMIT ?",
                (project_id, owner, legacy, limit),
            ).fetchall()
    result, remaining = [], 12000
    for row in rows:
        if remaining <= 0:
            break
        item = dict(row)
        item["content"] = item["content"][: min(3000, remaining)]
        remaining -= len(item["content"])
        result.append(item)
    return result
