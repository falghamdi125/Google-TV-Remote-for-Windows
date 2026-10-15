"""Render the application icon (gtvremote/assets/app.ico and docs/icon.png).

The icon is a remote control rising out of a tile in the app's accent blue:
a dark body like the window itself, the white d-pad ring with the blue OK
dot in the middle, and a few buttons below. It is drawn from a handful of
shapes at every size Windows asks for (16 to 256 px); the small sizes drop
the finer buttons and snap to the pixel grid so the title bar and taskbar
get a crisp icon rather than a blurred-down big one.

    python tools/render_app_icon.py              # write the .ico and docs/icon.png
    python tools/render_app_icon.py --pngs DIR   # also keep the per-size PNGs

Like render_icons.py, the drawing is done by Windows' own WPF engine via
PowerShell, so there is no Python imaging dependency. The .ico is packed
here: 32-bit bitmaps for the sizes up to 48 px, the form every icon reader
understands (Tk's ``wm iconbitmap`` among them), and PNG-compressed entries
for the big ones, which keeps the file small.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICO = ROOT / "gtvremote" / "assets" / "app.ico"
DOCS_PNG = ROOT / "docs" / "icon.png"

#: Every size stored in the .ico. Windows picks the nearest for the title
#: bar (16-24), taskbar (24-48), Explorer (16-256) and Alt+Tab (32-96).
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
PNG_FROM = 64           # entries this big are stored PNG-compressed

# --- the design, in fractions of the tile size ----------------------------
TILE_CORNER = 0.22
TILE = ("#5FA4FF", "#2A60DC")       # top-left to bottom-right
BODY = ("#30353F", "#14161B")       # top to bottom, the window's own greys
RING = "#EEF1F6"                    # d-pad ring
OK_DOT = "#4C8DFF"                  # theme.ACCENT, the OK key in the window
BUTTON = "#7A818E"

BODY_WIDTH = 0.48                   # the body runs off the bottom of the tile
BODY_TOP = 0.11
RING_Y, RING_RADIUS, RING_STROKE = 0.40, 0.165, 0.04
DOT_RADIUS = 0.05
ARROW_INNER, ARROW_OUTER, ARROW_HALF_BASE = 0.065, 0.105, 0.0225   # from the ring's centre
ROW_A_Y, ROW_A_RADIUS, ROW_A_X = 0.68, 0.035, (0.38, 0.50, 0.62)
ROW_B_Y, ROW_B_HEIGHT, ROW_B_WIDTH = 0.82, 0.07, 0.31
#: Soft shadow under the body: (spread, opacity) per layer, outermost last.
SHADOW = ((0.01, 0.10), (0.02, 0.08), (0.03, 0.06), (0.04, 0.04), (0.05, 0.03))
SHADOW_OFFSET = 0.015


def rgba(hex_rgb: str, alpha: float) -> str:
    return f"#{round(alpha * 255):02X}{hex_rgb[1:]}"


def gradient(start: tuple[float, float], end: tuple[float, float],
             stops: list[tuple[float, str]]) -> dict:
    """A linear gradient; points are relative to the shape's bounding box."""
    return {"from": list(start), "to": list(end), "stops": [list(s) for s in stops]}


def rrect(x: float, y: float, w: float, h: float, radius: float, fill) -> dict:
    return {"op": "rrect", "rect": [x, y, w, h], "radius": radius, "fill": fill}


def ellipse(cx: float, cy: float, radius: float, fill=None,
            stroke: str | None = None, width: float = 0) -> dict:
    op = {"op": "ellipse", "center": [cx, cy], "radius": radius, "fill": fill}
    if stroke:
        op.update(stroke=stroke, width=width)
    return op


def polygon(points: list[tuple[float, float]], fill: str) -> dict:
    d = " ".join(f"{'M' if i == 0 else 'L'} {x:.3f},{y:.3f}" for i, (x, y) in enumerate(points))
    return {"op": "path", "d": d + " Z", "fill": fill}


