import asyncio
import json

import httpx
import pytest

from accbot import AssetRef, FrameworkError, ModelItem, ModelPart, ToolDefinition
from accbot.config import ModelCard
from accbot.models import ModelClient, encode_items


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_native_tool_roundtrip_and_multimodal(protocol):
    tool = ToolDefinition(id="test.lookup", name="lookup", parameters={"type": "object", "properties": {}})
    requests = []

    def endpoint(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if protocol == "chat":
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "hello",
                                "reasoning_content": "provider state",
                                "tool_calls": [
                                    {
                                        "id": "call1",
                                        "type": "function",
                                        "function": {"name": tool.wire_name, "arguments": '{"x": 1}'},
                                    },
                                    {
                                        "id": "call2",
                                        "type": "function",
                                        "function": {"name": tool.wire_name, "arguments": '{"x": 2}'},
                                    },
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"total_tokens": 9},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "response1",
                "status": "completed",
                "output": [
                    {"type": "reasoning", "id": "reason1", "encrypted_content": "opaque", "summary": []},
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "hello"}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call1",
                        "name": tool.wire_name,
                        "arguments": '{"x": 1}',
                    },
                    {
                        "type": "function_call",
                        "call_id": "call2",
                        "name": tool.wire_name,
                        "arguments": '{"x": 2}',
                    },
                ],
                "usage": {"total_tokens": 9},
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as http:
            client = ModelClient(
                ModelCard(
                    id="m",
                    endpoint="https://models.test/v1",
                    model="test",
                    protocol=protocol,
                    api_key_env="KEY",
                ),
                http,
                lambda key: "test-secret",
            )
            incoming = ModelItem(
                role="user",
                content=(
                    ModelPart(text="look"),
                    ModelPart(type="image", asset=AssetRef(data="data:image/png;base64,aGVsbG8=")),
                ),
            )
            result = await client.generate([incoming], tools=(tool,))
            assert result.text == "hello"
            assert [call.name for call in result.tool_calls] == [tool.id, tool.id]
            assert result.usage["total_tokens"] == 9
            history = [
                incoming,
                *result.items,
                ModelItem.tool_result("call1", {"ok": True}),
                ModelItem.tool_result("call2", "done"),
            ]
            await client.generate(history, tools=(tool,))
            second = requests[1]["messages" if protocol == "chat" else "input"]
            if protocol == "responses":
                assert second[1]["encrypted_content"] == "opaque"
                assert second[-1]["call_id"] == "call2"
            else:
                assert second[1]["reasoning_content"] == "provider state"
                assert second[-1]["tool_call_id"] == "call2"
            assert "test-secret" not in str(requests)

    asyncio.run(run())


def test_opaque_state_is_not_silently_lost_across_protocols():
    item = ModelItem(kind="provider", provider_protocol="responses", provider_item={"type": "reasoning"})
    with pytest.raises(FrameworkError, match="opaque"):
        encode_items([item], (), "chat")


@pytest.mark.parametrize(
    "data",
    [
        {"choices": []},
        {
            "choices": [
                {"message": {"tool_calls": [{"id": "1", "function": {"name": "x", "arguments": "not json"}}]}}
            ]
        },
    ],
)
def test_malformed_model_response_is_a_framework_error(data):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data))
        ) as http:
            client = ModelClient(ModelCard(id="m", endpoint="https://test/v1", model="x"), http, lambda _: "")
            with pytest.raises(FrameworkError) as error:
                await client.generate([ModelItem.text("user", "x")])
            assert error.value.info.code == "provider"

    asyncio.run(run())


def test_http_error_never_echoes_secret_response_body():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "test-secret"}))
        ) as http:
            client = ModelClient(ModelCard(id="m", endpoint="https://test/v1", model="x"), http, lambda _: "")
            with pytest.raises(FrameworkError) as error:
                await client.generate([])
            assert "test-secret" not in str(error.value)

    asyncio.run(run())


def test_file_input_uses_protocol_specific_fields():
    item = ModelItem(content=(ModelPart(type="file", asset=AssetRef(url="https://files.test/a.pdf")),))
    assert encode_items([item], (), "responses")[0]["content"][0]["file_url"] == "https://files.test/a.pdf"
    with pytest.raises(FrameworkError):
        encode_items([item], (), "chat")
