"""Reed-Solomon error correction over GF(2^m), for the three 2-D symbols.

QR, Data Matrix, and Aztec all protect their data the same way:
the data codewords are read as the coefficients of a polynomial,
highest power first, and the check codewords are the remainder
of that polynomial times x^n divided by a generator of degree n.
What differs between them is three numbers, which is all a
:class:`Field` holds.

* **The field.**  QR and Data Matrix work in GF(256); Aztec in GF(16)
  for its mode message, and in GF(64) to GF(4096) for its data,
  as the symbol grows.
* **The primitive polynomial** that builds the field.  QR's is 0x11D
  and Data Matrix's 0x12D, so even the two GF(256)s are not the same
  field, and a table shared between them would be wrong for one.
* **The first root** of the generator.  QR's generator is
  (x - a^0)(x - a^1)...(x - a^(n-1)), and the other two start at a^1.

Nothing here decodes: an engine that writes symbols never reads one.

"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache

__all__ = ["Field", "field"]


class Field:
    """One Galois field GF(2^m), and the check codewords it computes.

    Attributes:
        size: How many elements the field has, which is 2^m.
        first: The exponent of the generator's first root.
        generators: Each generator polynomial built so far, by degree.
        exp: a^i for each i, twice over, so a product needs no modulo.
        log: The inverse of ``exp``; ``log[0]`` is never read.

    """

    def __init__(self, size: int, primitive: int, first: int) -> None:
        """Build the field's tables.

        Args:
            size: 2^m.
            primitive: The primitive polynomial,
                as an integer with its x^m term set.
            first: The exponent of the generator's first root.

        """
        self.size = size
        self.first = first
        self.generators: dict[int, tuple[int, ...]] = {}
        self.exp = [0] * (2 * size)
        self.log = [0] * size
        value = 1
        for power in range(size - 1):
            self.exp[power] = value
            self.log[value] = power
            value <<= 1
            if value & size:
                value ^= primitive
        for power in range(size - 1, 2 * size):
            self.exp[power] = self.exp[power - (size - 1)]

    def multiply(self, left: int, right: int) -> int:
        """Return the product of two field elements.

        Args:
            left: One factor.
            right: The other.

        """
        if not left or not right:
            return 0
        return self.exp[self.log[left] + self.log[right]]

    def generator(self, degree: int) -> tuple[int, ...]:
        """Return the generator polynomial of a degree, highest power first.

        Args:
            degree: How many check codewords it produces.

        """
        found = self.generators.get(degree)
        if found is not None:
            return found
        poly = [1]
        for index in range(degree):
            root = self.exp[index + self.first]
            grown = [*poly, 0]
            for position, coefficient in enumerate(poly):
                grown[position + 1] ^= self.multiply(coefficient, root)
            poly = grown
        self.generators[degree] = tuple(poly)
        return self.generators[degree]

    def check(self, data: Sequence[int], count: int) -> list[int]:
        """Return the check codewords for a block of data codewords.

        The remainder of ``data`` times x^count divided by the generator,
        by synthetic division: each data codeword in turn cancels the
        leading term, and what it leaves shifts up.

        Args:
            data: The block's data codewords, first codeword first.
            count: How many check codewords to compute.

        """
        generator = self.generator(count)
        remainder = [0] * count
        for codeword in data:
            factor = codeword ^ remainder[0]
            remainder = [*remainder[1:], 0]
            if factor:
                for index in range(count):
                    term = self.multiply(generator[index + 1], factor)
                    remainder[index] ^= term
        return remainder


@cache
def field(size: int, primitive: int, first: int) -> Field:
    """Return a field, built once per process.

    Args:
        size: 2^m.
        primitive: The primitive polynomial.
        first: The exponent of the generator's first root.

    """
    return Field(size, primitive, first)
