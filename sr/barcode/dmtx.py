"""Data Matrix, ECC 200: ISO/IEC 16022.

The standard fixes the error correction, the placement of the codewords
in the mapping matrix, the finder patterns, and the pad sequence.
It leaves two choices to the encoder, and doc/barcode.md#data-matrix
makes each of them a rule.

* **ASCII encodation, throughout.**  A pair of digits is one codeword,
  a byte up to 127 is one, and a byte above it is two, an Upper Shift
  and the byte less 127.  An ECI, where there is one, comes first.
  The standard's C40, Text, X12, EDIFACT, and Base 256 encodations are
  denser for some values and are never used, so a value always gets
  the same symbol.
* **The smallest square symbol that holds it.**  The six rectangular
  sizes are never chosen.

"""

from __future__ import annotations

from dataclasses import dataclass

from sr.barcode.reedsolomon import field
from sr.barcode.symbol import Matrix, Unencodable

__all__ = ["SIZES", "encode"]


@dataclass(frozen=True)
class Size:
    """One square symbol size.

    Attributes:
        side: The symbol's side in modules, finder patterns included.
        region: The side of one data region, inside its finder pattern.
        data: How many data codewords it holds.
        check: How many check codewords it carries, in all.
        blocks: How many blocks those are interleaved over.

    """

    side: int
    region: int
    data: int
    check: int
    blocks: int

    @property
    def regions(self) -> int:
        """Return how many data regions there are along one side."""
        return self.side // (self.region + 2)

    @property
    def mapping(self) -> int:
        """Return the side of the mapping matrix codewords are placed in."""
        return self.region * self.regions


# ISO/IEC 16022 table 7, the square sizes only.
SIZES = (
    Size(10, 8, 3, 5, 1),
    Size(12, 10, 5, 7, 1),
    Size(14, 12, 8, 10, 1),
    Size(16, 14, 12, 12, 1),
    Size(18, 16, 18, 14, 1),
    Size(20, 18, 22, 18, 1),
    Size(22, 20, 30, 20, 1),
    Size(24, 22, 36, 24, 1),
    Size(26, 24, 44, 28, 1),
    Size(32, 14, 62, 36, 1),
    Size(36, 16, 86, 42, 1),
    Size(40, 18, 114, 48, 1),
    Size(44, 20, 144, 56, 1),
    Size(48, 22, 174, 68, 1),
    Size(52, 24, 204, 84, 2),
    Size(64, 14, 280, 112, 2),
    Size(72, 16, 368, 144, 4),
    Size(80, 18, 456, 192, 4),
    Size(88, 20, 576, 224, 4),
    Size(96, 22, 696, 272, 4),
    Size(104, 24, 816, 336, 6),
    Size(120, 18, 1050, 408, 6),
    Size(132, 20, 1304, 496, 8),
    Size(144, 22, 1558, 620, 10),
)

# The ASCII encodation's special codewords.
PAD = 129
DIGIT_PAIR = 130
UPPER_SHIFT = 235
ECI = 241


def encode(payload: bytes, eci: int | None = None) -> Matrix:
    """Return the modules of the symbol that encodes a value.

    Args:
        payload: The value's bytes.
        eci: The ECI the symbol opens with, or ``None`` for none.

    Raises:
        Unencodable: Not even the largest symbol holds it.

    """
    codewords = data_codewords(payload, eci)
    for size in SIZES:
        if len(codewords) <= size.data:
            break
    else:
        raise Unencodable(
            f"it needs {len(codewords)} codewords and the largest"
            f" symbol holds {SIZES[-1].data}"
        )
    padded = pad(codewords, size.data)
    return draw(size, place(size.mapping, interleave(padded, size)))


def data_codewords(payload: bytes, eci: int | None = None) -> list[int]:
    """Return a value's data codewords, before any padding.

    Args:
        payload: The value's bytes.
        eci: The ECI they open with, or ``None`` for none.

    """
    codewords = ascii_encodation(payload)
    if eci is None:
        return codewords
    # A designator below 127 is one codeword, itself plus one.
    return [ECI, eci + 1, *codewords]


def ascii_encodation(payload: bytes) -> list[int]:
    """Return a value's codewords in ASCII encodation.

    Two digits in a row are always paired, reading from the left.

    Args:
        payload: The value's bytes.

    """
    found: list[int] = []
    index = 0
    while index < len(payload):
        byte = payload[index]
        following = payload[index + 1] if index + 1 < len(payload) else None
        if is_digit(byte) and following is not None and is_digit(following):
            found.append(DIGIT_PAIR + (byte - 0x30) * 10 + following - 0x30)
            index += 2
            continue
        if byte > 127:
            found.extend((UPPER_SHIFT, byte - 127))
        else:
            found.append(byte + 1)
        index += 1
    return found


def is_digit(byte: int) -> bool:
    """Report whether a byte is an ASCII digit.

    Args:
        byte: The byte.

    """
    return 0x30 <= byte <= 0x39


def pad(codewords: list[int], capacity: int) -> list[int]:
    """Return the codewords filled out to the symbol's capacity.

    The first pad is 129 as it stands; every later one is randomised
    by its position, per ISO/IEC 16022 5.2.3, so that a long run
    of padding does not print as a regular pattern.

    Args:
        codewords: The data codewords.
        capacity: How many the symbol holds.

    """
    found = list(codewords)
    if len(found) < capacity:
        found.append(PAD)
    while len(found) < capacity:
        position = len(found) + 1
        randomised = PAD + (149 * position) % 253 + 1
        found.append(randomised - 254 if randomised > 254 else randomised)
    return found


