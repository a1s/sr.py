"""QR Code, model 2: ISO/IEC 18004.

Everything the standard fixes is done as it says: the bit stream,
the Reed-Solomon blocks and their interleaving, the function patterns,
the zigzag placement, and the eight masks.  What it leaves to the encoder
is three choices, and doc/barcode.md#qr-code makes each of them a rule,
because each one changes the modules a printout records.

* **One mode for the whole value.**  Numeric where every byte is
  a digit, alphanumeric where every byte is in that mode's
  45-character set, and byte otherwise.  The standard allows a value
  to switch modes partway, and a printout that did so would be
  a different symbol.  An ECI header, where there is one, comes first.
* **The smallest version that holds it**, at the level the type names.
* **The mask with the lowest penalty**, scored by :func:`penalty`,
  the lower mask number winning a tie.

"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from sr.barcode.reedsolomon import field
from sr.barcode.symbol import Matrix, Unencodable, put, words_of

__all__ = ["LEVELS", "encode", "penalty"]

# The four error-correction levels, as doc/template.md names the types,
# and the two bits the format information spells each with.
LEVELS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}

# Per level, per version from 1 to 40, ten to a line: how many check
# codewords each block carries, and how many blocks the codewords are
# split into.  ISO/IEC 18004 table 9, rearranged: the data codewords
# per block follow from these and the version's capacity, so they are
# not written out.
CHECK_PER_BLOCK = {
    "L": (7, 10, 15, 20, 26, 18, 20, 24, 30, 18,
          20, 24, 26, 30, 22, 24, 28, 30, 28, 28,
          28, 28, 30, 30, 26, 28, 30, 30, 30, 30,
          30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    "M": (10, 16, 26, 18, 24, 16, 18, 22, 22, 26,
          30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
          26, 28, 28, 28, 28, 28, 28, 28, 28, 28,
          28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    "Q": (13, 22, 18, 26, 18, 24, 18, 22, 20, 24,
          28, 26, 24, 20, 30, 24, 28, 28, 26, 30,
          28, 30, 30, 30, 30, 28, 30, 30, 30, 30,
          30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    "H": (17, 28, 22, 16, 22, 28, 26, 26, 24, 28,
          24, 28, 22, 24, 24, 30, 28, 28, 26, 28,
          30, 24, 30, 30, 30, 30, 30, 30, 30, 30,
          30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
}  # fmt: skip
BLOCKS = {
    "L": (1, 1, 1, 1, 1, 2, 2, 2, 2, 4,
          4, 4, 4, 4, 6, 6, 6, 6, 7, 8,
          8, 9, 9, 10, 12, 12, 12, 13, 14, 15,
          16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    "M": (1, 1, 1, 2, 2, 4, 4, 4, 5, 5,
          5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
          17, 17, 18, 20, 21, 23, 25, 26, 28, 29,
          31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    "Q": (1, 1, 2, 2, 4, 4, 6, 6, 8, 8,
          8, 10, 12, 16, 12, 17, 16, 18, 21, 20,
          23, 23, 25, 27, 29, 34, 34, 35, 38, 40,
          43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    "H": (1, 1, 2, 4, 4, 4, 5, 6, 8, 8,
          11, 11, 16, 16, 18, 16, 19, 21, 25, 25,
          25, 34, 30, 32, 35, 37, 40, 42, 45, 48,
          51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
}  # fmt: skip

# The alphanumeric mode's characters, in the order of their values.
ALPHANUMERIC = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"

# The three modes: their indicators, and the width
# of the character count in versions 1-9, 10-26 and 27-40.
MODES = {
    "numeric": (0b0001, (10, 12, 14)),
    "alphanumeric": (0b0010, (9, 11, 13)),
    "byte": (0b0100, (8, 16, 16)),
}

# The ECI mode indicator, and the width of a designator below 128.
ECI_INDICATOR = 0b0111
ECI_BITS = 8

# The pad codewords that fill a symbol's data capacity, alternately.
PADS = (0xEC, 0x11)

# The generators of the format and version information's BCH codes,
# and the mask the format information is XORed with.
FORMAT_GENERATOR = 0b10100110111
FORMAT_MASK = 0b101010000010010
VERSION_GENERATOR = 0b1111100100101

# ISO/IEC 18004 7.8.3's weights for the four penalty rules.
N1, N2, N3, N4 = 3, 3, 40, 10

# The finder-like run of rule 3, dark modules as 1, without its margin.
FINDER = (1, 0, 1, 1, 1, 0, 1)


def encode(payload: bytes, level: str, eci: int | None = None) -> Matrix:
    """Return the modules of the symbol that encodes a value.

    Args:
        payload: The value's bytes.
        level: ``L``, ``M``, ``Q`` or ``H``.
        eci: The ECI the symbol opens with, or ``None`` for none.

    Raises:
        Unencodable: No version holds it at that level.

    """
    grid = unmasked(payload, level, eci)
    best: Matrix | None = None
    lowest = 0
    for mask in range(8):
        found = masked(grid, level, mask)
        score = penalty(found)
        if best is None or score < lowest:
            best = found
            lowest = score
    assert best is not None
    return best


def unmasked(payload: bytes, level: str, eci: int | None = None) -> Grid:
    """Return the symbol that encodes a value, before any mask.

    Args:
        payload: The value's bytes.
        level: ``L``, ``M``, ``Q``, or ``H``.
        eci: The ECI the symbol opens with, or ``None`` for none.

    Raises:
        Unencodable: No version holds it at that level.

    """
    mode = segment(payload)
    for version in range(1, 41):
        capacity = data_codewords(version, level)
        bits = stream(mode, payload, version, eci)
        if len(bits) <= capacity * 8:
            break
    else:
        raise Unencodable(
            f"it needs more than the {data_codewords(40, level)} codewords"
            f" a version 40 symbol holds at level {level}"
        )
    codewords = interleave(finish(bits, capacity), version, level)
    grid = Grid(version * 4 + 17)
    functions(grid, version)
    format_bits(grid, level, 0)
    place(grid, codewords)
    return grid


def segment(payload: bytes) -> str:
    """Return the one mode a value is encoded in.

    Args:
        payload: The value's bytes.

    """
    if all(0x30 <= byte <= 0x39 for byte in payload):
        return "numeric"
    if all(chr(byte) in ALPHANUMERIC for byte in payload):
        return "alphanumeric"
    return "byte"


def stream(
    mode: str,
    payload: bytes,
    version: int,
    eci: int | None = None,
) -> list[int]:
    """Return the bits of one segment, before the terminator.

    Args:
        mode: ``numeric``, ``alphanumeric``, or ``byte``.
        payload: The value's bytes.
        version: The version, which sets the character count's width.
        eci: The ECI the segment is preceded by, or ``None`` for none.

    """
    indicator, widths = MODES[mode]
    width = widths[0 if version <= 9 else 1 if version <= 26 else 2]
    bits: list[int] = []
    if eci is not None:
        put(bits, ECI_INDICATOR, 4)
        put(bits, eci, ECI_BITS)
    put(bits, indicator, 4)
    if mode == "numeric":
        put(bits, len(payload), width)
        for start in range(0, len(payload), 3):
            group = payload[start : start + 3]
            put(bits, int(group), (4, 7, 10)[len(group) - 1])
    elif mode == "alphanumeric":
        put(bits, len(payload), width)
        codes = [ALPHANUMERIC.index(chr(byte)) for byte in payload]
        for start in range(0, len(codes), 2):
            pair = codes[start : start + 2]
            if len(pair) == 2:
                put(bits, pair[0] * 45 + pair[1], 11)
            else:
                put(bits, pair[0], 6)
    else:
        put(bits, len(payload), width)
        for byte in payload:
            put(bits, byte, 8)
    return bits


def finish(bits: list[int], capacity: int) -> list[int]:
    """Return the data codewords: the stream, terminated and padded.

    Args:
        bits: The segment's bits.
        capacity: How many data codewords the symbol holds.

    """
    bits = bits + [0] * min(4, capacity * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    codewords = words_of(bits, 8)
    for index in range(capacity - len(codewords)):
        codewords.append(PADS[index % 2])
    return codewords


def raw_modules(version: int) -> int:
    """Return how many modules a version leaves for codewords.

    Everything the function patterns, the format, and the version
    information do not take, remainder bits included.

    Args:
        version: 1 to 40.

    """
    found = (16 * version + 128) * version + 64
    if version >= 2:
        count = version // 7 + 2
        found -= (25 * count - 10) * count - 55
        if version >= 7:
            found -= 36
    return found


def data_codewords(version: int, level: str) -> int:
    """Return how many data codewords a version holds at a level.

    Args:
        version: 1 to 40.
        level: ``L``, ``M``, ``Q``, or ``H``.

    """
    blocks = BLOCKS[level][version - 1]
    check = CHECK_PER_BLOCK[level][version - 1]
    return raw_modules(version) // 8 - blocks * check


def interleave(codewords: list[int], version: int, level: str) -> list[int]:
    """Return the final codeword sequence: blocks, checked and interleaved.

    The short blocks come first, and a long block holds one data
    codeword more.  The data codewords are read across the blocks
    a column at a time, then the check codewords the same way.

    Args:
        codewords: The data codewords.
        version: The symbol's version.
        level: Its error-correction level.

    """
    count = BLOCKS[level][version - 1]
    check = CHECK_PER_BLOCK[level][version - 1]
    short = len(codewords) // count
    longs = len(codewords) % count
    gf = field(256, 0x11D, 0)
    data_blocks: list[list[int]] = []
    start = 0
    for index in range(count):
        size = short + (1 if index >= count - longs else 0)
        data_blocks.append(codewords[start : start + size])
        start += size
    check_blocks = [gf.check(block, check) for block in data_blocks]
    found: list[int] = []
    for column in range(short + 1):
        found.extend(one[column] for one in data_blocks if column < len(one))
    for column in range(check):
        found.extend(block[column] for block in check_blocks)
    return found


def alignment_centres(version: int) -> list[int]:
    """Return the rows and columns the alignment patterns are centred on.

    Args:
        version: 1 to 40.

    """
    if version == 1:
        return []
    count = version // 7 + 2
    size = version * 4 + 17
    step = (version * 8 + count * 3 + 5) // (count * 4 - 4) * 2
    found = [size - 7 - index * step for index in range(count - 1)]
    return [6, *reversed(found)]


def bch(value: int, generator: int) -> int:
    """Return a value with its BCH check bits appended.

    Args:
        value: The bits to protect.
        generator: The code's generator polynomial.

    """
    degree = generator.bit_length() - 1
    remainder = value << degree
    while remainder.bit_length() > degree:
        shift = remainder.bit_length() - generator.bit_length()
        remainder ^= generator << shift
    return (value << degree) | remainder


class Grid:
    """A symbol being drawn: its modules, and which of them are fixed.

    Attributes:
        size: The side, in modules.
        dark: Each module's colour, ``True`` for dark.
        fixed: Whether a module belongs to a function pattern
            or the format and version information.

    """

    def __init__(self, size: int) -> None:
        """Start an empty symbol.

        Args:
            size: The side, in modules.

        """
        self.size = size
        self.dark = [[False] * size for _ in range(size)]
        self.fixed = [[False] * size for _ in range(size)]

    def set(self, column: int, row: int, dark: bool) -> None:
        """Set a function module.

        Args:
            column: Its column.
            row: Its row.
            dark: Its colour.

        """
        self.dark[row][column] = dark
        self.fixed[row][column] = True


def masked(grid: Grid, level: str, mask: int) -> Matrix:
    """Return a symbol with one of the eight masks applied.

    The format information is drawn for that mask first,
    since rule 3 of the penalty reads it like any other module.

    Args:
        grid: The symbol, its codewords placed.
        level: Its error-correction level.
        mask: The mask, 0 to 7.

    """
    format_bits(grid, level, mask)
    test = MASKS[mask]
    return tuple(
        tuple(
            dark != (not fixed and test(column, row))
            for column, (dark, fixed) in enumerate(zip(*pair, strict=True))
        )
        for row, pair in enumerate(zip(grid.dark, grid.fixed, strict=True))
    )


def functions(grid: Grid, version: int) -> None:
    """Draw the finder, separator, timing, and alignment patterns.

    The version information is drawn too, where the version has it,
    and the format information's modules are reserved.

    Args:
        grid: The symbol.
        version: Its version.

    """
    size = grid.size
    for index in range(size):
        grid.set(6, index, index % 2 == 0)
        grid.set(index, 6, index % 2 == 0)
    for column, row in ((3, 3), (size - 4, 3), (3, size - 4)):
        for down in range(-4, 5):
            for across in range(-4, 5):
                x, y = column + across, row + down
                if 0 <= x < size and 0 <= y < size:
                    ring = max(abs(across), abs(down))
                    grid.set(x, y, ring not in (2, 4))
    centres = alignment_centres(version)
    last = len(centres) - 1
    for one, row in enumerate(centres):
        for other, column in enumerate(centres):
            if (one, other) in ((0, 0), (0, last), (last, 0)):
                continue
            for down in range(-2, 3):
                for across in range(-2, 3):
                    ring = max(abs(across), abs(down))
                    grid.set(column + across, row + down, ring != 1)
    if version >= 7:
        bits = bch(version, VERSION_GENERATOR)
        for index in range(18):
            dark = (bits >> index) & 1 == 1
            near, far = index // 3, size - 11 + index % 3
            grid.set(far, near, dark)
            grid.set(near, far, dark)


def format_bits(grid: Grid, level: str, mask: int) -> None:
    """Draw both copies of the format information, and the dark module.

    Args:
        grid: The symbol.
        level: Its error-correction level.
        mask: The mask being tried.

    """
    bits = bch(LEVELS[level] << 3 | mask, FORMAT_GENERATOR) ^ FORMAT_MASK
    size = grid.size

    def bit(index: int) -> bool:
        return (bits >> index) & 1 == 1

    for index in range(6):
        grid.set(8, index, bit(index))
    grid.set(8, 7, bit(6))
    grid.set(8, 8, bit(7))
    grid.set(7, 8, bit(8))
    for index in range(9, 15):
        grid.set(14 - index, 8, bit(index))
    for index in range(8):
        grid.set(size - 1 - index, 8, bit(index))
    for index in range(8, 15):
        grid.set(8, size - 15 + index, bit(index))
    grid.set(8, size - 8, True)


def place(grid: Grid, codewords: list[int]) -> None:
    """Lay the codewords' bits out in the zigzag the standard describes.

    Two columns at a time from the right, alternately upward and
    downward, skipping the vertical timing pattern and every module
    already fixed.  The modules left over are the remainder bits, light.

    Args:
        grid: The symbol, its function patterns drawn.
        codewords: Every codeword, interleaved.

    """
    bits: list[int] = []
    for codeword in codewords:
        put(bits, codeword, 8)
    taken = 0
    for column, row in zigzag(grid.size):
        if grid.fixed[row][column]:
            continue
        grid.dark[row][column] = taken < len(bits) and bits[taken] == 1
        taken += 1


def zigzag(size: int) -> Iterator[tuple[int, int]]:
    """Yield every module's column and row in placement order.

    Args:
        size: The symbol's side.

    """
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        upward = (right + 1) & 2 == 0
        for step in range(size):
            row = size - 1 - step if upward else step
            yield right, row
            yield right - 1, row
        right -= 2


# The eight data masks, each a test of a module's column and row.
MASKS: tuple[Callable[[int, int], bool], ...] = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


def penalty(modules: Matrix) -> int:
    """Return a masked symbol's penalty score.

    Args:
        modules: The symbol, function patterns and format included.

    """
    size = len(modules)
    columns = tuple(zip(*modules, strict=True))
    score = 0
    for line in (*modules, *columns):
        score += runs_penalty(line) + finder_penalty(line)
    for row in range(size - 1):
        for column in range(size - 1):
            colour = modules[row][column]
            if (
                modules[row][column + 1] == colour
                and modules[row + 1][column] == colour
                and modules[row + 1][column + 1] == colour
            ):
                score += N2
    dark = sum(sum(row) for row in modules)
    total = size * size
    score += N4 * (abs(dark * 20 - total * 10) // total)
    return score


def runs_penalty(line: tuple[bool, ...]) -> int:
    """Return rule 1's score for one row or column.

    Args:
        line: The modules, in order.

    """
    score = 0
    length = 1
    for index in range(1, len(line) + 1):
        if index < len(line) and line[index] == line[index - 1]:
            length += 1
            continue
        if length >= 5:
            score += N1 + length - 5
        length = 1
    return score


def finder_penalty(line: tuple[bool, ...]) -> int:
    """Return rule 3's score for one row or column.

    Each side of a finder-like run scores on its own, so a run with
    four light modules on both sides scores twice: once for each of
    the two 11-module windows it is part of.

    Args:
        line: The modules, in order.

    """
    score = 0
    bits = tuple(int(one) for one in line)
    for start in range(len(bits) - 6):
        if bits[start : start + 7] != FINDER:
            continue
        before = bits[max(0, start - 4) : start]
        after = bits[start + 7 : start + 11]
        if len(before) == 4 and not any(before):
            score += N3
        if len(after) == 4 and not any(after):
            score += N3
    return score
