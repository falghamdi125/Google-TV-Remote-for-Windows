"""Build the Windows Installer package: dist/GoogleTVRemote-<version>.msi.

    python installer/build_msi.py        # needs dist/GoogleTVRemote.exe (build_exe.bat)

The package is described by GoogleTVRemote.wxs and built with the WiX
Toolset v3: candle and light from the PATH (or %WIX%\\bin) when WiX is
installed, as on GitHub's Windows runners; otherwise the portable binaries
are fetched once into build/wix314/. The licence shown by the installer is
made from LICENSE at build time.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gtvremote import __version__  # noqa: E402

WXS = ROOT / "installer" / "GoogleTVRemote.wxs"
EXE = ROOT / "dist" / "GoogleTVRemote.exe"
BUILD = ROOT / "build" / "msi"
WIX_CACHE = ROOT / "build" / "wix314"
WIX_URL = "https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip"
EXTENSIONS = ("WixUIExtension", "WixUtilExtension")      # the dialogs; the launch-on-finish action


def wix_bin() -> Path:
    """The folder holding candle.exe and light.exe, fetched if need be."""
    found = shutil.which("candle")
    if found:
        return Path(found).parent
    installed = Path(os.environ.get("WIX", "")) / "bin"
    if (installed / "candle.exe").exists():
        return installed
    if not (WIX_CACHE / "candle.exe").exists():
        print(f"fetching the WiX Toolset into {WIX_CACHE}")
        WIX_CACHE.mkdir(parents=True, exist_ok=True)
        archive = WIX_CACHE / "wix314-binaries.zip"
        with urllib.request.urlopen(WIX_URL, timeout=60) as response:
            archive.write_bytes(response.read())
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(WIX_CACHE)
        archive.unlink()
    return WIX_CACHE


def license_rtf(text: str) -> str:
    """The licence as the plain RTF the installer's dialog expects."""
    out = []
    for char in text:
        if char in "\\{}":
            out.append("\\" + char)
        elif char == "\n":
            out.append("\\par\n")
        elif ord(char) > 127:
            out.append(f"\\u{ord(char)}?")
        else:
            out.append(char)
    return "{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Segoe UI;}}\\f0\\fs18\n" + "".join(out) + "}"


def main() -> int:
    if not EXE.exists():
        print(f"{EXE} is missing - run build_exe.bat first")
        return 1
    wix = wix_bin()
    BUILD.mkdir(parents=True, exist_ok=True)
    rtf = BUILD / "license.rtf"
    rtf.write_text(license_rtf((ROOT / "LICENSE").read_text(encoding="utf-8")), encoding="ascii")
    wixobj = BUILD / "GoogleTVRemote.wixobj"
    msi = ROOT / "dist" / f"GoogleTVRemote-{__version__}.msi"
    extensions = [arg for name in EXTENSIONS for arg in ("-ext", name)]
    subprocess.run([str(wix / "candle.exe"), "-nologo", "-arch", "x64",
                    f"-dVersion={__version__}", f"-dSourceDir={ROOT}", f"-dLicenseRtf={rtf}",
                    *extensions, "-out", str(wixobj), str(WXS)], check=True)
    # ICE91 warns that the .exe sits under the user profile: that is the point
    # of a per-user package.
    subprocess.run([str(wix / "light.exe"), "-nologo", "-cultures:en-US", "-spdb", "-sice:ICE91",
                    *extensions, "-out", str(msi), str(wixobj)], check=True)
    print(f"wrote {msi} ({msi.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