def interleave(codewords: list[int], size: Size) -> list[int]:
    """Return the data codewords followed by their interleaved check codewords.

    Codeword ``k`` belongs to block ``k`` mod the block count,
    for the data and for the check codewords alike, which is
    what keeps the data codewords in their order.

    Args:
        codewords: The data codewords, padded.
        size: The symbol.

    """
    gf = field(256, 0x12D, 1)
    count = size.blocks
    per_block = size.check // count
    found = list(codewords) + [0] * size.check
    for block in range(count):
        check = gf.check(codewords[block::count], per_block)
        for index, codeword in enumerate(check):
            found[len(codewords) + block + index * count] = codeword
    return found


def place(side: int, codewords: list[int]) -> list[list[bool]]:
    """Return the mapping matrix with every codeword's bits placed.

    ISO/IEC 16022 annex F, as the standard writes it: codewords are
    placed in an L shape, the "utah", along diagonals that sweep up and
    to the right and then back down, with four special shapes where a
    diagonal meets a corner, and a fixed pattern for any corner left over.

    Args:
        side: The mapping matrix's side.
        codewords: Every codeword, in placement order.

    """
    cells: list[list[int | None]] = [[None] * side for _ in range(side)]

    def module(row: int, column: int, codeword: int, bit: int) -> None:
        if row < 0:
            row += side
            column += 4 - (side + 4) % 8
        if column < 0:
            column += side
            row += 4 - (side + 4) % 8
        cells[row][column] = (codewords[codeword] >> (8 - bit)) & 1

    def utah(row: int, column: int, codeword: int) -> None:
        for bit, (down, across) in enumerate(UTAH, start=1):
            module(row + down, column + across, codeword, bit)

    def corner(shape: tuple[tuple[int, int], ...], codeword: int) -> None:
        for bit, (row, column) in enumerate(shape, start=1):
            module(row, column, codeword, bit)

    index = 0
    row, column = 4, 0
    while True:
        if row == side and column == 0:
            corner(corner_shape(1, side), index)
            index += 1
        if row == side - 2 and column == 0 and side % 4:
            corner(corner_shape(2, side), index)
            index += 1
        if row == side - 2 and column == 0 and side % 8 == 4:
            corner(corner_shape(3, side), index)
            index += 1
        if row == side + 4 and column == 2 and side % 8 == 0:
            corner(corner_shape(4, side), index)
            index += 1
        while True:
            if row < side and column >= 0 and cells[row][column] is None:
                utah(row, column, index)
                index += 1
            row -= 2
            column += 2
            if not (row >= 0 and column < side):
                break
        row += 1
        column += 3
        while True:
            if row >= 0 and column < side and cells[row][column] is None:
                utah(row, column, index)
                index += 1
            row += 2
            column -= 2
            if not (row < side and column >= 0):
                break
        row += 3
        column += 1
        if not (row < side or column < side):
            break
    if cells[side - 1][side - 1] is None:
        cells[side - 1][side - 1] = cells[side - 2][side - 2] = 1
        cells[side - 1][side - 2] = cells[side - 2][side - 1] = 0
    return [[cell == 1 for cell in line] for line in cells]


# The eight modules of a codeword's L, relative to its corner, bit 1 first.
UTAH = (
    (-2, -2), (-2, -1),
    (-1, -2), (-1, -1), (-1, 0),
    (0, -2), (0, -1), (0, 0),
)  # fmt: skip


def corner_shape(which: int, side: int) -> tuple[tuple[int, int], ...]:
    """Return where one of the four corner shapes puts each bit.

    Args:
        which: 1 to 4, in the standard's numbering.
        side: The mapping matrix's side.

    """
    last = side - 1
    if which == 1:
        return (
            (last, 0), (last, 1), (last, 2), (0, last - 1),
            (0, last), (1, last), (2, last), (3, last),
        )  # fmt: skip
    if which == 2:
        return (
            (last - 2, 0), (last - 1, 0), (last, 0), (0, last - 3),
            (0, last - 2), (0, last - 1), (0, last), (1, last),
        )  # fmt: skip
    if which == 3:
        return (
            (last - 2, 0), (last - 1, 0), (last, 0), (0, last - 1),
            (0, last), (1, last), (2, last), (3, last),
        )  # fmt: skip
    return (
        (last, 0), (last, last), (0, last - 2), (0, last - 1),
        (0, last), (1, last - 2), (1, last - 1), (1, last),
    )  # fmt: skip


def draw(size: Size, mapping: list[list[bool]]) -> Matrix:
    """Return the symbol: the mapping matrix split into finder-bounded regions.

    Each region has a solid dark edge on its left and bottom,
    and a clock track of alternating modules on its top and right
    that starts dark at the region's top left corner.

    Args:
        size: The symbol.
        mapping: The placed mapping matrix.

    """
    region = size.region
    rows: list[tuple[bool, ...]] = []
    for band in range(size.regions):
        for inner in range(-1, region + 1):
            row: list[bool] = []
            for column_band in range(size.regions):
                for across in range(-1, region + 1):
                    row.append(
                        finder(inner, across, region)
                        if inner in (-1, region) or across in (-1, region)
                        else mapping[band * region + inner][
                            column_band * region + across
                        ]
                    )
            rows.append(tuple(row))
    return tuple(rows)


def finder(down: int, across: int, region: int) -> bool:
    """Return the colour of one module of a region's finder pattern.

    Args:
        down: The row within the region, -1 for the clock track above it.
        across: The column, -1 for the solid edge to its left.
        region: The region's side inside its finder.

    """
    if across == -1 or down == region:
        return True
    if down == -1:
        return (across + 1) % 2 == 0
    return (down + 1) % 2 == 1
