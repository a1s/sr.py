"""Aztec Code: ISO/IEC 24778.

The standard fixes the bull's-eye, the mode message, the reference grid,
the spiral of the data layers, the bit stuffing, and the Reed-Solomon
fields.  What it leaves to the encoder is how the value becomes bits,
how much error correction to add, and what size of symbol to draw, and
doc/barcode.md#aztec-code makes each of them a rule.

* **An ECI, where there is one, opens the bits** as FLG(n), reached
  from Upper by a shift to Punct, and the search starts after it.
* **The bits are found by** :func:`high_level`, a search for the
  shortest sequence the five character modes and Binary Shift allow.
  Where several are equally short the search's own order decides which
  is written, so that order is part of the rule, and so is one
  departure from the standard's tables: Punct's code 7, the double
  quote, is never used, and a double quote is written in Binary Shift.
* **The error correction is at least 33% of the data bits, plus
  eleven bits**, and the symbol is the smallest that holds both:
  a compact symbol of one to four layers, then a full-range one of
  four layers or more.  A full-range symbol of one to three layers
  is never drawn, since a compact one of a layer more is no larger.

"""

from __future__ import annotations

from dataclasses import dataclass

from sr.barcode.reedsolomon import Field, field
from sr.barcode.symbol import Matrix, Unencodable, put, words_of

__all__ = ["encode", "high_level"]

# The five character modes, in the order the search tries them.
UPPER, LOWER, DIGIT, MIXED, PUNCT = range(5)

# The error correction's floor: this share of the data bits,
# in per cent and rounded down, and this many bits more.
CHECK_PERCENT = 33
CHECK_EXTRA = 11

# The most layers a symbol has.
MOST_LAYERS = 32

# A compact symbol holds this many codewords at most, whatever room
# its layers have.
COMPACT_WORDS = 64

# The codeword size in bits, by layer count.
WORD_SIZE = (
    4, 6, 6, 8, 8, 8, 8, 8, 8, 10, 10,
    10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10,
    10, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
)  # fmt: skip

# The Galois field each codeword size is computed in, and its polynomial.
FIELDS = {
    4: (16, 0x13),
    6: (64, 0x43),
    8: (256, 0x12D),
    10: (1024, 0x409),
    12: (4096, 0x1069),
}

# The cheapest latch from each mode to each other one:
# the codes, packed into one number, and how many bits they take.
LATCHES: tuple[tuple[tuple[int, int], ...], ...] = (
    # From Upper.
    ((0, 0), (28, 5), (30, 5), (29, 5), ((29 << 5) + 30, 10)),
    # From Lower: Upper by way of Digit, which is shorter than Mixed.
    (((30 << 4) + 14, 9), (0, 0), (30, 5), (29, 5), ((29 << 5) + 30, 10)),
    # From Digit, whose codes are four bits.
    (
        (14, 4),
        ((14 << 5) + 28, 9),
        (0, 0),
        ((14 << 5) + 29, 9),
        ((14 << 10) + (29 << 5) + 30, 14),
    ),
    # From Mixed.
    ((29, 5), (28, 5), ((29 << 5) + 30, 10), (0, 0), (30, 5)),
    # From Punct, which latches to nothing but Upper.
    (
        (31, 5),
        ((31 << 5) + 28, 10),
        ((31 << 5) + 30, 10),
        ((31 << 5) + 29, 10),
        (0, 0),
    ),
)

# The shifts the modes offer, by the mode shifted from and to.
SHIFTS = {
    (UPPER, PUNCT): 0,
    (LOWER, PUNCT): 0,
    (LOWER, UPPER): 28,
    (MIXED, PUNCT): 0,
    (DIGIT, PUNCT): 0,
    (DIGIT, UPPER): 15,
}

# The Binary Shift code, the same in every mode that has one.
BINARY_SHIFT = 31

# Punct's code 0, FLG(n), and the width of the digit count after it.
FLAG = 0
FLAG_COUNT_BITS = 3

# The longest Binary Shift run, which the 11-bit length allows.
LONGEST_RUN = 2047 + 31

