#!/usr/bin/env python3
"""Convenience launcher so the app runs without installing anything.

    python run.py add "Buy milk" -p high
    python run.py list
    python run.py --help

Kept deliberately tiny: it only forwards ``sys.argv`` to
:func:`todo_app.cli.main`, which keeps the real logic inside the package.
"""

from __future__ import annotations

import sys

from todo_app.cli import main

if __name__ == "__main__":
    sys.exit(main())
