"""Prices for an order, for members and for guests."""

TAX_RATE = 0.2


def price_for_member(subtotal):
    """Members get 10 percent off, then tax; the result is rounded to cents."""
    discounted = subtotal * 0.9
    taxed = discounted * (1 + TAX_RATE)
    return round(taxed, 2)


def price_for_guest(subtotal):
    """Guests get 5 percent off orders of 100 or more, then tax; rounded to cents."""
    discounted = subtotal * 0.95 if subtotal >= 100 else subtotal
    taxed = discounted * (1 + TAX_RATE)
    return round(taxed, 2)
