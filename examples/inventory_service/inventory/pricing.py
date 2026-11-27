from __future__ import annotations

import re

SKU_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-]{1,23}$")


def normalize_sku(raw_sku: str) -> str:
    """Normalize and validate an incoming warehouse SKU code."""
    if not isinstance(raw_sku, str):
        raise TypeError("SKU must be a string.")
    cleaned = raw_sku.strip().upper()
    if not SKU_RE.match(cleaned):
        raise ValueError(f"Invalid SKU format: {raw_sku!r}")
    return cleaned
