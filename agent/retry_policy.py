"""A small, deterministic, bounded retry policy for tool execution
failures -- distinguishes "this was probably a transient blip, worth
trying again" from "this will fail the same way every time, or retrying
it is itself dangerous."

Deliberately conservative: unknown/unrecognized error types default to
NOT retrying. A tool that fails in a way this policy doesn't recognize
should surface that failure clearly rather than silently retrying
something that might not be safe to repeat.
"""
from typing import Optional

MAX_RETRIES = 2

# Failures that are almost certainly transient (network hiccup, rate
# limit, timeout) -- worth one or two automatic retries.
_TRANSIENT_ERROR_TYPES = {
    "ConnectionError",
    "ConnectionResetError",
    "Timeout",
    "TimeoutError",
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "InternalServerError",
}

# Failures that will fail the same way every time -- retrying wastes
# time and can't succeed.
_NEVER_RETRY_ERROR_TYPES = {
    "PermissionError",
    "AuthenticationError",
    "AuthorizationError",
    "KeyError",  # malformed tool input -- won't fix itself
    "ValueError",
    "TypeError",
}

# Tools where a "retry" risks repeating a real-world effect that already
# happened (a click, a keypress, a sent message, a finalized login) --
# never auto-retried regardless of the error type, since the first
# attempt may have partially succeeded even though it raised.
_NEVER_AUTO_RETRY_TOOLS = {
    "confirm_login",
    "send_email",
    "computer_click",
    "computer_type",
    "computer_press_key",
    "computer_confirm_action",
}


def should_retry(tool_name: str, error: BaseException, attempt: int) -> bool:
    """attempt is 1-indexed: the attempt number that just failed. Returns
    True if another attempt should be made."""

    if attempt >= MAX_RETRIES:
        return False

    if tool_name in _NEVER_AUTO_RETRY_TOOLS:
        return False

    error_type = type(error).__name__

    if error_type in _NEVER_RETRY_ERROR_TYPES:
        return False

    return error_type in _TRANSIENT_ERROR_TYPES


def retry_delay_seconds(attempt: int) -> float:
    """Simple linear backoff -- this isn't a high-volume system where
    exponential backoff matters; a short, predictable pause is enough to
    let a transient blip clear."""
    return 0.5 * attempt
