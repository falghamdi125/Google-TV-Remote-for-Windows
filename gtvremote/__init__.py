"""Google TV / Android TV remote control for Windows.

Speaks the Android TV Remote Service v2 protocol (TLS ports 6467 pairing /
6466 control) with no third-party protocol dependencies.
"""

__version__ = "1.0.0"

import logging  # noqa: E402

# Library convention: silent unless the application attaches a handler
# (the window keeps a connection log, see gtvremote.ui.window).
logging.getLogger(__name__).addHandler(logging.NullHandler())

# Submodules import ``__version__`` from here, so it must be defined first.
from .discovery import Device, discover  # noqa: E402
from .pairing import BadCodeError, PairingError, PairingSession  # noqa: E402
from .remote import NotPairedError, RemoteClient  # noqa: E402

__all__ = [
    "__version__",
    "BadCodeError",
    "Device",
    "NotPairedError",
    "PairingError",
    "PairingSession",
    "RemoteClient",
    "discover",
]
