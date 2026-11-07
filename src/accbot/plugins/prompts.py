"""Prompt files captured with a plugin activation, never read again by old tasks."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..errors import ErrorCode, FrameworkError


@dataclass(frozen=True)
class PromptBundle:
    files: dict[str, str]

    @classmethod
    def capture(cls, directory: Path):
        root = directory / "prompts"
        files = {}
        if root.exists():
            if not root.resolve().is_relative_to(directory.resolve()):
                raise FrameworkError(ErrorCode.CONFIGURATION, "提示词目录不能指向插件包外部")
            for file in sorted(root.rglob("*.md")):
                relative = file.relative_to(root)
                if not file.is_file() or any(part.startswith(".") for part in relative.parts):
                    continue
                if not file.resolve().is_relative_to(root.resolve()):
                    raise FrameworkError(ErrorCode.CONFIGURATION, "提示词文件不能指向提示词目录外部")
                if file.stat().st_size > 256 * 1024:
                    raise FrameworkError(ErrorCode.CONFIGURATION, "提示词文件超过 256 KiB")
                try:
                    files[relative.as_posix()] = file.read_text("utf-8")
                except UnicodeError as exc:
                    raise FrameworkError(ErrorCode.CONFIGURATION, "提示词文件必须为 UTF-8") from exc
        return cls(files)

    @property
    def system(self):
        return self.files.get("system.md", "")

    def tool(self, filename):
        if not isinstance(filename, str):
            raise FrameworkError(ErrorCode.CONFIGURATION, "prompt_file 必须是 Markdown 文件名")
        path = PurePosixPath(filename)
        if path.is_absolute() or ".." in path.parts or "\\" in filename or path.suffix != ".md":
            raise FrameworkError(
                ErrorCode.CONFIGURATION, "prompt_file 必须是 prompts/tools/ 内的 Markdown 文件"
            )
        try:
            return self.files[f"tools/{path}"]
        except KeyError:
            raise FrameworkError(
                ErrorCode.CONFIGURATION, f"缺少工具提示词文件：prompts/tools/{path}"
            ) from None
