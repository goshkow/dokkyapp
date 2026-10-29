from __future__ import annotations

import json
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic

from koddi.dokky.cache import number
import math
from koddi.storage.db import db, durable_write


current_metrics: ContextVar[TurnMetrics | None] = ContextVar("turn_metrics", default=None)
MAX_REQUEST_TIMINGS = 32


def count(value) -> int:
    return number(value, integer=True)


@dataclass
class TurnMetrics:
    turn_id: int | None = None
    started: float = field(default_factory=monotonic)
    llm_requests: int = 0
    transport_attempts: int = 0
    retries: int = 0
    loop_cycles: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    tool_errors: int = 0
    tool_seconds: float = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    usage_samples: int = 0
    input_token_samples: int = 0
    output_token_samples: int = 0
    cached_token_samples: int = 0
    cache_write_samples: int = 0
    reasoning_samples: int = 0
    replayed_responses: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    # объективные гейты: счётчики по статусам и последние значения метрик (None - не измерялось)
    quality_gates: dict = field(default_factory=lambda: {status: 0 for status in (
        "attempted", "passed", "failed", "skipped", "unavailable", "cancelled", "error")})
    quality_metrics: dict = field(default_factory=dict)
    stop_reason: str = "running"
    request_timings: list[dict] = field(default_factory=list)

    def usage(self, usage: dict, metadata: dict | None = None) -> None:
        metadata = metadata if isinstance(metadata, dict) else {}
        if metadata.get("replayed") is True:
            self.replayed_responses += 1
            return
        usage = usage if isinstance(usage, dict) else {}
        if not usage:
            return
        self.usage_samples += 1

        def observe(field, sample_field, *values):
            for value in values:
                if (
                    type(value) in (int, float)
                    and value >= 0
                    and (type(value) is int or math.isfinite(value))
                ):
                    setattr(self, field, getattr(self, field) + count(value))
                    setattr(self, sample_field, getattr(self, sample_field) + 1)
                    return

        observe(
            "input_tokens",
            "input_token_samples",
            usage.get("input_tokens"),
            usage.get("prompt_tokens"),
        )
        observe(
            "output_tokens",
            "output_token_samples",
            usage.get("output_tokens"),
            usage.get("completion_tokens"),
        )
        inputs = usage.get("input_tokens_details", usage.get("prompt_tokens_details"))
        inputs = inputs if isinstance(inputs, dict) else {}
        outputs = usage.get("output_tokens_details", usage.get("completion_tokens_details"))
        outputs = outputs if isinstance(outputs, dict) else {}
        extra = metadata.get("usage")
        extra = extra if isinstance(extra, dict) else {}
        cache = metadata.get("cache")
        cache = cache if isinstance(cache, dict) else {}
        observe(
            "cached_input_tokens",
            "cached_token_samples",
            inputs.get("cached_tokens"),
            extra.get("cached_tokens"),
            cache.get("cached_tokens"),
        )
        observe(
            "cache_write_tokens",
            "cache_write_samples",
            inputs.get("cache_write_tokens"),
            extra.get("cache_write_tokens"),
        )
        observe(
            "reasoning_tokens",
            "reasoning_samples",
            outputs.get("reasoning_tokens"),
            extra.get("reasoning_tokens"),
        )

    def tool(self, result: dict, elapsed: float) -> None:
        self.tool_results += 1
        self.tool_seconds += max(0, elapsed)
        self.tool_errors += int(result.get("ok") is False)
        if isinstance(result.get("gates"), list):
            self.gates(result["gates"])
        checks = result.get("checks")
        if isinstance(checks, list):
            self.passed_checks += sum(isinstance(c, dict) and c.get("ok") is True for c in checks)
            self.failed_checks += sum(isinstance(c, dict) and c.get("ok") is False for c in checks)

    def gates(self, items: list) -> None:
        """Результаты гейтов (выжимки quality.run или автоматического прогона) в метриках хода."""
        for item in items:
            if not isinstance(item, dict) or item.get("status") not in self.quality_gates:
                continue
            self.quality_gates["attempted"] += 1
            self.quality_gates[item["status"]] += 1
            summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
            gate = item.get("gate")
            values = {"crap": {"crap_max": summary.get("max_crap"), "crap_violations": summary.get("violations")},
                      "dry": {"dry_open": summary.get("open_candidates")},
                      "mutation": {"mutation_score": summary.get("score"), "mutants_survived": summary.get("survived")},
                      "architecture": {"layer_violations_new": summary.get("new")}}.get(gate, {})
            for key, value in values.items():
                if value is None:
                    continue
                entry = self.quality_metrics.setdefault(key, {"first": value, "last": value})
                entry["last"] = value

    def snapshot(self) -> dict:
        result = {
            key: value for key, value in vars(self).items() if key not in {"started", "turn_id"}
        }
        result["scope"] = "local_turn_excluding_child_turns"
        result["duration_seconds"] = round(max(0, monotonic() - self.started), 3)
        result["tool_seconds"] = round(self.tool_seconds, 3)
        result["cache_hit_rate"] = (
            min(1, self.cached_input_tokens / self.input_tokens)
            if self.input_tokens and self.cached_token_samples == self.input_token_samples
            else None
        )
        result["token_usage_complete"] = (
            self.input_token_samples == self.output_token_samples == self.llm_requests
        )
        result["cache_usage_complete"] = self.cached_token_samples == self.llm_requests
        for metric_field, samples in (
            ("input_tokens", self.input_token_samples),
            ("output_tokens", self.output_token_samples),
            ("reasoning_tokens", self.reasoning_samples),
            ("cached_input_tokens", self.cached_token_samples),
            ("cache_write_tokens", self.cache_write_samples),
        ):
            if not samples:
                result[metric_field] = None
        # себестоимость знает сервер; неизвестное значение нельзя подменять нулём или прайс-листом
        result["api_cost"] = None
        result["cost_source"] = "server_ledger_required"
        return result

    def persist(self, turn_id: int) -> None:
        with db() as conn:
            conn.execute(
                "INSERT INTO turn_metrics(turn_id,metrics_json) VALUES (?,?) "
                "ON CONFLICT(turn_id) DO UPDATE SET metrics_json=excluded.metrics_json,updated_at=CURRENT_TIMESTAMP",
                (turn_id, json.dumps(self.snapshot(), ensure_ascii=False)),
            )


