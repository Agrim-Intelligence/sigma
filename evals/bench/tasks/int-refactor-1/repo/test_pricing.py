from pricing import price_for_guest, price_for_member


def test_member_price():
    assert price_for_member(100) == 108.0


def test_guest_small_order_pays_full_price():
    assert price_for_guest(50) == 60.0


def test_guest_large_order_gets_discount():
    assert price_for_guest(200) == 228.0


def test_shared_pricing_function_exists():
    import pricing

    assert pricing.price_with(100, 0.1) == 108.0
