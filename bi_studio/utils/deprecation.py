"""Small helpers for soft-deprecating legacy BI Studio entrypoints."""
from __future__ import annotations

import warnings


def warn_deprecated(name: str, replacement: str | None = None) -> None:
    message = f"{name} is deprecated"
    if replacement:
        message = f"{message}; use {replacement} instead"
    warnings.warn(message, DeprecationWarning, stacklevel=2)
