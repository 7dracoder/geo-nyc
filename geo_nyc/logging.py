"""Single source of truth for application logging.

We intentionally avoid the upstream ``print()``-everywhere style: all
runtime messages go through the standard :mod:`logging` module so
operators can plug in their own handlers and so test runs stay quiet by
default.
"""

from __future__ import annotations

import logging
import sys
from typing import Final

_DEFAULT_FORMAT: Final[str] = (
    "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
)
_DEFAULT_DATEFMT: Final[str] = "%Y-%m-%dT%H:%M:%S%z"

_configured = False


def configure_logging(level: str | int = "INFO") -> None:
    """Idempotently configure the root logger.

    Subsequent calls are a no-op so importing ``configure_logging`` from
    multiple entry points (uvicorn, pytest, scripts) is safe.
    """

    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT, _DEFAULT_DATEFMT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn ships its own access logger; drop it down to WARNING so we
    # don't spam stdout during tests / demos.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger.

    Always prefer this over ``logging.getLogger(__name__)`` directly so
    we get the configured formatting even if the caller hasn't run
    :func:`configure_logging` yet.
    """

    configure_logging()
    return logging.getLogger(name)
