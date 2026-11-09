import asyncio
import shutil

import pytest
from fastapi.testclient import TestClient
from test_ai_plugins import deliver, event
from test_ai_plugins import setup_ai as setup_ai

from accbot import ErrorCode, FrameworkError, GenerationResult, ModelItem, PersonaCard, ToolCall
from accbot.config import ModelCard
from accbot.runtime import Runtime
from accbot.web import create_app


def test_agent_reads_card_edits_on_subsequent_calls_and_memory_summary(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai()
        prompt = runtime.store.prompts_dir / plugin.config.chat_prompt_id / "prompt.md"
        persona = runtime.store.personas_dir / f"{plugin.config.persona_id}.json"
        calls = 0

        async def respond(items, tools, options):
            nonlocal calls
            calls += 1
            if calls == 1:
                # Edit while the agent is running, without a management API call or plugin reload.
                prompt.write_text("DISK_CHAT <persona> <plugins_insert_prompt>", encoding="utf-8")
                original = PersonaCard.model_validate_json(persona.read_text("utf-8"))
                persona.write_text(
                    original.model_copy(update={"system_prompt": "DISK_PERSONA"}).model_dump_json(),
                    encoding="utf-8",
                )
                return GenerationResult(
                    items=(
                        ModelItem(
                            role="assistant",
                            calls=(ToolCall(name="ai.tools.send_text", arguments={"text": "进度"}),),
                        ),
                    )
                )
            assert "DISK_CHAT DISK_PERSONA" in items[0].content[0].text
            return GenerationResult(items=(ModelItem.text("assistant", "完成"),))

        model.responder = respond
        try:
            await deliver(runtime, event())
            assert calls == 2
            channel = next(iter(plugin.sessions.channels.values()))
            memory_prompt = runtime.store.prompts_dir / plugin.config.memory_prompt_id / "prompt.md"
            memory_prompt.write_text("DISK_MEMORY <tools_insert_prompt>", encoding="utf-8")
            plugin.sessions.seal(channel)
            await runtime.jobs.drain()
            assert model.requests[-1][0][0].content[0].text.startswith("DISK_MEMORY")
            assert not channel.records
        finally:
            await runtime.close()

    asyncio.run(run())


def test_unrelated_configuration_saves_do_not_overwrite_disk_cards(workspace):
    runtime = Runtime(workspace[1])
    prompt = runtime.store.prompts_dir / "chat/prompt.md"
    persona = runtime.store.personas_dir / "default.json"
    prompt_text = "MANUAL_TEMPLATE <persona>"
    persona_text = (
        runtime.personas.get("default")
        .model_copy(update={"system_prompt": "MANUAL_PERSONA"})
        .model_dump_json()
    )

    async def change_settings():
        try:
            await runtime.start()
            prompt.write_text(prompt_text, encoding="utf-8")
            persona.write_text(persona_text, encoding="utf-8")
            # No catalog read between the file edits and these unrelated saves.
            await runtime.replace_models([ModelCard(id="test", model="m", endpoint="https://unused.test")])
            await runtime.replace_connections([])
            await runtime.set_active([])
            assert prompt.read_text("utf-8") == prompt_text
            assert persona.read_text("utf-8") == persona_text
            # Editing one catalog must not rewrite the other from stale runtime settings either.
            await runtime.replace_prompts(runtime.prompts.list())
            assert persona.read_text("utf-8") == persona_text
            await runtime.replace_personas(runtime.personas.list())
            assert prompt.read_text("utf-8") == prompt_text
        finally:
            await runtime.close()

    asyncio.run(change_settings())


def test_management_reads_card_files_without_reload(workspace):
    runtime = Runtime(workspace[1])
    with TestClient(create_app(runtime)) as client:
        prompt = runtime.store.prompts_dir / "chat/prompt.md"
        persona = runtime.store.personas_dir / "default.json"
        prompt.write_text("LATEST_TEMPLATE", encoding="utf-8")
        persona.write_text(
            runtime.personas.get("default")
            .model_copy(update={"system_prompt": "LATEST_PERSONA"})
            .model_dump_json(),
            encoding="utf-8",
        )
        assert client.get("/api/prompts/chat").json()["content"] == "LATEST_TEMPLATE"
        assert client.get("/api/personas/default").json()["system_prompt"] == "LATEST_PERSONA"
        settings = client.get("/api/settings").json()
        assert (
            next(c for c in settings["system_prompts"] if c["id"] == "chat")["content"] == "LATEST_TEMPLATE"
        )
        assert settings["personas"][0]["system_prompt"] == "LATEST_PERSONA"


@pytest.mark.parametrize("kind", ["prompt", "persona"])
def test_invalid_or_deleted_card_never_falls_back_to_cached_content(workspace, kind):
    runtime = Runtime(workspace[1])

    async def run():
        try:
            catalog = runtime.prompts if kind == "prompt" else runtime.personas
            key = "chat" if kind == "prompt" else "default"
            path = (
                runtime.store.prompts_dir / "chat/prompt.md"
                if kind == "prompt"
                else runtime.store.personas_dir / "default.json"
            )
            catalog.get(key)
            path.write_text("" if kind == "prompt" else "{", encoding="utf-8")
            with pytest.raises(FrameworkError) as error:
                catalog.get(key)
            assert error.value.info.code == ErrorCode.CONFIGURATION
            if kind == "prompt":
                shutil.rmtree(path.parent)
            else:
                path.unlink()
            with pytest.raises(FrameworkError) as error:
                catalog.get(key)
            assert error.value.info.code == ErrorCode.NOT_FOUND
        finally:
            await runtime.close()

    asyncio.run(run())


def test_fresh_runtime_initializes_file_backed_cards(tmp_path):
    runtime = Runtime(tmp_path / "new")

    async def run():
        try:
            assert runtime.store.path.exists()
            assert runtime.prompts.get("chat").content
            assert runtime.personas.get("default").system_prompt
        finally:
            await runtime.close()

    asyncio.run(run())
