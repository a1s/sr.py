"""Reading a printout back into the model a renderer draws from.

doc/render.md#reproducibility asks that a printout serialized
and read back render to the same bytes as the one it was built from.
So the renderer draws from one shape only, :mod:`sr.printout.model`,
and this is what turns a file into that shape.
The engine hands its own printout over directly,
and `sr.py render` reads one through here.

Unlike the untyped reader in :mod:`sr.printout.inspect`,
which shows whatever a file holds, this one refuses a field
that is missing or of the wrong type, naming where it is.
A renderer cannot draw a box whose corner is a string,
and guessing a value for it would draw something
the printout never said.

Two things are done here and not by the renderer.

* **Paths are resolved.**  doc/printout.md#paths reads a
  relative path against the directory the printout was read from,
  and an absolute one as it stands.  The model holds paths
  as the engine resolved them, which is absolute, so after
  this the renderer cannot tell the two routes apart.
* **A page's geometry is completed.**  A page line states only
  the fields that differ from the header's, and the model holds
  a whole :class:`~sr.printout.model.Paper` for such a page.

"""

from __future__ import annotations

import base64
import binascii
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sr.errors import WARNING_KINDS, BuildError, BuildWarning, Location
from sr.printout.inspect import read_printout
from sr.printout.model import (
    VERSION,
    Barcode,
    Box,
    Crop,
    FontEntry,
    Image,
    Line,
    Mark,
    Outline,
    Page,
    Paper,
    Printout,
    Rectangle,
    Report,
    Text,
    Xref,
)

__all__ = ["printout_of", "read"]

# The spellings a colour has in a printout.
COLOUR = re.compile(r"#[0-9A-Fa-f]{6}")

# What the enumerated fields may say, per doc/printout.md#marks.
ALIGNMENTS = ("left", "center", "right", "justified")
DASHES = ("solid", "dot", "dash", "dashdot")
LINKS = ("url", "outline")

# The six fields of a page's geometry: the printout's key,
# and the model's name for it.
GEOMETRY = (
    ("width", "width"),
    ("height", "height"),
    ("leftMargin", "left"),
    ("rightMargin", "right"),
    ("topMargin", "top"),
    ("bottomMargin", "bottom"),
)


def read(path: Path) -> Printout:
    """Return the printout a file holds, as the model.

    Args:
        path: The file; ``.cbor`` is CBOR, anything else NDJSON,
            as doc/cli.md#sr-render reads the extension.

    Raises:
        BuildError: The file is not a printout, or a field in it
            is missing or of the wrong type.
        OSError: The file will not read.

    """
    return printout_of(read_printout(path), path.parent, path.as_posix())


def printout_of(
    records: list[Any],
    base: Path,
    file: str | None = None,
) -> Printout:
    """Return a printout's records as the model.

    Args:
        records: What the file held: the header, then the pages.
        base: The directory relative paths resolve against.
        file: The file, for a diagnostic.

    Raises:
        BuildError: A field is missing or of the wrong type.

    """
    if not records:
        raise BuildError("not a printout: no header", Location(file=file))
    header = Fields.of(records[0], "the header", file)
    version = header.count("sr")
    if version != VERSION:
        raise header.wrong(
            f"its format version is {version}, not {VERSION}",
        )
    if header.text("kind") != "header":
        raise header.wrong("its kind is not `header`")
    paper = paper_of(header.mapping("page"))
    fonts = header.array("fonts")
    warnings = header.optional_array("warnings")
    pages = tuple(
        page_of(record, ordinal, paper, base, file)
        for ordinal, record in enumerate(records[1:], start=1)
    )
    return Printout(
        report=report_of(header.optional_mapping("report")),
        built=header.text("built"),
        engine=header.text("engine"),
        strict_fonts=header.flag("strictFonts"),
        paper=paper,
        fonts=tuple(
            font_of(header.item(one, f"font {index}"), base)
            for index, one in enumerate(fonts, start=1)
        ),
        data=data_of(header.optional_mapping("data")),
        pages=pages,
        warnings=tuple(
            warning_of(header.item(one, f"warning {index}"))
            for index, one in enumerate(warnings, start=1)
        ),
        group_runs=counts_of(header.optional_mapping("groupRuns")),
        group_keys=counts_of(header.optional_mapping("groupKeys")),
    )