# The four Punct pairs, by their two bytes.
PAIRS = {(0x0D, 0x0A): 2, (0x2E, 0x20): 3, (0x2C, 0x20): 4, (0x3A, 0x20): 5}


def character_codes() -> tuple[dict[int, int], ...]:
    """Return, for each mode, the code of every byte it has.

    The tables of ISO/IEC 24778 table 2, less the shifts and latches,
    and less the double quote, Punct's code 7, which the encoder writes
    in Binary Shift.

    """
    upper = {0x20: 1} | {byte: byte - 0x41 + 2 for byte in range(0x41, 0x5B)}
    lower = {0x20: 1} | {byte: byte - 0x61 + 2 for byte in range(0x61, 0x7B)}
    digit = {0x20: 1, 0x2C: 12, 0x2E: 13}
    digit |= {byte: byte - 0x30 + 2 for byte in range(0x30, 0x3A)}
    mixed_bytes = (
        0x20, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
        0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1B, 0x1C, 0x1D, 0x1E,
        0x1F, 0x40, 0x5C, 0x5E, 0x5F, 0x60, 0x7C, 0x7E, 0x7F,
    )  # fmt: skip
    mixed = {byte: code for code, byte in enumerate(mixed_bytes, start=1)}
    # Codes 2 to 5 are the pairs, and 7 would be the double quote.
    punct_bytes = (
        0x0D, None, None, None, None, 0x21, None, 0x23, 0x24, 0x25,
        0x26, 0x27, 0x28, 0x29, 0x2A, 0x2B, 0x2C, 0x2D, 0x2E, 0x2F,
        0x3A, 0x3B, 0x3C, 0x3D, 0x3E, 0x3F, 0x5B, 0x5D, 0x7B, 0x7D,
    )  # fmt: skip
    punct = {}
    for code, byte in enumerate(punct_bytes, start=1):
        if byte is not None:
            punct[byte] = code
    return upper, lower, digit, mixed, punct


CODES = character_codes()


@dataclass(frozen=True)
class Token:
    """One step of an encoding, linked back to the steps before it.

    Attributes:
        previous: The token before, or ``None`` for the first.
        value: A code, for a plain token.
        bits: How many bits that code takes; 0 for a Binary Shift run.
        start: Where a Binary Shift run starts in the value's bytes.
        count: How many bytes the run takes; 0 for a plain token.

    """

    previous: Token | None
    value: int = 0
    bits: int = 0
    start: int = 0
    count: int = 0

    def add(self, value: int, bits: int) -> Token:
        """Return this encoding with a code appended.

        Args:
            value: The code.
            bits: Its width.

        """
        return Token(self, value=value, bits=bits)

    def add_binary(self, start: int, count: int) -> Token:
        """Return this encoding with a Binary Shift run appended.

        Args:
            start: The run's first byte, as an index into the value.
            count: How many bytes it takes.

        """
        return Token(self, start=start, count=count)


EMPTY = Token(None)


