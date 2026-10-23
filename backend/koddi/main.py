from __future__ import annotations

import uvicorn

from koddi.api import app
from koddi.config import migrate_legacy_data, settings
from koddi.logs import configure_logging
from koddi.storage import init_db


def main() -> None:
    migrate_legacy_data()
    configure_logging()
    init_db()
    uvicorn.run(app, host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
