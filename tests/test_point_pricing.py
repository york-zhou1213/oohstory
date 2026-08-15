from __future__ import annotations

from app.point_pricing import deconstruction_point_units, point_label, point_value


def test_deconstruction_review_reward_rounds_half_up_to_hundredths() -> None:
    cases = {
        0: 0,
        1_499: 0,
        1_500: 1,
        150_000: 50,
        270_000: 90,
        298_500: 100,
        300_000: 100,
        450_000: 150,
    }
    assert {characters: deconstruction_point_units(characters) for characters in cases} == cases
    assert point_value(50) == 0.5
    assert point_value(90) == 0.9
    assert point_value(100) == 1
    assert point_label(150) == "1.5"
