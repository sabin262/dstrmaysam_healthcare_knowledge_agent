from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


def retry_transient(func: Callable[..., T]) -> Callable[..., T]:
    try:
        from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

        return retry(
            reraise=True,
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=0.5, max=8),
            retry=retry_if_exception_type(Exception),
        )(func)
    except Exception:
        def wrapper(*args, **kwargs):
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(0.5 * (2**attempt))
            assert last_error is not None
            raise last_error

        return wrapper
