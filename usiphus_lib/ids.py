"""Stable identifiers shared by several engines."""
from __future__ import annotations


def objective_id(topic_id: str, index: int) -> str:
    """Identifier of the ``index``-th (1-based) learning objective of a topic, e.g. ``"retina-01-LO02"``.

    Resource discovery, evidence packets and curriculum coverage must all agree on this format.
    """
    return f"{topic_id}-LO{index:02d}"
