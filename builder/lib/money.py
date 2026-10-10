"""Money helpers. Embeddable in Freya code nodes (no imports needed)."""


def fmt(x):
    return '%.2f' % round(float(x), 2)


def in_bounds(amount, floor, full):
    a = round(float(amount), 2)
    return round(float(floor), 2) <= a <= round(float(full), 2)
