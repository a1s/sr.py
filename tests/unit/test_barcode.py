"""The barcode encoders, one rule at a time.

The differential suite holds every symbology to the reference
over the probes in tests/differential/probes/barcode/.
What is here is each rule of doc/barcode.md on its own,
and the published examples each standard works through,
so that a change breaks the test named after the sentence it broke.
"""

from __future__ import annotations

import pytest

from sr.barcode import (
    QUIET,
    Symbol,
    Unencodable,
    aztec,
    designator,
    dmtx,
    encode,
    linear,
    payload,
    qr,
)
from sr.barcode.readable import reflectance, unreadable
from sr.barcode.reedsolomon import field
from sr.barcode.symbol import runs, words_of

# The three 2-D families, one type each.
TWO_D = ["QR-M", "DataMatrix", "Aztec"]


def bits_of(text: str) -> list[int]:
    """Return a string of 0 and 1 as bits."""
    return [int(one) for one in text.replace(" ", "")]


# -- what every type shares -------------------------------------------


@pytest.mark.parametrize("kind", list(QUIET))
def test_every_type_refuses_an_empty_value(kind: str) -> None:
    with pytest.raises(Unencodable, match="the value is empty"):
        encode(kind, "")


@pytest.mark.parametrize(
    ("kind", "quiet"),
    [("Code128", 10), ("2of5i", 10), ("QR-M", 4), ("DataMatrix", 1), ("Aztec", 0)],
)
def test_every_type_carries_its_quiet_zone(kind: str, quiet: int) -> None:
    symbol = encode(kind, "12")
    assert symbol.quiet == quiet
    if symbol.linear:
        assert symbol.stripes()[0] == symbol.stripes()[-1] == quiet
    else:
        rows = symbol.rows()
        assert rows[:quiet] == ((symbol.length,),) * quiet


def test_a_1d_symbol_starts_light_and_alternates() -> None:
    symbol = Symbol(bars=(2, 1, 3), quiet=10)
    assert symbol.stripes() == (10, 2, 1, 3, 10)
    assert symbol.length == 26
    assert symbol.depth == 0


def test_a_row_that_opens_dark_opens_with_a_light_run_of_nothing() -> None:
    assert runs((True, True, False, True)) == (0, 2, 1, 1)
    assert runs((False, True, True)) == (1, 2)


def test_a_2d_symbols_rows_carry_the_quiet_zone_on_both_sides() -> None:
    symbol = Symbol(modules=((True, False), (False, True)), quiet=1)
    assert symbol.rows() == ((4,), (1, 1, 2), (2, 1, 1), (4,))
    assert symbol.length == symbol.depth == 4


def test_bits_read_back_as_the_codewords_they_were_written_from() -> None:
    assert words_of([1, 0, 1, 1, 0, 0, 1, 0], 4) == [11, 2]


# -- the charset and the ECI ------------------------------------------


def test_a_2d_value_is_its_utf8_bytes_by_default() -> None:
    assert payload("Café", "utf-8") == b"Caf\xc3\xa9"


def test_iso_8859_1_is_a_byte_per_character() -> None:
    assert payload("Café", "iso-8859-1") == b"Caf\xe9"


@pytest.mark.parametrize("kind", TWO_D)
def test_iso_8859_1_refuses_a_character_outside_it(kind: str) -> None:
    with pytest.raises(Unencodable, match="'€' is not in ISO 8859-1"):
        encode(kind, "5 €", "iso-8859-1")


@pytest.mark.parametrize("kind", TWO_D)
def test_the_charset_changes_a_non_ascii_values_symbol(kind: str) -> None:
    utf8 = encode(kind, "Café")
    assert encode(kind, "Café", "iso-8859-1") != utf8


@pytest.mark.parametrize(("charset", "number"), [("utf-8", 26), ("iso-8859-1", 3)])
def test_an_eci_names_the_charset(charset: str, number: int) -> None:
    assert designator("Café", charset, eci=True) == number
    assert designator("Café", charset, eci=False) is None


@pytest.mark.parametrize("kind", TWO_D)
@pytest.mark.parametrize("charset", ["utf-8", "iso-8859-1"])
def test_an_ascii_value_is_one_symbol_whatever_the_charset(
    kind: str, charset: str
) -> None:
    assert encode(kind, "Hello, 123", charset, eci=True) == encode(kind, "Hello, 123")