def design(size: int) -> list[dict]:
    """The drawing for one size, outermost shape first."""
    s = float(size)
    snap = size <= 32

    def px(fraction: float, step: float = 1.0) -> float:
        """Scale to pixels, on a grid of ``step`` px at the small sizes."""
        value = fraction * s
        return round(value / step) * step if snap else value

    corner = s * TILE_CORNER
    ops = [rrect(0, 0, s, s, corner, gradient((0, 0), (1, 1), [(0, TILE[0]), (1, TILE[1])])),
           {"op": "clip", "rect": [0, 0, s, s], "radius": corner}]

    x0, x1 = px(0.5 - BODY_WIDTH / 2), px(0.5 + BODY_WIDTH / 2)
    top, width = px(BODY_TOP), x1 - px(0.5 - BODY_WIDTH / 2)
    bottom = s * 1.3                        # clipped by the tile
    if size >= 32:
        for spread, alpha in SHADOW:
            d = spread * s
            ops.append(rrect(x0 - d, top - d + SHADOW_OFFSET * s, width + 2 * d,
                             bottom - top + 2 * d, width / 2 + d, rgba("#000000", alpha)))
    # The visible part of the body is the top 75 % of the shape, so the
    # gradient reaches its bottom colour at the tile's edge.
    ops.append(rrect(x0, top, width, bottom - top, width / 2,
                     gradient((0, 0), (0, 1), [(0, BODY[0]), (0.75, BODY[1])])))

    cx, cy = s / 2, px(RING_Y, 0.5)
    outer = px(RING_RADIUS, 0.5)
    stroke = max(px(RING_STROKE, 0.5), 1.5) if snap else RING_STROKE * s
    ops.append(ellipse(cx, cy, outer - stroke / 2, stroke=RING, width=stroke))
    if size >= 20:
        ops.append(ellipse(cx, cy, px(DOT_RADIUS, 0.5), fill=OK_DOT))
    if size >= 48:                          # the four direction arrows
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            tip = (cx + dx * ARROW_OUTER * s, cy + dy * ARROW_OUTER * s)
            base = (cx + dx * ARROW_INNER * s, cy + dy * ARROW_INNER * s)
            wing = (-dy * ARROW_HALF_BASE * s, dx * ARROW_HALF_BASE * s)
            ops.append(polygon([tip, (base[0] + wing[0], base[1] + wing[1]),
                                (base[0] - wing[0], base[1] - wing[1])], RING))
    if size >= 32:
        for fx in ROW_A_X:
            ops.append(ellipse(px(fx, 0.5), px(ROW_A_Y, 0.5), px(ROW_A_RADIUS, 0.5), fill=BUTTON))
    if size >= 24:
        h, w = px(ROW_B_HEIGHT), px(ROW_B_WIDTH)
        ops.append(rrect(cx - w / 2, px(ROW_B_Y) - h / 2, w, h, h / 2, BUTTON))
    ops.append({"op": "unclip"})
    return ops


