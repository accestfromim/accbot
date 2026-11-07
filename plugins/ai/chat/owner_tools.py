"""Optional owner-private tools. Authorization comes from the actual inbound message."""

import asyncio
import base64
import codecs
import os
import signal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from accbot import Tool, ToolContext


class OwnerTools:
    def __init__(self, authorize):
        self.authorize = authorize

    @Tool(prompt_file="read_file.md")
    def read_file(
        self,
        path: str,
        ctx: ToolContext,
        offset: Annotated[int, Field(ge=0)] = 0,
        max_bytes: Annotated[int, Field(ge=1, le=65536)] = 32768,
        encoding: Literal["utf-8", "base64"] = "utf-8",
    ):
        """主人私聊：读取进程有权访问的普通文件，按字节分页，返回 next_offset。"""
        self.authorize(ctx)
        file = Path(path).expanduser().resolve()
        if not file.is_file():
            raise ValueError("路径不是普通文件")
        with file.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(max_bytes)
        consumed = len(data)
        if encoding == "base64":
            content = base64.b64encode(data).decode()
        else:
            decoder = codecs.getincrementaldecoder("utf-8")()
            content = decoder.decode(data, final=offset + consumed >= file.stat().st_size)
            consumed -= len(decoder.getstate()[0])
            if data and not consumed:
                raise ValueError("max_bytes 太小，无法容纳一个 UTF-8 字符")
        return {
            "path": str(file),
            "content": content,
            "encoding": encoding,
            "next_offset": offset + consumed,
            "eof": offset + consumed >= file.stat().st_size,
        }

    @Tool(prompt_file="write_file.md")
    def write_file(
        self,
        path: str,
        content: str,
        ctx: ToolContext,
        mode: Literal["overwrite", "append"] = "overwrite",
        encoding: Literal["utf-8", "base64"] = "utf-8",
        create_parent_directories: bool = False,
    ):
        """主人私聊：覆盖或追加服务器文件，单次最多 256 KiB；创建父目录需显式指定。"""
        self.authorize(ctx)
        data = content.encode() if encoding == "utf-8" else base64.b64decode(content, validate=True)
        if len(data) > 256 * 1024:
            raise ValueError("单次写入超过 256 KiB")
        file = Path(path).expanduser().resolve()
        if file.exists() and not file.is_file():
            raise ValueError("路径不是普通文件")
        if create_parent_directories:
            file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("ab" if mode == "append" else "wb") as stream:
            stream.write(data)
        return {"path": str(file), "bytes_written": len(data), "mode": mode}

    @Tool(timeout=310, prompt_file="execute_shell.md")
    async def execute_shell(
        self,
        command: Annotated[str, Field(min_length=1, max_length=16384)],
        ctx: ToolContext,
        working_directory: str = "",
        timeout_seconds: Annotated[int, Field(ge=1, le=300)] = 30,
    ):
        """主人私聊：执行独立的非交互 Bash；超时终止进程组，合并输出最多保留 64 KiB。"""
        self.authorize(ctx)
        process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-lc",
            command,
            cwd=working_directory or None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        output = bytearray()
        truncated = False

        async def collect():
            nonlocal truncated
            while chunk := await process.stdout.read(8192):
                remaining = 65536 - len(output)
                output.extend(chunk[:remaining])
                truncated |= len(chunk) > remaining
            await process.wait()

        timed_out = False
        try:
            async with asyncio.timeout(timeout_seconds):
                await collect()
        except TimeoutError:
            timed_out = True
        finally:
            # Also close children that inherited the pipe when Bash exits early.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        return {
            "output": output.decode("utf-8", errors="replace"),
            "exit_code": process.returncode,
            "timed_out": timed_out,
            "truncated": truncated,
        }
