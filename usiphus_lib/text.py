"""Small text normalisers.  The two functions are deliberately different; pick by intent."""
from __future__ import annotations

from typing import Any


def clean(value: Any) -> str:
    """Text with surrounding whitespace removed and every internal whitespace run collapsed to one space.

    ``None`` and other falsy values become ``""``.
    """
    return " ".join(str(value or "").strip().split())


def strip_text(value: Any) -> str:
    """Text with only the surrounding whitespace removed (internal spacing preserved).

    ``None`` and other falsy values become ``""``.
    """
    return str(value or "").strip()
