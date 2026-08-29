"""Command-line entry point.

``python -m gtvremote`` opens the remote window. Two headless helpers are
available for scripting and troubleshooting::

    python -m gtvremote --scan               # list TVs on the network
    python -m gtvremote --pair 192.168.1.42  # pair from a terminal
"""

from __future__ import annotations

import argparse
import sys

from . import __version__


def _scan() -> int:
    from . import discovery

    print(f"Scanning for Google TV devices ({discovery.DEFAULT_TIMEOUT:.0f}s)...")
    devices = discovery.discover()
    if not devices:
        print("No devices found. Check the TV is on and on this network.")
        return 1
    for device in devices:
        print(f"  {device.name or '(unnamed)'}\t{device.host}:{device.port}")
    return 0


def _pair(host: str) -> int:
    from .pairing import PairingError, PairingSession

    with PairingSession(host) as session:
        try:
            session.begin()
            print(f"Pairing with {session.server_name or host}.")
            code = input("Enter the 6-digit code shown on the TV: ").strip()
            name = session.send_code(code)
        except PairingError as exc:
            print(f"Pairing failed: {exc}")
            return 1
        except OSError as exc:
            print(f"Could not reach {host}: {exc}")
            return 1
    print(f"Paired with {name or host}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gtvremote",
        description="Remote control for Google TV / Android TV devices. "
                    "With no options, opens the remote window.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-s", "--scan", action="store_true",
                      help="list Google TV devices on the network and exit")
    mode.add_argument("-p", "--pair", metavar="HOST",
                      help="pair with the TV at HOST from the terminal and exit")
    args = parser.parse_args(argv)

    if args.scan:
        return _scan()
    if args.pair:
        return _pair(args.pair)

    from .ui import main as run_ui
    run_ui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
