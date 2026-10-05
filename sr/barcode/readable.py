"""Whether a scanner can tell a barcode's ink from its paper.

doc/template.md#colour: a scanner reads in red light, so what decides
a pair is how much red each colour reflects, not how dark either looks.
The check works from the colours alone and cannot know what a printer
will do to them, so it is deliberately coarse.  It refuses a pair
that cannot work in principle, and passes everything else.

A colour's red reflectance is its red channel taken out of sRGB's
transfer curve, the linear light the channel stands for: 0 for no red
at all, 1 for full red.  The green and blue channels play no part,
which is why yellow and orange read as white, and navy as black.
Two tests follow, in this order, and the first to fail is the one
reported:

* **The background reflects at least 40 points more than the bars.**
  A pair the wrong way round, light bars on a dark ground, fails here.
* **The bars reflect less than half what the background does.**

Naming only ``ink`` measures it against white, which is what an
unprinted page is.

"""

from __future__ import annotations

from sr.color import channels

__all__ = ["reflectance", "unreadable"]

# How far apart the two reflectances must be, and the share
# of the background's that the bars must stay under.
DIFFERENCE = 0.4
SHARE = 0.5

# The background an `ink` alone is measured against.
WHITE = "#FFFFFF"


def reflectance(color: str) -> float:
    """Return how much red light a colour reflects, from 0 to 1.

    Args:
        color: A ``#RRGGBB`` colour.

    """
    red = channels(color)[0] / 255
    if red <= 0.04045:
        return red / 12.92
    return float(((red + 0.055) / 1.055) ** 2.4)


def unreadable(ink: str, paper: str | None) -> tuple[str, str] | None:
    """Return why a pair of colours will not scan, or ``None`` where it will.

    The answer names the property at fault along with the reason.
    Bars too light for their background are the ink's fault;
    too small a difference is the paper's where the template named one,
    and the ink's where the background is the unprinted page.

    Args:
        ink: The bars' colour.
        paper: The background's, or ``None`` for an unprinted page.

    """
    ground = WHITE if paper is None else paper
    bars = reflectance(ink)
    background = reflectance(ground)
    said = f"ink {ink} on paper {ground} will not scan: "
    if background - bars < DIFFERENCE:
        places = decimals(background - bars, DIFFERENCE)
        return "ink" if paper is None else "paper", said + (
            f"the bars reflect {percent(bars, places)} of red light and"
            f" the background {percent(background, places)}, a difference"
            f" of {percent(background - bars, places)} where a scanner"
            f" needs {percent(DIFFERENCE)}"
        )
    if bars >= background * SHARE:
        return "ink", said + (
            f"the bars reflect {percent(bars)} of red light against"
            f" the background's {percent(background)}, and a scanner"
            f" needs them under {percent(background * SHARE)}"
        )
    return None


def decimals(value: float, threshold: float) -> int:
    """Return how many decimals tell a percentage from its threshold.

    None, unless a whole percentage would read the same as the threshold
    the value falls short of, as a difference of 39.6% does against 40%.

    Args:
        value: The fraction measured.
        threshold: The fraction it falls short of.

    """
    places = 0
    while places < 3 and round(value * 100, places) == round(threshold * 100, places):
        places += 1
    return places


def percent(value: float, places: int = 0) -> str:
    """Return a reflectance as a percentage, for a message.

    Args:
        value: The fraction.
        places: How many decimals to show.

    """
    if not places:
        return f"{round(value * 100)}%"
    return f"{round(value * 100, places):.{places}f}%"
