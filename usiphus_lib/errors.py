"""Common base class for engine errors."""
from __future__ import annotations


class UsiphusError(ValueError):
    """Base of every Usiphus engine error.

    It remains a ``ValueError`` so existing ``except ValueError`` handlers keep working, while new code can
    catch ``UsiphusError`` to handle "an engine rejected its input" without also catching programming errors.
    """
