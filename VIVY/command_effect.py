"""创作领域顶栏：单行乱码逐字落为文案；老式传呼机 / 黑底点阵终端风（深蓝像素字，PyQt6）。"""

from __future__ import annotations

import random
import string
from typing import Optional

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class _ScanlineOverlay(QWidget):
    """极淡横线缓慢滚动，略带老式液晶条纹感（不重 CRT）。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._phase = 0
        self._spacing = 4

    def advance(self) -> None:
        self._phase = (self._phase + 1) % max(1, self._spacing)
        self.update()

    def paintEvent(self, event):
        del event
        w, h = max(self.width(), 1), max(self.height(), 1)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(QColor(200, 210, 230, 14))
        p.setPen(pen)
        sp = self._spacing
        off = self._phase % sp
        y = off
        while y <= h + sp:
            p.drawLine(0, int(y), w, int(y))
            y += sp


class CommandEffect(QWidget):
    _ASCII_NOISE = string.ascii_uppercase + string.digits + "!@#$%&*?_-\\/[]{}"
    _CJK_NOISE = (
        "創作編章識空無電脳術式領叡乱異構同期執行維稳断層図録片屬"
        "的一是在不了有和人这中大为上个国我以要地出就分对成会可主发年动"
        "同业工能干过子说产种面而方后多定行学法所民得经十三之进着等部"
    )
    # 深蓝点阵字 / 暗终端描边
    _TEXT_BLUE = "#4d8ccb"
    _BORDER_BLUE = "#2e4d73"
    _BG_TERMINAL = "rgba(6, 7, 10, 248)"

    @staticmethod
    def _make_pixel_font() -> QFont:
        """尽量点阵：优先 Fixedsys/-terminal，否则新宋体 12px 等宽块面感。"""
        chosen = QFont("SimSun")
        for name in ("Fixedsys", "Terminal", "NSimSun", "SimSun", "Courier New"):
            f = QFont(name)
            if f.exactMatch():
                chosen = f
                break
        chosen.setStyleHint(QFont.StyleHint.TypeWriter)
        chosen.setPixelSize(12)
        chosen.setStyleStrategy(
            QFont.StyleStrategy.NoAntialias
            | QFont.StyleStrategy.PreferBitmap
            | QFont.StyleStrategy.PreferDefault
        )
        chosen.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        chosen.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.5)
        return chosen

    def __init__(self, parent: Optional[QWidget] = None, target_text: str = "创作模式"):
        super().__init__(parent)
        self._target = target_text or "创作模式"
        self._locked = 0
        self._tick_count = 0
        self._ticks_per_lock = 3

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)

        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(72)
        self._scan_timer.timeout.connect(self._on_scan_tick)

        self._scan_overlay = _ScanlineOverlay(self)

        self._label = QLabel(self)
        self._label.setObjectName("commandEffectLabel")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(False)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.addWidget(self._label)

        self._label.setFont(self._make_pixel_font())

        glow = QGraphicsDropShadowEffect(self)
        glow.setBlurRadius(10)
        glow.setColor(QColor(35, 75, 130, 38))
        glow.setOffset(0, 0)
        self.setGraphicsEffect(glow)

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._apply_style()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scan_overlay.setGeometry(self.rect())
        self._scan_overlay.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        self._scan_overlay.setGeometry(self.rect())
        self._scan_overlay.raise_()

    def set_target_text(self, text: str) -> None:
        self._target = text or "创作模式"

    def _apply_style(self) -> None:
        t = self._TEXT_BLUE
        b = self._BORDER_BLUE
        bg = self._BG_TERMINAL
        self.setStyleSheet(
            f"""
            CommandEffect {{
                background-color: {bg};
                border: 1px solid {b};
                border-radius: 2px;
            }}
            QLabel#commandEffectLabel {{
                color: {t};
                background: transparent;
                padding: 1px 3px;
                text-shadow: 0 0 1px rgba(100, 160, 220, 0.35);
            }}
            """
        )

    def _on_scan_tick(self) -> None:
        self._scan_overlay.advance()

    def _noise_char_for(self, ref: str) -> str:
        if not ref:
            return random.choice(self._CJK_NOISE)
        if ord(ref[0]) < 128:
            return random.choice(self._ASCII_NOISE)
        return random.choice(self._CJK_NOISE)

    def _build_line(self) -> str:
        t = self._target
        n = len(t)
        if n == 0:
            return ""
        parts: list[str] = []
        for i, ch in enumerate(t):
            if i < self._locked:
                parts.append(ch)
            else:
                parts.append(self._noise_char_for(ch))
        return "".join(parts)

    def _on_tick(self) -> None:
        n = len(self._target)
        if n == 0:
            self._label.setText("")
            return

        if self._locked < n:
            self._tick_count += 1
            if self._tick_count >= self._ticks_per_lock:
                self._tick_count = 0
                self._locked += 1
            self._label.setText(self._build_line())
            self._timer.setInterval(random.randint(55, 95))
            return

        self._label.setText(self._target)
        self._timer.stop()

    def set_effect_size(self, w: int, h: int) -> None:
        self.setFixedSize(w, h)

    def set_offset_from_master(self, master: QWidget, dx: int, dy: int) -> None:
        top_left = master.mapToGlobal(QPoint(0, 0))
        self.move(top_left.x() + dx, top_left.y() + dy)

    def follow_master(self, master: QWidget, dx: int, dy: int, w: int, h: int) -> None:
        self.set_effect_size(w, h)
        self.set_offset_from_master(master, dx, dy)

    def start_effect(self) -> None:
        self._locked = 0
        self._tick_count = 0
        self._label.setText(self._build_line())
        self._timer.setInterval(random.randint(60, 90))
        self._timer.start()
        self._scan_timer.start()
        self.show()
        self.raise_()
        self._scan_overlay.setGeometry(self.rect())
        self._scan_overlay.raise_()

    def stop_effect(self) -> None:
        self._timer.stop()
        self._scan_timer.stop()
        self.hide()

    def is_effect_running(self) -> bool:
        """定时器是否在跑（逐字动画进行中）。落字结束后为 False，窗口仍可 isVisible。"""
        return self._timer.isActive()
