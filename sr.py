#!/usr/bin/env python3
"""The CLI launcher script.

One file at the root, so that ``python sr.py validate -t x.kdl`` works in
a checkout with nothing installed.  The program itself is in ``sr.cli``;
this is the shebang, the path, and the exit code.

"""

from __future__ import annotations

import sys
from pathlib import Path

# A checkout is not necessarily on the path, and this file sits beside
# the package.  Appended rather than prepended: an `sr` a caller has
# deliberately installed should still win over the one next door.
sys.path.append(str(Path(__file__).resolve().parent))

from sr.cli import main

if __name__ == "__main__":
    sys.exit(main())
