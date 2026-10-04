"""What an encoder hands the layout: a symbol, and its run geometry.

Every encoder produces one of two shapes.  A 1-D symbol is a run list:
the widths of its bars and spaces in modules, bar first, ending on a bar.
A 2-D symbol is a module matrix: rows of booleans, dark as ``True``.
Neither carries its quiet zone; :class:`Symbol` adds it, because
the margin belongs to the symbology rather than to the value encoded,
and because doc/printout.md#barcode makes it part of the geometry
for every type alike.

The one converter here turns both into what the printout records.
A 1-D symbol becomes ``stripes``: the leading quiet zone, the runs,
the trailing quiet zone, which starts light and alternates because
every symbol starts and ends on a bar.  A 2-D symbol becomes ``rows``,
each the run lengths of one row of modules starting with a light run,
so a row that opens dark opens with a light run of nothing -- possible
only for a symbology with no quiet zone, which is Aztec.

"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Matrix", "Symbol", "Unencodable", "put", "runs", "words_of"]

# A 2-D symbol's modules, row by row, dark as True.
Matrix = tuple[tuple[bool, ...], ...]


class Unencodable(ValueError):
    """A value the symbology cannot carry.

    The message is the reason alone -- which character, or which limit --
    since the caller names the type and the value around it.

    """


@dataclass(frozen=True)
class Symbol:
    """An encoded symbol, in modules, with its quiet zone.

    Attributes:
        bars: A 1-D symbol's runs, bar first, without its quiet zone;
            empty for a 2-D one.
        modules: A 2-D symbol's matrix, without its quiet zone;
            empty for a 1-D one.
        quiet: The quiet zone, in modules, at each end of a 1-D symbol
            or all round a 2-D one.

    """

    bars: tuple[int, ...] = ()
    modules: Matrix = ()
    quiet: int = 0

    @property
    def linear(self) -> bool:
        """Report whether this is a 1-D symbol."""
        return not self.modules

    @property
    def length(self) -> int:
        """Return the extent along the coding direction, in modules."""
        if self.linear:
            return sum(self.bars) + 2 * self.quiet
        return len(self.modules[0]) + 2 * self.quiet

    @property
    def depth(self) -> int:
        """Return a 2-D symbol's extent across the coding direction.

        Its row count, quiet zone included.  A 1-D symbol's bars
        have no depth of their own in modules, so this is 0 for one.

        """
        if self.linear:
            return 0
        return len(self.modules) + 2 * self.quiet

    def stripes(self) -> tuple[int, ...]:
        """Return a 1-D symbol's runs, quiet zones included, light first."""
        return (self.quiet, *self.bars, self.quiet)

    def rows(self) -> tuple[tuple[int, ...], ...]:
        """Return a 2-D symbol's rows as runs, light first."""
        width = self.length
        margin = (width,)
        inside = tuple(
            runs((False,) * self.quiet + row + (False,) * self.quiet)
            for row in self.modules
        )
        return (margin,) * self.quiet + inside + (margin,) * self.quiet


def runs(row: tuple[bool, ...]) -> tuple[int, ...]:
    """Return a row of modules as run lengths, starting with a light run.

    Args:
        row: The modules, dark as ``True``.

    """
    found = [0]
    dark = False
    for module in row:
        if module != dark:
            found.append(0)
            dark = module
        found[-1] += 1
    return tuple(found)


def put(bits: list[int], value: int, width: int) -> None:
    """Append a value's low bits to a bit stream, most significant first.

    Args:
        bits: The stream so far.
        value: The number to write.
        width: How many bits it takes.

    """
    bits.extend((value >> shift) & 1 for shift in range(width - 1, -1, -1))


def words_of(bits: list[int], width: int) -> list[int]:
    """Return a bit stream read back as codewords, most significant bit first.

    Args:
        bits: The stream, a whole number of codewords long.
        width: The codeword size.

    """
    found = []
    for start in range(0, len(bits), width):
        value = 0
        for bit in bits[start : start + width]:
            value = value << 1 | bit
        found.append(value)
    return found
