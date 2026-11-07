import json
from collections.abc import Sequence

import httpx

from ..config import ModelCard
from ..errors import ErrorCode, FrameworkError, Outcome
from ..tools import ToolDefinition
from ..types import thaw
from .codecs import encode_items, parse_response
from .types import GenerationResult, ModelItem


def _reject_constant(value):
    raise ValueError("non-finite JSON value")


class ModelClient:
    def __init__(self, card: ModelCard, http: httpx.AsyncClient, secret_resolver, *, prompts=None):
        self.card, self.http, self.secret_resolver = card, http, secret_resolver
        self.prompts = prompts

    async def generate(
        self,
        items: Sequence[ModelItem] | str,
        tools: Sequence[ToolDefinition] = (),
        *,
        system_prompt_id: str | None = None,
        persona_id: str | None = None,
        **options,
    ) -> GenerationResult:
        raw = isinstance(items, str)
        if not raw and (
            not isinstance(items, Sequence) or any(not isinstance(item, ModelItem) for item in items)
        ):
            raise FrameworkError(
                ErrorCode.INVALID_ARGUMENT, "generate requires ModelItem objects or a JSON message array"
            )
        if any(not isinstance(tool, ToolDefinition) for tool in tools):
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "generate requires ToolDefinition objects")
        if len({tool.id for tool in tools}) != len(tools):
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "duplicate tools in model request")
        if raw:
            try:
                encoded = json.loads(items, parse_constant=_reject_constant)
                # JSON round-trip is not a role parser; native provider fields stay untouched.
                if not isinstance(encoded, list) or any(not isinstance(item, dict) for item in encoded):
                    raise ValueError
            except ValueError:
                raise FrameworkError(
                    ErrorCode.INVALID_ARGUMENT, "raw input must be a JSON array of native message objects"
                ) from None
        elif system_prompt_id is not None:
            if self.prompts is None:
                raise FrameworkError(ErrorCode.CONFIGURATION, "model client has no system prompt catalog")
            content = self.prompts.render(system_prompt_id, persona_id=persona_id, tools=tools)
            items = (ModelItem.text("system", content), *items)
        elif persona_id is not None:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "persona_id requires system_prompt_id")
        protocol = self.card.protocol
        if not raw:
            encoded = encode_items(items, tools, protocol)
        suffix = "chat/completions" if protocol == "chat" else "responses"
        url = self.card.endpoint.rstrip("/")
        if not url.endswith("/" + suffix):
            url += "/" + suffix
        params = {**thaw(self.card.parameters), **options}
        if set(params) & {"model", "messages", "input", "tools", "stream", "system_prompt_id", "persona_id"}:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "reserved model request parameter")
        payload = {
            **params,
            "model": self.card.model,
            "messages" if protocol == "chat" else "input": encoded,
            "stream": False,
        }
        if protocol == "responses":
            payload.setdefault("store", False)
        if tools:
            payload["tools"] = [tool.to_schema(protocol) for tool in tools]
        headers = {}
        if self.card.api_key_env:
            headers["Authorization"] = "Bearer " + self.secret_resolver(self.card.api_key_env)
        try:
            response = await self.http.post(url, json=payload, headers=headers, timeout=self.card.timeout)
            if response.is_error:
                raise FrameworkError(
                    ErrorCode.PROVIDER, f"model endpoint returned HTTP {response.status_code}"
                )
            return parse_response(response.json(), protocol, tools)
        except httpx.TimeoutException as exc:
            raise FrameworkError(
                ErrorCode.TIMEOUT, "model request timed out", outcome=Outcome.UNKNOWN
            ) from exc
        except httpx.HTTPError as exc:
            raise FrameworkError(ErrorCode.PROVIDER, "model endpoint connection failed") from exc
        except ValueError as exc:
            raise FrameworkError(ErrorCode.PROVIDER, "model endpoint returned invalid JSON") from exc


class ModelRegistry:
    def __init__(self, cards, http, secret_resolver, *, prompts=None):
        self.http, self.secret_resolver = http, secret_resolver
        self.prompts = prompts
        self.replace(cards)

    def replace(self, cards):
        mapping = {card.id: card for card in cards}
        if len(mapping) != len(cards):
            raise FrameworkError(ErrorCode.CONFIGURATION, "duplicate model id")
        self._clients = {
            key: ModelClient(card, self.http, self.secret_resolver, prompts=self.prompts)
            for key, card in mapping.items()
        }

    def list(self):
        return tuple(client.card for client in self._clients.values())

    def get(self, model_id):
        if model_id not in self._clients:
            raise FrameworkError(ErrorCode.NOT_FOUND, f"model not found: {model_id}")
        return self._clients[model_id]
