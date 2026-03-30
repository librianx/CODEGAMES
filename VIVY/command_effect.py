"""创作领域顶栏：单行乱码逐字落为文案；老式传呼机 / 黑底点阵终端风（深蓝像素字，PyQt6）。"""

from __future__ import annotations

import random
import string
from typing import Optional

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
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
    # 深蓝点阵字；外框为灰白色塑料壳（实际绘制在 paintEvent）
    _TEXT_BLUE = "#4d8ccb"
    _BEZEL_PX = 5
    _BEZEL_HI = (236, 239, 244)  # 高光（上/左）
    _BEZEL_LO = (132, 138, 150)  # 阴影（下/右）

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
        self._label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.addWidget(self._label)

        self._label.setFont(self._make_pixel_font())

        glow = QGraphicsDropShadowEffect(self)
        glow.setBlurRadius(14)
        glow.setColor(QColor(210, 215, 225, 70))
        glow.setOffset(0, 0)
        self.setGraphicsEffect(glow)

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 顶层透明窗下，Qt 可能不绘制 QWidget 样式表背景/边框；强制启用样式背景更稳
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._apply_style()

    def paintEvent(self, event):
        # 顶层透明窗 + 样式表可能不生效：自绘厚塑料边框 + 内屏黑底
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        W, H = self.width(), self.height()
        fw = self._BEZEL_PX
        if W < fw * 2 + 4 or H < fw * 2 + 4:
            fw = max(2, min(W, H) // 5)
        hi = QColor(*self._BEZEL_HI, 255)
        lo = QColor(*self._BEZEL_LO, 255)
        # 上亮下暗的塑料壳
        p.fillRect(0, 0, W, fw, hi)
        p.fillRect(0, 0, fw, H, hi)
        p.fillRect(0, H - fw, W, fw, lo)
        p.fillRect(W - fw, 0, fw, H, lo)
        inner_bg = QColor(6, 7, 10, 248)
        p.fillRect(fw, fw, W - 2 * fw, H - 2 * fw, inner_bg)

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
        self.setStyleSheet(
            f"""
            CommandEffect {{
                background-color: transparent;
                border: none;
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

    def fit_to_text(
        self,
        max_text_width: int = 400,
        max_outer_height: int = 140,
        min_outer_width: int = 168,
    ) -> tuple[int, int]:
        """按目标文案折行估算外壳尺寸（用于长句不全截断）。"""
        lay = self.layout()
        ml, mt, mr, mb = 10, 7, 10, 7
        if lay is not None:
            m = lay.contentsMargins()
            ml, mt, mr, mb = m.left(), m.top(), m.right(), m.bottom()
        fw = self._BEZEL_PX
        cap = max(80, int(max_text_width))
        inner_w = max(48, cap - ml - mr - 2 * fw)
        txt = (self._target or "").strip() or " "
        fm = QFontMetrics(self._label.font())
        flags = Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap
        br = fm.boundingRect(QRect(0, 0, inner_w, 50_000), int(flags), txt)
        outer_w = max(min_outer_width, min(cap, inner_w + ml + mr + 2 * fw + 2))
        line_h = max(fm.height(), 12)
        need_h = br.height() + mt + mb + 2 * fw + 6
        outer_h = max(line_h + mt + mb + 2 * fw, min(int(max_outer_height), need_h))
        self.set_effect_size(outer_w, outer_h)
        return outer_w, outer_h

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
