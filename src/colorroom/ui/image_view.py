"""The source image view: shows the picture, the pixel markers and the eyedropper loupe."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.colorspace import readable_text_color
from ..core.index import Point
from . import theme

_PREVIEW_LIMIT = 2400  # longest side of the picture kept for display
_LOUPE_PIXELS = 11  # the loupe shows an 11 x 11 pixel neighbourhood
_LOUPE_ZOOM = 10
_INSET = 18  # space kept free around the picture


@dataclass(frozen=True)
class Marker:
    point: Point
    color: str


def rgba_to_qimage(rgba: np.ndarray) -> QImage:
    """Wrap an RGBA array as a QImage (a copy, so the array may be freed)."""
    height, width = rgba.shape[:2]
    data = np.ascontiguousarray(rgba)
    return QImage(data.data, width, height, 4 * width, QImage.Format.Format_RGBA8888).copy()


def qimage_to_rgba(image: QImage) -> np.ndarray:
    """Convert any QImage to an RGBA array of shape (height, width, 4)."""
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = converted.width(), converted.height()
    buffer = np.frombuffer(converted.constBits(), dtype=np.uint8, count=converted.sizeInBytes())
    rows = buffer.reshape(height, converted.bytesPerLine())
    return rows[:, : width * 4].reshape(height, width, 4).copy()


class ImageView(QWidget):
    """Displays the analyzed image scaled to fit, with markers for located colors."""

    pixelClicked = Signal(float, float)  # fractional position of a click while picking
    openRequested = Signal()  # the empty view was clicked

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rgba: Optional[np.ndarray] = None
        self._preview: Optional[QImage] = None
        self._scaled: Optional[QPixmap] = None
        self._scaled_key = None
        self._inspected: Optional[Marker] = None
        self._highlighted: Optional[Marker] = None
        self._picking = False
        self._hover: Optional[QPointF] = None
        self._drop_active = False
        self._checker = theme.checker_brush()
        self.setMouseTracking(True)
        self.setMinimumSize(260, 170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(520, 300)

    # ----------------------------------------------------------------------------------
    # State
    # ----------------------------------------------------------------------------------

    def set_image(self, rgba: Optional[np.ndarray]) -> None:
        self._rgba = rgba
        self._scaled = None
        self._scaled_key = None
        self._hover = None
        if rgba is None:
            self._preview = None
        else:
            height, width = rgba.shape[:2]
            image = QImage(rgba.data, width, height, 4 * width, QImage.Format.Format_RGBA8888)
            longest = max(width, height)
            if longest > _PREVIEW_LIMIT:
                image = image.scaled(
                    max(1, round(width * _PREVIEW_LIMIT / longest)),
                    max(1, round(height * _PREVIEW_LIMIT / longest)),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            else:
                image = image.copy()
            self._preview = image
        self._update_cursor()
        self.update()

    def set_markers(self, inspected: Optional[Marker], highlighted: Optional[Marker]) -> None:
        if (inspected, highlighted) != (self._inspected, self._highlighted):
            self._inspected, self._highlighted = inspected, highlighted
            self.update()

    def set_picking(self, picking: bool) -> None:
        self._picking = picking
        self._update_cursor()
        self.update()

    def set_drop_active(self, active: bool) -> None:
        if active != self._drop_active:
            self._drop_active = active
            self.update()

    def _update_cursor(self) -> None:
        if self._preview is None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self._picking:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.unsetCursor()

    # ----------------------------------------------------------------------------------
    # Geometry
    # ----------------------------------------------------------------------------------

    def image_rect(self) -> Optional[QRectF]:
        """Where the picture is drawn inside the widget, keeping its proportions."""
        if self._rgba is None:
            return None
        height, width = self._rgba.shape[:2]
        # The inset leaves room for a marker that sits on the very edge of the picture.
        available = QRectF(self.rect()).adjusted(_INSET, _INSET, -_INSET, -_INSET)
        scale = min(available.width() / width, available.height() / height)
        shown_width, shown_height = width * scale, height * scale
        return QRectF(
            available.x() + (available.width() - shown_width) / 2,
            available.y() + (available.height() - shown_height) / 2,
            shown_width,
            shown_height,
        )

    def _fraction_at(self, position: QPointF) -> Optional[tuple[float, float]]:
        rect = self.image_rect()
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            return None
        x = (position.x() - rect.x()) / rect.width()
        y = (position.y() - rect.y()) / rect.height()
        return (x, y) if 0 <= x < 1 and 0 <= y < 1 else None

    # ----------------------------------------------------------------------------------
    # Mouse
    # ----------------------------------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:
        if self._picking and self._rgba is not None:
            self._hover = event.position()
            self.update()

    def leaveEvent(self, event) -> None:
        if self._hover is not None:
            self._hover = None
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._rgba is None:
            self.openRequested.emit()
        elif self._picking:
            fraction = self._fraction_at(event.position())
            if fraction is not None:
                self.pixelClicked.emit(*fraction)

    # ----------------------------------------------------------------------------------
    # Painting
    # ----------------------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(frame, 10, 10)
        painter.save()
        painter.setClipPath(clip)
        rect = self.image_rect()
        if rect is not None and self._preview is not None:
            painter.fillRect(self.rect(), QColor(theme.STAGE))
            painter.fillRect(rect, self._checker)  # shows through transparent pixels
            self._draw_image(painter, rect)
        else:
            painter.fillRect(self.rect(), self._checker)
            self._draw_empty(painter)
        painter.restore()
        if self._drop_active:
            painter.setPen(QPen(QColor(theme.ACCENT), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(90, 60, 176, 28))
            painter.drawRoundedRect(frame.adjusted(1, 1, -1, -1), 10, 10)
        if rect is not None:
            for marker, scale in ((self._inspected, 1.0), (self._highlighted, 1.5)):
                if marker is not None:
                    self._draw_marker(painter, rect, marker, scale)
            if self._picking and self._hover is not None:
                self._draw_loupe(painter, rect)

    def _draw_image(self, painter: QPainter, rect: QRectF) -> None:
        ratio = self.devicePixelRatioF()
        target = QSize(max(1, round(rect.width() * ratio)), max(1, round(rect.height() * ratio)))
        key = (target.width(), target.height(), self._preview.cacheKey())
        if self._scaled is None or self._scaled_key != key:
            enlarging = target.width() >= self._preview.width() * 2
            mode = Qt.TransformationMode.FastTransformation if enlarging else Qt.TransformationMode.SmoothTransformation
            self._scaled = QPixmap.fromImage(self._preview.scaled(target, Qt.AspectRatioMode.IgnoreAspectRatio, mode))
            self._scaled.setDevicePixelRatio(ratio)
            self._scaled_key = key
        painter.drawPixmap(rect.topLeft(), self._scaled)

    def _draw_empty(self, painter: QPainter) -> None:
        area = QRectF(self.rect())
        painter.fillRect(area, QColor(240, 235, 250, 215))
        painter.setPen(QColor(theme.INK))
        title_font = theme.heading_font(19)
        painter.setFont(title_font)
        title_height = QFontMetrics(title_font).height()
        center = area.center().y()
        painter.drawText(
            QRectF(area.x() + 16, center - title_height - 4, area.width() - 32, title_height + 4),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
            "Find the colors in any image.",
        )
        painter.setFont(theme.small_font(9.5))
        painter.setPen(QColor(theme.MUTED))
        painter.drawText(
            QRectF(area.x() + 16, center + 6, area.width() - 32, 60),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            "Drop an image here, or click to choose one.\nPNG, JPG, WebP, GIF, BMP, AVIF or TIFF · up to 24 megapixels",
        )

    def _draw_marker(self, painter: QPainter, rect: QRectF, marker: Marker, scale: float) -> None:
        center = QPointF(rect.x() + marker.point.x * rect.width(), rect.y() + marker.point.y * rect.height())
        radius = 13 * scale
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(33, 29, 38, 70))
        painter.drawEllipse(center + QPointF(0, 2), radius + 4, radius + 4)  # soft shadow
        painter.setBrush(QColor(theme.INK))
        painter.drawEllipse(center, radius + 4.5, radius + 4.5)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(center, radius + 2.5, radius + 2.5)
        painter.setBrush(QColor(marker.color))
        painter.drawEllipse(center, radius - 0.5, radius - 0.5)
        arm = 4.5 * scale
        for color, width in ((QColor(0, 0, 0, 150), 3.6), (QColor("white"), 1.8)):
            painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(center + QPointF(-arm, 0), center + QPointF(arm, 0))
            painter.drawLine(center + QPointF(0, -arm), center + QPointF(0, arm))

    def _draw_loupe(self, painter: QPainter, rect: QRectF) -> None:
        """A magnified view of the pixels under the pointer, with the HEX it would pick."""
        fraction = self._fraction_at(self._hover)
        if fraction is None:
            return
        height, width = self._rgba.shape[:2]
        column = min(width - 1, int(fraction[0] * width))
        row = min(height - 1, int(fraction[1] * height))
        half = _LOUPE_PIXELS // 2
        patch = np.zeros((_LOUPE_PIXELS, _LOUPE_PIXELS, 4), dtype=np.uint8)
        top, left = max(0, row - half), max(0, column - half)
        bottom, right = min(height, row + half + 1), min(width, column + half + 1)
        patch[top - (row - half) : bottom - (row - half), left - (column - half) : right - (column - half)] = self._rgba[
            top:bottom, left:right
        ]
        size = _LOUPE_PIXELS * _LOUPE_ZOOM
        label_height = 26
        x = self._hover.x() + 22
        y = self._hover.y() - size - label_height - 22
        if x + size > self.width() - 4:
            x = self._hover.x() - size - 22
        if y < 4:
            y = self._hover.y() + 22
        box = QRectF(x, y, size, size + label_height)

        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(33, 29, 38, 60))
        painter.drawRoundedRect(box.adjusted(-3, -1, 3, 5), 12, 12)  # shadow
        shape = QPainterPath()
        shape.addRoundedRect(box, 10, 10)
        painter.setClipPath(shape)
        zoomed = QRectF(x, y, size, size)
        painter.fillRect(zoomed, self._checker)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        image = QImage(patch.data, _LOUPE_PIXELS, _LOUPE_PIXELS, 4 * _LOUPE_PIXELS, QImage.Format.Format_RGBA8888)
        painter.drawImage(zoomed, image)
        red, green, blue, alpha = (int(value) for value in self._rgba[row, column])
        color = f"#{red:02X}{green:02X}{blue:02X}"
        label = QRectF(x, y + size, size, label_height)
        painter.fillRect(label, QColor(color) if alpha else QColor(theme.WHITE))
        painter.setFont(theme.mono_font(9.5))
        painter.setPen(QColor(readable_text_color(color)) if alpha else QColor(theme.MUTED))
        painter.drawText(label, Qt.AlignmentFlag.AlignCenter, color if alpha else "transparent")
        painter.setClipping(False)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        cell = QRectF(x + half * _LOUPE_ZOOM, y + half * _LOUPE_ZOOM, _LOUPE_ZOOM, _LOUPE_ZOOM)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("black"), 1))
        painter.drawRect(cell.adjusted(-1, -1, 0, 0))
        painter.setPen(QPen(QColor("white"), 1))
        painter.drawRect(cell)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("white"), 2))
        painter.drawRoundedRect(box, 10, 10)
        painter.setPen(QPen(QColor(theme.INK), 1))
        painter.drawRoundedRect(box.adjusted(-1.5, -1.5, 1.5, 1.5), 11, 11)
        painter.restore()
