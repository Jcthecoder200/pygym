"""`python -m pygym` opens the main menu (same as the `pygym` command)."""
from __future__ import annotations

import sys
from typing import List, Optional

from .menu import main as _menu_main


def main(argv: Optional[List[str]] = None) -> int:
    return _menu_main(argv)


if __name__ == "__main__":
    sys.exit(main())
