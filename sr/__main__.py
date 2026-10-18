"""Run the command line as ``python -m sr``.

The same program as the ``s-reports`` command an installed package
puts on the path, for an interpreter whose scripts directory is not
on it.

"""

from __future__ import annotations

import sys

from sr.cli import main

if __name__ == "__main__":
    sys.exit(main())
