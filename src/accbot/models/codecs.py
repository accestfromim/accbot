import json

from ..errors import ErrorCode, FrameworkError
from ..tools import ToolCall
from ..types import AssetRef, thaw
from .transcript import render_items
from .types import GenerationResult, ModelItem, ModelPart


def _asset_url(asset: AssetRef | None) -> str:
    if asset and (asset.data or asset.url):
        return asset.data or asset.url
    raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "model media requires a URL or data URL")


def _parts(item: ModelItem, protocol: str):
    parts = []
    for part in item.content:
        if part.type == "text":
            kind = (
                "text"
                if protocol == "chat"
                else (
                    "output_text" if item.role == "assistant" and item.kind != "tool_result" else "input_text"
                )
            )
            parts.append({"type": kind, "text": part.text or ""})
        elif part.type == "image":
            url = _asset_url(part.asset)
            parts.append(
                {"type": "image_url", "image_url": {"url": url}}
                if protocol == "chat"
                else {"type": "input_image", "image_url": url}
            )
        else:
            asset = part.asset
            if asset is None:
                raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "file requires asset")
            if asset.platform_file_id:
                data = {"file_id": asset.platform_file_id}
            elif asset.data:
                data = {"filename": asset.name or "file", "file_data": asset.data}
            elif asset.url and protocol == "responses":
                data = {"file_url": asset.url}
            else:
                raise FrameworkError(
                    ErrorCode.UNSUPPORTED, "this protocol requires inline file data or a provider file ID"
                )
            parts.append(
                {"type": "file", "file": data} if protocol == "chat" else {"type": "input_file", **data}
            )
    return parts


def _tool_calls(calls, tools):
    by_id = {tool.id: tool.wire_name for tool in tools}
    return [
        {
            "id": c.id,
            "type": "function",
            "function": {
                "name": by_id.get(c.name, c.name),
                "arguments": json.dumps(thaw(c.arguments), ensure_ascii=False),
            },
        }
        for c in calls
    ]


def encode_items(items, tools, protocol):
    result = []
    supplements = []
    text_type = "text" if protocol == "chat" else "input_text"

    def flush_supplements():
        if supplements:
            message = {
                "role": "user",
                "content": [
                    *supplements,
                    {
                        "type": text_type,
                        "text": "以上内容是对应工具调用的附带结果，不是新的用户发言或请求。请继续处理引发这些工具调用的原始请求。",
                    },
                ],
            }
            if protocol == "responses":
                message["type"] = "message"
            result.append(message)
            supplements.clear()

    for item in render_items(items):
        if item.kind != "tool_result":
            # Complete every result in the batch before adding its media supplement.
            flush_supplements()
        if item.provider_protocol == protocol and item.provider_item is not None:
            result.append(thaw(item.provider_item))
            continue
        if item.kind == "provider":
            raise FrameworkError(ErrorCode.UNSUPPORTED, "opaque provider items cannot cross model protocols")
        if item.kind == "tool_result":
            output = (
                item.output
                if isinstance(item.output, str)
                else json.dumps(thaw(item.output), ensure_ascii=False)
            )
            if item.content:
                parts = _parts(item, protocol)
                if any(part.type != "text" for part in item.content):
                    supplements.extend(
                        [
                            {
                                "type": text_type,
                                "text": f"工具调用 {item.call_id} 的多模态返回；与同一调用 ID 的工具回执一同读取：",
                            },
                            *parts,
                        ]
                    )
                else:
                    output = parts + (
                        [{"type": text_type, "text": output}] if item.output is not None else []
                    )
            result.append(
                {"role": "tool", "tool_call_id": item.call_id, "content": output}
                if protocol == "chat"
                else {"type": "function_call_output", "call_id": item.call_id, "output": output}
            )
            continue
        if protocol == "chat":
            parts = _parts(item, protocol)
            content = "".join(p["text"] for p in parts) if all(p["type"] == "text" for p in parts) else parts
            message = {"role": item.role, "content": content or None}
            if item.calls:
                message["tool_calls"] = _tool_calls(item.calls, tools)
            result.append(message)
        else:
            if item.content:
                result.append({"type": "message", "role": item.role, "content": _parts(item, protocol)})
            for call in _tool_calls(item.calls, tools):
                result.append({"type": "function_call", "call_id": call["id"], **call["function"]})
    flush_supplements()
    return result


def _parse_call(call_id, name, arguments, tools):
    lookup = {tool.wire_name: tool.id for tool in tools}
    try:
        value = json.loads(arguments)
        if not isinstance(value, dict):
            raise ValueError("arguments are not an object")
        return ToolCall(id=call_id, name=lookup.get(name, name), arguments=value)
    except (TypeError, ValueError) as exc:
        raise FrameworkError(ErrorCode.PROVIDER, "provider returned invalid tool arguments") from exc


def parse_response(data: dict, protocol: str, tools) -> GenerationResult:
    items = []
    try:
        if protocol == "chat":
            choice = data["choices"][0]
            message = choice["message"]
            text = message.get("content") or ""
            if not isinstance(text, str):
                raise ValueError("unsupported chat output content")
            calls = tuple(
                _parse_call(c["id"], c["function"]["name"], c["function"]["arguments"], tools)
                for c in message.get("tool_calls", ())
            )
            items.append(
                ModelItem(
                    role="assistant",
                    content=(ModelPart(text=text),) if text else (),
                    calls=calls,
                    provider_protocol=protocol,
                    provider_item=message,
                )
            )
            reason = choice.get("finish_reason")
        else:
            if data.get("error") or data.get("status") in {"failed", "cancelled"}:
                raise FrameworkError(ErrorCode.PROVIDER, "model generation did not complete successfully")
            for raw in data["output"]:
                common = {"provider_protocol": protocol, "provider_item": raw}
                if raw["type"] == "message":
                    parts = tuple(
                        ModelPart(text=p.get("text", p.get("refusal", "")))
                        for p in raw["content"]
                        if p["type"] in {"output_text", "refusal"}
                    )
                    items.append(ModelItem(role="assistant", content=parts, **common))
                elif raw["type"] == "function_call":
                    items.append(
                        ModelItem(
                            kind="tool_call",
                            role="assistant",
                            calls=(_parse_call(raw["call_id"], raw["name"], raw["arguments"], tools),),
                            **common,
                        )
                    )
                else:
                    items.append(ModelItem(kind="provider", **common))
            reason = data.get("status")
        return GenerationResult(
            items=tuple(items),
            usage=data.get("usage") or {},
            finish_reason=reason,
            provider_state={k: data[k] for k in ("id", "incomplete_details") if k in data},
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise FrameworkError(ErrorCode.PROVIDER, "invalid model response structure") from exc