@pytest.mark.parametrize("kind", TWO_D)
def test_an_eci_changes_a_non_ascii_values_symbol(kind: str) -> None:
    assert encode(kind, "Café", eci=True) != encode(kind, "Café")


# -- Reed-Solomon -----------------------------------------------------


def test_qr_check_codewords_match_the_standards_example() -> None:
    # ISO/IEC 18004's worked example: HELLO WORLD at 1-M.
    data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
    assert field(256, 0x11D, 0).check(data, 10) == [
        196, 35, 39, 119, 235, 215, 231, 226, 93, 23,
    ]  # fmt: skip


def test_data_matrix_check_codewords_match_the_standards_example() -> None:
    # ISO/IEC 16022's worked example: 123456 in the 10 x 10 symbol.
    codewords = dmtx.ascii_encodation(b"123456")
    assert codewords == [142, 164, 186]
    assert field(256, 0x12D, 1).check(codewords, 5) == [114, 25, 5, 88, 102]


# -- Code 128 ---------------------------------------------------------

START_A, START_B, START_C = 103, 104, 105
CODE_A, CODE_B, CODE_C = 101, 100, 99


@pytest.mark.parametrize(
    ("value", "symbols"),
    [
        ("12", [START_B, 17, 18]),
        ("123", [START_B, 17, 18, 19]),
        ("1234", [START_C, 12, 34]),
        ("12345", [START_C, 12, 34, CODE_B, 21]),
        ("A123", [START_B, 33, 17, 18, 19]),
        ("A1234", [START_B, 33, CODE_C, 12, 34]),
        ("A12345B", [START_B, 33, CODE_C, 12, 34, CODE_B, 21, 34]),
        ("A\x01", [START_A, 33, 65]),
        ("Aa\x01", [START_B, 33, 65, CODE_A, 65]),
        ("a\x01a", [START_B, 65, CODE_A, 65, CODE_B, 65]),
        ("1234\x01", [START_C, 12, 34, CODE_A, 65]),
        ("1234A\x01", [START_C, 12, 34, CODE_B, 33, CODE_A, 65]),
        ("\x011234a", [START_A, 65, CODE_C, 12, 34, CODE_B, 65]),
    ],
)
def test_code128_chooses_its_code_sets_by_the_rule(
    value: str, symbols: list[int]
) -> None:
    assert linear.code128_values(value) == symbols


def test_code128_ends_with_its_check_character_and_the_stop() -> None:
    runs_ = linear.code128("Code 128")
    # Start B, eight characters and the check, six elements each;
    # then the stop's seven.
    assert len(runs_) == 10 * 6 + 7
    assert runs_[-7:] == (2, 3, 3, 1, 1, 1, 2)
    assert sum(runs_) == 11 * 10 + 13


def test_code128_refuses_a_character_outside_ascii() -> None:
    with pytest.raises(Unencodable, match="outside ASCII 0-127"):
        linear.code128("é")


# -- Code 39 ----------------------------------------------------------


def test_code39_draws_wide_elements_two_modules_wide_with_no_check() -> None:
    runs_ = linear.code39("CODE 39")
    # The start, seven characters, and the stop, twelve modules each,
    # and a narrow space between each two.
    assert sum(runs_) == 9 * 12 + 8
    assert set(runs_) == {1, 2}


@pytest.mark.parametrize("value", ["abc", "A*B"])
def test_code39_refuses_what_it_has_no_character_for(value: str) -> None:
    with pytest.raises(Unencodable, match="is not one of the digits"):
        linear.code39(value)


# -- Code 93 ----------------------------------------------------------


def test_code93_carries_both_check_characters() -> None:
    # TEST93 is the specification's example; its checks are + and 6.
    values = [CODE93.index(one) for one in "TEST93+6"]
    runs_ = linear.code93("TEST93")
    expected = [int(width) for width in linear.CODE93_EDGE]
    for value in values:
        expected.extend(int(width) for width in linear.CODE93[value])
    expected.extend(int(width) for width in linear.CODE93_EDGE)
    expected.append(1)
    assert list(runs_) == expected


CODE93 = linear.CODE93_CHARACTERS


