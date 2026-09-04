"""Vertical facecam composition for the "Facecam Top" style preset.

The signature layout (from mark's reference mockup, 2026-09-04) is a
three-layer overlay, all cut from the single stream source:

1. Background — the whole frame cover-scaled to the vertical canvas,
   heavily blurred and dimmed.
2. Game — the frame cropped to *exclude* the facecam region (or the full
   frame), scaled to canvas width, upper-middle.
3. Facecam — the guide rect cropped out, scaled to ~44% of canvas width,
   rounded corners, top-centre with a small buffer above and a gap above
   the game layer.

Everything here is pure math + string building so it unit-tests without
Qt or ffmpeg. The window computes geometry once per render and threads it
through ``build_composite_export_args``; ``ffmpeg_export`` runs it as
``extra_args`` unchanged (a single input split inside ``-filter_complex``,
the same graph shape the legacy reel.py used).

Captions keep their output-pixel-space contract: the ``ass=`` filter is
appended at the very end of the graph.
"""
from __future__ import annotations

from typing import Optional

# Layout fractions of the output frame.
FACECAM_WIDTH_FRAC = 0.44  # facecam layer width / out_w
TOP_BUFFER_FRAC = 0.03     # gap above the facecam layer / out_h
GAP_FRAC = 0.03            # gap between facecam bottom and game top / out_h
FACECAM_RADIUS_FRAC = 0.04 # corner radius / facecam width

# Legacy clamp bounds, ported from reel.py's guide model.
_MIN_EXTENT = 0.08
_MAX_EXTENT = 0.70

GAME_MODES = ("avoid_facecam", "full_frame")

_LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"


