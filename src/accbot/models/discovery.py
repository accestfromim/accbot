"""Explicit, bounded protocol probes used by the model setup wizard."""

import time

from ..config import ModelCard
from ..errors import ErrorCode, FrameworkError
from .client import ModelClient
from .types import ModelItem


def normalize_endpoint(value: str) -> str:
    value = value.strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/models"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    try:
        ModelCard(id="probe", endpoint=value, model="probe")
    except ValueError as exc:
        raise FrameworkError(
            ErrorCode.INVALID_ARGUMENT, "请填写有效的 HTTP(S) API 地址，不要在地址中包含密钥。"
        ) from exc
    return value


async def detect_protocol(http, endpoint: str, model: str, key: str | None = None, *, timeout: float = 300):
    endpoint = normalize_endpoint(endpoint)
    attempts = []
    for protocol in ("responses", "chat"):
        started = time.perf_counter()
        card = ModelCard(
            id="probe",
            endpoint=endpoint,
            model=model,
            protocol=protocol,
            timeout=timeout,
            api_key_env="PROBE" if key else None,
        )
        client = ModelClient(card, http, lambda _: key)
        limits = (
            [{"max_output_tokens": 32}]
            if protocol == "responses"
            else [{"max_tokens": 32}, {"max_completion_tokens": 32}]
        )
        for options in limits:
            try:
                await client.generate([ModelItem.text("user", "Reply with OK only.")], **options)
                return {
                    "protocol": protocol,
                    "endpoint": endpoint,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "attempts": attempts,
                }
            except FrameworkError as exc:
                attempts.append({"protocol": protocol, "message": exc.info.message})
                if "HTTP 400" not in exc.info.message:
                    break
    raise FrameworkError(
        ErrorCode.PROVIDER,
        "未能识别可用协议，请检查端点、密钥和模型名称。"
        + "；".join(f"{a['protocol']}: {a['message']}" for a in attempts),
    )


async def fetch_model_names(http, endpoint: str, key: str | None = None):
    endpoint = normalize_endpoint(endpoint)
    headers = {"Authorization": "Bearer " + key} if key else {}
    try:
        result = await http.get(endpoint + "/models", headers=headers, timeout=8)
        if result.is_error:
            return []
        data = result.json()
        return sorted(
            {str(item["id"]) for item in data.get("data", []) if isinstance(item, dict) and item.get("id")}
        )
    except Exception:
        return []
