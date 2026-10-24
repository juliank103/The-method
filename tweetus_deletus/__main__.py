"""Enable `python -m tweetus_deletus ...`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
