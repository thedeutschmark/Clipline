"""Facecam guide dialog — draw the webcam rect once, preview the stack.

The guide is a normalized rect over the source frame (fractions, so it
survives any resolution). Drag the box over your facecam; the right panel
composes the vertical layout live using the same geometry math the render
uses (``composite.composite_geometry`` at preview scale), so what you see
here is what ffmpeg writes.
"""
from __future__ import annotations

import subprocess
from typing import Optional

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from native.services.composite import (
    GAME_MODES,
    composite_geometry,
    normalize_facecam_layout,
)
from native.services.paths import PROCESSING_DIR
from native.services.settings import save_facecam_layout
from native.ui import theme
from native.ui.project_state import ProjectState

_DEFAULT_RECT = {"x": 0.02, "y": 0.02, "w": 0.25, "h": 0.25}
_HIT_PX = 10  # grab tolerance around the resize handle
_MIN_RECT_PX = 24

_PREVIEW_W, _PREVIEW_H = 270, 480  # 9:16


def _extract_frame(ffmpeg: str, source, position_s: Optional[float]) -> Optional[QPixmap]:
    """One ffmpeg-extracted still, best-effort (caption editor pattern)."""
    try:
        pos = float(position_s) if position_s is not None else 1.0
    except (TypeError, ValueError):
        pos = 1.0
    out_png = PROCESSING_DIR / "_guide_frame.png"
    try:
        result = subprocess.run(
            [ffmpeg, "-y", "-ss", f"{max(0.0, pos):.3f}", "-i", str(source),
             "-frames:v", "1", str(out_png)],
            capture_output=True, timeout=10,
        )
    except OSError:
        return None
    if result.returncode == 0 and out_png.exists():
        px = QPixmap(str(out_png))
        if not px.isNull():
            return px
    return None


