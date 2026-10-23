from koddi.tools.read_cache import FileReadCache


def test_cache_bounded_and_invalidates_on_edits(tmp_path):
    path = tmp_path / "code.py"
    path.write_bytes(b"one")
    cache = FileReadCache(max_bytes=5)
    assert cache.read(path, 0, 20)[0] == b"one"
    first = cache.read(path, 0, 20)
    assert len(cache.entries) == 1
    path.write_bytes(b"two-two")
    assert cache.read(path, 0, 20)[0] == b"two-two"
    assert cache.read(path, 0, 20)[1] != first[1]
    assert cache.size <= 5
    cache.clear()
    assert not cache.entries and not cache.size
