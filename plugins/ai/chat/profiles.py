"""Shared user portraits, selected by typed context identities rather than names."""

import hashlib
from dataclasses import dataclass
from urllib.parse import quote

from accbot import ModelItem, UserRef

from .identity import MemoryKey
from .storage import atomic_text

MAX_PROFILE_BYTES = 16 * 1024


@dataclass(frozen=True)
class Profile:
    content: str = ""
    revision: str = ""


def participants(items):
    users = {}
    for item in items:
        if item.role != "user":
            continue
        for sender in (item.sender, item.reply_to.sender if item.reply_to else None):
            if sender is not None:
                users[MemoryKey.of_user(sender.ref)] = sender.ref
    return users


class Profiles:
    def __init__(self, root):
        self.directory = root / "profiles"

    def path(self, user: UserRef):
        key = MemoryKey.of_user(user)

        def part(value):
            return quote(value, safe="").replace(".", "%2E")

        directory = self.directory / part(key.platform)
        if key.platform != "qq":
            directory /= "scope-" + part(key.scope)
        return directory / f"user-{part(key.id)}.md"

    def read(self, user):
        path = self.path(user)
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_PROFILE_BYTES + 1)
        except FileNotFoundError:
            return Profile()
        if len(data) > MAX_PROFILE_BYTES:
            raise ValueError("用户画像超过 16 KiB")
        return Profile(data.decode("utf-8"), hashlib.sha256(data).hexdigest())

    def update(self, user, content, revision):
        if not content.strip() or len(content.encode("utf-8")) > MAX_PROFILE_BYTES:
            raise ValueError("用户画像需要非空 Markdown，最多 16 KiB")
        current = self.read(user)
        if current.revision != revision:
            return {
                "updated": False,
                "conflict": True,
                "revision": current.revision,
            }
        # No await between comparison and replacement: concurrent conversations cannot
        # overwrite a newer portrait in this process using an obsolete revision.
        atomic_text(self.path(user), content)
        return {"updated": True, "revision": hashlib.sha256(content.encode("utf-8")).hexdigest()}

    def items(self, messages, cache):
        result = []
        for key, user in participants(messages).items():
            if key not in cache:
                cache[key] = self.read(user)
            profile = cache[key]
            if profile.content:
                result.append(
                    ModelItem.text(
                        "system",
                        "用户画像（背景资料）：\n"
                        f"平台：{key.platform}\n作用域：{key.scope}\n用户 ID：{key.id}\n"
                        f"revision：{profile.revision}\n\n{profile.content}",
                    )
                )
        return result