@pytest.mark.parametrize(
    ("character", "spelled"),
    [
        ("A", ("A",)),
        ("-", ("-",)),
        ("$", ("($)", "D")),
        ("/", ("(/)", "O")),
        ("+", ("(/)", "K")),
        ("%", ("(/)", "E")),
        ("a", ("(+)", "A")),
        ("\x00", ("(%)", "U")),
        ("\x01", ("($)", "A")),
        ("\x1b", ("(%)", "A")),
        ("!", ("(/)", "A")),
        (":", ("(/)", "Z")),
        (";", ("(%)", "F")),
        ("@", ("(%)", "V")),
        ("[", ("(%)", "K")),
        ("`", ("(%)", "W")),
        ("{", ("(%)", "P")),
        ("\x7f", ("(%)", "T")),
    ],
)
def test_code93_spells_full_ascii_through_the_shared_table(
    character: str, spelled: tuple[str, ...]
) -> None:
    shifts = {"($)": 43, "(%)": 44, "(/)": 45, "(+)": 46}
    expected = tuple(shifts.get(one, None) or CODE93.index(one) for one in spelled)
    if character == "$":
        expected = (45, CODE93.index("D"))
    assert linear.full_ascii(ord(character)) == expected


def test_code93_refuses_a_character_outside_ascii() -> None:
    with pytest.raises(Unencodable, match="outside the ASCII range"):
        linear.code93("é")


# -- Interleaved 2 of 5 -----------------------------------------------


def test_interleaved_draws_wide_elements_three_modules_wide() -> None:
    assert linear.interleaved("0016") == (
        1, 1, 1, 1,
        1, 1, 1, 1, 3, 3, 3, 3, 1, 1,
        3, 1, 1, 3, 1, 3, 1, 1, 3, 1,
        3, 1, 1,
    )  # fmt: skip


def test_interleaved_refuses_an_odd_number_of_digits() -> None:
    with pytest.raises(Unencodable, match="3 is an odd number of them"):
        linear.interleaved("123")


def test_interleaved_refuses_a_character_that_is_not_a_digit() -> None:
    with pytest.raises(Unencodable, match="'a' is not a digit"):
        linear.interleaved("12a4")


# -- QR Code ----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "mode"),
    [
        ("0123", "numeric"),
        ("HELLO 123", "alphanumeric"),
        ("Hello", "byte"),
        ("١٢٣", "byte"),
    ],
)
def test_qr_encodes_a_value_in_one_mode(value: str, mode: str) -> None:
    assert qr.segment(value.encode()) == mode


def test_qr_writes_an_eci_header_before_the_segment() -> None:
    bits = qr.stream("byte", b"\xe9", 1, eci=3)
    assert bits == bits_of("0111 00000011 0100 00000001 11101001")


def test_qr_counts_the_eci_header_in_choosing_the_version() -> None:
    # Version 1-L holds 17 bytes alone, and 16 after a 12-bit ECI header.
    assert len(qr.encode(b"\xe9" * 17, "L")) == 21
    assert len(qr.encode(b"\xe9" * 17, "L", eci=3)) == 25


@pytest.mark.parametrize(
    ("version", "level", "capacity"),
    [(1, "L", 19), (1, "H", 9), (7, "M", 124), (40, "L", 2956), (40, "H", 1276)],
)
def test_qr_capacities_are_the_standards(
    version: int, level: str, capacity: int
) -> None:
    assert qr.data_codewords(version, level) == capacity


def test_qr_takes_the_smallest_version_that_holds_the_value() -> None:
    assert len(qr.encode(b"A" * 25, "L")) == 21
    assert len(qr.encode(b"A" * 26, "L")) == 25


