"""Render the app-button icons (gtvremote/assets/icons/*.png).

Each icon is the app's own logo glyph on a tile in the brand colour, drawn
at every size the interface can ask for (see SIZES) plus a dimmed variant
for the disabled state. The glyphs are the SVG paths published by the
Simple Icons project (https://simpleicons.org, CC0); the logos themselves
remain trademarks of their owners and identify the apps the buttons open.

    python tools/render_icons.py            # render from tools/icons/*.svg
    python tools/render_icons.py --fetch    # refresh the .svg sources first

Rasterising is done by Windows' own WPF engine (anti-aliased, no Python
imaging dependencies), so this runs on any Windows machine with PowerShell.
Disney+ has no freely available glyph, so its tile carries the wordmark set
in a script face instead; drop a ``disneyplus.svg`` into tools/icons to use
a real one.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "tools" / "icons"
OUTPUT = ROOT / "gtvremote" / "assets" / "icons"
SVG_URL = "https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/{slug}.svg"

#: Sizes (pixels) rendered for each icon; the app picks the nearest one.
#: Covers the app buttons' icon_size 30 at every UI scale from 0.8 to 2.2.
SIZES = tuple(range(20, 69, 4))
CORNER = 0.24                                   # tile corner radius / size
DIM_TILE, DIM_GLYPH = "#33373F", "#9AA0A6"      # disabled state (see ui/icons.py)

#: slug -> tile colour, glyph colour, glyph size as a fraction of the tile,
#: and either the Simple Icons slug (default: the key) or a text wordmark.
APPS: dict[str, dict] = {
    "youtube":    {"tile": "#FFFFFF", "glyph": "#FF0000", "scale": 0.74},
    "netflix":    {"tile": "#000000", "glyph": "#E50914", "scale": 0.58},
    "primevideo": {"tile": "#1F2E3E", "glyph": "#FFFFFF", "scale": 0.88},
    "disneyplus": {"tile": "#0E1B5C", "glyph": "#FFFFFF", "scale": 0.88,
                   "text": "Disney+", "font": "Segoe Script"},
    "spotify":    {"tile": "#191414", "glyph": "#1DB954", "scale": 0.80},
}

_NUMBER = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def normalize_path(d: str) -> str:
    """Re-space minified SVG path data so any parser reads it the same way.

    Minified paths run numbers together ("1.5-2.3", ".5.5") and, after an
    arc command, write the two flags with no separator ("0 011 0"). The
    flags are single characters, so they are split off by position.
    """
    out: list[str] = []
    params: list[str] = []
    command = ""
    i = 0
    while i < len(d):
        char = d[i]
        if char in " ,\t\r\n":
            i += 1
            continue
        if char.isalpha():
            command = char.upper()
            if command not in _ARITY:
                raise ValueError(f"unknown path command {char!r}")
            out.append(char)
            params = []
            i += 1
            continue
        if command == "A" and len(params) % 7 in (3, 4):
            if char not in "01":
                raise ValueError(f"bad arc flag {char!r} at {i}")
            token = char
            i += 1
        else:
            match = _NUMBER.match(d, i)
            if not match:
                raise ValueError(f"cannot read path data at {i}: {d[i:i + 12]!r}")
            token = match.group()
            i = match.end()
        out.append(token)
        params.append(token)
    return " ".join(out)


def svg_path(svg: str) -> str:
    match = re.search(r'\sd="([^"]+)"', svg)
    if not match:
        raise ValueError("no <path d=...> in the SVG")
    return normalize_path(match.group(1))


def fetch(slug: str, target: Path) -> None:
    with urllib.request.urlopen(SVG_URL.format(slug=slug), timeout=20) as response:
        target.write_bytes(response.read())


# The renderer: one DrawingVisual per icon, encoded to PNG with alpha at the
# tile's rounded corners so it sits on any button colour.
POWERSHELL = r"""
param([string]$SpecPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationCore, PresentationFramework, WindowsBase
$spec = Get-Content -Raw -Encoding UTF8 $SpecPath | ConvertFrom-Json
function Brush([string]$hex) {
    $color = [System.Windows.Media.ColorConverter]::ConvertFromString($hex)
    $brush = New-Object System.Windows.Media.SolidColorBrush($color)
    $brush.Freeze()
    return $brush
}
foreach ($item in $spec.items) {
    $size = [int]$item.size
    if ($item.path) {
        $geometry = [System.Windows.Media.Geometry]::Parse("F1 " + $item.path)
    } else {
        $family = New-Object System.Windows.Media.FontFamily($item.font)
        $typeface = New-Object System.Windows.Media.Typeface($family,
            [System.Windows.FontStyles]::Normal, [System.Windows.FontWeights]::Bold,
            [System.Windows.FontStretches]::Normal)
        $text = New-Object System.Windows.Media.FormattedText($item.text,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Windows.FlowDirection]::LeftToRight, $typeface, 100.0,
            [System.Windows.Media.Brushes]::White, 1.0)
        $geometry = $text.BuildGeometry((New-Object System.Windows.Point(0, 0)))
    }
    $bounds = $geometry.Bounds
    $scale = $size * [double]$item.scale / [Math]::Max($bounds.Width, $bounds.Height)
    # Every argument is parenthesised: PowerShell's comma binds tighter
    # than arithmetic, so "$a / 2, $b" would be "$a / (2, $b)".
    $centre = $size / 2.0
    $glyphX = -($bounds.X + $bounds.Width / 2)
    $glyphY = -($bounds.Y + $bounds.Height / 2)

    $visual = New-Object System.Windows.Media.DrawingVisual
    $context = $visual.RenderOpen()
    $radius = $size * [double]$item.corner
    $rect = New-Object System.Windows.Rect(0, 0, $size, $size)
    $context.DrawRoundedRectangle((Brush $item.tile), $null, $rect, $radius, $radius)

    $group = New-Object System.Windows.Media.TransformGroup
    $group.Children.Add((New-Object System.Windows.Media.TranslateTransform($glyphX, $glyphY)))
    $group.Children.Add((New-Object System.Windows.Media.ScaleTransform($scale, $scale)))
    $group.Children.Add((New-Object System.Windows.Media.TranslateTransform($centre, $centre)))
    $context.PushTransform($group)
    $context.DrawGeometry((Brush $item.glyph), $null, $geometry)
    $context.Pop()
    $context.Close()

    $bitmap = New-Object System.Windows.Media.Imaging.RenderTargetBitmap(
        $size, $size, 96, 96, [System.Windows.Media.PixelFormats]::Pbgra32)
    $bitmap.Render($visual)
    $encoder = New-Object System.Windows.Media.Imaging.PngBitmapEncoder
    $encoder.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($bitmap))
    $stream = [System.IO.File]::Create($item.out)
    $encoder.Save($stream)
    $stream.Close()
}
Write-Output ("rendered " + $spec.items.Count + " icons")
"""


def build_spec() -> list[dict]:
    items = []
    for slug, app in APPS.items():
        glyph: dict = {}
        if "text" in app:
            glyph = {"text": app["text"], "font": app["font"]}
        else:
            svg = SOURCES / f"{app.get('source', slug)}.svg"
            if not svg.exists():
                print(f"fetching {svg.name}")
                fetch(app.get("source", slug), svg)
            glyph = {"path": svg_path(svg.read_text(encoding="utf-8"))}
        for size in SIZES:
            for dim in (False, True):
                name = f"{slug}-{size}{'-dim' if dim else ''}.png"
                items.append({
                    "out": str(OUTPUT / name), "size": size, "corner": CORNER,
                    "scale": app["scale"],
                    "tile": DIM_TILE if dim else app["tile"],
                    "glyph": DIM_GLYPH if dim else app["glyph"],
                    **glyph,
                })
    return items


def main(argv: list[str]) -> int:
    SOURCES.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if "--fetch" in argv:
        for slug, app in APPS.items():
            if "text" not in app:
                source = app.get("source", slug)
                print(f"fetching {source}.svg")
                fetch(source, SOURCES / f"{source}.svg")
    items = build_spec()
    with tempfile.TemporaryDirectory() as tmp:
        spec = Path(tmp) / "icons.json"
        script = Path(tmp) / "render.ps1"
        spec.write_text(json.dumps({"items": items}), encoding="utf-8")
        script.write_text(POWERSHELL, encoding="utf-8")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(script), "-SpecPath", str(spec)], check=True)
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
