import pytest

from docling_desk.translation.fit import MIN_SCALE, fitted_size, line_count


def test_wide_characters_break_anywhere_and_words_only_at_spaces():
    assert line_count("あ" * 10, 10) == 1
    assert line_count("あ" * 10, 5) == 2
    assert line_count("あ" * 11, 5) == 3
    # A latin word is not split between lines while it fits one line.
    assert line_count("aaaaaaaa bbbbbbbb", 6) == 2
    assert line_count("aaaaaaaa bbbbbbbb", 20) == 1


def test_explicit_line_breaks_and_overlong_words_add_lines():
    assert line_count("one\ntwo\nthree", 50) == 3
    assert line_count("", 10) == 1
    assert line_count("x" * 100, 10) > 5


def test_size_shrinks_until_the_lines_fit_and_stops_at_the_floor():
    assert fitted_size("短い", 100, 20, 12, 1.2) == 12
    shrunk = fitted_size("あ" * 40, 100, 40, 12, 1.2)
    assert 12 * MIN_SCALE < shrunk < 12
    assert line_count("あ" * 40, 100 / shrunk) * 1.2 * shrunk <= 40.5
    assert fitted_size("あ" * 4000, 100, 20, 12, 1.2) == pytest.approx(12 * MIN_SCALE)


def test_degenerate_boxes_do_not_loop_forever():
    assert fitted_size("text", 0, 0, 12, 1.2) == pytest.approx(12 * MIN_SCALE)