@dataclass(frozen=True)
class Fields:
    """One object of a printout, read field by field.

    Attributes:
        found: The object.
        where: What it is, for a diagnostic.
        file: The file it was read from, for a diagnostic.

    """

    found: dict[str, Any]
    where: str
    file: str | None

    @classmethod
    def of(cls, value: Any, where: str, file: str | None) -> Fields:
        """Return an object's fields, where the value is an object.

        Args:
            value: What the printout holds there.
            where: What it is.
            file: The file it was read from.

        Raises:
            BuildError: The value is not an object.

        """
        if not isinstance(value, dict):
            raise BuildError(
                f"not a printout: {where} is not an object",
                Location(file=file),
            )
        return cls(value, where, file)

    def wrong(self, said: str) -> BuildError:
        """Return the error saying what is wrong with this object.

        Args:
            said: What is wrong, without where.

        """
        return BuildError(
            f"not a printout: {self.where}: {said}",
            Location(file=self.file),
        )

    def item(self, value: Any, where: str) -> Fields:
        """Return the fields of an object this one holds.

        Args:
            value: The object.
            where: What it is, inside this one.

        """
        return Fields.of(value, where, self.file)

    def need(self, key: str) -> Any:
        """Return a field that has to be there.

        Args:
            key: The field.

        Raises:
            BuildError: It is not there.

        """
        if key not in self.found:
            raise self.wrong(f"`{key}` is missing")
        return self.found[key]

    def text(self, key: str) -> str:
        """Return a text field that has to be there.

        Args:
            key: The field.

        """
        value = self.need(key)
        if not isinstance(value, str):
            raise self.wrong(f"`{key}` is not text")
        return value

    def optional_text(self, key: str) -> str | None:
        """Return a text field that may be absent.

        Args:
            key: The field.

        """
        return self.text(key) if key in self.found else None

    def choice(self, key: str, allowed: tuple[str, ...]) -> str:
        """Return a text field that names one of a fixed set.

        Args:
            key: The field.
            allowed: What the format lets it say.

        """
        value = self.text(key)
        if value not in allowed:
            raise self.wrong(
                f"`{key}` is {value!r}, and not one of {', '.join(allowed)}"
            )
        return value

    def colour(self, key: str) -> str:
        """Return a colour, which the format spells ``#RRGGBB``.

        Args:
            key: The field.

        """
        value = self.text(key)
        if COLOUR.fullmatch(value) is None:
            raise self.wrong(f"`{key}` is {value!r}, which is not #RRGGBB")
        return value

    def optional_colour(self, key: str) -> str | None:
        """Return a colour that may be absent.

        Args:
            key: The field.

        """
        return self.colour(key) if key in self.found else None

    def length(self, key: str) -> float:
        """Return a number of points.

        NDJSON as Python reads it, and CBOR, can both carry a NaN
        or an infinity, and so can an integer too large for a double.
        None is a length: doc/printout.md rounds every length
        to three decimals.

        Args:
            key: The field.

        """
        value = self.need(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise self.wrong(f"`{key}` is not a number")
        try:
            points = float(value)
        except OverflowError:
            points = math.inf
        if not math.isfinite(points):
            raise self.wrong(f"`{key}` is not a finite number")
        return points

    def optional_length(self, key: str) -> float | None:
        """Return a number of points that may be absent.

        Args:
            key: The field.

        """
        return self.length(key) if key in self.found else None

    def count(self, key: str) -> int:
        """Return a whole number.

        Args:
            key: The field.

        """
        value = self.need(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise self.wrong(f"`{key}` is not a whole number")
        return value

    def optional_count(self, key: str) -> int | None:
        """Return a whole number that may be absent.

        Args:
            key: The field.

        """
        return self.count(key) if key in self.found else None

    def flag(self, key: str, default: bool | None = None) -> bool:
        """Return a boolean, or the default where it is absent and has one.

        Args:
            key: The field.
            default: What its absence means, where it may be absent.

        """
        if key not in self.found and default is not None:
            return default
        value = self.need(key)
        if not isinstance(value, bool):
            raise self.wrong(f"`{key}` is not a boolean")
        return value

    def mapping(self, key: str) -> Fields:
        """Return an object field that has to be there.

        Args:
            key: The field.

        """
        return self.item(self.need(key), f"{self.where}'s `{key}`")

    def optional_mapping(self, key: str) -> Fields | None:
        """Return an object field that may be absent.

        Args:
            key: The field.

        """
        return self.mapping(key) if key in self.found else None

    def array(self, key: str) -> list[Any]:
        """Return an array field that has to be there.

        Args:
            key: The field.

        """
        value = self.need(key)
        if not isinstance(value, list):
            raise self.wrong(f"`{key}` is not an array")
        return value

    def optional_array(self, key: str) -> list[Any]:
        """Return an array field, empty where it is absent.

        Args:
            key: The field.

        """
        return self.array(key) if key in self.found else []


def resolved(text: str, base: Path) -> Path:
    """Return a path from a printout, as doc/printout.md#paths reads it.

    Args:
        text: The path, as the printout spells it.
        base: The directory the printout was read from.

    """
    path = Path(text)
    return path if path.is_absolute() else base / path


def paper_of(fields: Fields) -> Paper:
    """Return the header's page geometry.

    Args:
        fields: The header's `page` object.

    """
    return Paper(**{model: fields.length(key) for key, model in GEOMETRY})


def report_of(fields: Fields | None) -> Report:
    """Return what the template said about itself.

    Args:
        fields: The header's `report` object, where there is one.

    """
    if fields is None:
        return Report()
    return Report(
        name=fields.optional_text("name"),
        description=fields.optional_text("description"),
        version=fields.optional_text("version"),
        author=fields.optional_text("author"),
    )


def font_of(fields: Fields, base: Path) -> FontEntry:
    """Return one entry of the header's font table.

    Args:
        fields: The entry.
        base: The directory relative paths resolve against.

    """
    file = fields.optional_text("resolvedFile")
    data = fields.optional_text("resolvedData")
    if (file is None) == (data is None):
        raise fields.wrong(
            "exactly one of `resolvedFile` and `resolvedData` is expected"
        )
    return FontEntry(
        name=fields.text("name"),
        size=fields.count("size"),
        bold=fields.flag("bold"),
        italic=fields.flag("italic"),
        underline=fields.flag("underline"),
        face=fields.text("resolvedFace"),
        step=fields.text("resolvedBy"),
        requested=fields.optional_text("requested"),
        file=None if file is None else resolved(file, base),
        data=data,
        index=fields.optional_count("resolvedIndex") or 0,
    )


def data_of(fields: Fields | None) -> dict[str, bytes | str]:
    """Return the header's data table, each blob as its bytes or text.

    Args:
        fields: The header's `data` object, where there is one.

    """
    if fields is None:
        return {}
    found: dict[str, bytes | str] = {}
    for key, value in fields.found.items():
        entry = fields.item(value, f"data {key!r}")
        content = entry.text("content")
        encoding = entry.optional_text("encoding")
        if encoding is None:
            found[key] = content
        elif encoding == "base64":
            try:
                found[key] = base64.b64decode(content, validate=True)
            except (binascii.Error, ValueError):
                raise entry.wrong("its content is not base64") from None
        else:
            raise entry.wrong(f"its encoding {encoding!r} is not base64")
    return found


def warning_of(fields: Fields) -> BuildWarning:
    """Return one warning the header carries.

    Args:
        fields: The warning.

    """
    kind = fields.text("kind")
    if kind not in WARNING_KINDS:
        raise fields.wrong(f"its kind {kind!r} is not one the format defines")
    return BuildWarning(
        kind,
        fields.text("message"),
        node=fields.optional_text("node"),
        prop=fields.optional_text("prop"),
        record=fields.optional_count("record"),
    )


def counts_of(fields: Fields | None) -> dict[str, int]:
    """Return a table of counts by name, `groupRuns` or `groupKeys`.

    Args:
        fields: The table, where there is one.

    """
    if fields is None:
        return {}
    return {key: fields.count(key) for key in fields.found}


def page_of(
    record: Any,
    ordinal: int,
    paper: Paper,
    base: Path,
    file: str | None,
) -> Page:
    """Return one page.

    Args:
        record: The page line.
        ordinal: Its position among the page lines, from 1.
        paper: The header's geometry, which the page overrides.
        base: The directory relative paths resolve against.
        file: The file, for a diagnostic.

    """
    fields = Fields.of(record, f"page line {ordinal}", file)
    if fields.text("kind") != "page":
        raise fields.wrong("its kind is not `page`")
    number = fields.count("number")
    fields = Fields(fields.found, f"page {number}", file)
    overrides: dict[str, float] = {}
    for key, model in GEOMETRY:
        if key in fields.found:
            overrides[model] = fields.length(key)
    own = None
    if overrides:
        own = Paper(
            **{
                model: overrides.get(model, getattr(paper, model))
                for _, model in GEOMETRY
            }
        )
    marks = marks_of(
        fields.array("marks"),
        fields,
        f"{fields.where}, mark ",
        base,
    )
    return Page(number, marks, own)


def marks_of(
    values: list[Any],
    holder: Fields,
    prefix: str,
    base: Path,
) -> tuple[Mark, ...]:
    """Return a page's marks, or an xref's, in paint order.

    A mark is named as doc/cli.md#sr-inspect names it: from 1
    in paint order, and one inside an xref after the xref's number.

    Args:
        values: The marks array.
        holder: The page or the xref, for the file it came from.
        prefix: What goes before each mark's number.
        base: The directory relative paths resolve against.

    """
    return tuple(
        mark_of(holder.item(value, f"{prefix}{index}"), base)
        for index, value in enumerate(values, start=1)
    )


def box_of(fields: Fields) -> Box:
    """Return a mark's box.

    Args:
        fields: The mark.

    """
    box = fields.mapping("box")
    return Box(
        box.length("x"),
        box.length("y"),
        box.length("width"),
        box.length("height"),
    )


def mark_of(fields: Fields, base: Path) -> Mark:
    """Return one mark, of whichever kind it is.

    Args:
        fields: The mark.
        base: The directory relative paths resolve against.

    """
    kind = fields.text("kind")
    if kind == "outline":
        return Outline(
            Box(fields.length("x"), fields.length("y"), 0.0, 0.0),
            title=fields.text("title"),
            level=fields.count("level"),
            name=fields.optional_text("name"),
            closed=fields.flag("closed"),
        )
    box = box_of(fields)
    if kind == "text":
        lines = fields.array("lines")
        if not all(isinstance(one, str) for one in lines):
            raise fields.wrong("`lines` holds something that is not text")
        return Text(
            box,
            font=fields.text("font"),
            color=fields.colour("color"),
            align=fields.choice("align", ALIGNMENTS),
            leading=fields.length("leading"),
            lines=tuple(lines),
            last_line_justified=fields.flag("lastLineJustified", False),
        )
    if kind == "line":
        return Line(
            box,
            width=fields.length("width"),
            dash=fields.choice("dash", DASHES),
            color=fields.colour("color"),
            backslant=fields.flag("backslant"),
        )
    if kind == "rectangle":
        return Rectangle(
            box,
            width=fields.length("width"),
            dash=fields.choice("dash", DASHES),
            stroke=fields.optional_colour("stroke"),
            fill=fields.optional_colour("fill"),
            radius=fields.optional_length("radius") or 0.0,
        )
    if kind == "image":
        return image_of(fields, box, base)
    if kind == "barcode":
        return barcode_of(fields, box)
    if kind == "xref":
        return Xref(
            box,
            link=fields.choice("type", LINKS),
            target=fields.text("target"),
            caption=fields.optional_text("caption"),
            marks=marks_of(
                fields.array("marks"),
                fields,
                f"{fields.where}.",
                base,
            ),
        )
    raise fields.wrong(f"its kind {kind!r} is not one the format defines")


def image_of(fields: Fields, box: Box, base: Path) -> Image:
    """Return an image mark.

    Args:
        fields: The mark.
        box: Its box, already read.
        base: The directory a `file` resolves against.

    """
    data = fields.optional_text("data")
    file = fields.optional_text("file")
    if (data is None) == (file is None):
        raise fields.wrong("exactly one of `data` and `file` is expected")
    crop = None
    if "crop" in fields.found:
        held = fields.mapping("crop")
        crop = Crop(
            held.length("x"),
            held.length("y"),
            held.length("width"),
            held.length("height"),
        )
    return Image(
        box,
        data=data,
        file=None if file is None else resolved(file, base),
        crop=crop,
    )


def barcode_of(fields: Fields, box: Box) -> Barcode:
    """Return a barcode mark.

    Args:
        fields: The mark.
        box: Its box, already read.

    """
    stripes: tuple[int, ...] = ()
    rows: tuple[tuple[int, ...], ...] = ()
    if "rows" in fields.found:
        listed = fields.array("rows")
        rows = tuple(runs_of(row, fields, "rows") for row in listed)
    else:
        stripes = runs_of(fields.array("stripes"), fields, "stripes")
    return Barcode(
        box,
        symbology=fields.text("type"),
        value=fields.text("value"),
        module=fields.length("module"),
        vertical=fields.flag("vertical"),
        ink=fields.colour("ink"),
        paper=fields.optional_colour("paper"),
        stripes=stripes,
        rows=rows,
    )


def runs_of(value: Any, fields: Fields, key: str) -> tuple[int, ...]:
    """Return one array of runs, each a whole number of modules.

    Args:
        value: The array.
        fields: The mark, for a diagnostic.
        key: The field the array is in.

    """
    if not isinstance(value, list) or not all(map(is_run, value)):
        raise fields.wrong(f"`{key}` is not an array of runs")
    return tuple(value)


def is_run(value: Any) -> bool:
    """Report whether a value is a run: a whole number of modules.

    Args:
        value: One element of a run array.

    """
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value >= 0
