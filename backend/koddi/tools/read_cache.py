from __future__ import annotations

import hashlib
import os
import time
from collections import OrderedDict
from pathlib import Path


class FileReadCache:
    def __init__(self, max_bytes=4 * 1024 * 1024, ttl=60):
        self.max_bytes, self.ttl = max_bytes, ttl
        self.entries = OrderedDict()
        self.size = 0
        self.hits = self.misses = self.invalidations = 0

    @staticmethod
    def version(stat):
        return (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)

    def read(self, path: Path, offset: int, size: int) -> tuple[bytes, str]:
        version = self.version(path.stat())
        key = (str(path), offset, size, *version)
        now = time.monotonic()
        for old in list(self.entries):
            if now - self.entries[old][0] > self.ttl or (old[0] == str(path) and old[3:] != version):
                self.size -= len(self.entries.pop(old)[1])
        if key in self.entries:
            self.hits += 1
            self.entries.move_to_end(key)
            _, data, digest = self.entries[key]
            return data, digest
        self.misses += 1
        with path.open("rb") as handle:
            opened = self.version(os.fstat(handle.fileno()))
            handle.seek(offset)
            data = handle.read(size)
            after = self.version(os.fstat(handle.fileno()))
        digest = hashlib.sha256(data).hexdigest()
        # изменившийся во время чтения файл не становится долгоживущим снимком
        stable = version == opened == after == self.version(path.stat())
        if stable and len(data) <= self.max_bytes:
            self.entries[key] = (now, data, digest)
            self.size += len(data)
            while self.size > self.max_bytes:
                self.size -= len(self.entries.popitem(last=False)[1][1])
        return data, digest

    def clear(self):
        self.invalidations += bool(self.entries)
        self.entries.clear()
        self.size = 0

    def stats(self) -> dict:
        return {"hits": self.hits, "misses": self.misses, "invalidations": self.invalidations,
                "bytes": self.size, "entries": len(self.entries)}
