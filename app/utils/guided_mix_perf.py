"""Dev profiling helpers for Guided Mix (bench-critical path)."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger("tvw.guided_mix.perf")

_request_id: ContextVar[str | None] = ContextVar("guided_mix_perf_request_id", default=None)


def guided_mix_perf_enabled() -> bool:
    raw = os.environ.get("TVW_GUIDED_MIX_PERF", "1")
    return str(raw).strip().lower() not in {"0", "false", "off", "no"}


def new_guided_mix_request_id() -> str:
    rid = uuid.uuid4().hex[:10]
    _request_id.set(rid)
    return rid


def get_guided_mix_request_id() -> str | None:
    return _request_id.get()


def set_guided_mix_request_id(request_id: str | None) -> None:
    _request_id.set(request_id)


def json_byte_size(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    try:
        return len(json.dumps(value, separators=(",", ":"), default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


@contextmanager
def guided_mix_perf_span(name: str, **fields: Any):
    """Time a span and log structured dev metrics when perf logging is enabled."""
    ctx: dict[str, Any] = {}
    if not guided_mix_perf_enabled():
        yield ctx
        return
    t0 = time.perf_counter()
    try:
        yield ctx
    finally:
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        rid = get_guided_mix_request_id()
        payload = {"span": name, "elapsed_ms": elapsed_ms, **fields, **ctx}
        if rid:
            payload["request"] = rid
        logger.info("guided_mix_perf %s", payload)


def log_guided_step_complete_summary(**fields: Any) -> None:
    if not guided_mix_perf_enabled():
        return
    rid = get_guided_mix_request_id()
    if rid:
        fields = {**fields, "request": rid}
    logger.info("guided_mix_step_complete %s", fields)


def log_guided_mix_stage(stage: str, **fields: Any) -> None:
    if not guided_mix_perf_enabled():
        return
    rid = get_guided_mix_request_id()
    payload = {"stage": stage, **fields}
    if rid:
        payload["request"] = rid
    logger.info("[guided-mix perf] %s", payload)
