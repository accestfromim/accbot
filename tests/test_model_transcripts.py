import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest
from pydantic import ValidationError

from accbot import (
    AssetRef,
    ChatMessage,
    ConversationRef,
    FrameworkError,
    GroupRef,
    Member,
    MessageBuilder,
    MessageQuote,
    MessageRef,
    ModelItem,
    ModelPart,
    ModelQuote,
    ToolCall,
    ToolDefinition,
    User,
    UserRef,
)
from accbot.config import ModelCard
from accbot.models import ModelClient, encode_items, render_items


def user(number="1", name="群友"):
    return User(ref=UserRef(platform="qq", scope="bot", id=number), name=name)


def message(number, text, **fields):
    return ModelItem.text(
        "user",
        text,
        sender=user(number),
        message_id=number,
        sent_at=datetime(2026, 11, 7, 12, 30, 15, tzinfo=timezone.utc),
        conversation=ConversationRef(connection_id="main", platform="qq", scope="bot", id="group"),
        **fields,
    )


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_structured_transcript_preserves_metadata_quotes_and_multimodal_order(protocol):
    quote = ModelQuote(message_id="original", sender=user("9", "被引用者"), content=(ModelPart(text="原文"),))
    image = ModelPart(type="image", asset=AssetRef(data="data:image/png;base64,aGVsbG8="))
    a = message("1", "你看过了吗？")
    b = message("2", "看过了。", reply_to=quote).model_copy(
        update={"content": (ModelPart(text="图片之前"), image, ModelPart(text="图片之后"))}
    )
    items = [ModelItem.text("system", "规则"), a, b]
    before = [i.model_dump_json() for i in items]
    encoded = encode_items(items, (), protocol)
    assert len(encoded) == 2
    parts = encoded[1]["content"]
    text = "".join(p.get("text", "") for p in parts)
    assert text.count("下面是群聊历史：") == 1
    assert all(
        s in text
        for s in ("用户 ID：1", "用户 ID：2", "平台：qq", "作用域：bot", "2026-11-07T12:30:15+00:00")
    )
    assert "引用发送者：被引用者（用户 ID：9；" in text
    assert all(s in text for s in ("消息id: 1", "消息id: 2", "被引用者", "原文"))
    assert "original" not in text and "引用消息id" not in text
    assert parts[0]["text"].endswith("图片之前")
    assert parts[1]["type"] == ("image_url" if protocol == "chat" else "input_image")
    assert parts[2]["text"].startswith("图片之后")
    assert [i.model_dump_json() for i in items] == before
    assert ModelItem.model_validate_json(b.model_dump_json()) == b


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_assistant_tools_system_and_provider_items_are_merge_boundaries(protocol):
    assistant = ModelItem(role="assistant", calls=(ToolCall(id="c", name="lookup"),))
    result = ModelItem.tool_result("c", "done")
    # Its default role is user, but its kind must prevent merging with ordinary user items.
    assert result.role == "user"
    native = {"type": "reasoning", "id": "r", "encrypted_content": "opaque"}
    if protocol == "chat":
        native = {"role": "assistant", "content": "answer", "reasoning_content": "opaque"}
    opaque = ModelItem(kind="provider", provider_protocol=protocol, provider_item=native)
    system = ModelItem.text("system", "memory")
    developer = ModelItem.text("developer", "instructions")
    a, b = message("1", "A"), message("2", "B")
    items = [a, b, system, a, b, assistant, result, opaque, a, b, developer]
    rendered = render_items(items)
    assert len(rendered) == 8
    assert rendered[1] is system and rendered[3] is assistant and rendered[4] is result
    assert rendered[5] is opaque and rendered[-1] is developer
    encoded = encode_items(items, (), protocol)
    assert native in encoded
    assert any(i.get("tool_call_id", i.get("call_id")) == "c" for i in encoded)


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_appending_after_assistant_or_silence_keeps_the_serialized_prefix(protocol):
    prefix = [ModelItem.text("system", "规则"), message("1", "A"), message("2", "B")]
    initial = encode_items(prefix, (), protocol)
    for response in (
        [ModelItem.text("assistant", "回答")],
        [
            ModelItem(role="assistant", calls=(ToolCall(id="c", name="stay_silent"),)),
            ModelItem.tool_result("c", {"silent": True}),
        ],
    ):
        before = encode_items(prefix + response, (), protocol)
        after = encode_items(prefix + response + [message("3", "C"), message("4", "D")], (), protocol)
        assert before[: len(initial)] == initial
        assert after[: len(before)] == before
        assert len(after) == len(before) + 1


