"""Immutable resource bundles; interpretation belongs to consuming plugins."""

from pathlib import Path, PurePosixPath

from .errors import ErrorCode, FrameworkError


class ResourceBundle:
    def __init__(self, directory: Path):
        root = directory.resolve()
        self._files = {}
        for file in sorted(root.rglob("*")):
            if not file.is_file() or "__pycache__" in file.parts or file.name == "plugin.toml":
                continue
            if not file.resolve().is_relative_to(root):
                raise FrameworkError(ErrorCode.CONFIGURATION, "resource link points outside its bundle")
            self._files[file.relative_to(root).as_posix()] = file.read_bytes()

    def list(self) -> tuple[str, ...]:
        return tuple(self._files)

    def read(self, path: str) -> bytes:
        key = PurePosixPath(path)
        if key.is_absolute() or ".." in key.parts or str(key) not in self._files:
            raise FrameworkError(ErrorCode.NOT_FOUND, "resource not found in bundle")
        return self._files[str(key)]

    def read_text(self, path: str, encoding="utf-8") -> str:
        return self.read(path).decode(encoding)
