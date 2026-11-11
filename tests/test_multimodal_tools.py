import json

import pytest

from accbot import AssetRef, ModelItem, ModelPart, ToolCall, ToolResult
from accbot.models import count_tokens, encode_items


def picture(label="picture"):
    return ModelPart(type="image", asset=AssetRef(data=f"data:image/png;base64,{label}"))


def test_tool_media_survives_model_item_round_trip_without_json_stringification():
    result = ToolResult(call_id="image", ok=True, value={"message_id": "original", "count": 1})
    item = ModelItem.from_tool_result(result, content=(picture(), ModelPart(text="图片说明")))
    restored = ModelItem.model_validate_json(item.model_dump_json())
    assert restored == item and restored.content[0].type == "image"
    assert "data:image" not in json.dumps(item.output)
    assert count_tokens([item], protocol="responses") >= 8192


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_media_stays_with_its_tool_batch_before_later_assistant_and_tools(protocol):
    items = [
        ModelItem.text("user", "原来的群聊问题"),
        ModelItem(
            role="assistant", calls=(ToolCall(id="image", name="view"), ToolCall(id="other", name="other"))
        ),
        ModelItem.tool_result("image", {"message_id": "original"}, content=(picture(),)),
        ModelItem.tool_result("other", "并行工具结果"),
        ModelItem(role="assistant", calls=(ToolCall(id="later", name="other"),)),
        ModelItem.tool_result("later", "后续工具结果"),
        ModelItem.text("assistant", "后续回复"),
    ]
    payload = encode_items(items, (), protocol)
    prefix = encode_items(items[:4], (), protocol)
    assert payload[: len(prefix)] == prefix
    if protocol == "responses":
        outputs = [i for i in payload if i["type"] == "function_call_output"]
        assert [i["call_id"] for i in outputs] == ["image", "other", "later"]
        assert json.loads(outputs[0]["output"]) == {"message_id": "original"}
        users = [i for i in payload if i.get("role") == "user"]
        assert len(users) == 2
        supplement = users[1]["content"]
        assert "image" in supplement[0]["text"]
        assert supplement[1] == {"type": "input_image", "image_url": "data:image/png;base64,picture"}
        assert "不是新的用户发言或请求" in supplement[-1]["text"]
        assert payload.index(outputs[1]) < payload.index(users[1]) < payload.index(outputs[2])
    else:
        assert [i["role"] for i in payload] == [
            "user",
            "assistant",
            "tool",
            "tool",
            "user",
            "assistant",
            "tool",
            "assistant",
        ]
        supplement = payload[4]["content"]
        assert "image" in supplement[0]["text"]
        assert supplement[1]["image_url"]["url"] == "data:image/png;base64,picture"
        assert "不是新的用户发言或请求" in supplement[-1]["text"]
        assert payload[2]["tool_call_id"] == "image" and payload[3]["tool_call_id"] == "other"
    assert "data:image" not in str(payload[-2:])


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_parallel_media_follows_all_results_in_the_same_batch(protocol):
    payload = encode_items(
        [
            ModelItem(role="assistant", calls=(ToolCall(id="a", name="view"), ToolCall(id="b", name="view"))),
            ModelItem.tool_result("a", "a result", content=(picture("a"),)),
            ModelItem.tool_result("b", "b result", content=(picture("b"),)),
        ],
        (),
        protocol,
    )
    if protocol == "chat":
        assert [i["role"] for i in payload] == ["assistant", "tool", "tool", "user"]
    else:
        assert [i["type"] for i in payload] == [
            "function_call",
            "function_call",
            "function_call_output",
            "function_call_output",
            "message",
        ]
        assert payload[-1]["role"] == "user"
    content = payload[-1]["content"]
    assert "调用 a" in content[0]["text"] and "调用 b" in content[2]["text"]
    images = [p for p in content if p["type"] in {"image_url", "input_image"}]
    assert [p["image_url"]["url"] if protocol == "chat" else p["image_url"] for p in images] == [
        "data:image/png;base64,a",
        "data:image/png;base64,b",
    ]


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_text_only_tool_parts_do_not_create_supplementary_user_turns(protocol):
    payload = encode_items([ModelItem.tool_result("a", content=(ModelPart(text="正文"),))], (), protocol)
    assert len(payload) == 1
    assert payload[0]["content" if protocol == "chat" else "output"] == [
        {"type": "text" if protocol == "chat" else "input_text", "text": "正文"}
    ]
