"""Сервер локального API. Отдельным модулем, чтобы uvicorn и FastAPI грузились уже после показа окна."""
from __future__ import annotations

import threading

import uvicorn


class DesktopApiServer(uvicorn.Server):
    def __init__(self, config):
        super().__init__(config)
        self.stop_requested = threading.Event()
        self._main_loop_entered = threading.Event()

    def request_shutdown(self):
        self.stop_requested.set()
        if self._main_loop_entered.is_set():
            self.should_exit = True

    async def main_loop(self):
        # uvicorn 0.30 пропускает lifespan.shutdown при should_exit во время startup.
        self._main_loop_entered.set()
        if self.stop_requested.is_set():
            self.should_exit = True
        await super().main_loop()
