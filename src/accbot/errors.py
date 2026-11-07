"""Framework errors. Plugin exceptions need not inherit from these classes."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ErrorCode(StrEnum):
    CONFIGURATION = "configuration"
    DEPENDENCY = "dependency"
    INVALID_ARGUMENT = "invalid_argument"
    PROVIDER = "provider"
    TIMEOUT = "timeout"
    UNSUPPORTED = "unsupported"
    UNKNOWN_EVENT = "unknown_event"
    INACTIVE = "inactive"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    BACKPRESSURE = "backpressure"
    PLUGIN = "plugin"
    CLOSED = "closed"


class Outcome(StrEnum):
    FAILED = "failed"
    UNKNOWN = "unknown"


class ErrorInfo(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: ErrorCode
    message: str
    operation_id: str | None = None
    outcome: Outcome = Outcome.FAILED


class FrameworkError(Exception):
    def __init__(self, code: ErrorCode, message: str, *, operation_id=None, outcome=Outcome.FAILED):
        super().__init__(message)
        self.info = ErrorInfo(code=code, message=message, operation_id=operation_id, outcome=outcome)


def error_info(exc: Exception, operation_id: str | None = None) -> ErrorInfo:
    if isinstance(exc, FrameworkError):
        return exc.info.model_copy(update={"operation_id": exc.info.operation_id or operation_id})
    if isinstance(exc, TimeoutError):
        return ErrorInfo(
            code=ErrorCode.TIMEOUT,
            message="operation timed out",
            operation_id=operation_id,
            outcome=Outcome.UNKNOWN,
        )
    return ErrorInfo(code=ErrorCode.PLUGIN, message=f"{type(exc).__name__}: {exc}", operation_id=operation_id)
