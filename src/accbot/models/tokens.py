"""Offline DeepSeek text counts and provider-independent context budget estimates."""

import json
from collections.abc import Sequence
from functools import lru_cache
from importlib.resources import files
from typing import Literal

from tokenizers import Tokenizer

from ..tools import ToolDefinition
from .codecs import encode_items
from .transcript import render_items
from .types import ModelItem


@lru_cache(maxsize=1)
def _tokenizer() -> Tokenizer:
    data = json.loads(files("accbot.models").joinpath("tokenizer/tokenizer.json").read_text("utf-8"))
    # User text spelling a control token is still ordinary text, not a special token.
    data["added_tokens"] = []
    data["post_processor"] = None
    return Tokenizer.from_str(json.dumps(data))


@lru_cache(maxsize=512)
def _short_text_tokens(text: str) -> int:
    return len(_tokenizer().encode(text, add_special_tokens=False).ids)


def count_text_tokens(text: str) -> int:
    """Count text with the bundled V4.1 vocabulary; bound retained text to 512 × 8192 characters."""
    if len(text) <= 8192:
        return _short_text_tokens(text)
    return len(_tokenizer().encode(text, add_special_tokens=False).ids)


def _payload_tokens(value) -> int:
    if isinstance(value, str):
        return count_text_tokens(value)
    if isinstance(value, list):
        return sum(_payload_tokens(part) for part in value)
    if isinstance(value, dict):
        if value.get("type") in {"image_url", "input_image", "file", "input_file"}:
            # No downloads or Base64 tokenization; remote media preprocessing is unknown.
            return 8192
        return sum(count_text_tokens(key) + _payload_tokens(part) for key, part in value.items())
    return count_text_tokens(json.dumps(value))


def count_tokens(
    items: Sequence[ModelItem],
    tools: Sequence[ToolDefinition] = (),
    *,
    protocol: Literal["chat", "responses"] = "chat",
) -> int:
    """Estimate input tokens, including rendered metadata, tool schemas and media allowances.

    Text uses DeepSeek V4.1 tokenization. Role/field overhead and media are estimates,
    not a promise to match a provider's usage. Output reserve remains the caller's choice.
    """
    total = 0
    for item in render_items(items):
        # Count preserved provider content once, including reasoning and native tool calls.
        for payload in encode_items([item], tools, item.provider_protocol or protocol):
            total += 8 + _payload_tokens(payload)
    for tool in tools:
        total += count_text_tokens(json.dumps(tool.to_schema(protocol), ensure_ascii=False))
    return total