def _frac(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_facecam_layout(layout: Optional[dict]) -> dict:
    """Coerce a guide rect into a safe ``{enabled, x, y, w, h, game_mode}``.

    ``x/y/w/h`` are fractions of the source frame. Extents clamp to the
    legacy ``[0.08, 0.70]`` bounds and the rect is kept inside the frame.
    """
    if not layout:
        return {
            "enabled": False, "x": 0.0, "y": 0.0, "w": 0.25, "h": 0.25,
            "game_mode": "avoid_facecam",
        }
    w = min(_MAX_EXTENT, max(_MIN_EXTENT, _frac(layout.get("w"), 0.25)))
    h = min(_MAX_EXTENT, max(_MIN_EXTENT, _frac(layout.get("h"), 0.25)))
    x = min(1.0 - w, max(0.0, _frac(layout.get("x"), 0.0)))
    y = min(1.0 - h, max(0.0, _frac(layout.get("y"), 0.0)))
    game_mode = layout.get("game_mode", "avoid_facecam")
    if game_mode not in GAME_MODES:
        game_mode = "avoid_facecam"
    return {
        "enabled": bool(layout.get("enabled", True)),
        "x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4),
        "game_mode": game_mode,
    }


def game_crop_region(src_w: int, src_h: int, layout: dict, game_mode: Optional[str] = None) -> dict:
    """Pick the source crop for the game layer, in source pixels.

    ``avoid_facecam`` cuts whichever strip — the facecam's side column or
    its rows — loses the least game area, keeping the cam-free remainder.
    """
    mode = game_mode or layout.get("game_mode", "avoid_facecam")
    if mode == "full_frame":
        return {"x": 0, "y": 0, "w": src_w, "h": src_h}

    cam_x = int(round(layout["x"] * src_w))
    cam_y = int(round(layout["y"] * src_h))
    cam_w = int(round(layout["w"] * src_w))
    cam_h = int(round(layout["h"] * src_h))

    side_loss = cam_w / src_w
    vert_loss = cam_h / src_h
    if side_loss <= vert_loss:
        # Cut the cam's side column; keep the opposite side.
        if cam_x + cam_w / 2 < src_w / 2:
            x = min(src_w, cam_x + cam_w)
            return {"x": x, "y": 0, "w": src_w - x, "h": src_h}
        return {"x": 0, "y": 0, "w": max(0, cam_x), "h": src_h}
    # Cut the cam's rows; keep the opposite side.
    if cam_y + cam_h / 2 < src_h / 2:
        y = min(src_h, cam_y + cam_h)
        return {"x": 0, "y": y, "w": src_w, "h": src_h - y}
    return {"x": 0, "y": 0, "w": src_w, "h": max(0, cam_y)}


def _even(value: float) -> int:
    return int(round(value / 2.0)) * 2


def composite_geometry(src_w: int, src_h: int, layout: dict, out_w: int, out_h: int) -> dict:
    """Every pixel number the filter graph needs, in output space.

    All sizing is fraction-based so the same layout renders identically
    (modulo rounding) at 1080x1920 and 2160x3840.
    """
    game = game_crop_region(src_w, src_h, layout)
    game_out_h = _even(out_w * game["h"] / game["w"])

    cam_w = int(round(layout["w"] * src_w))
    cam_h = int(round(layout["h"] * src_h))
    face_out_w = _even(FACECAM_WIDTH_FRAC * out_w)
    face_out_h = _even(face_out_w * (cam_h / cam_w)) if cam_w else face_out_w
    face_x = (out_w - face_out_w) // 2
    face_y = int(round(TOP_BUFFER_FRAC * out_h))
    game_y = face_y + face_out_h + int(round(GAP_FRAC * out_h))

    return {
        "out_w": out_w,
        "out_h": out_h,
        "game": {
            "crop_x": game["x"], "crop_y": game["y"],
            "crop_w": game["w"], "crop_h": game["h"],
            "out_h": game_out_h, "y": game_y,
        },
        "facecam": {
            "crop_x": int(round(layout["x"] * src_w)),
            "crop_y": int(round(layout["y"] * src_h)),
            "crop_w": cam_w, "crop_h": cam_h,
            "out_w": face_out_w, "out_h": face_out_h,
            "x": face_x, "y": face_y,
            "radius": max(8, int(round(face_out_w * FACECAM_RADIUS_FRAC))),
        },
        # Blur radius scaled to output so 4K keeps the 1080p look.
        "blur_radius": max(10, int(round(20 * out_h / 1920))),
    }


def _rounded_alpha_expr(radius: int) -> str:
    # Signed distance to a rounded rectangle: pixels outside the corner
    # circles drop to transparent. Commas must be escaped inside filter
    # expressions.
    corner = f"(W/2-{radius})"
    dx = f"max(0\\,abs(X-W/2)-{corner})"
    dy = f"max(0\\,abs(Y-H/2)-(H/2-{radius}))"
    return f"255*lt(hypot({dx}\\,{dy})\\,{radius})"


def build_composite_filter_complex(
    geom: dict, subtitle_ass: Optional[str] = None,
) -> tuple[str, list[str]]:
    """Build the ``-filter_complex`` graph + ``-map`` args for the layout.

    Returns ``(filter_complex, map_args)``. With ``normalize_audio`` the
    caller moves loudnorm into the graph (see ``build_composite_export_args``)
    — ``-af`` may not sit next to ``-filter_complex``.
    """
    g = geom["game"]
    f = geom["facecam"]
    out_w, out_h = geom["out_w"], geom["out_h"]

    game_chain = (
        f"[0:v]crop={g['crop_w']}:{g['crop_h']}:{g['crop_x']}:{g['crop_y']},"
        f"scale={out_w}:{g['out_h']}:flags=lanczos[game]"
    )
    face_chain = (
        f"[0:v]crop={f['crop_w']}:{f['crop_h']}:{f['crop_x']}:{f['crop_y']},"
        f"scale={f['out_w']}:{f['out_h']}:flags=lanczos,"
        f"format=yuva420p,"
        f"geq=r='r(X\\,Y)':g='g(X\\,Y)':b='b(X\\,Y)':a='{_rounded_alpha_expr(f['radius'])}'[face]"
    )
    bg_chain = (
        f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
        f"crop={out_w}:{out_h},"
        f"boxblur={geom['blur_radius']}:2,"
        f"eq=brightness=-0.3:saturation=0.85[bg]"
    )
    tail = f"ass='{subtitle_ass}'" if subtitle_ass else "null"
    filter_complex = ";".join(
        (game_chain, face_chain, bg_chain,
         f"[bg][game]overlay=0:{g['y']}[g1]",
         f"[g1][face]overlay={f['x']}:{f['y']}[g2]",
         f"[g2]{tail}[v]")
    )
    return filter_complex, ["-map", "[v]", "-map", "0:a?"]


def build_composite_export_args(
    start_ms: int, end_ms: int, geom: dict,
    normalize_audio: bool = True, subtitle_ass: Optional[str] = None,
) -> list[str]:
    """Compose ffmpeg args for a composite clip export.

    Mirrors ``build_clip_export_args``' codec tail but with
    ``-filter_complex`` + explicit maps instead of ``-vf`` (the two are
    mutually exclusive). Returns the args between input and output paths.
    """
    fc, maps = build_composite_filter_complex(geom, subtitle_ass)
    if normalize_audio:
        fc += f";[0:a]{_LOUDNORM}[a]"
        maps = ["-map", "[v]", "-map", "[a]"]
    args = [
        "-ss", f"{start_ms / 1000:.3f}", "-to", f"{end_ms / 1000:.3f}",
        "-filter_complex", fc, *maps,
        "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
    ]
    return args
