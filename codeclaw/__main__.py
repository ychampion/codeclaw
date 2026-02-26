"""Module entry point for ``python -m codeclaw``."""

import sys

from . import __version__
from .cli import main


if __name__ == "__main__":
    # Keep version reporting robust even if CLI parsing changes.
    if any(arg in {"--version", "-V"} for arg in sys.argv[1:]):
        print(__version__)
        raise SystemExit(0)
    main()
