import asyncio
import importlib
import inspect
import logging
import tomllib
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from ..errors import ErrorCode, FrameworkError
from ..lifecycle import Activation
from ..resources import ResourceBundle
from ..types import new_id
from .base import ApiHandle, Plugin, PluginContext
from .frontend import FrontendBundle
from .loader import _GenerationFinder
from .manifest import PluginManifest
from .prompts import PromptBundle

log = logging.getLogger(__name__)


class PluginManager:
    def __init__(self, runtime, directory: Path, data_root: Path):
        self.runtime, self.directory, self.data_root = runtime, directory, data_root
        self.manifests: dict[str, PluginManifest] = {}
        self.paths: dict[str, Path] = {}
        self.instances: dict[str, tuple[Plugin, Activation]] = {}
        self.apis: dict[str, ApiHandle] = {}
        self.frontends: dict[str, tuple[FrontendBundle, dict[str, bytes]]] = {}
        self.frontend_epochs: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def discover(self):
        manifests, paths = {}, {}
        for path in sorted(self.directory.rglob("plugin.toml")):
            relative = path.relative_to(self.directory)
            if any(part.startswith(".") or part == "__pycache__" for part in relative.parts[:-1]):
                continue
            if any(
                (parent / "plugin.toml").exists()
                for parent in path.parent.parents
                if parent != self.directory and parent.is_relative_to(self.directory)
            ):
                continue
            try:
                manifest = PluginManifest.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:
                raise FrameworkError(ErrorCode.CONFIGURATION, f"invalid plugin manifest: {path}") from exc
            if manifest.id in manifests:
                raise FrameworkError(ErrorCode.CONFLICT, f"duplicate plugin id: {manifest.id}")
            manifests[manifest.id], paths[manifest.id] = manifest, path.parent
        self.manifests, self.paths = manifests, paths
        return tuple(manifests.values())

    @property
    def active(self) -> set[str]:
        return {key for key, (_, owner) in self.instances.items() if owner.active}

    def refresh(self):
        """Discover installs without replacing metadata of running source generations."""
        previous_manifests, previous_paths = self.manifests, self.paths
        self.discover()
        for key in self.active:
            self.manifests[key] = previous_manifests[key]
            self.paths[key] = previous_paths[key]
        return tuple(self.manifests.values())

    def plan(self, selected) -> list[str]:
        selected = set(selected)
        order, visiting, visited = [], set(), set()

        def visit(key):
            if key in visiting:
                raise FrameworkError(ErrorCode.DEPENDENCY, f"dependency cycle includes: {key}")
            if key in visited:
                return
            if key not in self.manifests:
                raise FrameworkError(ErrorCode.DEPENDENCY, f"plugin not found: {key}")
            visiting.add(key)
            for dep, spec in self.manifests[key].requires.items():
                if dep not in selected:
                    raise FrameworkError(ErrorCode.DEPENDENCY, f"{key} requires selected plugin {dep}")
                if dep not in self.manifests:
                    raise FrameworkError(ErrorCode.DEPENDENCY, f"dependency not found: {dep}")
                try:
                    compatible = Version(self.manifests[dep].version) in SpecifierSet(spec)
                except (InvalidVersion, InvalidSpecifier) as exc:
                    raise FrameworkError(
                        ErrorCode.DEPENDENCY, f"invalid dependency version for {key}"
                    ) from exc
                if not compatible:
                    raise FrameworkError(ErrorCode.DEPENDENCY, f"{key} requires {dep}{spec}")
                visit(dep)
            visiting.remove(key)
            visited.add(key)
            order.append(key)

        for key in sorted(selected):
            visit(key)
        return order

    def _export(self, owner, api):
        if owner.retired:
            raise FrameworkError(ErrorCode.INACTIVE, "cannot export an API for a retired plugin")
        if owner.plugin_id in self.apis:
            raise FrameworkError(ErrorCode.CONFLICT, "plugin already exported an API")
        self.apis[owner.plugin_id] = ApiHandle(owner, api)
        owner.own(lambda: self.apis.pop(owner.plugin_id, None))

    def require(self, plugin_id) -> ApiHandle:
        handle = self.apis.get(plugin_id)
        if handle is None:
            raise FrameworkError(ErrorCode.NOT_FOUND, f"plugin API not found: {plugin_id}")
        handle._owner.ensure_active()
        return handle

    async def _activate(self, key):
        owner = Activation(key)
        namespace = f"_accbot_{key.replace('.', '_').replace('-', '_')}_{owner.id}"
        finder = _GenerationFinder(namespace, self.paths[key])
        owner.finalize_when_idle(finder.remove)
        try:
            manifest = self.manifests[key]
            self.frontends[key] = (FrontendBundle.capture(self.paths[key]), finder.sources)
            owner.own(lambda: self.frontends.pop(key, None))
            if manifest.kind == "code":
                finder.install()
                module_name, class_name = manifest.entrypoint.split(":")
                module = importlib.import_module(f"{namespace}.{module_name}")
                cls = getattr(module, class_name)
                if not issubclass(cls, Plugin):
                    raise FrameworkError(ErrorCode.CONFIGURATION, "entrypoint must subclass accbot.Plugin")
            else:
                cls = Plugin
            data_dir = self.data_root / key
            data_dir.mkdir(parents=True, exist_ok=True)
            prompts = PromptBundle.capture(self.paths[key])
            self.runtime.prompts.register(owner, prompts.system)
            context = PluginContext(self.runtime, self, owner, data_dir, prompt_bundle=prompts)
            instance = cls(context)
            if manifest.kind == "resource":
                context.export(ResourceBundle(self.paths[key]))
            for _, method in inspect.getmembers(instance, callable):
                for event_class, where in getattr(method, "__accbot_events__", ()):
                    context.subscribe(event_class, method, where=where.bind(instance) if where else None)
                if hasattr(method, "__accbot_tool__"):
                    context.register_tool(method)
            await instance.on_activate()
            self.instances[key] = (instance, owner)
            owner.activate()
        except BaseException:
            owner.retire()
            raise

    async def activate(self, selected):
        async with self._lock:
            order = self.plan(self.active | set(selected))
            activated = []
            try:
                for key in order:
                    if key not in self.active:
                        await self._activate(key)
                        activated.append(key)
            except BaseException:
                for key in reversed(activated):
                    await self._deactivate(key)
                raise
            return order

    def _affected(self, targets):
        affected = set(targets) & self.active
        while True:
            previous = affected.copy()
            affected |= {key for key in self.active if set(self.manifests[key].requires) & affected}
            if previous == affected:
                return affected

    async def _deactivate(self, key):
        entry = self.instances.pop(key, None)
        if entry:
            instance, owner = entry
            self.frontend_epochs[key] = new_id()
            # Keep the code namespace alive during the deactivation callback as well.
            owner.acquire()
            owner.retire()
            try:
                await instance.on_deactivate()
            except Exception:
                log.exception("plugin deactivation callback failed: %s", key)
            finally:
                owner.release()

    async def deactivate(self, target):
        async with self._lock:
            order = self.plan(self.active)
            affected = self._affected({target})
            for key in reversed(order):
                if key in affected:
                    await self._deactivate(key)
            return sorted(affected)

    async def reload(self, target):
        async with self._lock:
            before = self.active
            if target not in before:
                raise FrameworkError(ErrorCode.INACTIVE, f"cannot reload inactive plugin: {target}")
            old_manifests, old_paths = self.manifests, self.paths
            old_order = self.plan(before)
            affected = self._affected({target})
            try:
                self.discover()
                # Code and metadata of unaffected active instances stay on their old generation.
                for key in before - affected:
                    self.manifests[key] = old_manifests[key]
                    self.paths[key] = old_paths[key]
                order = self.plan(before)
            except BaseException:
                self.manifests, self.paths = old_manifests, old_paths
                raise
            for key in reversed(old_order):
                if key in affected:
                    await self._deactivate(key)
            failed = {}
            for key in order:
                if key not in affected:
                    continue
                unavailable = set(self.manifests[key].requires) - self.active
                if unavailable:
                    failed[key] = f"dependency not active: {', '.join(sorted(unavailable))}"
                    continue
                try:
                    await self._activate(key)
                except Exception as exc:
                    failed[key] = f"{type(exc).__name__}: {exc}"
                    log.exception("plugin reload failed: %s", key)
            return {"active": sorted(self.active), "failed": failed, "affected": sorted(affected)}

    async def close(self):
        async with self._lock:
            for key in reversed(self.plan(self.active)):
                await self._deactivate(key)
