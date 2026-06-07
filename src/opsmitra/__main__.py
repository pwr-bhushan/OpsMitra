"""Module entry point so ``python -m opsmitra`` works."""
from __future__ import annotations

import sys

from opsmitra.cli import main

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