# The renderer: one DrawingVisual per size, saved as a PNG and as raw
# straight-alpha BGRA pixels for the bitmap entries of the .ico.
POWERSHELL = r"""
param([string]$SpecPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationCore, PresentationFramework, WindowsBase
$spec = Get-Content -Raw -Encoding UTF8 $SpecPath | ConvertFrom-Json

function Color([string]$hex) {
    return [System.Windows.Media.Color][System.Windows.Media.ColorConverter]::ConvertFromString($hex)
}
function Brush($fill) {
    if ($null -eq $fill) { return $null }
    if ($fill -is [string]) {
        $brush = New-Object System.Windows.Media.SolidColorBrush((Color $fill))
    } else {
        $brush = New-Object System.Windows.Media.LinearGradientBrush
        $brush.StartPoint = New-Object System.Windows.Point([double]$fill.from[0], [double]$fill.from[1])
        $brush.EndPoint = New-Object System.Windows.Point([double]$fill.to[0], [double]$fill.to[1])
        foreach ($stop in $fill.stops) {
            $brush.GradientStops.Add((New-Object System.Windows.Media.GradientStop((Color $stop[1]), [double]$stop[0])))
        }
    }
    $brush.Freeze()
    return $brush
}
function Rect($r) {
    return New-Object System.Windows.Rect([double]$r[0], [double]$r[1], [double]$r[2], [double]$r[3])
}

foreach ($item in $spec.items) {
    $size = [int]$item.size
    $visual = New-Object System.Windows.Media.DrawingVisual
    $context = $visual.RenderOpen()
    foreach ($op in $item.ops) {
        switch ($op.op) {
            'clip' {
                $radius = [double]$op.radius
                $context.PushClip((New-Object System.Windows.Media.RectangleGeometry((Rect $op.rect), $radius, $radius)))
            }
            'unclip' { $context.Pop() }
            'rrect' {
                $radius = [double]$op.radius
                $context.DrawRoundedRectangle((Brush $op.fill), $null, (Rect $op.rect), $radius, $radius)
            }
            'ellipse' {
                $pen = $null
                if ($op.stroke) {
                    $pen = New-Object System.Windows.Media.Pen((Brush $op.stroke), [double]$op.width)
                }
                $centre = New-Object System.Windows.Point([double]$op.center[0], [double]$op.center[1])
                $context.DrawEllipse((Brush $op.fill), $pen, $centre, [double]$op.radius, [double]$op.radius)
            }
            'path' {
                $geometry = [System.Windows.Media.Geometry]::Parse($op.d)
                $context.DrawGeometry((Brush $op.fill), $null, $geometry)
            }
        }
    }
    $context.Close()

    $bitmap = New-Object System.Windows.Media.Imaging.RenderTargetBitmap(
        $size, $size, 96, 96, [System.Windows.Media.PixelFormats]::Pbgra32)
    $bitmap.Render($visual)
    $encoder = New-Object System.Windows.Media.Imaging.PngBitmapEncoder
    $encoder.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($bitmap))
    $stream = [System.IO.File]::Create($item.png)
    $encoder.Save($stream)
    $stream.Close()

    $straight = New-Object System.Windows.Media.Imaging.FormatConvertedBitmap(
        $bitmap, [System.Windows.Media.PixelFormats]::Bgra32, $null, 0)
    $stride = $size * 4
    $pixels = New-Object byte[] ($stride * $size)
    $straight.CopyPixels($pixels, $stride, 0)
    [System.IO.File]::WriteAllBytes($item.raw, $pixels)
}
Write-Output ("rendered " + $spec.items.Count + " sizes")
"""


def render(directory: Path) -> dict[int, tuple[bytes, bytes]]:
    """Draw every size into ``directory``; returns {size: (png, bgra)}."""
    items = [{"size": size, "ops": design(size),
              "png": str(directory / f"app-{size}.png"),
              "raw": str(directory / f"app-{size}.bgra")} for size in SIZES]
    spec = directory / "icon.json"
    script = directory / "render.ps1"
    spec.write_text(json.dumps({"items": items}), encoding="utf-8")
    script.write_text(POWERSHELL, encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(script), "-SpecPath", str(spec)], check=True)
    return {item["size"]: (Path(item["png"]).read_bytes(), Path(item["raw"]).read_bytes())
            for item in items}


def bitmap_entry(size: int, bgra: bytes) -> bytes:
    """A 32-bit BITMAPINFOHEADER image plus the 1-bit mask an .ico carries."""
    stride = size * 4
    rows = [bgra[y * stride:(y + 1) * stride] for y in range(size)]
    mask_stride = (size + 31) // 32 * 4
    masks = []
    for row in rows:
        bits = bytearray(mask_stride)
        for x in range(size):
            if row[x * 4 + 3] == 0:             # fully transparent
                bits[x // 8] |= 0x80 >> (x % 8)
        masks.append(bytes(bits))
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                         size * (stride + mask_stride), 0, 0, 0, 0)
    # Both planes are stored bottom-up.
    return header + b"".join(reversed(rows)) + b"".join(reversed(masks))


def pack_ico(images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    directory, blobs = [], []
    for size, data in images:
        directory.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                                     1, 32, len(data), offset))
        blobs.append(data)
        offset += len(data)
    return header + b"".join(directory) + b"".join(blobs)


def main(argv: list[str]) -> int:
    keep = Path(argv[argv.index("--pngs") + 1]) if "--pngs" in argv else None
    with tempfile.TemporaryDirectory() as tmp:
        rendered = render(Path(tmp))
        if keep is not None:
            keep.mkdir(parents=True, exist_ok=True)
            for size in SIZES:
                shutil.copy(Path(tmp) / f"app-{size}.png", keep / f"app-{size}.png")
    images = [(size, png if size >= PNG_FROM else bitmap_entry(size, bgra))
              for size, (png, bgra) in rendered.items()]
    ICO.write_bytes(pack_ico(images))
    DOCS_PNG.write_bytes(rendered[256][0])
    print(f"wrote {ICO} ({ICO.stat().st_size // 1024} KB) and {DOCS_PNG}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
