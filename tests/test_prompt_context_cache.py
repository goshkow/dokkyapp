from __future__ import annotations

from koddi.agents.context_cache import PromptContextCache


def test_prompt_context_cache_hits_and_invalidates_on_input_change() -> None:
    cache = PromptContextCache()
    calls = 0

    def build() -> str:
        nonlocal calls
        calls += 1
        return f"prompt-{calls}"

    first, hit_first, key_first = cache.get_or_build(
        {"task": "A", "mode": "chat", "memory": []}, build
    )
    second, hit_second, key_second = cache.get_or_build(
        {"task": "A", "mode": "chat", "memory": []}, build
    )
    third, hit_third, key_third = cache.get_or_build(
        {"task": "A", "mode": "build", "memory": []}, build
    )

    assert (first, second, third) == ("prompt-1", "prompt-1", "prompt-2")
    assert hit_first is False
    assert hit_second is True
    assert hit_third is False
    assert key_first == key_second
    assert key_third != key_first


def test_prompt_context_cache_clear_increments_generation() -> None:
    cache = PromptContextCache()
    cache.get_or_build({"task": "A"}, lambda: "A")
    assert cache.clear() == 1
    assert cache.generation == 1
