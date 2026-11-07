import asyncio
import base64
import importlib.util
import io
import socket
import sys
from pathlib import Path

import httpx
import pytest
from PIL import Image

from accbot import AssetRef, ModelItem, ToolCall


def module(name, path):
    # Only pure leaf modules here. Plugin lifecycle integration is tested through PluginManager.
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


context = module("_test_ai_context", "plugins/ai/chat/context.py")
web = module("_test_ai_web", "plugins/ai/tools/web.py")
network = module("_test_ai_network", "plugins/ai/tools/network.py")
vision = module("_test_ai_vision", "plugins/ai/tools/vision.py")


def test_context_trims_whole_turns_and_preserves_current_and_prefix():
    prefix = [ModelItem.text("system", "personality and memory")]
    old = [
        ModelItem.text("user", "old"),
        ModelItem(role="assistant", calls=(ToolCall(id="a", name="web"),)),
        ModelItem.tool_result("a", {"data": "x" * 2000}),
    ]
    recent = [ModelItem.text("user", "recent"), ModelItem.text("assistant", "answer")]
    current = [ModelItem.text("user", "new")]
    budget = context.cost(prefix + recent + current)
    assert context.window(prefix, [old, recent], current, budget) == prefix + recent + current
    with pytest.raises(ValueError):
        context.window(prefix, [], current, 1)
    unfinished = [ModelItem(role="assistant", calls=(ToolCall(id="x", name="send_text"),))]
    repaired = context.close_tool_calls(unfinished)
    assert repaired[-1].call_id == "x" and "结果未知" in repaired[-1].output["error"]
    assert context.close_tool_calls(repaired) == repaired


def test_search_parsing_fallback_and_web_extraction():
    bing = b'<li class="b_algo"><h2><a href="https://example.org">A result</a></h2><div class="b_caption"><p>summary</p></div></li>'
    assert web.search_results(bing, "https://cn.bing.com", 5) == [
        {"title": "A result", "url": "https://example.org", "snippet": "summary"}
    ]
    duck = '<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fpage">Other</a><span class="result__snippet">text</span></div>'
    parsed = web.search_results(duck, "https://html.duckduckgo.com", 1)
    assert parsed[0]["url"] == "https://example.org/page"
    content = web.readable(
        b"<html><title>title</title><script>secret script</script><p>body</p></html>", "text/html"
    )
    assert content["title"] == "title" and "secret script" not in content["text"]
    assert web.readable(b"a" * 21000, "text/plain")["truncated"]
    with pytest.raises(ValueError):
        web.readable(b"binary", "application/octet-stream")

    async def run():
        class HTTP:
            async def get(self, url):
                if "bing" in url:
                    raise OSError("offline")
                return duck, "text/html", url

        result = await web.search(HTTP(), "cats", 5)
        assert result["provider"] == "DuckDuckGo" and result["warnings"]

    asyncio.run(run())


def test_public_http_pins_dns_revalidates_redirect_and_limits_body(monkeypatch):
    async def run():
        loop = asyncio.get_running_loop()
        seen = []

        async def resolve(host, port, **kwargs):
            ip = "127.0.0.1" if host == "private.test" else "93.184.216.34"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

        def respond(request):
            seen.append(request)
            if request.url.path == "/redirect":
                return httpx.Response(302, headers={"location": "http://private.test/internal"})
            return httpx.Response(200, content=b"12345", headers={"content-type": "text/plain"})

        monkeypatch.setattr(loop, "getaddrinfo", resolve)
        client_class = httpx.AsyncClient
        monkeypatch.setattr(
            network.httpx,
            "AsyncClient",
            lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs),
        )
        reader = network.PublicHTTP()
        body, _, url = await reader.get("https://public.test/page")
        assert body == b"12345" and url == "https://public.test/page"
        assert seen[0].url.host == "93.184.216.34"
        assert seen[0].headers["host"] == "public.test"
        assert seen[0].extensions["sni_hostname"] == "public.test"
        for url in ("http://private.test/a", "file:///etc/passwd", "https://user:password@public.test/a"):
            with pytest.raises(ValueError):
                await reader.get(url)
        with pytest.raises(ValueError):
            await reader.get("https://public.test/redirect")
        assert all(r.url.host != "127.0.0.1" for r in seen)
        with pytest.raises(ValueError):
            await reader.get("https://public.test/large", limit=4)

    asyncio.run(run())


def test_vision_format_alpha_resize_and_all_or_error():
    output = io.BytesIO()
    Image.new("RGBA", (2000, 1000), (1, 2, 3, 100)).save(output, "PNG")
    compressed, mime = vision.compress(output.getvalue())
    assert mime == "image/png"
    with Image.open(io.BytesIO(compressed)) as image:
        assert image.size == (1024, 512) and image.mode == "RGBA"

    async def run():
        data = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
        images = await vision.load_images(None, [AssetRef(data=data)])
        assert len(images) == 1
        with pytest.raises(ValueError):
            await vision.load_images(None, [AssetRef(data=data), AssetRef(path="/etc/passwd")])
        with pytest.raises(ValueError):
            await vision.load_images(None, [])

    asyncio.run(run())
