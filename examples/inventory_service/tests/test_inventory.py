import pytest

from inventory.manager import InventoryManager
from inventory.pricing import normalize_sku


def test_add_reserve_and_release_flow():
    mgr = InventoryManager()
    assert mgr.add_item("sku-101", 20) == 20
    assert mgr.available_quantity("SKU-101") == 20

    assert mgr.reserve("sku-101", 6) == 14
    assert mgr.release("SKU-101", 2) == 16


def test_over_reservation_is_blocked():
    mgr = InventoryManager()
    mgr.add_item("ITEM-9", 5)
    with pytest.raises(ValueError, match="Insufficient stock"):
        mgr.reserve("ITEM-9", 6)


def test_normalize_sku_validation():
    assert normalize_sku("  ab-12  ") == "AB-12"
    with pytest.raises(ValueError):
        normalize_sku("bad sku!")
    with pytest.raises(TypeError):
        normalize_sku(12345)  # type: ignore[arg-type]
