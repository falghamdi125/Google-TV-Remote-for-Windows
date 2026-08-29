"""Double-click launcher: opens the remote without a console window.

This is also the entry point the standalone .exe is built from (see
build_exe.bat). From a terminal, prefer ``python -m gtvremote``.
"""

from gtvremote.ui import main

if __name__ == "__main__":
    main()