class _GuideCanvas(QWidget):
    """Source frame with a draggable / resizable guide rect overlay."""

    rect_changed = Signal(dict)  # normalized {x, y, w, h} — live during drag

    def __init__(self, frame: QPixmap, initial: dict, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(360)
        self._frame = frame
        self._scaled: QPixmap | None = None
        self._rect = dict(initial)
        self._drag: str | None = None  # "move" | "resize" | "draw"
        self._draw_anchor = QPoint()
        self.setMouseTracking(True)

    # -- geometry ---------------------------------------------------------

    def _video_rect(self) -> QRect:
        fw, fh = self._frame.width(), self._frame.height()
        if fw <= 0 or fh <= 0:
            return QRect()
        scale = min(self.width() / fw, self.height() / fh)
        vw, vh = int(fw * scale), int(fh * scale)
        return QRect((self.width() - vw) // 2, (self.height() - vh) // 2, vw, vh)

    def _norm_to_px(self, rect: dict) -> QRect:
        vr = self._video_rect()
        return QRect(
            int(vr.left() + rect["x"] * vr.width()),
            int(vr.top() + rect["y"] * vr.height()),
            int(rect["w"] * vr.width()),
            int(rect["h"] * vr.height()),
        )

    def _px_to_norm(self, rect: QRect) -> dict:
        vr = self._video_rect()
        if vr.width() <= 0 or vr.height() <= 0:
            return dict(self._rect)
        return {
            "x": max(0.0, min(1.0, (rect.left() - vr.left()) / vr.width())),
            "y": max(0.0, min(1.0, (rect.top() - vr.top()) / vr.height())),
            "w": max(0.0, min(1.0, rect.width() / vr.width())),
            "h": max(0.0, min(1.0, rect.height() / vr.height())),
        }

    # -- painting ---------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.BG_INK))
        vr = self._video_rect()
        if vr.isEmpty():
            return
        if self._scaled is None or self._scaled.size() != vr.size():
            self._scaled = self._frame.scaled(
                vr.width(), vr.height(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        painter.drawPixmap(vr.topLeft(), self._scaled)

        rect = self._norm_to_px(self._rect)
        # Dim outside the guide so the selection reads instantly.
        dim = QColor(theme.BG_INK)
        dim.setAlpha(120)
        path = QPainterPath()
        path.addRect(vr)
        hole = QPainterPath()
        hole.addRect(rect)
        painter.fillPath(path.subtracted(hole), dim)

        painter.setPen(QPen(QColor(theme.ACCENT), 2))
        painter.drawRect(rect)
        # Resize handle: bottom-right square.
        handle = QRect(rect.right() - _HIT_PX, rect.bottom() - _HIT_PX, _HIT_PX * 2, _HIT_PX * 2)
        painter.fillRect(handle, QColor(theme.ACCENT))
        painter.setPen(QColor(theme.ACCENT_INK))
        painter.drawText(
            rect.adjusted(4, 2, 0, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            f"{self._rect['w']:.0%} × {self._rect['h']:.0%}",
        )

    # -- dragging ---------------------------------------------------------

    def _handle_at(self, pos: QPoint) -> QRect:
        rect = self._norm_to_px(self._rect)
        return QRect(rect.right() - _HIT_PX, rect.bottom() - _HIT_PX,
                     _HIT_PX * 2, _HIT_PX * 2)

    def mousePressEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self._handle_at(pos).contains(pos):
            self._drag = "resize"
        elif self._norm_to_px(self._rect).contains(pos):
            self._drag = "move"
            self._move_offset = pos - self._norm_to_px(self._rect).topLeft()
        else:
            self._drag = "draw"
            self._draw_anchor = pos
            vr = self._video_rect()
            self._rect = self._px_to_norm(QRect(pos, pos))
        self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self._drag is None:
            if self._handle_at(pos).contains(pos):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif self._norm_to_px(self._rect).contains(pos):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)
            return
        if self._drag == "move":
            rect = self._norm_to_px(self._rect)
            rect.moveTo(pos - self._move_offset)
            self._rect = self._px_to_norm(rect)
        elif self._drag == "resize":
            rect = self._norm_to_px(self._rect)
            vr = self._video_rect()
            right = max(rect.left() + _MIN_RECT_PX, min(pos.x(), vr.right()))
            bottom = max(rect.top() + _MIN_RECT_PX, min(pos.y(), vr.bottom()))
            rect = QRect(rect.topLeft(), QPoint(right, bottom))
            self._rect = self._px_to_norm(rect)
        elif self._drag == "draw":
            self._rect = self._px_to_norm(QRect(self._draw_anchor, pos).normalized())
        self.update()
        self.rect_changed.emit(dict(self._rect))

    def mouseReleaseEvent(self, _event) -> None:
        self._drag = None
        self.rect_changed.emit(dict(self._rect))


class _StackPreview(QWidget):
    """Live 9:16 composition of the guide — the render, previewed."""

    def __init__(self, frame: QPixmap, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(_PREVIEW_W, _PREVIEW_H)
        self._frame = frame
        self._layout: dict | None = None
        # Cheap blur: crush the frame down, scale it back up smoothly.
        tiny = frame.scaledToWidth(28, Qt.TransformationMode.SmoothTransformation)
        self._blurred = tiny.scaled(
            _PREVIEW_W, _PREVIEW_H,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def set_layout(self, layout: dict) -> None:
        self._layout = layout
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.BG_INK))
        if not self._layout:
            return
        fw, fh = self._frame.width(), self._frame.height()
        geom = composite_geometry(fw, fh, self._layout, _PREVIEW_W, _PREVIEW_H)
        g, f = geom["game"], geom["facecam"]

        # Layer 1 — blurred, dimmed cover of the whole frame.
        painter.drawPixmap(0, 0, self._blurred)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 115))

        # Layer 2 — the game crop, full width, at its band.
        game = self._frame.copy(
            g["crop_x"], g["crop_y"], g["crop_w"], g["crop_h"]
        ).scaled(
            _PREVIEW_W, g["out_h"],
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawPixmap(0, g["y"], game)

        # Layer 3 — rounded facecam crop, top-centre.
        face = self._frame.copy(
            f["crop_x"], f["crop_y"], f["crop_w"], f["crop_h"]
        ).scaled(
            f["out_w"], f["out_h"],
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(
            float(f["x"]), float(f["y"]), float(f["out_w"]), float(f["out_h"]),
            float(f["radius"]), float(f["radius"]),
        )
        painter.setClipPath(clip)
        painter.drawPixmap(f["x"], f["y"], face)
        painter.restore()

        # Caption hint bars where the karaoke captions land.
        painter.setPen(Qt.PenStyle.NoPen)
        white = QColor(255, 255, 255, 150)
        painter.setBrush(white)
        bar_y = int(_PREVIEW_H * 0.80)
        painter.drawRoundedRect(_PREVIEW_W // 6, bar_y, _PREVIEW_W * 2 // 3, 12, 6, 6)
        painter.drawRoundedRect(_PREVIEW_W // 4, bar_y + 20, _PREVIEW_W // 2, 12, 6, 6)


class FacecamGuideDialog(QDialog):
    """Modal: draw the facecam guide, preview the vertical stack, save."""

    def __init__(
        self,
        state: ProjectState,
        ffmpeg: str,
        position_s: Optional[float] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Facecam guide")
        self.setStyleSheet(theme.GLOBAL_QSS)
        self._state = state

        source = state.source
        frame = _extract_frame(ffmpeg, source, position_s) if source else None
        if frame is None or frame.isNull():
            QMessageBox.warning(
                self, "No frame",
                "Couldn't grab a frame from the source to draw on.\n"
                "Try again with the video loaded in the Ingest stage.",
            )
            self.reject()
            return

        existing = state.facecam_layout or {}
        initial = {k: existing.get(k, _DEFAULT_RECT[k]) for k in ("x", "y", "w", "h")}
        self._game_mode = existing.get("game_mode", "avoid_facecam")
        if self._game_mode not in GAME_MODES:
            self._game_mode = "avoid_facecam"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)

        title = QLabel("Draw the box over your webcam")
        title.setProperty("title", True)
        outer.addWidget(title)
        hint = QLabel(
            "Drag the box to move it, drag its corner to resize, or click-drag "
            "anywhere to draw a fresh one. The right side previews the vertical "
            "render."
        )
        hint.setWordWrap(True)
        hint.setProperty("hint", True)
        outer.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(8)
        left_kicker = QLabel("SOURCE FRAME")
        left_kicker.setProperty("kicker", True)
        left.addWidget(left_kicker)
        self._canvas = _GuideCanvas(frame, initial, self)
        self._canvas.rect_changed.connect(self._on_rect_changed)
        left.addWidget(self._canvas, 1)
        self._coords = QLabel()
        self._coords.setProperty("mono", True)
        left.addWidget(self._coords)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        right_kicker = QLabel("VERTICAL PREVIEW")
        right_kicker.setProperty("kicker", True)
        right.addWidget(right_kicker)
        self._preview = _StackPreview(frame, self)
        right.addWidget(self._preview)
        right.addStretch(1)
        game_label = QLabel("Game layer")
        game_label.setProperty("kicker", True)
        right.addWidget(game_label)
        self._game_combo = QComboBox()
        self._game_combo.addItem("Avoid facecam (crop it out)", "avoid_facecam")
        self._game_combo.addItem("Full frame", "full_frame")
        self._game_combo.setCurrentIndex(
            self._game_combo.findData(self._game_mode)
        )
        self._game_combo.currentIndexChanged.connect(self._on_game_mode_changed)
        right.addWidget(self._game_combo)
        body.addLayout(right, 0)

        outer.addLayout(body, 1)

        remember_row = QHBoxLayout()
        remember_row.setSpacing(8)
        self._remember = QCheckBox("Remember as:")
        self._remember.setChecked(True)
        remember_row.addWidget(self._remember)
        self._name_input = QLineEdit("main")
        self._name_input.setMaximumWidth(180)
        remember_row.addWidget(self._name_input)
        remember_row.addStretch(1)
        outer.addLayout(remember_row)

        buttons = QHBoxLayout()
        clear_btn = QPushButton("Clear Guide")
        clear_btn.clicked.connect(self._on_clear)
        buttons.addWidget(clear_btn)
        buttons.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        save_btn = QPushButton("Save Guide")
        save_btn.setProperty("primary", True)
        save_btn.clicked.connect(self._on_save)
        buttons.addWidget(save_btn)
        outer.addLayout(buttons)

        self._rect = dict(initial)
        self._refresh()

    # ── internals ──────────────────────────────────────────────────────

    def _current_layout(self) -> dict:
        return {
            "enabled": True,
            "x": self._rect["x"], "y": self._rect["y"],
            "w": self._rect["w"], "h": self._rect["h"],
            "game_mode": self._game_mode,
        }

    def _refresh(self) -> None:
        self._coords.setText(
            f"x {self._rect['x']:.1%}  y {self._rect['y']:.1%}    "
            f"{self._rect['w']:.1%} × {self._rect['h']:.1%}"
        )
        self._preview.set_layout(self._current_layout())

    def _on_rect_changed(self, rect: dict) -> None:
        self._rect = dict(rect)
        self._refresh()

    def _on_game_mode_changed(self, _index: int) -> None:
        self._game_mode = self._game_combo.currentData()
        self._refresh()

    def _on_save(self) -> None:
        layout = normalize_facecam_layout(self._current_layout())
        self._state.set_facecam_layout(layout)
        if self._remember.isChecked():
            save_facecam_layout(self._name_input.text(), layout)
        self.accept()

    def _on_clear(self) -> None:
        self._state.set_facecam_layout(None)
        self.accept()
