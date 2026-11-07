"""Ownership of registrations and leases held by calls already in progress."""

import asyncio
import logging
from collections.abc import Callable

from .errors import ErrorCode, FrameworkError
from .types import new_id

log = logging.getLogger(__name__)


class Activation:
    def __init__(self, plugin_id: str):
        self.plugin_id = plugin_id
        self.id = new_id()
        self.active = False
        self.retired = False
        self.inflight = 0
        self.ready = asyncio.Event()
        self._cleanup: list[Callable] = []
        self._finalizers: list[Callable] = []

    def ensure_active(self):
        if not self.active:
            raise FrameworkError(ErrorCode.INACTIVE, f"plugin activation is inactive: {self.plugin_id}")

    def own(self, cleanup: Callable):
        if self.retired:
            raise FrameworkError(ErrorCode.INACTIVE, "activation has retired")
        self._cleanup.append(cleanup)

    def finalize_when_idle(self, callback: Callable):
        self._finalizers.append(callback)

    def activate(self):
        if self.retired:
            raise FrameworkError(ErrorCode.INACTIVE, "cannot reactivate a retired instance")
        self.active = True
        self.ready.set()

    def acquire(self, *, allow_staging=False):
        if not allow_staging or self.retired:
            self.ensure_active()
        self.inflight += 1

    def release(self):
        self.inflight -= 1
        self._finalize()

    def retain_inflight(self):
        """Extend an existing call lease, e.g. for a thread surviving an await timeout."""
        if self.inflight <= 0:
            raise FrameworkError(ErrorCode.INACTIVE, "no existing execution lease")
        self.inflight += 1

    def retire(self):
        self.active = False
        self.retired = True
        self.ready.set()
        for cleanup in reversed(self._cleanup):
            try:
                cleanup()
            except Exception:
                log.exception("registration cleanup failed for %s", self.plugin_id)
        self._cleanup.clear()
        self._finalize()

    def _finalize(self):
        if self.retired and not self.inflight:
            callbacks, self._finalizers = self._finalizers, []
            for callback in callbacks:
                callback()