def test_qr_draws_version_information_from_version_7() -> None:
    symbol = qr.encode(b"A" * 160, "M")
    assert len(symbol) == 45
    # The version information block above the bottom-left finder spells 7.
    bits = qr.bch(7, qr.VERSION_GENERATOR)
    block = [symbol[45 - 11 + index % 3][index // 3] for index in range(18)]
    assert block == [(bits >> index) & 1 == 1 for index in range(18)]


def test_qr_refuses_what_version_40_does_not_hold() -> None:
    with pytest.raises(Unencodable, match="version 40"):
        qr.encode(b"a" * 2954, "L")


def test_qr_scores_runs_of_five_or_more() -> None:
    line = (True,) * 7 + (False,) * 2 + (True,) * 5
    assert qr.runs_penalty(line) == (3 + 2) + 3


def test_qr_scores_a_finder_like_pattern_with_light_inside_the_symbol() -> None:
    pattern = (True, False, True, True, True, False, True)
    assert qr.finder_penalty((False,) * 4 + pattern) == 40
    assert qr.finder_penalty(pattern + (False,) * 4) == 40
    # Light on both sides is two windows, and scores twice.
    assert qr.finder_penalty((False,) * 4 + pattern + (False,) * 4) == 80
    # Light on neither side within the symbol: the quiet zone does not count.
    assert qr.finder_penalty((False,) * 3 + pattern + (False,) * 3) == 0


def test_qr_takes_the_mask_with_the_lowest_penalty() -> None:
    grid = qr.unmasked(b"Ver1", "L")
    scores = [qr.penalty(qr.masked(grid, "L", mask)) for mask in range(8)]
    best = scores.index(min(scores))
    assert qr.encode(b"Ver1", "L") == qr.masked(grid, "L", best)


@pytest.mark.parametrize("level", ["L", "M", "Q", "H"])
@pytest.mark.parametrize("value", ["01234567", "HELLO WORLD", "hello", "x" * 200])
def test_qr_agrees_with_the_qrcode_package_under_every_mask(
    value: str, level: str
) -> None:
    qrcode = pytest.importorskip("qrcode")
    levels = {
        "L": qrcode.constants.ERROR_CORRECT_L,
        "M": qrcode.constants.ERROR_CORRECT_M,
        "Q": qrcode.constants.ERROR_CORRECT_Q,
        "H": qrcode.constants.ERROR_CORRECT_H,
    }
    grid = qr.unmasked(value.encode(), level)
    for mask in range(8):
        other = qrcode.QRCode(
            error_correction=levels[level], border=0, mask_pattern=mask
        )
        other.add_data(value, optimize=0)
        other.make(fit=True)
        theirs = tuple(tuple(bool(one) for one in row) for row in other.get_matrix())
        assert qr.masked(grid, level, mask) == theirs


# -- Data Matrix ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "codewords"),
    [
        (b"A", [66]),
        (b"12", [142]),
        (b"123", [142, 52]),
        (b"a1b22", [98, 50, 99, 152]),
        (b"\xc3\xa9", [235, 68, 235, 42]),
    ],
)
def test_data_matrix_uses_ascii_encodation(value: bytes, codewords: list[int]) -> None:
    assert dmtx.ascii_encodation(value) == codewords


def test_data_matrix_opens_with_an_eci_codeword() -> None:
    assert dmtx.data_codewords(b"\xe9", 3) == [241, 4, 235, 106]
    assert dmtx.data_codewords(b"\xe9") == [235, 106]


def test_data_matrix_randomises_every_pad_but_the_first() -> None:
    assert dmtx.pad([66], 8) == [66, 129, 70, 220, 115, 11, 161, 56]


@pytest.mark.parametrize(
    ("value", "side"),
    [("A", 10), ("ABCD", 12), ("a" * 30, 22), ("a" * 45, 32), ("x" * 1556, 144)],
)
def test_data_matrix_takes_the_smallest_square_symbol(value: str, side: int) -> None:
    symbol = dmtx.encode(value.encode())
    assert len(symbol) == len(symbol[0]) == side


def test_data_matrix_draws_a_finder_pattern_round_each_region() -> None:
    symbol = dmtx.encode(b"a" * 45)
    for region in (0, 16):
        assert all(symbol[row][region] for row in range(32))
        assert all(symbol[region + 15][column] for column in range(32))
        top = [symbol[region][column] for column in range(16)]
        assert top == [column % 2 == 0 for column in range(16)]


def test_data_matrix_refuses_what_the_largest_symbol_does_not_hold() -> None:
    with pytest.raises(Unencodable, match="the largest symbol holds 1558"):
        dmtx.encode(b"x" * 1559)


# -- Aztec Code -------------------------------------------------------


def test_aztec_writes_upper_case_in_upper_mode() -> None:
    assert aztec.high_level(b"AB") == bits_of("00010 00011")


def test_aztec_shifts_for_one_character() -> None:
    # L/L a, then U/S B: one upper-case letter is cheaper shifted.
    assert aztec.high_level(b"aB") == bits_of("11100 00010 11100 00011")


def test_aztec_latches_back_to_upper_through_digit() -> None:
    # L/L abc, then D/L and U/L, which is shorter than M/L U/L.
    assert aztec.high_level(b"abcDEF") == bits_of(
        "11100 00010 00011 00100 11110 1110 00101 00110 00111"
    )


def test_aztec_uses_punct_pairs() -> None:
    # P/S and the ". " pair, in Upper.
    assert aztec.high_level(b"A. B") == bits_of("00010 00000 00011 00011")


def test_aztec_writes_a_double_quote_in_binary_shift() -> None:
    assert aztec.high_level(b'"') == bits_of("11111 00001 00100010")


def test_aztec_writes_bytes_no_mode_has_in_binary_shift() -> None:
    assert aztec.high_level("é".encode()) == bits_of("11111 00010 11000011 10101001")


def test_aztec_opens_with_an_eci_as_flg_n() -> None:
    # P/S, FLG(n), n = 1 in three bits, and 3 in its Digit code; then
    # the search goes on from Upper.
    assert aztec.high_level(b"\xe9", 3) == bits_of(
        "00000 00000 001 0101 11111 00001 11101001"
    )
    # Two digits for 26, without leading zeros.
    assert aztec.high_level(b"A", 26)[:21] == bits_of("00000 00000 010 0100 1000")


def test_aztec_writes_a_long_binary_shift_with_an_eleven_bit_length() -> None:
    bits = aztec.high_level(b"\x80" * 100)
    assert bits[:21] == bits_of("11111 00000 00001000101")
    assert len(bits) == 21 + 800


def test_aztec_stuffs_codewords_that_are_all_one_bit() -> None:
    assert aztec.stuff(bits_of("000000 111111"), 6) == bits_of("000001 011111 111110")


@pytest.mark.parametrize(
    ("value", "side"),
    [("A", 15), ("Aztec Code 2D :)", 19), ("compact four " * 6, 27)],
)
def test_aztec_takes_the_smallest_symbol(value: str, side: int) -> None:
    assert len(aztec.encode(value.encode())) == side


def test_aztec_draws_a_full_range_symbol_with_its_reference_grid() -> None:
    symbol = aztec.encode(("compact four " * 8)[:96].encode())
    assert len(symbol) == 31
    # Outside the bull's-eye the centre row is the grid line, dark on
    # every other module from the one beside the edge.
    line = [symbol[15][column] for column in range(8)]
    assert line == [column % 2 == 1 for column in range(8)]


def test_aztec_refuses_what_the_largest_symbol_does_not_hold() -> None:
    with pytest.raises(Unencodable, match="the largest symbol holds"):
        aztec.encode(b"1" * 3749)


# -- the colours ------------------------------------------------------


@pytest.mark.parametrize(
    ("color", "expected"),
    [("#000000", 0.0), ("#FFFFFF", 1.0), ("#FFFF00", 1.0), ("#000080", 0.0)],
)
def test_reflectance_is_the_red_channel_in_linear_light(
    color: str, expected: float
) -> None:
    assert reflectance(color) == expected


def test_reflectance_follows_the_srgb_curve() -> None:
    assert reflectance("#C0C0C0") == pytest.approx(0.5271, abs=1e-4)
    assert reflectance("#0A0000") == pytest.approx(10 / 255 / 12.92)


@pytest.mark.parametrize(
    ("ink", "paper"),
    [
        ("#000000", None),
        ("#000080", "#FFE9B0"),
        ("#800000", "#FFA500"),
        ("#BB0000", None),
        ("#000000", "#AA0000"),
    ],
)
def test_a_pair_a_scanner_can_read_passes(ink: str, paper: str | None) -> None:
    assert unreadable(ink, paper) is None


@pytest.mark.parametrize(
    ("ink", "paper", "prop", "reason"),
    [
        ("#FFFF00", None, "ink", "a difference of 0% where a scanner needs 40%"),
        ("#000000", "#000080", "paper", "a difference of 0%"),
        ("#FFFFFF", "#000000", "paper", "a difference of -100%"),
        ("#000000", "#A90000", "paper", "of 39.7% where a scanner needs 40%"),
        ("#CC0000", None, "ink", "of 39.6% where a scanner needs 40%"),
        ("#C0C0C0", None, "ink", "needs them under 50%"),
        ("#BC0000", None, "ink", "needs them under 50%"),
        ("#B80000", "#FA0000", "ink", "needs them under 48%"),
    ],
)
def test_a_pair_a_scanner_cannot_read_names_the_colour_at_fault(
    ink: str, paper: str | None, prop: str, reason: str
) -> None:
    found = unreadable(ink, paper)
    assert found is not None
    assert found[0] == prop
    assert reason in found[1]
