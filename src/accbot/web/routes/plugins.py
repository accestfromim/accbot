from fastapi import APIRouter
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from ...errors import FrameworkError
from ..schemas import Selection


def create_router(runtime):
    router = APIRouter(prefix="/api/plugins")

    @router.get("")
    async def listing():
        manager = runtime.plugins
        result = []
        for manifest in manager.manifests.values():
            relative = manager.paths[manifest.id].relative_to(manager.directory).as_posix()
            group = relative.rsplit("/", 1)[0] if "/" in relative else ""
            dependents = sorted(
                key for key in manager.active if manifest.id in manager.manifests[key].requires
            )
            result.append(
                {
                    **manifest.model_dump(mode="json"),
                    "active": manifest.id in manager.active,
                    "directory": relative,
                    "group": group,
                    "has_frontend": (
                        "index.html" in manager.frontends[manifest.id][0].files
                        if manifest.id in manager.active
                        else (manager.paths[manifest.id] / "frontend" / "index.html").is_file()
                    ),
                    "required_by": dependents,
                    "activation_id": manager.instances[manifest.id][1].id
                    if manifest.id in manager.active
                    else None,
                }
            )
        return sorted(result, key=lambda item: (item["group"], item["directory"]))

    @router.post("/discover")
    async def discover():
        async with runtime.management_lock:
            runtime.plugins.refresh()
            return await listing()

    @router.post("/plan")
    async def plan(body: Selection):
        return {"order": runtime.plugins.plan(body.selected)}

    @router.post("/preview")
    async def preview(body: Selection):
        manager, selected = runtime.plugins, set(body.selected)
        issues, recommended, visited = [], set(), set()

        def inspect(key):
            if key in visited:
                return
            visited.add(key)
            if key not in manager.manifests:
                issues.append({"plugin": key, "kind": "missing", "message": f"未找到插件 {key}"})
                return
            for dep, spec in manager.manifests[key].requires.items():
                target = manager.manifests.get(dep)
                try:
                    compatible = target is not None and Version(target.version) in SpecifierSet(spec)
                except InvalidSpecifier:
                    issues.append(
                        {
                            "plugin": key,
                            "dependency": dep,
                            "kind": "version",
                            "message": f"{key} 为 {dep} 声明了无效版本约束：{spec}",
                        }
                    )
                    continue
                if target is None:
                    issues.append(
                        {
                            "plugin": key,
                            "dependency": dep,
                            "kind": "missing",
                            "message": f"{key} 依赖 {dep}，但插件目录中未找到它",
                        }
                    )
                elif not compatible:
                    issues.append(
                        {
                            "plugin": key,
                            "dependency": dep,
                            "kind": "version",
                            "message": f"{dep} 需要 {spec}，当前为 {target.version}",
                        }
                    )
                else:
                    if dep not in selected:
                        recommended.add(dep)
                        issues.append(
                            {
                                "plugin": key,
                                "dependency": dep,
                                "kind": "unselected",
                                "message": f"{key} 需要同时勾选 {dep}",
                            }
                        )
                    inspect(dep)

        for key in sorted(selected):
            inspect(key)
        order = []
        try:
            order = manager.plan(selected)
        except FrameworkError as exc:
            if not issues:
                issues.append({"kind": "dependency", "message": exc.info.message})
        return {"valid": not issues, "order": order, "issues": issues, "recommended": sorted(recommended)}

    @router.put("/active")
    async def activate(body: Selection):
        return {"active": await runtime.set_active(body.selected)}

    @router.post("/{plugin_id}/reload")
    async def reload(plugin_id: str):
        return await runtime.reload(plugin_id)

    @router.post("/{plugin_id}/unload")
    async def unload(plugin_id: str):
        return await runtime.unload(plugin_id)

    return router
