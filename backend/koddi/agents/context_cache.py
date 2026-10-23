from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Callable
from typing import Any


class PromptContextCache:
    def __init__(self, max_entries: int = 64) -> None:
        self.max_entries = max_entries
        self._entries: OrderedDict[str, str] = OrderedDict()
        self.generation = 0

    def get_or_build(
        self, inputs: dict[str, Any], build: Callable[[], str]
    ) -> tuple[str, bool, str]:
        serialized = json.dumps(inputs, ensure_ascii=False, sort_keys=True, default=str)
        key = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        cached = self._entries.get(key)
        if cached is not None:
            self._entries.move_to_end(key)
            return cached, True, key
        value = build()
        self._entries[key] = value
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        return value, False, key

    def clear(self) -> int:
        count = len(self._entries)
        self._entries.clear()
        self.generation += 1
        return count


prompt_context_cache = PromptContextCache()