def test_rendering_has_no_hidden_session_state_and_keeps_old_plain_messages_readable():
    a, b, c = [ModelItem.text("user", text) for text in ("old A", "old B", "new C")]
    first, second = render_items([a, b]), render_items([a, b, c])
    assert len(first) == len(second) == 1
    assert "new C" not in first[0].content[0].text
    assert "new C" in second[0].content[0].text
    assert render_items([a])[0] is a


def test_metadata_cannot_be_silently_discarded_on_tool_or_provider_items():
    for fields in (
        {"role": "system"},
        {"kind": "tool_result", "call_id": "c"},
        {"provider_protocol": "chat", "provider_item": {"role": "user", "content": "raw"}},
    ):
        with pytest.raises(ValidationError):
            ModelItem(sender=user(), **fields)


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_quote_snapshot_keeps_sender_and_body_with_or_without_original(protocol):
    conversation = message("1", "").conversation
    original = ChatMessage(
        ref=MessageRef(conversation=conversation, id="a"),
        sender=user("1", "昵称"),
        member=Member(user=user(), group=GroupRef(platform="qq", scope="bot", id="group"), nickname="群名片"),
        content=MessageBuilder()
        .text("雨伞留在教室")
        .mention(user("2").ref)
        .media("image", AssetRef(url="https://unused.test/umbrella"))
        .build(),
        reply_to=MessageQuote(content=MessageBuilder().text("更早的引用不展开").build()),
    )
    reply = ChatMessage(
        ref=MessageRef(conversation=conversation, id="b"),
        sender=user("2"),
        content=MessageBuilder().text("你明天帮我带来吧").build(),
        reply_to=MessageQuote.from_message(original),
    )
    restored = ChatMessage.model_validate_json(reply.model_dump_json())
    assert restored == reply
    assert set(restored.reply_to.model_dump()) == {"sender", "content", "sent_at"}
    assert restored.reply_to.sender.name == "群名片"
    with pytest.raises(ValidationError):
        restored.reply_to.content.segments[0].text = "修改原文"
    a, b = ModelItem.from_message(original), ModelItem.from_message(restored)
    before = b.model_dump_json()
    for history in ([a, b], [b]):
        content = encode_items(history, (), protocol)[0]["content"]
        text = content if isinstance(content, str) else "".join(part["text"] for part in content)
        assert "引用发送者：群名片（用户 ID：1；" in text
        assert "引用原文：\n雨伞留在教室@2[图片]" in text
        assert text.count("雨伞留在教室") == len(history)
    assert "更早的引用" not in str(b) and b.model_dump_json() == before


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_raw_json_bypasses_rendering_and_all_prompt_parameters_but_parses_tools(protocol):
    tool = ToolDefinition(id="p.lookup", name="lookup", system_prompt="MUST_NOT_BE_INJECTED")
    raw = [
        {"role": "system", "content": "custom <persona>"},
        {"role": "user", "content": "A"},
        {"role": "user", "content": "B"},
    ]
    if protocol == "responses":
        raw.insert(1, {"type": "reasoning", "id": "old", "encrypted_content": "opaque"})
    requests = []

    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["messages" if protocol == "chat" else "input"] == raw
        assert "system_prompt_id" not in body and "persona_id" not in body
        assert "MUST_NOT_BE_INJECTED" not in request.content.decode()
        if protocol == "chat":
            payload = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call",
                                    "type": "function",
                                    "function": {"name": tool.wire_name, "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            }
        else:
            payload = {
                "output": [
                    {"type": "function_call", "call_id": "call", "name": tool.wire_name, "arguments": "{}"}
                ]
            }
        return httpx.Response(200, json=payload)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            model = ModelClient(
                ModelCard(id="m", endpoint="https://unused.test/v1", model="m", protocol=protocol),
                http,
                lambda _: "",  # No prompt catalog: invalid card IDs must be ignored in raw mode.
            )
            result = await model.generate(
                json.dumps(raw), tools=[tool], system_prompt_id="missing", persona_id="missing"
            )
            assert result.tool_calls[0].name == tool.id
            assert len(requests) == 1

    asyncio.run(run())


@pytest.mark.parametrize("raw", ["plain text", '{"messages":[]}', '"text"', '["text"]', '[{"x":NaN}]'])
def test_invalid_raw_input_fails_before_network(raw):
    async def run():
        def provider(request):
            pytest.fail("invalid raw input reached provider")

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            model = ModelClient(
                ModelCard(id="m", endpoint="https://unused.test/v1", model="m"), http, lambda _: ""
            )
            with pytest.raises(FrameworkError) as error:
                await model.generate(raw)
            assert error.value.info.code == "invalid_argument"
            assert raw not in str(error.value)

    asyncio.run(run())