@dataclass(frozen=True)
class State:
    """One way of encoding the value as far as the search has read it.

    Attributes:
        token: The codes so far, less a Binary Shift run still open.
        mode: The mode it is in, or will return to after that run.
        binary: How many bytes the open Binary Shift run holds.
        bits: How many bits all of it takes, that run included.

    """

    token: Token
    mode: int
    binary: int
    bits: int

    def latch(self, mode: int, value: int) -> State:
        """Return this state latched to a mode, with a code appended.

        Args:
            mode: The mode to be in; latching to the current one is free.
            value: The code to append in it.

        """
        token = self.token
        bits = self.bits
        if mode != self.mode:
            latch, width = LATCHES[self.mode][mode]
            token = token.add(latch, width)
            bits += width
        width = 4 if mode == DIGIT else 5
        return State(token.add(value, width), mode, 0, bits + width)

    def shift(self, mode: int, value: int) -> State:
        """Return this state with one code appended through a shift.

        Args:
            mode: The mode shifted to, which is Upper or Punct.
            value: The code to append in it.

        """
        width = 4 if self.mode == DIGIT else 5
        token = self.token.add(SHIFTS[self.mode, mode], width).add(value, 5)
        return State(token, self.mode, 0, self.bits + width + 5)

    def add_binary(self, index: int) -> State:
        """Return this state with one more byte in Binary Shift.

        Digit and Punct have no Binary Shift, so a state in either latches
        to Upper first.  A run's first byte costs the shift and its length
        too, and so does its 32nd, where a second shift takes over,
        until the 63rd makes it one run with a longer length.

        Args:
            index: The byte's position in the value.

        """
        token = self.token
        mode = self.mode
        bits = self.bits
        if mode in (PUNCT, DIGIT):
            latch, width = LATCHES[mode][UPPER]
            token = token.add(latch, width)
            bits += width
            mode = UPPER
        if self.binary in (0, 31):
            grows = 18
        elif self.binary == 62:
            grows = 9
        else:
            grows = 8
        found = State(token, mode, self.binary + 1, bits + grows)
        if found.binary == LONGEST_RUN:
            found = found.end_binary(index + 1)
        return found

    def end_binary(self, index: int) -> State:
        """Return this state with its Binary Shift run closed.

        Args:
            index: The position just past the run's last byte.

        """
        if not self.binary:
            return self
        token = self.token.add_binary(index - self.binary, self.binary)
        return State(token, self.mode, 0, self.bits)

    def at_least_as_good(self, other: State) -> bool:
        """Report whether this state can do no worse than another.

        It can if it is no longer once it has latched to the other's mode,
        and once it has paid for a Binary Shift the other is in and
        it is not, or is in further.

        Args:
            other: The state compared against.

        """
        size = self.bits + LATCHES[self.mode][other.mode][1]
        further = self.binary == 0 or self.binary > other.binary
        if other.binary > 0 and further:
            size += 10
        return size <= other.bits

    def stream(self, payload: bytes) -> list[int]:
        """Return the bits this state's encoding spells.

        Args:
            payload: The value's bytes, which a Binary Shift run copies.

        """
        tokens = []
        token: Token | None = self.end_binary(len(payload)).token
        while token is not None:
            tokens.append(token)
            token = token.previous
        bits: list[int] = []
        for one in reversed(tokens):
            if one.count:
                binary_run(bits, payload, one.start, one.count)
            else:
                put(bits, one.value, one.bits)
        return bits


def binary_run(
    bits: list[int],
    payload: bytes,
    start: int,
    count: int,
) -> None:
    """Append a run of bytes in Binary Shift, with its headers.

    A run of up to 31 bytes takes one five-bit length.
    One of 32 to 62 is written as two runs, of 31 and the rest, and
    a longer one takes a length of five zero bits and eleven more.

    Args:
        bits: The stream so far.
        payload: The value's bytes.
        start: The run's first byte.
        count: How many bytes it takes.

    """
    for index in range(count):
        if index == 0 or (index == 31 and count <= 62):
            put(bits, BINARY_SHIFT, 5)
            if count > 62:
                put(bits, count - 31, 16)
            elif index == 0:
                put(bits, min(count, 31), 5)
            else:
                put(bits, count - 31, 5)
        put(bits, payload[start + index], 8)


def high_level(payload: bytes, eci: int | None = None) -> list[int]:
    """Return a value's bits, as the search finds them.

    The search reads the value a byte at a time, or two at a time for
    a Punct pair, and keeps every state that might still lead to the
    shortest encoding.  Each is extended in every way the next byte
    allows, in a fixed order, and :func:`simplify` drops a state
    that another is at least as good as.  At the end the first
    of the shortest states is written.

    Args:
        payload: The value's bytes.
        eci: The ECI the bits open with, or ``None`` for none.

    """
    start = State(EMPTY, UPPER, 0, 0)
    states = [start if eci is None else flagged(start, eci)]
    index = 0
    while index < len(payload):
        following = payload[index + 1] if index + 1 < len(payload) else 0
        pair = PAIRS.get((payload[index], following), 0)
        grown: list[State] = []
        for state in states:
            if pair:
                pair_states(state, index, pair, grown)
            else:
                byte_states(state, payload[index], index, grown)
        states = simplify(grown)
        index += 2 if pair else 1
    best = min(states, key=lambda state: state.bits)
    return best.stream(payload)


