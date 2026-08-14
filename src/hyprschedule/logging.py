"""Logging infrastructure.

CLI runs stay quiet by default; ``--verbose`` switches to DEBUG output.
"""

from __future__ import annotations

import logging
import sys

_LOGGER_NAME = "hyprschedule"
_CONFIGURED = False


def setup_logging(verbose: bool = False) -> None:
    """Configure the ``hyprschedule`` logger once.

    Level is DEBUG when *verbose* is true, otherwise WARNING.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger = logging.getLogger(_LOGGER_NAME)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.WARNING)
    logger.propagate = False
    _CONFIGURED = True


def get_logger(name: str = "") -> logging.Logger:
    """Return a child logger of the ``hyprschedule`` hierarchy."""
    if name:
        return logging.getLogger(f"{_LOGGER_NAME}.{name}")
    return logging.getLogger(_LOGGER_NAME)