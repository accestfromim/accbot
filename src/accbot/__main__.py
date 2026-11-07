import argparse
import json
import logging
from pathlib import Path

from .config import ConfigStore


def main():
    parser = argparse.ArgumentParser(prog="accbot")
    parser.add_argument("command", choices=["init", "serve", "check"], nargs="?", default="serve")
    parser.add_argument("--data-dir", default=".accbot")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    store = ConfigStore(Path(args.data_dir))
    if args.command == "init":
        settings = store.load()
        if not store.path.exists():
            store.save(settings)
        print(f"Framework configuration: {store.path.resolve()}")
    elif args.command == "check":
        import asyncio

        from .runtime import Runtime

        async def check():
            runtime = Runtime(args.data_dir)
            try:
                runtime.plugins.discover()
                print(json.dumps({"order": runtime.plugins.plan(runtime.settings.active_plugins)}))
            finally:
                await runtime.close()

        asyncio.run(check())
    else:
        import uvicorn

        from .runtime import Runtime
        from .web import create_app

        uvicorn.run(create_app(Runtime(args.data_dir)), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
