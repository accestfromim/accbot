import importlib.abc
import importlib.util
import sys
from pathlib import Path


class _SnapshotLoader(importlib.abc.Loader):
    def __init__(self, code: bytes, filename: str):
        self.code, self.filename = code, filename

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.__file__ = self.filename
        exec(compile(self.code, self.filename, "exec"), module.__dict__)


class _GenerationFinder(importlib.abc.MetaPathFinder):
    def __init__(self, namespace: str, root: Path, *, sources=None):
        self.namespace, self.root = namespace, root
        self.sources = (
            sources
            if sources is not None
            else {
                str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
                for p in root.rglob("*.py")
                if "__pycache__" not in p.parts
            }
        )

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(self.namespace + "."):
            return None
        relative = fullname[len(self.namespace) + 1 :].replace(".", "/")
        for candidate, package in ((relative + "/__init__.py", True), (relative + ".py", False)):
            if candidate in self.sources:
                return importlib.util.spec_from_loader(
                    fullname,
                    _SnapshotLoader(self.sources[candidate], str(self.root / candidate)),
                    is_package=package,
                )
        # Do not fall through to the filesystem for missing source files in this generation.
        raise ModuleNotFoundError(f"module not in plugin source snapshot: {fullname}")

    def install(self):
        loader = _SnapshotLoader(self.sources.get("__init__.py", b""), str(self.root / "__init__.py"))
        spec = importlib.util.spec_from_loader(self.namespace, loader, is_package=True)
        module = importlib.util.module_from_spec(spec)
        module.__path__ = [str(self.root)]
        sys.modules[self.namespace] = module
        sys.meta_path.insert(0, self)
        loader.exec_module(module)

    def remove(self):
        if self in sys.meta_path:
            sys.meta_path.remove(self)
        for name in tuple(sys.modules):
            if name == self.namespace or name.startswith(self.namespace + "."):
                sys.modules.pop(name, None)
