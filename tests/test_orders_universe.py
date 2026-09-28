from trading.config import load_exclusions
from trading.orders import fmt_price, round_price, round_qty
from trading.universe import split_multiplier


def test_rounding():
    assert round_price(1.23456, 0.001, up=True) == 1.235
    assert round_price(1.23456, 0.001, up=False) == 1.234
    assert round_qty(12.3456789, 2) == 12.34
    assert round_qty(12.9, 0) == 12.0


def test_fmt_price():
    assert fmt_price(1.5) == "1,5"
    assert fmt_price(0.0000123) == "0,0000123"
    assert fmt_price(84321.5) == "84.321,50"


def test_multiplier_and_exclusions():
    assert split_multiplier("1000PEPE") == ("PEPE", 1000)
    assert split_multiplier("1INCH") == ("1INCH", 1)
    ex = load_exclusions()
    assert ex.is_excluded("USDC") and ex.is_excluded("BTCUP") and ex.is_excluded("WBTC")
    assert not ex.is_excluded("JUP") and not ex.is_excluded("SOL")
