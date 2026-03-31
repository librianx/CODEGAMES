"""办公悬浮提示 UI（终端风，多行建议）。"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


class OfficeAssistOverlay(QWidget):
    """顶层悬浮条：显示标题 + 1-3 条 bullet。"""

    _TEXT_BLUE = "#4d8ccb"
    _BEZEL_PX = 5
    _BEZEL_HI = (236, 239, 244)
    _BEZEL_LO = (132, 138, 150)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._title = ""
        self._bullets: list[str] = []

        self._label = QLabel(self)
        self._label.setObjectName("officeAssistLabel")
        self._label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.addWidget(self._label)

        mono = QFont("NSimSun")
        if not mono.exactMatch():
            mono = QFont("SimSun")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        mono.setPixelSize(12)
        mono.setStyleStrategy(QFont.StyleStrategy.NoAntialias | QFont.StyleStrategy.PreferBitmap)
        self._label.setFont(mono)

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self.setStyleSheet(
            f"""
            QLabel#officeAssistLabel {{
                color: {self._TEXT_BLUE};
                background: transparent;
                padding: 1px 3px;
            }}
            """
        )

    def set_content(self, title: str, bullets: list[str]) -> None:
        self._title = (title or "").strip()
        self._bullets = [b.strip() for b in (bullets or []) if b and b.strip()]
        parts: list[str] = []
        if self._title:
            parts.append(self._title)
        for b in self._bullets[:3]:
            parts.append(f"- {b}")
        self._label.setText("\n".join(parts) if parts else "（未读到可用文本）")

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        W, H = self.width(), self.height()
        fw = self._BEZEL_PX
        if W < fw * 2 + 4 or H < fw * 2 + 4:
            fw = max(2, min(W, H) // 5)
        hi = QColor(*self._BEZEL_HI, 255)
        lo = QColor(*self._BEZEL_LO, 255)
        p.fillRect(0, 0, W, fw, hi)
        p.fillRect(0, 0, fw, H, hi)
        p.fillRect(0, H - fw, W, fw, lo)
        p.fillRect(W - fw, 0, fw, H, lo)
        inner_bg = QColor(6, 7, 10, 248)
        p.fillRect(fw, fw, W - 2 * fw, H - 2 * fw, inner_bg)
        p.setPen(QPen(QColor(90, 98, 110, 120), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def follow_master(self, master: QWidget, dx: int, dy: int, w: int, h: int) -> None:
        self.setFixedSize(w, h)
        top_left = master.mapToGlobal(QPoint(0, 0))
        self.move(top_left.x() + dx, top_left.y() + dy)

