from __future__ import annotations

from typing import Any

__all__ = ["extract_text", "index_document", "list_documents"]


def __getattr__(name: str) -> Any:
    # пакет импортируется первой строкой окна (вход работника PDF): сервис документов тащит за собой полприложения
    if name in __all__:
        from koddi.documents import service

        return getattr(service, name)
    raise AttributeError(name)
