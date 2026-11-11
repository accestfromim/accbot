from accbot import AssetRef, ModelItem, ModelPart, ModelQuote, ToolDefinition, User, UserRef
from accbot.models import count_text_tokens, count_tokens, parse_response, render_items


def test_deepseek_text_counter_is_local_and_counts_control_spellings_as_text():
    assert count_text_tokens("") == 0
    assert count_text_tokens(" x" * 1000) == 1000
    assert count_text_tokens("你好，群友。") == 5
    assert count_text_tokens("<｜begin▁of▁sentence｜>") > 1


def test_budget_uses_rendered_metadata_quotes_and_user_boundaries():
    user = User(ref=UserRef(platform="qq", scope="test", id="100"), name="群友")
    first = ModelItem.text(
        "user", "借伞", sender=user, reply_to=ModelQuote(sender=user, content=(ModelPart(text="原文"),))
    )
    second = ModelItem.text("user", "谢谢", sender=user)
    assert count_tokens([first, second]) == count_tokens(render_items([first, second]))
    assert count_tokens([first]) > count_tokens([ModelItem.text("user", "借伞")])
    assert count_tokens([first, ModelItem.text("assistant", "好的"), second]) > count_tokens([first, second])


def test_provider_output_is_counted_once_with_reasoning():
    def output(text, reasoning=""):
        return parse_response(
            {
                "choices": [
                    {"message": {"role": "assistant", "content": text, "reasoning_content": reasoning}}
                ]
            },
            "chat",
            (),
        ).items

    assert count_tokens(output(" x" * 1000)) - count_tokens(output("")) == 1000
    assert count_tokens(output("", " x" * 1000)) - count_tokens(output("")) == 1000
    native = parse_response(
        {"output": [{"type": "reasoning", "summary": [{"text": "推理内容"}]}]}, "responses", ()
    )
    assert count_tokens(native.items) > 0


def test_media_uses_fixed_allowance_without_counting_base64():
    def picture(size):
        return ModelItem(
            role="user",
            content=(ModelPart(type="image", asset=AssetRef(data="data:image/png;base64," + "A" * size)),),
        )

    assert count_tokens([picture(20)]) == count_tokens([picture(100000)])
    assert count_tokens([picture(20)]) >= 8192
    file = ModelItem(
        role="user", content=(ModelPart(type="file", asset=AssetRef(url="https://unused.test/document.pdf")),)
    )
    assert count_tokens([file], protocol="responses") >= 8192


def test_tool_schemas_and_results_contribute_to_budget():
    tool = ToolDefinition(id="test.lookup", name="lookup", parameters={"type": "object", "properties": {}})
    message = ModelItem.text("user", "查询")
    assert count_tokens([message], [tool]) > count_tokens([message])
    assert count_tokens([ModelItem.tool_result("call", " x" * 1000)]) > 1000
