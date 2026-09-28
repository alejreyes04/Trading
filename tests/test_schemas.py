import pytest
from pydantic import ValidationError

from trading.schemas import Decisions, EntryDecision, check_decisions_against


def entry(sym="A", action="enter", size=1.0):
    return {"symbol": sym, "action": action, "size_mult": size, "p_tp_before_sl": 0.4, "rationale": "x"}


def test_size_mult_cannot_exceed_one():
    with pytest.raises(ValidationError):
        EntryDecision(**entry(size=2.0))
    with pytest.raises(ValidationError):
        EntryDecision(**entry(action="skip", size=1.0))


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        EntryDecision(**entry(), leverage=3)


def test_decisions_must_match_shortlist():
    d = Decisions(run_id="r", generated_at="t", market_view="v", entries=[EntryDecision(**entry("A")),
                                                                          EntryDecision(**entry("Z"))])
    errors = check_decisions_against(d, ["A", "B"], [])
    assert any("Z" in e for e in errors) and any("B" in e for e in errors)
