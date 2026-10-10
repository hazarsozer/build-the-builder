from builder.lib import money as M
def test_bounds_inclusive():
    assert M.in_bounds(126, 126.0, 420.0) and M.in_bounds(420.004, 126, 420) and not M.in_bounds(125.99, 126, 420)
def test_fmt(): assert M.fmt(420) == "420.00"
