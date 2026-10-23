from __future__ import annotations

import hashlib
import json
from collections import OrderedDict


class FailureTracker:
    """Повторные ошибки одной операции; успешные опросы не считаются зацикливанием."""

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self.failures: OrderedDict[str, int] = OrderedDict()

    @staticmethod
    def key(name: str, arguments: dict) -> str:
        return hashlib.sha256(
            json.dumps([name, arguments], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def needs_approval(self, name: str, arguments: dict) -> bool:
        return self.failures.get(self.key(name, arguments), 0) >= self.threshold

    def record(self, name: str, arguments: dict, result: dict) -> None:
        key = self.key(name, arguments)
        if result.get("ok") is not False:
            if result.get("change_set_id") or name in {
                "shell.run",
                "git.checkout",
                "devserver.start",
            }:
                self.failures.clear()
            else:
                self.failures.pop(key, None)
        elif not result.get("approval_required") and not result.get("cancelled"):
            self.failures[key] = self.failures.get(key, 0) + 1
            self.failures.move_to_end(key)
            while len(self.failures) > 128:
                self.failures.popitem(last=False)

    def reset(self, name: str, arguments: dict) -> None:
        self.failures.pop(self.key(name, arguments), None)


def local_title(text: str) -> str:
    from koddi.logs.service import redact

    line = next(
        (line.strip().lstrip("# ") for line in text.splitlines() if line.strip()), "Новая задача"
    )
    return " ".join(redact(line).split())[:72] or "Новая задача"
