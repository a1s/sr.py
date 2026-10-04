# Barcode encoding

How the value a [`barcode`](template.md#barcode) holds becomes
the modules a [printout](printout.md#barcode) records.

Every symbology's standard fixes most of a symbol and leaves the encoder
a few choices: which code set to start in, which mask to apply, whether
to add a check character, what size of symbol to draw. Each choice
changes the runs a printout carries, so each is a rule here rather than
a freedom, and two engines that follow these rules draw the same symbol.
Where a standard fixes something, this document names the standard
and does not restate it.

How large a symbol is drawn, and where in its box,
is [layout's](layout.md#a-barcode-in-its-box).

## Contents

- [What every type shares](#what-every-type-shares)
- [Code 128](#code-128)
- [Code 39](#code-39)
- [Code 93](#code-93)
- [Interleaved 2 of 5](#interleaved-2-of-5)
- [QR Code](#qr-code)
- [Data Matrix](#data-matrix)
- [Aztec Code](#aztec-code)

## What every type shares

**The value** is the string the element finally holds: what `expr`
evaluates to with `format` applied, or the `text` or the `data` node's
content as written. Every type refuses an empty value.

**Characters and bytes.** The 1-D types encode characters, and take
ASCII only. The 2-D types encode bytes: the value's characters in
the element's [`charset`](template.md#character-set), UTF-8 by default.
In ISO 8859-1 a character above U+00FF is refused. Each 2-D standard
reads bytes as ISO 8859-1 unless the symbol carries an ECI, so a UTF-8
value outside ASCII reads back right without ECI only on a reader that
detects UTF-8 for itself.

**The ECI.** With `eci=#true`, a value with a character outside ASCII
opens its symbol with the ECI that names its charset: 000026 for UTF-8,
000003 for ISO 8859-1. Each 2-D type's section says how it is written.
An ASCII value never gets one. No type writes an FNC1, or any other
character that programs a reader.

**The quiet zone** is the margin a symbol's standard requires, in modules:
ten at each end of a 1-D symbol, four all round a QR symbol, one
all round a Data Matrix symbol, and none round an Aztec symbol.
It is part of the symbol, of its size, and of its runs.

**The runs.** A 1-D symbol is a sequence of bars and spaces that starts and
ends with a bar. Its `stripes` are the leading quiet zone, those widths in
modules, and the trailing quiet zone, so the first entry is light and the
two alternate. A 2-D symbol is a matrix of modules, and each entry of its
`rows` is one row's run lengths, quiet zone included, starting with a light
run. A row that opens dark, which only an Aztec symbol's can, opens with a
light run of zero. A row ends with its last run, and no empty run follows it.

**Refusal.** A value a type cannot carry is an error when its band is built,
naming the type, the value, and the reason: a character the type does not
have, an odd number of digits for Interleaved 2 of 5, or more data than the
largest symbol holds. It is not checked when the template loads, not even
for a `text`, so `validate` and `build` agree about which templates load.

## Code 128

ISO/IEC 15417, over ASCII 0 to 127.

Code 128 has three code sets: A holds codes 0 to 95, B codes 32 to 127,
and C the pairs of digits. One value can be written many ways, and the
symbol is chosen by reading the value from the left:

- **A run of four digits or more goes in set C**, wherever it is, and set C
  takes as many pairs of it as there are. An odd digit left over goes in
  set A or B with what follows it. A run of three digits or fewer is never
  in set C.
- **Outside set C, the symbol stays in set A or set B** for as long as each
  character is there. Codes 0 to 31 are in A only, codes 96 to 127 in B
  only, and every other code in both. A character the current set lacks
  is reached by a latch, Code A or Code B, and never by Shift.
- **The start code** is Start C where the value opens with a run of four
  digits or more. Otherwise it is Start A where the first character in the
  value that only one of A and B holds is a control character, and Start B
  in every other case, a value whose characters are all in both included.
- **Leaving set C**, the latch is to A where the next character is a control
  character, and to B otherwise. Only that character is read.

No FNC1 to FNC4 is written. The check character is the standard's: the start
code's value plus each later symbol's value times its position, modulo 103.
The stop pattern follows it.

| Value | Symbols before the check character |
|---|---|
| `123` | Start B, `1`, `2`, `3` |
| `12345` | Start C, `12`, `34`, Code B, `5` |
| `A1234` | Start B, `A`, Code C, `12`, `34` |
| `A` U+0001 | Start A, `A`, U+0001 |
| `1234A` U+0001 | Start C, `12`, `34`, Code B, `A`, Code A, U+0001 |

## Code 39

ISO/IEC 16388, over the digits, `A` to `Z`, space, and `- . $ / + %`.
`*` is the start and stop character, and a value holding one is refused
like any other character outside the set. Full ASCII Code 39 is not offered.

A wide element is **two modules**, a narrow one is one, and characters are
separated by a narrow space, so each character but the last takes thirteen
modules. There is **no check character**. The symbol is `*`, the value, `*`.

## Code 93

ANSI/AIM BC5, the uniform symbology specification for Code 93,
over ASCII 0 to 127.

Code 93 has 43 characters of its own, four shift characters, and a start
and stop character. The digits, `A` to `Z`, space, `-`, and `.` are written
as themselves. **Every other character is a shift character and a letter**,
by the full ASCII table Code 39 and Code 93 share: lower case is `(+)` and
the upper-case letter, the control characters are `($)` or `(%)` and a letter,
and the punctuation `(/)` or `(%)` and a letter. That includes `$ / + %`,
which are written `(/)D (/)O (/)K (/)E` although Code 93 has a character
of its own for each of them.

**Both check characters** follow the data, as the specification requires.
C is the sum of each character's value weighted 1, 2, ... 20 from the right,
the weights starting again at 1 after 20, modulo 47. K is the same over the
characters and C, with weights 1 to 15. Then the stop character and the
one-module termination bar.

## Interleaved 2 of 5

ISO/IEC 16390, over an even number of digits. An odd number is refused
rather than padded, and `format` is how a value is given a fixed width.

A wide element is **three modules** and a narrow one is one. The start is
a narrow bar, a narrow space, a narrow bar and a narrow space, and the stop
a wide bar, a narrow space and a narrow bar. Each pair of digits is ten
elements, the first digit's five in the bars and the second's in the
spaces, starting with a bar. There is **no check digit**.

## QR Code

ISO/IEC 18004, model 2. The type names the error-correction level,
`QR-L` to `QR-H`. An encoder chooses three things, and these are the rules.

**One segment, in one mode.** Numeric where every byte is an ASCII
digit; alphanumeric where every byte is one of that mode's 45: the
digits, `A` to `Z`, space, and `$ % * + - . / :`; and byte otherwise.
A value is never split into segments of different modes, and Kanji mode
and structured append are never used.

**An ECI**, where there is one, comes before the segment: the ECI mode
indicator 0111, then the designator in eight bits, 00011010 for 000026
and 00000011 for 000003.

**The smallest version** from 1 to 40 that holds the ECI and the segment
at the type's level: the mode indicator, the character count at the width
that version gives it, the data, a terminator of up to four zero bits,
zero bits to a whole byte, and the pad codewords 0xEC and 0x11 alternately
to the version's capacity. A value version 40 does not hold is refused.

**The mask with the lowest penalty**, the lower-numbered mask winning a tie.
Each mask's penalty is computed over the whole symbol with that mask applied,
function patterns and that mask's format information included, and the quiet
zone left out. It is the sum of four scores:

1. Each run of five or more modules of one colour in a row or a column
   scores 3, and 1 more for each module beyond five.
2. Each two by two block of one colour scores 3.
   Blocks that overlap are each counted.
3. Each place in a row or a column where dark, light, dark, dark, dark,
   light, dark is preceded or followed by four light modules scores 40.
   The four must lie inside the symbol, since the quiet zone is left out,
   and a place with four light modules on both sides counts once.
4. With `d` dark modules among `n`, the score is 10 times
   `floor(|20d - 10n| / n)`: 10 for each whole 5% the proportion of dark
   modules is away from half.

Everything else is the standard's: the block structure and interleaving,
the placement, the remainder bits, which are light before masking,
and the format and version information.

## Data Matrix

ISO/IEC 16022, ECC 200.

**ASCII encodation throughout.** The value's bytes are read from the left.
Two ASCII digits in a row are one codeword, 130 plus their value. A byte
from 0 to 127 is one codeword, the byte plus 1, and a byte from 128 to 255
is two, Upper Shift (235) and the byte less 127. No other encodation is
used, even where C40, Text, X12, EDIFACT, or Base 256 would be shorter,
and no FNC1 or macro codeword is written.

**An ECI**, where there is one, comes first: the ECI codeword, 241, then
the designator plus 1 in one codeword, 27 for 000026 and 4 for 000003.

**The smallest square symbol** from 10 x 10 to 144 x 144 that
holds the codewords. The rectangular sizes are never used.
A value the 144 x 144 symbol does not hold is refused.

**Padding** fills the symbol's data capacity: the first pad codeword is 129,
and each later one is `129 + ((149 p) mod 253) + 1`, less 254 where that
is more than 254, with `p` the pad's position among the data codewords,
counting from 1.

Everything else is the standard's: the error correction, the interleaving
of the larger sizes' blocks, each data codeword `k` in block `k` mod the
block count, the placement of the codewords, and the finder patterns.

## Aztec Code

ISO/IEC 24778. Compact and full-range symbols are drawn, and runes never are.
If we ever need to draw runes, we'll make another barcode type for them.

### The bits

The value's bytes become bits by a search for a short encoding through
the five character modes, Upper, Lower, Mixed, Punct and Digit, and Binary
Shift. The standard's character tables are used with one change: **Punct's
code 7, the double quote, is never used**, and a double quote is written in
Binary Shift like a byte no mode holds. Several encodings are often equally
short, and which one is written is decided by the search's order, so the
search is the rule:

A **state** is one encoding of the bytes read so far: its codes, the mode
it is in, the bytes in a Binary Shift run it has open, and its length
in bits, counting the open run as it will be written. The search starts
with one state, in Upper, with nothing written, or with the ECI where
there is one. An ECI is written as P/S, then FLG(n), which is Punct's
code 0, then `n` in three bits, then the designator's `n` digits without
leading zeros, each in its four-bit Digit code. That is 21 bits for 000026
and 17 for 000003, and the state is still in Upper after it.

These are what each step costs:

| | |
|---|---|
| A code | 4 bits in Digit, 5 in any other mode |
| A latch | the bits in the table below |
| A shift | from Upper, Lower, Mixed or Digit to Punct, and from Lower or Digit to Upper: one code in the current mode, then the character's code |
| A byte in Binary Shift | 18 bits for a run's first byte and its 32nd, 9 for its 63rd, and 8 for any other. A state in Punct or Digit latches to Upper before it opens a run. A run that reaches 2078 bytes is closed. |

A latch is the shortest the standard's latch codes allow,
and some are two or three latches in a row:

| From | to Upper | to Lower | to Mixed | to Punct | to Digit |
|---|---|---|---|---|---|
| Upper | | L/L, 5 | M/L, 5 | M/L P/L, 10 | D/L, 5 |
| Lower | D/L U/L, 9 | | M/L, 5 | M/L P/L, 10 | D/L, 5 |
| Mixed | U/L, 5 | L/L, 5 | | P/L, 5 | U/L D/L, 10 |
| Punct | U/L, 5 | U/L L/L, 10 | U/L M/L, 10 | | U/L D/L, 10 |
| Digit | U/L, 4 | U/L L/L, 9 | U/L M/L, 9 | U/L M/L P/L, 14 | |

A closed run of `n` bytes is written as Binary Shift, a five-bit `n`,
and the bytes where `n` is 31 or less; as two such runs, of 31 bytes
and of the rest, where `n` is 32 to 62; and as Binary Shift, five
zero bits, `n - 31` in eleven bits, and the bytes beyond that.

The value is read from the left, two bytes at a time where they are one of
Punct's pairs, CR LF, `. `, `, ` or `: `, and one byte at a time otherwise.
Each state in turn makes new states, in this order:

- **For one byte**, for each of Upper, Lower, Digit, Mixed and Punct, in
  that order, that holds it: a **latch** to that mode and the byte's code,
  if the state's own mode lacks the byte, or the mode is the state's own
  or Digit; then a **shift** to that mode and the code, if the state's mode
  lacks the byte and has a shift to that mode. Last, the byte added to
  a **Binary Shift** run, if the state has one open or its mode lacks
  the byte. A latch or a shift closes any run the state had open.
- **For a pair**: a latch to Punct and the pair's code; a shift to Punct
  and the code, if the state is not in Punct; for `. ` and `, `, a latch
  to Digit, the full stop's or comma's code and the space's; and both bytes
  added to a Binary Shift run, if the state has one open. The first three
  close any open run.

The new states are then **pruned**, taken in the order they were made.
One is dropped if a state already kept is at least as good as it. Otherwise
every kept state it is at least as good as is dropped, and it is kept, after
the rest. State `S` is **at least as good as** state `T` when `S`'s length
plus the latch from `S`'s mode to `T`'s, plus 10 more where `T` has a run
open and `S` has none open or a longer one, is no more than `T`'s length.

When the value has been read, the first of the shortest states is written,
its open run closed.

### The symbol

The error correction is at least 33% of the data bits, rounded down,
plus eleven bits. The symbols are tried in this order: compact ones
of one to four layers, then full-range ones of four to 32.  A full-range
symbol of one to three layers is never drawn. Of each, in turn:

1. Its layers hold `(88 + 16 L) L` bits for a compact symbol of `L`
   layers, and `(112 + 16 L) L` for a full-range one. It is passed
   over when the data and error-correction bits are more than that.
2. Its codewords are 6 bits for one or two layers, 8 for three to eight,
   10 for nine to 22, and 12 for 23 to 32. The data bits are cut into
   codewords, and a codeword whose first bits but its last are all zeros
   or all ones takes the opposite bit last, the bit that displaces
   starting the next codeword. The last codeword is filled out with ones.
3. A compact symbol holding more than 64 codewords is passed over.
4. It is drawn when the codewords' bits and the error correction fit
   in the layers' bits, less what is left over from a whole codeword.

A value no symbol holds is refused.

The layers hold, in order, the zero bits left over from a whole codeword,
the data codewords, and as many Reed-Solomon check codewords as fill the
rest, computed in GF(64), GF(256), GF(1024) or GF(4096) as the codeword
size gives. The mode message, the layers' arrangement, the reference grid,
and the finder pattern are the standard's.