def flagged(state: State, eci: int) -> State:
    """Return a state with an ECI appended, as FLG(n) and its digits.

    The digits are the designator's, without leading zeros, each in
    its four-bit Digit code; the state stays in the mode it was in.

    Args:
        state: The state to extend, in Upper.
        eci: The designator.

    """
    digits = str(eci)
    token = state.token.add(SHIFTS[state.mode, PUNCT], 5).add(FLAG, 5)
    token = token.add(len(digits), FLAG_COUNT_BITS)
    for digit in digits:
        token = token.add(int(digit) + 2, 4)
    width = 5 + 5 + FLAG_COUNT_BITS + 4 * len(digits)
    return State(token, state.mode, 0, state.bits + width)


def byte_states(
    state: State,
    byte: int,
    index: int,
    found: list[State],
) -> None:
    """Add every way of extending a state by one byte, in the search's order.

    For each mode that has the byte, Upper first, a latch to it, and
    a shift to it where the state's mode offers one.  A latch is tried
    only where the state's own mode lacks the byte, or the mode is the
    state's own or Digit; a shift only where the state's mode lacks it.
    Then Binary Shift, where a run is open or the state's mode lacks it.

    Args:
        state: The state to extend.
        byte: The byte.
        index: Its position in the value.
        found: Where the new states go.

    """
    here = byte in CODES[state.mode]
    plain: State | None = None
    for mode in range(5):
        code = CODES[mode].get(byte)
        if code is None:
            continue
        if plain is None:
            plain = state.end_binary(index)
        if not here or mode in (state.mode, DIGIT):
            found.append(plain.latch(mode, code))
        if not here and (state.mode, mode) in SHIFTS:
            found.append(plain.shift(mode, code))
    if state.binary > 0 or not here:
        found.append(state.add_binary(index))


def pair_states(
    state: State,
    index: int,
    pair: int,
    found: list[State],
) -> None:
    """Add every way of extending a state by a Punct pair, in order.

    A latch to Punct, a shift to it, the two characters in Digit where
    both are there, and both bytes in Binary Shift where a run is open.

    Args:
        state: The state to extend.
        index: The pair's position in the value.
        pair: Its Punct code.
        found: Where the new states go.

    """
    plain = state.end_binary(index)
    found.append(plain.latch(PUNCT, pair))
    if state.mode != PUNCT:
        found.append(plain.shift(PUNCT, pair))
    if pair in (3, 4):
        # The full stop or comma, then the space, both in Digit.
        found.append(plain.latch(DIGIT, 16 - pair).latch(DIGIT, 1))
    if state.binary > 0:
        found.append(state.add_binary(index).add_binary(index + 1))


def simplify(states: list[State]) -> list[State]:
    """Return the states none of the others is at least as good as.

    Each candidate, in the order it was made, is dropped where
    a state already kept is at least as good, and otherwise drops
    every kept state it is at least as good as and is kept itself, last.

    Args:
        states: The candidates, in the order they were made.

    """
    kept: list[State] = []
    for state in states:
        if any(old.at_least_as_good(state) for old in kept):
            continue
        kept = [old for old in kept if not state.at_least_as_good(old)]
        kept.append(state)
    return kept


