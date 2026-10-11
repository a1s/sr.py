"""Where an `image` element's bytes come from, and what they are.

doc/template.md#image gives an image exactly one source: a file, a `data`
node, or a `content` child.  Whichever it is, the bytes are sniffed and
their header read here, at build time and whatever the element's `scale`
or `embed`, since the natural size is what every `scale` starts from.
So an `embed=#false` file is still read, and has to be there when the
report is built as well as when it is rendered.

The format is sniffed only to find the header.  The mark does not record it,
and the bytes travel as they are: a renderer sniffs them as this does.

What reaches the mark is decided here too:

* **its `data` entry**, for an embedded image, is named by the report's
  :class:`~sr.layout.blobs.DataTable` the first time the bytes are met.
* **its `file`**, for `embed=#false`, is the path as resolved against
  `basedir`, which the writer makes relative to the printout.

A file is read once per build however many bands draw it,
and a blob's header is parsed once however many elements name it.

"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sr.errors import BuildError, Location
from sr.fonts.face import near
from sr.fonts.resolve import tidy
from sr.image import BadImage, Header, read_header
from sr.layout.blobs import DataTable
from sr.template.model import Image

__all__ = ["Picture", "Pictures"]

# What a `content` child's blob is called, having no name of its own.
CONTENT_NAME = "image"


@dataclass(frozen=True)
class Picture:
    """An image, sniffed and sized, and where the printout finds it.

    Exactly one of ``data`` and ``file`` is set, as on the mark.

    Attributes:
        width: The natural width, in pixels, which is points.
        height: The natural height.
        data: The `data` entry holding the bytes, for an embedded image.
        file: The file, for `embed=#false`.

    """

    width: int
    height: int
    data: str | None = None
    file: Path | None = None


class Pictures:
    """Resolves the images of one report.

    Attributes:
        basedir: What a relative ``file`` resolves against.
        blobs: The report's `data` nodes' contents, by name.
        table: The printout's data table, which names what is embedded.
        file: The template, for a diagnostic.

    """

    def __init__(
        self,
        basedir: Path,
        blobs: Mapping[str, bytes],
        table: DataTable,
        file: str | None = None,
    ) -> None:
        """Hold what every image in one report is resolved against.

        Args:
            basedir: What a relative ``file`` resolves against.
            blobs: The report's `data` nodes' contents, by name.
            table: The printout's data table.
            file: The template, for a diagnostic.

        """
        self.basedir = basedir
        self.blobs = blobs
        self.table = table
        self.file = file
        self.files: dict[Path, tuple[bytes, Header]] = {}
        self.headers: dict[bytes, Header] = {}

    def picture(self, image: Image, record: int | None) -> Picture:
        """Return what an `image` element draws.

        Args:
            image: The node.
            record: The record it is built for, for a diagnostic.

        Raises:
            BuildError: The file will not read, or the bytes are not an image.

        """
        if image.file is not None:
            path = tidy(self.basedir / image.file)
            content, header = self.read(path, image, record)
            if not image.embed:
                return self.made(header, file=path)
            data = self.table.generated(path.name, content)
            return self.made(header, data=data)
        if image.data is not None:
            content = self.blobs[image.data]
            header = self.header(content, image, "data", record)
            return self.made(header, data=self.table.named(image.data))
        assert image.content is not None
        header = self.header(image.content, image, None, record)
        data = self.table.generated(CONTENT_NAME, image.content)
        return self.made(header, data=data)

    @staticmethod
    def made(
        header: Header, data: str | None = None, file: Path | None = None
    ) -> Picture:
        """Return a picture of these bytes, at their natural size.

        Args:
            header: What the bytes' header says.
            data: The entry holding the bytes, where they are embedded.
            file: The file, where they are not.

        """
        return Picture(header.width, header.height, data, file)

    def read(
        self, path: Path, image: Image, record: int | None
    ) -> tuple[bytes, Header]:
        """Return a file's bytes and header, reading it the first time only.

        Args:
            path: The file, resolved.
            image: The node, for a diagnostic.
            record: The record it is built for, for a diagnostic.

        Raises:
            BuildError: The file will not read, or is not an image.

        """
        found = self.files.get(path)
        if found is not None:
            return found
        try:
            content = path.read_bytes()
        except OSError as refused:
            raise BuildError(
                f"cannot read {near(path).as_posix()}: {refused.strerror or refused}",
                self.where(image, "file", record),
            ) from None
        found = (content, self.header(content, image, "file", record))
        self.files[path] = found
        return found

    def header(
        self, content: bytes, image: Image, prop: str | None, record: int | None
    ) -> Header:
        """Return what a blob's header says, parsing it the first time only.

        Args:
            content: The bytes.
            image: The node, for a diagnostic.
            prop: The property that named the bytes, for a diagnostic.
            record: The record it is built for, for a diagnostic.

        Raises:
            BuildError: The bytes are not an image this engine can size.

        """
        found = self.headers.get(content)
        if found is not None:
            return found
        try:
            found = read_header(content)
        except BadImage as refused:
            raise BuildError(str(refused), self.where(image, prop, record)) from None
        self.headers[content] = found
        return found

    def where(self, image: Image, prop: str | None, record: int | None) -> Location:
        """Return where a diagnostic about an image is.

        Args:
            image: The node.
            prop: The property it is about, where it is about one.
            record: The record it was built for.

        """
        return Location(file=self.file, path=image.path, prop=prop, record=record)
