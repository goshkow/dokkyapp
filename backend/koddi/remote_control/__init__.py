"""Доступ с телефона: чаты Докки Code видны и управляются из Докки на телефоне и на сайте.

Не путать с `koddi.remote` - это клиент AI-сервера. Протокол сервера описан в репозитории dokky
(docs/REMOTE_CONTROL.md).
"""

from koddi.remote_control.bridge import Hooks, RemoteControl, remote_control

__all__ = ["Hooks", "RemoteControl", "remote_control"]
