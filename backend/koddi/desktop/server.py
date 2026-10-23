from __future__ import annotations

import uvicorn

from koddi.api.app import app
from koddi.config import migrate_legacy_data, settings
from koddi.logs.service import configure_logging
from koddi.storage import init_db


def start_api_server(port: int | None = None, *, private: bool = False) -> None:
    """Запускает локальный API для настольного процесса, не подтягивая модуль интерфейса."""
    migrate_legacy_data()
    configure_logging()
    init_db()
    if private:
        from koddi.desktop.transport import desktop_access

        desktop_access.enable()
        desktop_access.origin = f"http://127.0.0.1:{port or settings.port}"
    uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1" if private else settings.host,
            port=port or settings.port,
            log_level="warning",
            access_log=False,
        )
    ).run()
