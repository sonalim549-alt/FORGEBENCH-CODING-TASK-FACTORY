from __future__ import annotations


class InventoryManager:
    """Tracks warehouse SKU stock levels and order reservations."""

    def __init__(self) -> None:
        self._stock: dict[str, int] = {}
        self._reserved: dict[str, int] = {}

    def add_item(self, sku: str, quantity: int) -> int:
        if not isinstance(sku, str) or not sku.strip():
            raise ValueError("SKU must be a non-empty string.")
        if quantity < 0:
            raise ValueError("Quantity cannot be negative.")
        key = sku.strip().upper()
        self._stock[key] = self._stock.get(key, 0) + quantity
        return self._stock[key]

    def available_quantity(self, sku: str) -> int:
        key = sku.strip().upper()
        return self._stock.get(key, 0) - self._reserved.get(key, 0)

    def reserve(self, sku: str, quantity: int) -> int:
        if quantity <= 0:
            raise ValueError("Reservation quantity must be positive.")
        key = sku.strip().upper()
        avail = self.available_quantity(key)
        if quantity > avail:
            raise ValueError(f"Insufficient stock for {key}: requested {quantity}, available {avail}.")
        self._reserved[key] = self._reserved.get(key, 0) + quantity
        return self.available_quantity(key)

    def release(self, sku: str, quantity: int) -> int:
        if quantity <= 0:
            raise ValueError("Release quantity must be positive.")
        key = sku.strip().upper()
        current = self._reserved.get(key, 0)
        if quantity > current:
            raise ValueError(f"Cannot release {quantity}; only {current} reserved.")
        self._reserved[key] = current - quantity
        return self.available_quantity(key)
