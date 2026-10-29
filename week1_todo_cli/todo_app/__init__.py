"""To-Do List Manager - a small, dependency-free CLI application.

Package layout (see DESIGN.md for the rationale):

    errors.py    exception hierarchy
    models.py    Task / Priority / Status domain objects
    storage.py   persistence (JSON file, atomic writes) + in-memory backend
    service.py   use-cases and business rules
    cli.py       argparse front-end, rendering, error handling
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__"]