class ObservedClient:
    def __init__(self, client, metrics: TurnMetrics):
        self.client, self.metrics = client, metrics

    @property
    def config(self):
        return getattr(self.client, "config", {})

    async def stream(self, **kwargs):
        self.metrics.llm_requests += 1
        started = monotonic()
        attempts, retries = self.metrics.transport_attempts, self.metrics.retries
        observed = {
            name: (getattr(self.metrics, name), getattr(self.metrics, samples))
            for name, samples in (("input_tokens", "input_token_samples"),
                                  ("reasoning_tokens", "reasoning_samples"))
        }
        timing = {
            "request": self.metrics.llm_requests,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "running",
            "messages": len(kwargs.get("messages", [])),
            "tools": len(kwargs.get("tools", [])),
            "first_chunk_seconds": None,
            "first_output_seconds": None,
        }
        self.metrics.request_timings.append(timing)
        del self.metrics.request_timings[:-MAX_REQUEST_TIMINGS]
        if self.metrics.turn_id is not None:
            await durable_write(self.metrics.persist, self.metrics.turn_id)
        # streaming usage - накопительный итог запроса, а не дельты
        last_usage, last_metadata = {}, {}
        iterator = self.client.stream(**kwargs)
        try:
            async for chunk in iterator:
                changed = False
                if timing["first_chunk_seconds"] is None:
                    timing["first_chunk_seconds"] = round(max(0, monotonic() - started), 3)
                    changed = True
                # waiting/usage не означают, что провайдер уже начал возвращать ответ или вызов
                if timing["first_output_seconds"] is None and (
                    chunk.get("content") or chunk.get("tool_progress") or chunk.get("tool_call")
                ):
                    timing["first_output_seconds"] = round(max(0, monotonic() - started), 3)
                    changed = True
                if changed and self.metrics.turn_id is not None:
                    await durable_write(self.metrics.persist, self.metrics.turn_id)
                if chunk.get("reset"):
                    last_usage, last_metadata = {}, {}
                if "usage" in chunk:
                    last_usage, last_metadata = chunk["usage"], chunk.get("dokky", {})
                yield chunk
            timing["status"] = "completed"
        except asyncio.CancelledError:
            timing["status"] = "cancelled"
            raise
        except Exception:
            timing["status"] = "error"
            raise
        finally:
            self.metrics.usage(last_usage, last_metadata)
            if timing["status"] == "running":
                timing["status"] = "interrupted"
            timing["wall_seconds"] = round(max(0, monotonic() - started), 3)
            timing["transport_attempts"] = max(0, self.metrics.transport_attempts - attempts)
            timing["retries"] = max(0, self.metrics.retries - retries)
            for name, samples in (("input_tokens", "input_token_samples"),
                                  ("reasoning_tokens", "reasoning_samples")):
                previous, sample_count = observed[name]
                timing[name] = getattr(self.metrics, name) - previous if getattr(self.metrics, samples) > sample_count else None
            try:
                await iterator.aclose()
            finally:
                if self.metrics.turn_id is not None:
                    await durable_write(self.metrics.persist, self.metrics.turn_id)


def transport_attempt(*, retry: bool) -> None:
    metrics = current_metrics.get()
    if metrics is not None:
        metrics.transport_attempts += 1
        metrics.retries += int(retry)
