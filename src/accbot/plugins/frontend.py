"""Versioned, explicitly public plugin frontend files."""

import hashlib
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from ..errors import ErrorCode, FrameworkError

PUBLIC_SUFFIXES = {
    ".html",
    ".js",
    ".css",
    ".json",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".avif",
    ".ico",
    ".woff",
    ".woff2",
}
VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class _ComponentParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.roots = 0

    def invalid(self):
        raise FrameworkError(
            ErrorCode.CONFIGURATION, "frontend/index.html 必须只有一个根 div；脚本请放在 index.js 中"
        )

    def handle_starttag(self, tag, attrs):
        if tag in {"html", "head", "body", "script", "base"}:
            self.invalid()
        if not self.stack:
            self.roots += 1
            if tag != "div" or self.roots != 1:
                self.invalid()
        if tag not in VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.invalid()

    def handle_data(self, data):
        if not self.stack and data.strip():
            self.invalid()

    def handle_decl(self, decl):
        self.invalid()


@dataclass(frozen=True)
class FrontendBundle:
    files: dict[str, bytes]

    @classmethod
    def capture(cls, directory: Path):
        root = directory / "frontend"
        files = {}
        if root.exists():
            if not root.resolve().is_relative_to(directory.resolve()):
                raise FrameworkError(ErrorCode.CONFIGURATION, "插件前端目录不能指向插件包外部")
            for file in sorted(root.rglob("*")):
                relative = file.relative_to(root)
                if (
                    not file.is_file()
                    or any(p.startswith(".") for p in relative.parts)
                    or file.suffix.lower() not in PUBLIC_SUFFIXES
                ):
                    continue
                if not file.resolve().is_relative_to(root.resolve()):
                    raise FrameworkError(ErrorCode.CONFIGURATION, "插件前端资源不能指向前端目录外部")
                files[relative.as_posix()] = file.read_bytes()
        return cls(files)

    def component(self):
        try:
            html = self.read("index.html").decode("utf-8")
        except UnicodeError as exc:
            raise FrameworkError(ErrorCode.CONFIGURATION, "插件前端必须使用 UTF-8 编码") from exc
        parser = _ComponentParser()
        parser.feed(html)
        parser.close()
        if parser.roots != 1 or parser.stack:
            parser.invalid()
        return html

    def read(self, path):
        name = PurePosixPath(path)
        if name.is_absolute() or ".." in name.parts or "\\" in path or str(name) not in self.files:
            raise FrameworkError(ErrorCode.NOT_FOUND, "未找到插件前端资源")
        return self.files[str(name)]

    def revision(self, sources, activation_id):
        digest = hashlib.sha256((activation_id or "inactive").encode())
        for group in (self.files, sources):
            for name, data in sorted(group.items()):
                for item in (name.encode(), data):
                    digest.update(len(item).to_bytes(8, "big"))
                    digest.update(item)
        return digest.hexdigest()