def encode(payload: bytes, eci: int | None = None) -> Matrix:
    """Return the modules of the symbol that encodes a value.

    Args:
        payload: The value's bytes.
        eci: The ECI the symbol opens with, or ``None`` for none.

    Raises:
        Unencodable: Not even the largest symbol holds it.

    """
    bits = high_level(payload, eci)
    check_bits = len(bits) * CHECK_PERCENT // 100 + CHECK_EXTRA
    needed = len(bits) + check_bits
    word = 0
    stuffed: list[int] = []
    for attempt in range(MOST_LAYERS + 1):
        compact = attempt <= 3
        layers = attempt + 1 if compact else attempt
        capacity = layer_bits(layers, compact)
        if needed > capacity:
            continue
        if not stuffed or word != WORD_SIZE[layers]:
            word = WORD_SIZE[layers]
            stuffed = stuff(bits, word)
        if compact and len(stuffed) > word * COMPACT_WORDS:
            continue
        if len(stuffed) + check_bits <= capacity - capacity % word:
            break
    else:
        raise Unencodable(
            f"it needs {needed} bits and the largest symbol"
            f" holds {layer_bits(MOST_LAYERS, False)}"
        )
    message = checked(stuffed, capacity, word)
    mode = mode_message(compact, layers, len(stuffed) // word)
    return draw(compact, layers, message, mode)


def layer_bits(layers: int, compact: bool) -> int:
    """Return how many bits a symbol's data layers hold.

    Args:
        layers: How many layers it has.
        compact: Whether it is a compact symbol.

    """
    return ((88 if compact else 112) + 16 * layers) * layers


def stuff(bits: list[int], word: int) -> list[int]:
    """Return the bits cut into codewords, none of them all 0 or all 1.

    A codeword whose first ``word - 1`` bits are equal takes
    the opposite bit last, and the bit that displaces starts
    the next codeword.  The last codeword is filled out with 1s.

    Args:
        bits: The high-level encoding.
        word: The codeword size.

    """
    found: list[int] = []
    mask = (1 << word) - 2
    index = 0
    while index < len(bits):
        value = 0
        for offset in range(word):
            if index + offset >= len(bits) or bits[index + offset]:
                value |= 1 << (word - 1 - offset)
        if value & mask == mask:
            put(found, value & mask, word)
            index += word - 1
        elif value & mask == 0:
            put(found, value | 1, word)
            index += word - 1
        else:
            put(found, value, word)
            index += word
    return found


def checked(stuffed: list[int], capacity: int, word: int) -> list[int]:
    """Return the layers' bits: padding, the codewords, and the check words.

    The padding is the remainder of the layers' capacity
    over the codeword size, as zero bits, at the start.

    Args:
        stuffed: The data codewords' bits.
        capacity: How many bits the layers hold.
        word: The codeword size.

    """
    data = words_of(stuffed, word)
    gf = gf_for(word)
    words = data + gf.check(data, capacity // word - len(data))
    bits = [0] * (capacity % word)
    for codeword in words:
        put(bits, codeword, word)
    return bits


def gf_for(word: int) -> Field:
    """Return the Galois field a codeword size is computed in.

    Args:
        word: The codeword size.

    """
    size, primitive = FIELDS[word]
    return field(size, primitive, 1)


def mode_message(compact: bool, layers: int, words: int) -> list[int]:
    """Return the mode message: the layer and codeword counts, checked.

    Args:
        compact: Whether the symbol is compact.
        layers: Its layer count.
        words: How many data codewords it holds.

    """
    bits: list[int] = []
    if compact:
        put(bits, layers - 1, 2)
        put(bits, words - 1, 6)
        return checked(bits, 28, 4)
    put(bits, layers - 1, 5)
    put(bits, words - 1, 11)
    return checked(bits, 40, 4)


class Canvas:
    """A symbol being drawn, module by module.

    Attributes:
        dark: Each module's colour, ``True`` for dark, row by row.

    """

    def __init__(self, size: int) -> None:
        """Start a symbol with every module light.

        Args:
            size: Its side, in modules.

        """
        self.dark = [[False] * size for _ in range(size)]

    def set(self, column: int, row: int) -> None:
        """Darken one module.

        Args:
            column: Its column.
            row: Its row.

        """
        self.dark[row][column] = True


def draw(
    compact: bool,
    layers: int,
    message: list[int],
    mode: list[int],
) -> Matrix:
    """Return the symbol: the layers, the mode message, and the finders.

    The layers are laid out on a grid without the reference lines,
    then spread over the symbol through ``where``, which leaves
    a line free every sixteen modules out from the centre.  Each layer
    is two modules deep and is drawn as four sides, clockwise from the
    top left, two bits at a time across its depth.

    Args:
        compact: Whether the symbol is compact.
        layers: Its layer count.
        message: The layers' bits, the outermost layer first.
        mode: The mode message's bits.

    """
    base = (11 if compact else 14) + layers * 4
    if compact:
        size = base
        where = list(range(base))
    else:
        size = base + 1 + 2 * ((base // 2 - 1) // 15)
        where = [0] * base
        origin, centre = base // 2, size // 2
        for index in range(origin):
            offset = index + index // 15
            where[origin - index - 1] = centre - offset - 1
            where[origin + index] = centre + offset + 1
    canvas = Canvas(size)
    offset = 0
    for layer in range(layers):
        side = (layers - layer) * 4 + (9 if compact else 12)
        near = layer * 2
        far = base - 1 - layer * 2
        for step in range(side):
            along = offset + step * 2
            for bit in range(2):
                if message[along + bit]:
                    canvas.set(where[near + bit], where[near + step])
                if message[along + side * 2 + bit]:
                    canvas.set(where[near + step], where[far - bit])
                if message[along + side * 4 + bit]:
                    canvas.set(where[far - bit], where[far - step])
                if message[along + side * 6 + bit]:
                    canvas.set(where[far - step], where[near + bit])
        offset += side * 8
    centre = size // 2
    draw_mode(canvas, compact, centre, mode)
    bulls_eye(canvas, centre, 5 if compact else 7)
    if not compact:
        reference_grid(canvas, size, base)
    return tuple(tuple(row) for row in canvas.dark)


def draw_mode(
    canvas: Canvas,
    compact: bool,
    centre: int,
    mode: list[int],
) -> None:
    """Draw the mode message round the bull's-eye, clockwise.

    Args:
        canvas: The symbol.
        compact: Whether it is compact.
        centre: The bull's-eye's centre.
        mode: The mode message's bits.

    """
    if compact:
        for index in range(7):
            offset = centre - 3 + index
            if mode[index]:
                canvas.set(offset, centre - 5)
            if mode[index + 7]:
                canvas.set(centre + 5, offset)
            if mode[20 - index]:
                canvas.set(offset, centre + 5)
            if mode[27 - index]:
                canvas.set(centre - 5, offset)
        return
    for index in range(10):
        offset = centre - 5 + index + index // 5
        if mode[index]:
            canvas.set(offset, centre - 7)
        if mode[index + 10]:
            canvas.set(centre + 7, offset)
        if mode[29 - index]:
            canvas.set(offset, centre + 7)
        if mode[39 - index]:
            canvas.set(centre - 7, offset)


def bulls_eye(canvas: Canvas, centre: int, size: int) -> None:
    """Draw the bull's-eye and its six orientation marks.

    Args:
        canvas: The symbol.
        centre: Its centre.
        size: Its radius: 5 for a compact symbol, 7 for a full one.

    """
    for ring in range(0, size, 2):
        for along in range(centre - ring, centre + ring + 1):
            canvas.set(along, centre - ring)
            canvas.set(along, centre + ring)
            canvas.set(centre - ring, along)
            canvas.set(centre + ring, along)
    for column, row in (
        (centre - size, centre - size),
        (centre - size + 1, centre - size),
        (centre - size, centre - size + 1),
        (centre + size, centre - size),
        (centre + size, centre - size + 1),
        (centre + size, centre + size - 1),
    ):
        canvas.set(column, row)


def reference_grid(canvas: Canvas, size: int, base: int) -> None:
    """Draw a full-range symbol's reference grid.

    Lines through the centre and every sixteen modules out from it,
    across the whole symbol, alternating from a dark module on the
    centre line.

    Args:
        canvas: The symbol.
        size: Its side.
        base: The side its layers would have without the grid.

    """
    centre = size // 2
    line = 0
    for _ in range(0, base // 2 - 1, 15):
        for along in range(centre & 1, size, 2):
            canvas.set(centre - line, along)
            canvas.set(centre + line, along)
            canvas.set(along, centre - line)
            canvas.set(along, centre + line)
        line += 16
