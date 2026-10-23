from __future__ import annotations

import math


STATUSES = {"cold", "hit", "partial", "miss", "too_short"}
REASONS = {
    "reasoning_mode_changed", "system_prompt_changed", "tools_changed",
    "history_rewritten", "provider_cache_unavailable",
}


def number(value, *, integer=False):
    if type(value) not in (int, float) or value < 0:
        return 0
    if type(value) is float and not math.isfinite(value):
        return 0
    return min(int(value) if integer else value, 10**12)


def first_count(*values) -> int:
    for value in values:
        if type(value) in (int, float) and value >= 0 and (type(value) is int or math.isfinite(value)):
            return number(value, integer=True)
    return 0


def record_cache(session: dict, metadata: dict, usage: dict, tail: list) -> dict | None:
    cache = metadata.get("cache")
    if not isinstance(cache, dict) or not isinstance(cache.get("status"), str) or cache["status"] not in STATUSES:
        return None
    import json

    details = metadata.get("usage") or {}
    if not isinstance(details, dict):
        details = {}
    inputs = usage.get("input_tokens_details", usage.get("prompt_tokens_details"))
    inputs = inputs if isinstance(inputs, dict) else {}
    reasons = cache.get("reasons") or []
    if not isinstance(reasons, list):
        reasons = []
    step = {
        "status": cache["status"],
        "mode": session.get("reasoning_mode"),
        "prompt": first_count(usage.get("input_tokens"), usage.get("prompt_tokens"), cache.get("prompt_tokens")),
        "cache_read": first_count(inputs.get("cached_tokens"), cache.get("cached_tokens"), details.get("cached_tokens")),
        "cache_write": first_count(inputs.get("cache_write_tokens"), details.get("cache_write_tokens")),
        "ratio": min(1, number(cache.get("ratio"))),
        "gap_seconds": number(cache.get("gap_seconds")),
        "reasons": sorted({reason for reason in reasons if isinstance(reason, str) and reason in REASONS}),
        "tail_estimate": len(json.dumps(tail, ensure_ascii=False)) // 3 + 1,
        "replayed": metadata.get("replayed") is True,
    }
    if step["prompt"] and ("cached_tokens" in inputs or "cached_tokens" in cache or "cached_tokens" in details):
        step["ratio"] = min(1, step["cache_read"] / step["prompt"])
    step["write_tail_ratio"] = round(step["cache_write"] / max(1, step["tail_estimate"]), 3)
    step["rewrite_estimate"] = max(0, step["cache_write"] - step["tail_estimate"] - 128)
    previous = session.get("client_cache") or {}
    previous = previous if isinstance(previous, dict) else {}
    counts = previous.get("counts") or {}
    counts = {status: number(counts.get(status), integer=True) for status in STATUSES} if isinstance(counts, dict) else {}
    if not step["replayed"]:
        counts[step["status"]] = counts.get(step["status"], 0) + 1
    suspect = step["status"] in {"partial", "miss"} and step["prompt"] >= 1024 and step["gap_seconds"] < 1800
    streak = number(previous.get("suspect_streak"), integer=True) + 1 if suspect else 0
    if step["replayed"]:
        streak = number(previous.get("suspect_streak"), integer=True)
    session["client_cache"] = {"last": step, "counts": counts, "suspect_streak": streak}
    return step
