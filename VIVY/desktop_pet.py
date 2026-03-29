import os
import sys
import uuid
import threading
import time
import math
import random
from pathlib import Path
import inspect

import requests
from dotenv import load_dotenv
from PyQt6.QtCore import (
    Qt,
    QPoint,
    QPointF,
    QRectF,
    QUrl,
    QTimer,
    QObject,
    pyqtSignal,
    QRunnable,
    QThreadPool,
    QSize,
    QPropertyAnimation,
    QEasingCurve,
    pyqtProperty,
    QEvent,
)
from PyQt6.QtGui import QAction, QMouseEvent, QMovie, QImageReader
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtGui import (
    QPainter,
    QPen,
    QBrush,
    QColor,
    QRadialGradient,
    QPainterPath,
)
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QFrame,
    QMenu,
    QLineEdit,
    QTextEdit,
    QInputDialog,
    QMessageBox,
    QDialog,
    QListWidget,
    QListWidgetItem,
    QDialogButtonBox,
    QFileDialog,
    QGraphicsOpacityEffect,
)

# Import existing Flask backend
from app import app as flask_app
from db import init_db
from creative_assist import load_document_text
from immersive_writing import ImmersiveWritingWindow

CREATIVE_DOC_SUFFIXES = frozenset({".txt", ".md", ".docx", ".pdf"})

# 快捷按钮「脑洞短剧」发送的固定指令
IMPROV_SKETCH_MESSAGE = (
    "即兴写一个脑洞小短剧：共 3～8 行，格式为「场景一句」与「角色名：台词」交替；"
    "要有一个无厘头误会，结尾用一句反转或金句收束；不要写作课讲解，不要正式标题以外的套话。"
)

PROJECT_DIR = Path(__file__).resolve().parent
USER_ID_FILE = PROJECT_DIR / ".desktop_user_id"
GIF_PATH = PROJECT_DIR / "static" / "images" / "VIVYfirst.gif"
PNG_FALLBACK_PATH = PROJECT_DIR / "static" / "images" / "VIVYstatr.png"
ENV_FILE = PROJECT_DIR / ".env"
DOMAIN_SOUND_PATH = PROJECT_DIR / "static" / "sounds" / "domain_expand.wav"


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    p = 1.0 - t
    return 1.0 - p * p * p


class WorkerSignals(QObject):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    progress = pyqtSignal(object)


class RequestWorker(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self):
        try:
            # Only pass progress_callback if the target function supports it.
            kwargs = dict(self.kwargs)
            try:
                sig = inspect.signature(self.fn)
                if "progress_callback" in sig.parameters and "progress_callback" not in kwargs:
                    kwargs["progress_callback"] = self.signals.progress.emit
            except Exception:
                # If signature introspection fails, call without injecting.
                pass

            result = self.fn(*self.args, **kwargs)
            self.signals.finished.emit(result)
        except Exception as e:
            self.signals.error.emit(str(e))


class ChatDropFrame(QFrame):
    """左侧对话区容器：创作形态下可拖入文档触发与菜单相同的读取流程。"""

    def __init__(self, pet: "DesktopPet"):
        super().__init__()
        self.setObjectName("vivyChatColumn")
        self.pet = pet
        self._creative_drop_enabled = False
        self.setAcceptDrops(True)

    def set_creative_drop_enabled(self, enabled: bool):
        self._creative_drop_enabled = bool(enabled)

    def _local_doc_paths_from_mime(
        self, event: QDragEnterEvent | QDragMoveEvent | QDropEvent
    ) -> list:
        md = event.mimeData()
        if not md.hasUrls():
            return []
        out: list = []
        for url in md.urls():
            if url.isLocalFile():
                p = url.toLocalFile()
                if Path(p).suffix.lower() in CREATIVE_DOC_SUFFIXES:
                    out.append(p)
        return out

    def dragEnterEvent(self, event: QDragEnterEvent):
        if not self._creative_drop_enabled:
            event.ignore()
            return
        if self._local_doc_paths_from_mime(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent):
        self.dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent):
        if not self._creative_drop_enabled:
            event.ignore()
            return
        paths = self._local_doc_paths_from_mime(event)
        if not paths:
            if event.mimeData().hasUrls():
                self.pet._set_status_text("仅支持拖入 .txt、.md、.docx、.pdf")
            event.ignore()
            return
        event.acceptProposedAction()
        self.pet._start_creative_doc_stream(paths[0], "")


class CreativeDomainEffects(QWidget):
    """创作领域：扩散波、光晕、粒子、几何装饰；序时与 intro_finished 信号衔接领域按钮。"""

    intro_finished = pyqtSignal()

    _WAVE_STARTS_MS = (0, 88, 176, 264)
    _WAVE_DURATION_MS = 340
    _INTRO_END_MS = 620
    _INTRO_TICK_MS = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._openness = 0.0
        self._pulse_phase = 0.0
        self._rotation = 0.0
        self._active = False
        self._anim_open: QPropertyAnimation | None = None
        self._intro_ms = 0
        self._intro_done = True
        self._did_animate_intro = False
        self._particles: list = []
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._tick_idle)
        self._idle_timer.setInterval(48)
        self._intro_timer = QTimer(self)
        self._intro_timer.timeout.connect(self._tick_intro)
        self._intro_timer.setInterval(self._INTRO_TICK_MS)

    def get_openness(self) -> float:
        return self._openness

    def set_openness(self, v: float) -> None:
        self._openness = max(0.0, min(1.0, float(v)))
        self.update()

    openness = pyqtProperty(float, get_openness, set_openness)

    def _try_play_domain_sound(self) -> None:
        if not DOMAIN_SOUND_PATH.is_file():
            return
        try:
            from PyQt6.QtMultimedia import QSoundEffect

            eff = getattr(self, "_snd_domain", None)
            if eff is None:
                self._snd_domain = QSoundEffect(self)
                self._snd_domain.setSource(QUrl.fromLocalFile(str(DOMAIN_SOUND_PATH.resolve())))
                self._snd_domain.setVolume(0.38)
                eff = self._snd_domain
            eff.play()
        except Exception:
            pass

    def _reset_particles(self) -> None:
        w, h = max(self.width(), 64), max(self.height(), 64)
        rm = min(w, h) * 0.5
        self._particles = []
        for _ in range(34):
            self._particles.append(
                {
                    "a": random.uniform(0, 2 * math.pi),
                    "r": random.uniform(0.1, 0.36) * rm,
                    "vr": random.uniform(0.32, 1.05),
                    "w": random.uniform(-0.028, 0.028),
                    "delay": random.randint(0, 320),
                    "sz": random.uniform(1.0, 3.6),
                }
            )

    def _advance_particles(self, dt: float = 1.0) -> None:
        w, h = max(self.width(), 64), max(self.height(), 64)
        r_cap = min(w, h) * 0.58
        for pt in self._particles:
            if not self._intro_done and self._intro_ms < pt["delay"]:
                continue
            pt["a"] += pt["w"] * dt
            pt["r"] += pt["vr"] * 0.62 * dt
            if pt["r"] > r_cap:
                pt["r"] = random.uniform(0.08, 0.24) * min(w, h) * 0.45
                pt["a"] = random.uniform(0, 2 * math.pi)

    def _tick_intro(self) -> None:
        if not self._active:
            return
        self._intro_ms += self._INTRO_TICK_MS
        self._pulse_phase += 0.055
        self._advance_particles(0.35)
        self.update()
        if self._intro_ms >= self._INTRO_END_MS and not self._intro_done:
            self._intro_done = True
            self._intro_timer.stop()
            self._idle_timer.start()
            self.intro_finished.emit()

    def _tick_idle(self) -> None:
        if not self._active or self._openness < 0.05:
            return
        self._rotation = (self._rotation + 1.12) % 360.0
        self._pulse_phase += 0.088
        self._advance_particles(1.0)
        self.update()

    def set_creative_active(self, active: bool, animate: bool = True) -> None:
        self._active = active
        if self._anim_open is not None:
            self._anim_open.stop()
            self._anim_open = None
        self._intro_timer.stop()
        self._idle_timer.stop()

        if active:
            self._did_animate_intro = animate
            if animate:
                self._try_play_domain_sound()
                self._reset_particles()
                self._intro_ms = 0
                self._intro_done = False
                self._pulse_phase = 0.0
                self._rotation = 0.0
                self._anim_open = QPropertyAnimation(self, b"openness", self)
                self._anim_open.setDuration(300)
                self._anim_open.setStartValue(0.0)
                self._anim_open.setEndValue(1.0)
                self._anim_open.setEasingCurve(QEasingCurve.Type.OutCubic)
                self._anim_open.start()
                self._intro_timer.start()
            else:
                self._intro_done = True
                self._reset_particles()
                self.set_openness(1.0)
                self._idle_timer.start()
                QTimer.singleShot(0, self.intro_finished.emit)
        else:
            self._did_animate_intro = False
            if animate:
                self._anim_open = QPropertyAnimation(self, b"openness", self)
                self._anim_open.setDuration(340)
                self._anim_open.setStartValue(self._openness)
                self._anim_open.setEndValue(0.0)
                self._anim_open.setEasingCurve(QEasingCurve.Type.InCubic)
                self._anim_open.start()
            else:
                self.set_openness(0.0)

    def paintEvent(self, event):
        o = self._openness
        if o < 0.02:
            return
        w, h = self.width(), self.height()
        if w < 12 or h < 12:
            return
        cx, cy = w * 0.5, h * 0.5
        r_base = min(w, h) * 0.36
        r_max = min(w, h) * 0.5
        pulse = 0.88 + 0.12 * math.sin(self._pulse_phase)

        if self._did_animate_intro and not self._intro_done:
            settle = _ease_out_cubic(min(1.0, self._intro_ms / 440.0))
        else:
            settle = 1.0

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # —— 扩散波（序贯圆环放大并淡出）——
        if self._did_animate_intro and not self._intro_done:
            for start in self._WAVE_STARTS_MS:
                t = self._intro_ms - start
                if t < 0 or t > self._WAVE_DURATION_MS:
                    continue
                u = t / self._WAVE_DURATION_MS
                rr = (0.18 + 0.92 * _ease_out_cubic(u)) * r_max
                alpha = int(220 * ((1.0 - u) ** 1.35) * o)
                wave_pen = QPen(QColor(120, 240, 255, alpha))
                wave_pen.setWidthF(2.0)
                p.setPen(wave_pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr))

        # —— 背景领域光晕（随 settle 扩大并定格）——
        glow_r = r_base * (0.72 + 0.62 * settle) * (1.02 + 0.04 * pulse)
        rad = QRadialGradient(cx, cy, glow_r)
        rad.setColorAt(0.0, QColor(25, 100, 190, 0))
        rad.setColorAt(0.42, QColor(50, 200, 255, int(48 * o * settle * pulse)))
        rad.setColorAt(0.72, QColor(130, 100, 255, int(38 * o * settle)))
        rad.setColorAt(1.0, QColor(8, 30, 60, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(rad))
        p.drawEllipse(QRectF(cx - glow_r, cy - glow_r, glow_r * 2, glow_r * 2))

        vign = QRadialGradient(cx, cy, r_base * (1.05 + 0.22 * settle))
        vign.setColorAt(0.0, QColor(0, 0, 0, 0))
        vign.setColorAt(0.68, QColor(0, 12, 28, int(42 * o * settle)))
        vign.setColorAt(1.0, QColor(0, 0, 0, int(72 * o)))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setBrush(QBrush(vign))
        p.drawEllipse(QRectF(cx - glow_r, cy - glow_r, glow_r * 2, glow_r * 2))

        # —— 核心圈（承载 UI 的圆形边界提示）——
        core_r = min(w, h) * 0.47
        core_pen = QPen(QColor(140, 230, 255, int(110 * o * max(0.35, settle))))
        core_pen.setWidthF(1.4)
        p.setPen(core_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(cx - core_r, cy - core_r, 2 * core_r, 2 * core_r))

        # —— 装饰线：短划沿周向漂移 ——
        p.save()
        p.translate(cx, cy)
        p.rotate(self._rotation * 0.85)
        streak_pen = QPen(QColor(180, 120, 255, int(55 * o)))
        streak_pen.setWidthF(1.0)
        p.setPen(streak_pen)
        n_streak = 14
        for i in range(n_streak):
            ang = (2 * math.pi * i) / n_streak + self._pulse_phase * 0.08
            r0 = r_base * 0.78
            r1 = r_base * 0.98
            c, s = math.cos(ang), math.sin(ang)
            p.drawLine(QPointF(r0 * c, r0 * s), QPointF(r1 * c, r1 * s))
        p.restore()

        if self._intro_done or not self._did_animate_intro:
            # —— 旋转虚线环 ——
            p.save()
            p.translate(cx, cy)
            p.rotate(self._rotation)
            p.translate(-cx, -cy)
            dash_pen = QPen(QColor(130, 235, 255, int(195 * o)))
            dash_pen.setWidthF(1.1)
            dash_pen.setStyle(Qt.PenStyle.DashLine)
            dash_pen.setDashPattern([6.0, 5.0, 2.0, 5.0])
            p.setPen(dash_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for scale in (0.56, 0.72, 0.88):
                rr = r_base * scale * (0.97 + 0.03 * pulse)
                p.drawEllipse(QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr))
            p.restore()

            # —— 六边形 ——
            p.save()
            p.translate(cx, cy)
            p.rotate(-self._rotation * 0.6)
            hex_path = QPainterPath()
            n = 6
            rr = r_base * 0.9 * (1.0 + 0.02 * math.sin(self._pulse_phase * 1.25))
            for i in range(n + 1):
                ang = (2 * math.pi * i) / n - math.pi / 2
                pt = QPointF(rr * math.cos(ang), rr * math.sin(ang))
                if i == 0:
                    hex_path.moveTo(pt)
                else:
                    hex_path.lineTo(pt)
            hex_path.closeSubpath()
            accent = QPen(QColor(200, 140, 255, int(145 * o)))
            accent.setWidthF(1.3)
            p.setPen(accent)
            p.drawPath(hex_path)
            p.restore()

            # —— 放射线 ——
            p.save()
            p.translate(cx, cy)
            p.rotate(self._rotation * 0.46)
            spoke_pen = QPen(QColor(90, 220, 255, int(62 * o)))
            spoke_pen.setWidthF(0.72)
            p.setPen(spoke_pen)
            n_spokes = 18
            r0, r1 = r_base * 0.3, r_base * 1.02
            for i in range(n_spokes):
                ang = (2 * math.pi * i) / n_spokes
                c, s = math.cos(ang), math.sin(ang)
                p.drawLine(QPointF(r0 * c, r0 * s), QPointF(r1 * c, r1 * s))
            p.restore()

        # —— 粒子光点 ——
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        for pt in self._particles:
            if not self._intro_done and self._intro_ms < pt["delay"]:
                continue
            x = cx + pt["r"] * math.cos(pt["a"])
            y = cy + pt["r"] * math.sin(pt["a"])
            tw = 0.5 + 0.5 * math.sin(self._pulse_phase + pt["r"] * 0.09)
            alpha = int(200 * o * tw * (0.55 + 0.45 * settle))
            sz = pt["sz"]
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(200, 245, 255, alpha)))
            p.drawEllipse(QRectF(x - sz * 0.5, y - sz * 0.5, sz, sz))


class CreativeAvatarDock(QWidget):
    """中间列：领域绘层 + 头像 + 底部领域快捷按钮。"""

    def __init__(
        self,
        effects: CreativeDomainEffects,
        label: QLabel,
        actions_row: QWidget,
        parent=None,
    ):
        super().__init__(parent)
        self._effects = effects
        self._label = label
        self._actions = actions_row
        self._pad = 24
        effects.setParent(self)
        label.setParent(self)
        actions_row.setParent(self)
        effects.lower()
        label.raise_()
        actions_row.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._effects.setGeometry(0, 0, self.width(), self.height())
        action_h = 0
        if self._actions.isVisible():
            action_h = max(26, self._actions.sizeHint().height())
        sh = self._label.sizeHint()
        lw = max(self._label.minimumWidth(), sh.width())
        lh = max(self._label.minimumHeight(), sh.height())
        x = max(0, (self.width() - lw) // 2)
        usable = self.height() - action_h - (8 if action_h else 0)
        y = max(self._pad // 2, max(0, (usable - lh) // 2))
        self._label.setGeometry(x, y, lw, lh)
        if action_h:
            self._actions.setGeometry(2, self.height() - action_h - 3, self.width() - 4, action_h)
        else:
            self._actions.setGeometry(0, self.height(), 0, 0)

    def sizeHint(self):
        sh = self._label.sizeHint()
        extra = 0
        if self._actions.isVisible():
            extra = max(26, self._actions.sizeHint().height()) + 6
        return QSize(
            max(self._label.minimumWidth(), sh.width()) + self._pad * 2,
            max(self._label.minimumHeight(), sh.height()) + self._pad * 2 + extra,
        )


class DesktopPet(QWidget):
    def __init__(self, api_base: str):
        super().__init__()
        self.api_base = api_base.rstrip("/")
        self.user_id = self._load_or_create_user_id()

        self._dragging = False
        self._drag_offset = QPoint()
        self.latest_reply = ""
        self.thread_pool = QThreadPool.globalInstance()
        self._busy = False
        self.current_interest_signal = ""
        self.chat_mode = "chat"
        self._loaded_doc_path: str | None = None
        self._immersive_writing_window: ImmersiveWritingWindow | None = None

        # idle / wander
        self._idle_collapsed = False
        self._last_interaction_ts = time.time()
        self._idle_timeout_s = int(os.getenv("VIVY_IDLE_TIMEOUT", "18"))
        self._expanded_size = QSize(556, 280)
        self._expanded_size_with_memory = QSize(720, 296)
        self._collapsed_size = QSize(170, 170)

        # bubble priority: prevent system/status messages from overwriting replies
        self._bubble_lock_until_ts = 0.0

        self._build_ui()
        self._init_session()
        self._start_idle_watch()

    def _build_ui(self):
        self.setWindowTitle("VIVY 桌宠")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # chat/interaction area (left)
        self.controls_wrap = ChatDropFrame(self)
        self.controls_layout = QVBoxLayout(self.controls_wrap)
        self.controls_layout.setContentsMargins(0, 0, 0, 0)
        self.controls_layout.setSpacing(8)

        self.bubble = QFrame()
        self.bubble.setObjectName("bubble")
        self.bubble.setAcceptDrops(False)
        bubble_layout = QVBoxLayout(self.bubble)
        bubble_layout.setContentsMargins(12, 10, 12, 10)
        bubble_layout.setSpacing(8)

        self.bubble_text = QTextEdit()
        self.bubble_text.setReadOnly(True)
        self.bubble_text.setObjectName("bubbleText")
        self.bubble_text.setMinimumHeight(56)
        self.bubble_text.setMaximumHeight(92)
        self.bubble_text.setPlainText("VIVY 启动中…")
        self.bubble_text.setAcceptDrops(False)
        bubble_layout.addWidget(self.bubble_text)

        bubble_action_row = QHBoxLayout()
        bubble_action_row.setSpacing(6)
        self.btn_copy_reply = QPushButton("复制回复")
        self.btn_copy_reply.clicked.connect(self._copy_latest_reply)
        bubble_action_row.addWidget(self.btn_copy_reply)
        bubble_action_row.addStretch(1)
        bubble_layout.addLayout(bubble_action_row)

        self.controls_layout.addWidget(self.bubble)

        self.options_wrap = QFrame()
        self.options_layout = QVBoxLayout(self.options_wrap)
        self.options_layout.setContentsMargins(0, 0, 0, 0)
        self.options_layout.setSpacing(6)
        self.controls_layout.addWidget(self.options_wrap)
        self.options_wrap.hide()

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumHeight(150)
        self.image_label.setMaximumHeight(200)
        self.image_label.setMinimumWidth(120)
        self.image_label.setMaximumWidth(160)
        self._setup_avatar()

        self.creative_actions = QWidget()
        self.creative_actions.setObjectName("creativeDomainActions")
        ca_outer = QVBoxLayout(self.creative_actions)
        ca_outer.setContentsMargins(2, 0, 2, 0)
        ca_outer.setSpacing(3)
        ca_row1 = QHBoxLayout()
        ca_row1.setSpacing(4)
        ca_row2 = QHBoxLayout()
        ca_row2.setSpacing(4)

        self.btn_domain_doc = QPushButton("读文档")
        self.btn_domain_doc.setObjectName("vivyDomainBtn")
        self.btn_domain_doc.setToolTip("与右键菜单相同的文档创作辅助")
        self.btn_domain_doc.clicked.connect(self._load_document_for_creative)

        self.btn_domain_spark = QPushButton("创作灵感")
        self.btn_domain_spark.setObjectName("vivyDomainBtn")
        self.btn_domain_spark.clicked.connect(
            lambda: self._send_message("给我一个简短的创作灵感，带一点画面感。")
        )

        self.btn_domain_clear = QPushButton("清参考")
        self.btn_domain_clear.setObjectName("vivyDomainBtn")
        self.btn_domain_clear.setToolTip("清除已读取的文档参考状态")
        self.btn_domain_clear.clicked.connect(self._clear_loaded_document)

        self.btn_domain_immerse = QPushButton("沉浸写作")
        self.btn_domain_immerse.setObjectName("vivyDomainBtn")
        self.btn_domain_immerse.setToolTip("大屏专注写作（仅 VIVY 内）")
        self.btn_domain_immerse.clicked.connect(self._open_immersive_writing)

        self._creative_domain_buttons = [
            self.btn_domain_doc,
            self.btn_domain_spark,
            self.btn_domain_clear,
            self.btn_domain_immerse,
        ]
        for b in self._creative_domain_buttons:
            op = QGraphicsOpacityEffect(b)
            op.setOpacity(0.0)
            b.setGraphicsEffect(op)
        for b in (self.btn_domain_doc, self.btn_domain_spark, self.btn_domain_clear):
            ca_row1.addWidget(b)
        ca_row1.addStretch(1)
        ca_row2.addWidget(self.btn_domain_immerse)
        ca_row2.addStretch(1)
        ca_outer.addLayout(ca_row1)
        ca_outer.addLayout(ca_row2)
        self.creative_actions.hide()

        self.domain_aura = CreativeDomainEffects()
        self.domain_aura.intro_finished.connect(self._on_creative_domain_intro_finished)

        self.avatar_dock = CreativeAvatarDock(
            self.domain_aura, self.image_label, self.creative_actions
        )
        self.avatar_dock.setMinimumWidth(120 + 48)
        self.avatar_dock.setMaximumWidth(160 + 48)
        self._sync_avatar_dock_heights()

        # quick actions
        action_row = QHBoxLayout()
        action_row.setSpacing(6)

        self.btn_inspiration = QPushButton("今日灵感")
        self.btn_inspiration.clicked.connect(lambda: self._send_message("今天有什么灵感？"))
        action_row.addWidget(self.btn_inspiration)

        self.btn_improv_sketch = QPushButton("脑洞短剧")
        self.btn_improv_sketch.setToolTip("即兴生成一段无厘头小剧场（对白 + 场景）")
        self.btn_improv_sketch.clicked.connect(lambda: self._send_message(IMPROV_SKETCH_MESSAGE))
        action_row.addWidget(self.btn_improv_sketch)

        self.btn_question = QPushButton("换个问题")
        self.btn_question.clicked.connect(lambda: self._send_message("换个问题"))
        action_row.addWidget(self.btn_question)

        self.btn_mode = QPushButton("形态：普通")
        self.btn_mode.clicked.connect(self._toggle_chat_mode)
        action_row.addWidget(self.btn_mode)

        self.controls_layout.addLayout(action_row)

        # user input row
        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("和 VIVY 说点什么...")
        self.input_edit.returnPressed.connect(self._send_from_input)
        # typing/focus should count as interaction (prevent idle collapse while composing)
        self.input_edit.textEdited.connect(lambda _t: self._touch())
        self.input_edit.cursorPositionChanged.connect(lambda _a, _b: self._touch())
        self.input_edit.selectionChanged.connect(lambda: self._touch())
        self.input_edit.installEventFilter(self)
        self.input_edit.setAcceptDrops(False)
        input_row.addWidget(self.input_edit)

        self.btn_send = QPushButton("发送")
        self.btn_send.clicked.connect(self._send_from_input)
        input_row.addWidget(self.btn_send)

        self.controls_layout.addLayout(input_row)

        interest_row = QHBoxLayout()
        interest_row.setSpacing(6)
        self.interest_label = QLabel("兴趣：未选择")
        interest_row.addWidget(self.interest_label)

        self.btn_interest_yes = QPushButton("感兴趣")
        self.btn_interest_yes.clicked.connect(lambda: self._set_interest_signal("interested"))
        interest_row.addWidget(self.btn_interest_yes)

        self.btn_interest_no = QPushButton("不感兴趣")
        self.btn_interest_no.clicked.connect(lambda: self._set_interest_signal("not_interested"))
        interest_row.addWidget(self.btn_interest_no)

        self.btn_interest_clear = QPushButton("清除")
        self.btn_interest_clear.clicked.connect(lambda: self._set_interest_signal(""))
        interest_row.addWidget(self.btn_interest_clear)
        self.controls_layout.addLayout(interest_row)

        self.memory_wrap = QFrame()
        self.memory_wrap.setObjectName("memoryWrap")
        memory_layout = QVBoxLayout(self.memory_wrap)
        memory_layout.setContentsMargins(8, 8, 8, 8)
        memory_layout.setSpacing(8)

        self.memory_title = QLabel("记忆模块（可编辑）")
        memory_layout.addWidget(self.memory_title)

        self.memory_help = QLabel(
            "快速上手：①先刷新 ②再修改 ③最后保存"
        )
        self.memory_help.setWordWrap(True)
        self.memory_help.setObjectName("memoryHelp")
        memory_layout.addWidget(self.memory_help)

        guide_row = QHBoxLayout()
        self.btn_memory_guide = QPushButton("操作指南")
        self.btn_memory_guide.clicked.connect(self._show_memory_guide)
        guide_row.addWidget(self.btn_memory_guide)
        self.btn_memory_template = QPushButton("填入示例JSON")
        self.btn_memory_template.clicked.connect(self._apply_memory_json_template)
        guide_row.addWidget(self.btn_memory_template)
        memory_layout.addLayout(guide_row)

        self.label_summary = QLabel("短摘要 summary（1句，记录最近状态）")
        memory_layout.addWidget(self.label_summary)
        self.memory_summary = QTextEdit()
        self.memory_summary.setPlaceholderText("summary：短摘要（建议1句）")
        self.memory_summary.setMinimumHeight(40)
        self.memory_summary.setMaximumHeight(64)
        memory_layout.addWidget(self.memory_summary)

        self.label_summary_long = QLabel("长摘要 summary_long（长期偏好/目标/边界）")
        memory_layout.addWidget(self.label_summary_long)
        self.memory_summary_long = QTextEdit()
        self.memory_summary_long.setPlaceholderText("summary_long：长摘要（长期偏好/近期目标/边界）")
        self.memory_summary_long.setMinimumHeight(54)
        self.memory_summary_long.setMaximumHeight(82)
        memory_layout.addWidget(self.memory_summary_long)

        self.label_prefs = QLabel("偏好 preferences JSON（必须是合法 JSON）")
        memory_layout.addWidget(self.label_prefs)
        self.memory_prefs = QTextEdit()
        self.memory_prefs.setPlaceholderText("preferences JSON（请保持合法JSON格式）")
        self.memory_prefs.setMinimumHeight(54)
        self.memory_prefs.setMaximumHeight(90)
        memory_layout.addWidget(self.memory_prefs)

        self.label_turns = QLabel("最近对话回合（可按 ID 删除单条）")
        memory_layout.addWidget(self.label_turns)
        self.memory_turns = QTextEdit()
        self.memory_turns.setPlaceholderText("recent conversation turns")
        self.memory_turns.setReadOnly(True)
        self.memory_turns.setMinimumHeight(64)
        self.memory_turns.setMaximumHeight(100)
        memory_layout.addWidget(self.memory_turns)

        turn_del_row = QHBoxLayout()
        self.turn_id_input = QLineEdit()
        self.turn_id_input.setPlaceholderText("输入回合ID删除，如 123")
        turn_del_row.addWidget(self.turn_id_input)
        self.btn_turn_delete = QPushButton("删除回合")
        self.btn_turn_delete.clicked.connect(self._delete_turn_by_id)
        turn_del_row.addWidget(self.btn_turn_delete)
        memory_layout.addLayout(turn_del_row)

        memory_btns = QHBoxLayout()
        self.btn_memory_refresh = QPushButton("刷新记忆")
        self.btn_memory_refresh.clicked.connect(lambda: self._refresh_memory(silent=False))
        memory_btns.addWidget(self.btn_memory_refresh)
        self.btn_memory_save = QPushButton("保存记忆")
        self.btn_memory_save.clicked.connect(self._save_memory)
        memory_btns.addWidget(self.btn_memory_save)
        memory_layout.addLayout(memory_btns)

        # memory area should be independent (right side), not inside chat area
        self.memory_wrap.hide()

        root.addWidget(self.controls_wrap, 1)
        root.addWidget(self.avatar_dock, 0, alignment=Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(self.memory_wrap, 1)

        self.setStyleSheet(
            """
            QWidget {
                color: #eaf8ff;
                font-size: 11px;
            }
            #bubble {
                background: rgba(10, 18, 26, 210);
                border: 1px solid rgba(78, 208, 255, 160);
                border-radius: 14px;
            }
            #bubbleText {
                background: rgba(0, 0, 0, 0);
                border: 0;
                color: #eaf8ff;
                line-height: 1.35;
                selection-background-color: rgba(55, 214, 255, 140);
            }
            QLineEdit {
                background: rgba(10, 18, 26, 200);
                border: 1px solid rgba(78, 208, 255, 130);
                border-radius: 8px;
                padding: 4px 6px;
                color: #f2fbff;
            }
            QPushButton {
                background: rgba(39, 160, 209, 190);
                border: 1px solid rgba(121, 228, 255, 180);
                border-radius: 8px;
                padding: 4px 6px;
                color: #f3fcff;
            }
            QPushButton:hover {
                background: rgba(57, 186, 241, 210);
            }
            #memoryWrap {
                background: rgba(6, 10, 14, 235);
                border: 1px solid rgba(78, 208, 255, 210);
                border-radius: 12px;
            }
            #memoryWrap QLabel {
                color: #eaf8ff;
                font-weight: 600;
                font-size: 12px;
                margin-top: 2px;
            }
            #memoryHelp {
                color: #cdeeff;
                font-weight: 500;
                font-size: 12px;
                background: rgba(0, 0, 0, 80);
                border: 1px solid rgba(130, 230, 255, 120);
                border-radius: 8px;
                padding: 6px 8px;
            }
            #memoryWrap QTextEdit {
                background: rgba(0, 0, 0, 140);
                border: 1px solid rgba(130, 230, 255, 200);
                border-radius: 10px;
                color: #f4fdff;
                padding: 7px 10px;
                font-size: 12px;
                selection-background-color: rgba(55, 214, 255, 160);
            }
            #memoryWrap QTextEdit:focus {
                border: 1px solid rgba(55, 214, 255, 240);
                background: rgba(0, 0, 0, 170);
            }
            #creativeDomainActions {
                background: transparent;
            }
            #vivyDomainBtn {
                font-size: 10px;
                padding: 2px 5px;
                min-height: 20px;
                max-height: 24px;
            }
            """
        )

        self._apply_window_size()
        self.move(100, 120)
        self._set_interest_signal("")
        for _w in (self.image_label, self.domain_aura, self.avatar_dock):
            _w.installEventFilter(self)

    def _setup_avatar(self):
        # Use GIF as initial avatar; keep aspect ratio and smooth scaling.
        if GIF_PATH.exists():
            self.movie = QMovie(str(GIF_PATH))
            self.movie.setCacheMode(QMovie.CacheMode.CacheAll)
            reader = QImageReader(str(GIF_PATH))
            src_size = reader.size()
            target_w = 240
            if src_size.isValid() and src_size.width() > 0:
                target_h = int(target_w * (src_size.height() / src_size.width()))
                self.movie.setScaledSize(src_size.scaled(target_w, target_h, Qt.AspectRatioMode.KeepAspectRatio))
            else:
                self.movie.setScaledSize(self.image_label.size())
            self.image_label.setMovie(self.movie)
            self.movie.start()
            return

        # Fallback to PNG if GIF is missing
        from PyQt6.QtGui import QPixmap

        pix = QPixmap(str(PNG_FALLBACK_PATH))
        self.image_label.setPixmap(pix.scaledToWidth(240, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "movie") and self.movie is not None:
            reader = QImageReader(str(GIF_PATH))
            src_size = reader.size()
            if src_size.isValid() and src_size.width() > 0:
                target_w = min(260, max(220, self.image_label.width()))
                target_h = int(target_w * (src_size.height() / src_size.width()))
                self.movie.setScaledSize(src_size.scaled(target_w, target_h, Qt.AspectRatioMode.KeepAspectRatio))

    def _load_or_create_user_id(self) -> str:
        if USER_ID_FILE.exists():
            value = USER_ID_FILE.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = str(uuid.uuid4())
        USER_ID_FILE.write_text(value, encoding="utf-8")
        return value

    def _set_bubble_text(self, text: str, kind: str = "reply"):
        clean_text = (text or "").strip()
        now = time.time()
        if kind in ("status", "thinking"):
            # Don't overwrite a fresh reply/stream output or other high-priority content.
            if now < self._bubble_lock_until_ts:
                return
            self.bubble_text.setPlainText(clean_text)
            return

        # reply/thinking always wins
        self.latest_reply = clean_text
        self.bubble_text.setPlainText(clean_text)
        # After a reply update, lock bubble briefly to avoid flicker/overwrites.
        self._bubble_lock_until_ts = now + 3.0

    def _set_status_text(self, text: str):
        self._set_bubble_text(text, kind="status")

    def _set_busy(self, busy: bool, thinking_text: str | None = "VIVY 思考中..."):
        self._busy = busy
        self.btn_inspiration.setDisabled(busy)
        self.btn_improv_sketch.setDisabled(busy)
        self.btn_question.setDisabled(busy)
        self.btn_send.setDisabled(busy)
        self.input_edit.setDisabled(busy)
        self.btn_interest_yes.setDisabled(busy)
        self.btn_interest_no.setDisabled(busy)
        self.btn_interest_clear.setDisabled(busy)
        for b in getattr(self, "_creative_domain_buttons", []):
            b.setDisabled(busy)
        iw = getattr(self, "_immersive_writing_window", None)
        if iw is not None:
            iw.set_assist_busy(busy)
        # Keep memory module interactive even when chatting is busy.
        if busy and thinking_text:
            self._set_bubble_text(thinking_text, kind="thinking")
        if not busy:
            self._touch()

    def _run_async(self, fn, on_success, on_error, on_progress=None, thinking_text: str | None = "VIVY 思考中..."):
        if self._busy:
            return
        self._touch()
        self._set_busy(True, thinking_text=thinking_text)
        worker = RequestWorker(fn)
        worker.signals.finished.connect(lambda data: self._on_worker_success(data, on_success))
        worker.signals.error.connect(lambda err: self._on_worker_error(err, on_error))
        if on_progress is not None:
            worker.signals.progress.connect(on_progress)
        self.thread_pool.start(worker)

    def _run_background(self, fn, on_success=None, on_error=None, on_progress=None):
        """Run without entering busy/disable UI. Used for silent refresh tasks."""
        worker = RequestWorker(fn)
        if on_success is not None:
            worker.signals.finished.connect(on_success)
        if on_error is not None:
            worker.signals.error.connect(on_error)
        if on_progress is not None:
            worker.signals.progress.connect(on_progress)
        self.thread_pool.start(worker)

    def _on_worker_success(self, data, callback):
        self._set_busy(False)
        callback(data)

    def _on_worker_error(self, error_msg, callback):
        self._set_busy(False)
        callback(error_msg)

    def _copy_latest_reply(self):
        text = self.bubble_text.textCursor().selectedText().strip() or self.latest_reply
        if text:
            QApplication.clipboard().setText(text)
            self.btn_copy_reply.setText("已复制")
            QTimer.singleShot(800, lambda: self.btn_copy_reply.setText("复制回复"))

    def _on_stream_chat_progress(self, payload):
        if isinstance(payload, dict):
            partial = str(payload.get("partial") or "")
        else:
            partial = str(payload or "")
        if partial:
            self._set_bubble_text(partial)

    def _clear_option_buttons(self):
        for i in reversed(range(self.options_layout.count())):
            item = self.options_layout.itemAt(i)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def _show_options(self, question_id: str, question: str, options):
        self._clear_option_buttons()
        self.options_wrap.show()
        self._set_bubble_text(question)

        # Use a custom dialog with stable readable layout.
        if options:
            dlg = QDialog(self)
            dlg.setWindowTitle("VIVY 换个问题")
            dlg.setModal(True)
            dlg.resize(420, 260)
            dlg.setMinimumSize(380, 240)
            dlg.setStyleSheet(
                """
                QDialog {
                    background: #0c141d;
                    color: #eaf8ff;
                }
                QLabel {
                    color: #dff6ff;
                    font-size: 14px;
                }
                QListWidget {
                    background: #0a121b;
                    border: 1px solid #46c9ee;
                    border-radius: 8px;
                    color: #f2fbff;
                    font-size: 13px;
                    padding: 4px;
                }
                QListWidget::item {
                    padding: 7px 9px;
                    min-height: 22px;
                }
                QListWidget::item:selected {
                    background: #2ea8d0;
                    color: #04131c;
                }
                QPushButton {
                    background: #2b9ec5;
                    border: 1px solid #7be2ff;
                    border-radius: 8px;
                    color: #f3fcff;
                    padding: 6px 12px;
                    font-size: 13px;
                }
                """
            )

            layout = QVBoxLayout(dlg)
            label = QLabel(question or "请选择一个回答：")
            label.setWordWrap(True)
            layout.addWidget(label)

            listw = QListWidget()
            for opt in options:
                item = QListWidgetItem(opt.get("label", "选项"))
                item.setData(Qt.ItemDataRole.UserRole, opt.get("choice_id", ""))
                listw.addItem(item)
            if listw.count() > 0:
                listw.setCurrentRow(0)
            layout.addWidget(listw)

            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            layout.addWidget(buttons)

            def _accept():
                it = listw.currentItem()
                if it is None:
                    return
                choice_id = it.data(Qt.ItemDataRole.UserRole) or ""
                if choice_id:
                    dlg.accept()
                    self._submit_preference(question_id, choice_id)

            buttons.accepted.connect(_accept)
            buttons.rejected.connect(dlg.reject)
            listw.itemDoubleClicked.connect(lambda _it: _accept())

            # Place dialog beside the pet window (avoid covering icon & panels)
            try:
                screen = QGuiApplication.primaryScreen()
                g = screen.availableGeometry() if screen is not None else None
                parent_geo = self.frameGeometry()
                margin = 10
                x_left = parent_geo.left() - dlg.width() - margin
                x_right = parent_geo.right() + margin
                y = parent_geo.top()
                if g is not None:
                    y = max(g.top() + margin, min(y, g.bottom() - dlg.height() - margin))
                    if x_left >= g.left() + margin:
                        dlg.move(x_left, y)
                    elif x_right + dlg.width() <= g.right() - margin:
                        dlg.move(x_right, y)
                    else:
                        # fallback: top-left inside screen
                        dlg.move(g.left() + margin, y)
                else:
                    dlg.move(x_left, y)
            except Exception:
                pass

            dlg.exec()
            return

        for opt in options:
            btn = QPushButton(opt.get("label", "选项"))
            choice_id = opt.get("choice_id", "")
            btn.clicked.connect(lambda _, qid=question_id, cid=choice_id: self._submit_preference(qid, cid))
            self.options_layout.addWidget(btn)

    def _hide_options(self):
        self._clear_option_buttons()
        self.options_wrap.hide()

    def _request_json(self, path: str, payload: dict, timeout=20):
        url = f"{self.api_base}{path}"
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def _request_get_json(self, path: str, timeout=20):
        url = f"{self.api_base}{path}"
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def _set_interest_signal(self, signal: str):
        self._touch()
        self.current_interest_signal = signal or ""
        if self.current_interest_signal == "interested":
            self.interest_label.setText("兴趣：感兴趣")
        elif self.current_interest_signal == "not_interested":
            self.interest_label.setText("兴趣：不感兴趣")
        else:
            self.interest_label.setText("兴趣：未选择")

    def _sync_avatar_dock_heights(self):
        extra = 58 if self.chat_mode == "creative" else 0
        self.avatar_dock.setMinimumHeight(198 + extra)
        self.avatar_dock.setMaximumHeight(248 + extra)
        self._apply_window_size()

    def _on_creative_domain_intro_finished(self):
        if self.chat_mode != "creative":
            return
        self.creative_actions.show()
        self.avatar_dock.updateGeometry()
        if getattr(self, "_creative_domain_intro_instant", False):
            for b in self._creative_domain_buttons:
                eff = b.graphicsEffect()
                if isinstance(eff, QGraphicsOpacityEffect):
                    eff.setOpacity(1.0)
            return
        for i, b in enumerate(self._creative_domain_buttons):
            eff = b.graphicsEffect()
            if not isinstance(eff, QGraphicsOpacityEffect):
                continue
            anim = QPropertyAnimation(eff, b"opacity", self)
            anim.setDuration(360)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutBack)
            QTimer.singleShot(50 + i * 95, anim.start)

    def _finish_hide_creative_actions(self):
        if self.chat_mode != "creative":
            self.creative_actions.hide()

    def _hide_creative_domain_buttons(self, animated: bool):
        if not self.creative_actions.isVisible():
            for b in self._creative_domain_buttons:
                eff = b.graphicsEffect()
                if isinstance(eff, QGraphicsOpacityEffect):
                    eff.setOpacity(0.0)
            return
        if not animated:
            for b in self._creative_domain_buttons:
                eff = b.graphicsEffect()
                if isinstance(eff, QGraphicsOpacityEffect):
                    eff.setOpacity(0.0)
            self.creative_actions.hide()
            return
        n = len(self._creative_domain_buttons)
        for i, b in enumerate(self._creative_domain_buttons):
            eff = b.graphicsEffect()
            if not isinstance(eff, QGraphicsOpacityEffect):
                continue
            anim = QPropertyAnimation(eff, b"opacity", self)
            anim.setDuration(200)
            anim.setStartValue(eff.opacity())
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.Type.InCubic)
            QTimer.singleShot(i * 50, anim.start)
        QTimer.singleShot(50 * (n - 1) + 220, self._finish_hide_creative_actions)

    def _apply_chat_mode_ui(self):
        prev = getattr(self, "_chat_mode_for_fx", None)
        mode_changed = prev is not None and prev != self.chat_mode
        self._chat_mode_for_fx = self.chat_mode

        if self.chat_mode == "creative":
            self.btn_mode.setText("形态：创作")
            self.controls_wrap.setStyleSheet(
                "#vivyChatColumn { border-left: 2px solid rgba(100, 220, 255, 120); "
                "border-radius: 10px; padding-left: 6px; background: rgba(6, 22, 36, 85); }"
            )
            self._creative_domain_intro_instant = not mode_changed
            self._sync_avatar_dock_heights()
            self.domain_aura.set_creative_active(True, animate=mode_changed)
        else:
            self.btn_mode.setText("形态：普通")
            self.controls_wrap.setStyleSheet("")
            self._hide_creative_domain_buttons(animated=mode_changed)
            self.domain_aura.set_creative_active(False, animate=mode_changed)
            if mode_changed:
                QTimer.singleShot(430, self._sync_avatar_dock_heights)
            else:
                self._sync_avatar_dock_heights()

        self.controls_wrap.set_creative_drop_enabled(self.chat_mode == "creative")

    def _set_chat_mode(self, mode: str, persist: bool = True):
        m = (mode or "").strip().lower()
        if m not in ("chat", "creative"):
            m = "chat"
        self.chat_mode = m
        self._apply_chat_mode_ui()
        if not persist:
            return

        def _request():
            return self._request_json(
                "/api/memory/update",
                {"user_id": self.user_id, "preferences_patch": {"chat_mode": self.chat_mode}},
            )

        def _ok(_data):
            self._set_status_text("形态已切换。")

        def _err(error_msg):
            self._set_status_text(f"切换形态失败：{error_msg}")

        self._run_async(_request, _ok, _err, thinking_text="正在切换形态...")

    def _toggle_chat_mode(self):
        self._touch()
        self._set_chat_mode("creative" if self.chat_mode != "creative" else "chat", persist=True)

    def _refresh_memory(self, silent: bool = True):
        self._touch()
        def _request():
            return self._request_get_json(f"/api/memory?user_id={self.user_id}")

        def _ok(data):
            self.memory_summary.setPlainText((data.get("summary") or "").strip())
            self.memory_summary_long.setPlainText((data.get("summary_long") or "").strip())
            import json
            self.memory_prefs.setPlainText(json.dumps(data.get("preferences") or {}, ensure_ascii=False, indent=2))
            turns = data.get("recent_turns") or []
            lines = []
            for t in turns:
                tid = t.get("id", "")
                role = t.get("role", "")
                mode = t.get("mode", "")
                content = (t.get("content") or "").replace("\n", " ")
                if len(content) > 90:
                    content = content[:90] + "..."
                tag = f"{role}/{mode}" if mode else role
                lines.append(f"[{tid}] {tag}: {content}")
            self.memory_turns.setPlainText("\n".join(lines))
            prefs = data.get("preferences") or {}
            self._set_chat_mode(prefs.get("chat_mode") or "chat", persist=False)
            if not silent:
                self._set_status_text("记忆模块已刷新。")

        def _err(error_msg):
            if not silent:
                self._set_status_text(f"读取记忆失败：{error_msg}")

        if silent:
            # silent refresh should never disable the UI
            self._run_background(_request, on_success=_ok, on_error=_err)
        else:
            self._run_async(_request, _ok, _err, thinking_text="正在读取记忆...")

    def _save_memory(self):
        self._touch()
        def _request():
            import json
            prefs = json.loads((self.memory_prefs.toPlainText() or "{}").strip() or "{}")
            payload = {
                "user_id": self.user_id,
                "summary": self.memory_summary.toPlainText().strip(),
                "summary_long": self.memory_summary_long.toPlainText().strip(),
                "preferences": prefs,
            }
            return self._request_json("/api/memory/update", payload)

        def _ok(_data):
            self._set_status_text("记忆模块已保存。")

        def _err(error_msg):
            self._set_status_text(f"保存记忆失败：{error_msg}")

        self._run_async(_request, _ok, _err, thinking_text="正在保存记忆...")

    def _show_memory_guide(self):
        self._touch()
        QMessageBox.information(
            self,
            "记忆模块操作指南",
            (
                "最简单的使用流程：\n\n"
                "1) 点击“刷新记忆”\n"
                "   先拉取数据库里的最新内容，避免覆盖旧数据。\n\n"
                "2) 按需修改\n"
                "   - summary：写 1 句最近状态\n"
                "   - summary_long：写长期偏好/目标/边界\n"
                "   - preferences JSON：必须保持合法 JSON\n\n"
                "3) 点击“保存记忆”\n"
                "   保存后 VIVY 后续对话会按新记忆工作。\n\n"
                "4) 管理历史回合\n"
                "   在“最近对话回合”里看 [ID]，输入 ID 后点“删除回合”。"
            ),
        )

    def _apply_memory_json_template(self):
        self._touch()
        template = {
            "chat_mode": "chat",
            "topic_bias": "创作",
            "humor_level": "中",
            "comfort_style": "陪伴",
            "last_interest_signal": "interested"
        }
        import json

        self.memory_prefs.setPlainText(json.dumps(template, ensure_ascii=False, indent=2))
        self._set_status_text("已填入示例 JSON，可按需修改后保存。")

    def _delete_turn_by_id(self):
        self._touch()
        text = (self.turn_id_input.text() or "").strip()
        if not text:
            self._set_status_text("请输入要删除的回合ID。")
            return
        try:
            turn_id = int(text)
        except Exception:
            self._set_status_text("回合ID必须是数字。")
            return

        def _request():
            return self._request_json(
                "/api/memory/delete_turn",
                {"user_id": self.user_id, "turn_id": turn_id},
            )

        def _ok(_data):
            self.turn_id_input.clear()
            self._set_status_text(f"已删除回合 {turn_id}。")
            self._refresh_memory(silent=True)

        def _err(error_msg):
            self._set_status_text(f"删除失败：{error_msg}")

        self._run_async(_request, _ok, _err, thinking_text="正在删除回合...")

    def _set_api_key_interactive(self):
        self._touch()
        current = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
        key, ok = QInputDialog.getText(
            self,
            "配置 DeepSeek API Key",
            "请输入 DeepSeek API Key（会保存到 .env）：",
            QLineEdit.EchoMode.Password,
            current,
        )
        if not ok:
            return

        new_key = (key or "").strip()
        if not new_key:
            QMessageBox.warning(self, "提示", "API Key 不能为空。")
            return

        _save_env_value("DEEPSEEK_API_KEY", new_key)
        self._set_status_text("API Key 已保存，后续请求将使用新配置。")

    def _handle_messages(self, messages):
        if not messages:
            return

        for msg in messages:
            mtype = msg.get("type")
            if mtype == "chat":
                self._hide_options()
                self._set_bubble_text(msg.get("text", ""))
            elif mtype == "preference_question":
                self._show_options(
                    msg.get("question_id", ""),
                    msg.get("question", "我想更了解你一点。"),
                    msg.get("options", []),
                )
            elif mtype == "inspiration":
                self._hide_options()
                text = (
                    "【今日冲浪见闻】\n"
                    f"发现：{msg.get('discovery', '')}\n"
                    f"联想：{msg.get('vivy_association', '')}\n"
                    f"{msg.get('invitation_question', '')}"
                )
                self._set_bubble_text(text)

    def _init_session(self):
        def _request():
            return self._request_json("/api/init", {"user_id": self.user_id})

        def _ok(data):
            if data.get("user_id") and data.get("user_id") != self.user_id:
                self.user_id = data["user_id"]
                USER_ID_FILE.write_text(self.user_id, encoding="utf-8")
            self._handle_messages(data.get("messages", []))
            self._refresh_memory(silent=True)

        def _err(error_msg):
            self._set_status_text(f"启动失败：{error_msg}")

        self._run_async(_request, _ok, _err, thinking_text="VIVY 正在接入时间线...")

    def _submit_preference(self, question_id: str, choice_id: str):
        def _request():
            return self._request_json(
                "/api/preference_answer",
                {
                    "user_id": self.user_id,
                    "question_id": question_id,
                    "choice_id": choice_id,
                },
            )

        def _ok(data):
            self._handle_messages(data.get("messages", []))

        def _err(error_msg):
            self._set_status_text(f"记录偏好失败：{error_msg}")

        self._run_async(_request, _ok, _err, thinking_text="VIVY 正在记住你的偏好...")

    def _send_from_input(self):
        self._touch()
        text = self.input_edit.text().strip()
        if not text:
            return
        self.input_edit.clear()
        self._send_message(text)

    def _send_message(self, text: str):
        self._touch()
        # Commands that require structured messages should use non-stream endpoint.
        lower = (text or "").lower()
        is_structured = ("换个问题" in text) or ("了解我" in text) or ("灵感" in text) or ("冲浪" in lower)

        if is_structured:
            def _request():
                return self._request_json(
                    "/api/message",
                    {
                        "user_id": self.user_id,
                        "message": text,
                        "interest_signal": self.current_interest_signal or None,
                        "chat_mode": self.chat_mode,
                    },
                    timeout=25,
                )

            def _ok(data):
                messages = data.get("messages", [])
                self._handle_messages(messages)
                self._refresh_memory(silent=True)

            def _err(error_msg):
                self._set_status_text(f"请求失败：{error_msg}")

            self._run_async(_request, _ok, _err, thinking_text="VIVY 思考中...")
            return

        def _request_stream():
            url = f"{self.api_base}/api/message_stream"
            payload = {
                "user_id": self.user_id,
                "message": text,
                "interest_signal": self.current_interest_signal or None,
                "chat_mode": self.chat_mode,
            }
            resp = requests.post(url, json=payload, timeout=30, stream=True)
            resp.raise_for_status()

            import json

            assembled = ""
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                line = raw.strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                try:
                    obj = json.loads(data)
                except Exception:
                    continue

                if obj.get("error"):
                    raise RuntimeError(obj["error"])
                if obj.get("done"):
                    break
                delta = obj.get("delta") or ""
                if delta:
                    assembled += delta
                    yield {"partial": assembled, "delta": delta}

            return assembled

        def _ok_stream(data):
            self._refresh_memory(silent=True)

        def _err_stream(error_msg):
            self._set_status_text(f"请求失败：{error_msg}")

        def _consume(progress_callback=None):
            final_text = ""
            for payload in _request_stream():
                if isinstance(payload, dict):
                    final_text = str(payload.get("partial") or final_text)
                else:
                    final_text = str(payload or final_text)
                if progress_callback is not None:
                    progress_callback(payload)
            return {"ok": True, "text": final_text}

        self._run_async(
            _consume,
            _ok_stream,
            _err_stream,
            on_progress=self._on_stream_chat_progress,
            thinking_text="VIVY 思考中（流式输出）...",
        )

    def _touch(self):
        """刷新「活跃时间」；待机收起时不会自动展开，需双击头像区域唤醒。"""
        self._last_interaction_ts = time.time()

    def _set_idle_collapsed(self, collapsed: bool):
        if collapsed == self._idle_collapsed:
            return
        self._idle_collapsed = collapsed
        self.controls_wrap.setVisible(not collapsed)

        if collapsed:
            self.options_wrap.hide()
            self.memory_wrap.hide()
        self._apply_window_size()

    def _apply_window_size(self):
        if self._idle_collapsed:
            size = self._collapsed_size
        else:
            mw = getattr(self, "memory_wrap", None)
            memory_visible = mw is not None and mw.isVisible()
            size = self._expanded_size_with_memory if memory_visible else self._expanded_size
        self.setFixedSize(size)

    def _start_idle_watch(self):
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(800)
        self._idle_timer.timeout.connect(self._on_idle_tick)
        self._idle_timer.start()

    def _on_idle_tick(self):
        if self._busy or self._dragging:
            return
        # Don't collapse while user is typing/composing.
        if self.input_edit is not None and self.input_edit.hasFocus():
            return
        idle_for = time.time() - self._last_interaction_ts
        if idle_for >= self._idle_timeout_s:
            self._set_idle_collapsed(True)

    def eventFilter(self, obj, event):
        try:
            if event.type() == QEvent.Type.MouseButtonDblClick:
                if isinstance(event, QMouseEvent) and event.button() == Qt.MouseButton.LeftButton:
                    if obj in (self.image_label, self.domain_aura, self.avatar_dock):
                        self._last_interaction_ts = time.time()
                        self._set_idle_collapsed(not self._idle_collapsed)
                        return True
            if obj is self.input_edit:
                et = event.type()
                if et in (
                    event.Type.KeyPress,
                    event.Type.KeyRelease,
                    event.Type.MouseButtonPress,
                    event.Type.MouseButtonDblClick,
                    event.Type.FocusIn,
                ):
                    self._touch()
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def contextMenuEvent(self, event):
        self._touch()
        menu = QMenu(self)
        menu.setStyleSheet(
            """
            QMenu {
                background-color: rgba(8, 16, 24, 245);
                border: 1px solid rgba(78, 208, 255, 220);
                border-radius: 8px;
                color: #f2fbff;
                padding: 6px;
            }
            QMenu::item {
                background-color: transparent;
                color: #f2fbff;
                padding: 8px 14px;
                margin: 2px 2px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background-color: rgba(55, 214, 255, 210);
                color: #04131c;
                font-weight: 600;
            }
            QMenu::separator {
                height: 1px;
                background: rgba(130, 230, 255, 180);
                margin: 6px 4px;
            }
            """
        )
        act_reset = QAction("重置本机用户ID", self)
        act_set_api_key = QAction("设置 API Key", self)
        act_toggle_memory = QAction("显示/隐藏记忆模块", self)
        act_toggle_idle = QAction("切换待机收起", self)
        act_set_idle_timeout = QAction("设置待机时长", self)
        act_load_doc = QAction("读取文档（创作辅助）", self)
        act_clear_doc = QAction("清除已读取文档", self)
        act_immersive = QAction("沉浸写作窗口…", self)
        act_quit = QAction("退出 VIVY", self)

        act_reset.triggered.connect(self._reset_user)
        act_set_api_key.triggered.connect(self._set_api_key_interactive)
        act_toggle_memory.triggered.connect(self._toggle_memory_panel)
        act_toggle_idle.triggered.connect(lambda: self._set_idle_collapsed(not self._idle_collapsed))
        act_set_idle_timeout.triggered.connect(self._set_idle_timeout_interactive)
        act_load_doc.triggered.connect(self._load_document_for_creative)
        act_clear_doc.triggered.connect(self._clear_loaded_document)
        act_immersive.triggered.connect(self._open_immersive_writing)
        act_quit.triggered.connect(QApplication.instance().quit)

        menu.addAction(act_reset)
        menu.addAction(act_set_api_key)
        menu.addAction(act_toggle_memory)
        menu.addAction(act_toggle_idle)
        menu.addAction(act_set_idle_timeout)
        menu.addSeparator()
        menu.addAction(act_load_doc)
        menu.addAction(act_clear_doc)
        menu.addAction(act_immersive)
        menu.addSeparator()
        menu.addAction(act_quit)
        menu.exec(event.globalPos())

    def _toggle_memory_panel(self):
        self.memory_wrap.setVisible(not self.memory_wrap.isVisible())
        # Keep main panel size stable regardless of memory panel visibility.
        self._apply_window_size()

    def _set_idle_timeout_interactive(self):
        self._touch()
        dlg = QInputDialog(self)
        dlg.setInputMode(QInputDialog.InputMode.IntInput)
        dlg.setWindowTitle("设置待机时长")
        dlg.setLabelText("多少秒不操作后进入待机：")
        dlg.setIntValue(int(self._idle_timeout_s))
        dlg.setIntMinimum(5)
        dlg.setIntMaximum(3600)
        dlg.setIntStep(1)
        dlg.setStyleSheet(
            """
            QInputDialog {
                background-color: #0c141d;
            }
            QLabel {
                color: #eaf8ff;
                font-size: 13px;
                font-weight: 500;
            }
            QSpinBox {
                color: #04131c;
                background-color: #dff6ff;
                border: 2px solid #46c9ee;
                border-radius: 8px;
                padding: 6px 10px;
                font-size: 15px;
                font-weight: 600;
                min-height: 28px;
                selection-background-color: rgba(55, 214, 255, 160);
                selection-color: #04131c;
            }
            QSpinBox:focus {
                border: 2px solid #7be2ff;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 22px;
                background: #2b9ec5;
                border-left: 1px solid #46c9ee;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background: #3ab8de;
            }
            QPushButton {
                background-color: #2b9ec5;
                border: 1px solid #7be2ff;
                border-radius: 8px;
                color: #04131c;
                padding: 6px 16px;
                font-size: 13px;
                font-weight: 600;
                min-width: 72px;
            }
            QPushButton:hover {
                background-color: #3ab8de;
            }
            """
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        value = dlg.intValue()

        self._idle_timeout_s = int(value)
        _save_env_value("VIVY_IDLE_TIMEOUT", str(self._idle_timeout_s))
        self._set_status_text(f"待机时长已设置为 {self._idle_timeout_s} 秒。")

    def _load_document_for_creative(self):
        self._touch()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择要读取的文档",
            str(PROJECT_DIR),
            "Documents (*.txt *.md *.docx *.pdf);;All Files (*.*)",
        )
        if not path:
            return

        goal, ok = QInputDialog.getText(
            self,
            "创作目标（可选）",
            "你希望 VIVY 从这个文档里帮你做什么？（可留空）",
            QLineEdit.EchoMode.Normal,
            "",
        )
        if not ok:
            return

        self._start_creative_doc_stream(path, (goal or "").strip())

    def _open_immersive_writing(self):
        self._touch()
        if self._immersive_writing_window is None:
            self._immersive_writing_window = ImmersiveWritingWindow(self)
            self._immersive_writing_window.set_assist_busy(self._busy)
        self._immersive_writing_window.show()
        self._immersive_writing_window.raise_()
        self._immersive_writing_window.activateWindow()

    def _start_creative_doc_stream(self, path: str, goal: str = ""):
        """菜单「读取文档」与创作形态下拖放共用：流式调用 creative_doc_stream。"""
        self._touch()
        goal = (goal or "").strip()

        def _request_stream():
            doc = load_document_text(path)
            url = f"{self.api_base}/api/creative_doc_stream"
            payload = {
                "user_id": self.user_id,
                "doc_path": doc.path,
                "doc_text": doc.text,
                "goal": goal,
            }
            resp = requests.post(url, json=payload, timeout=30, stream=True)
            resp.raise_for_status()

            import json

            assembled = ""
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                line = raw.strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                if obj.get("error"):
                    raise RuntimeError(obj["error"])
                if obj.get("done"):
                    break
                delta = obj.get("delta") or ""
                if delta:
                    assembled += delta
                    yield assembled

        def _ok_stream(_unused):
            self._loaded_doc_path = path
            self._refresh_memory(silent=True)

        def _err_stream(error_msg):
            self._set_status_text(f"文档创作辅助失败：{error_msg}")

        def _consume(progress_callback=None):
            for partial in _request_stream():
                if progress_callback is not None:
                    progress_callback(partial)
            return {"ok": True}

        self._run_async(
            _consume,
            _ok_stream,
            _err_stream,
            on_progress=lambda partial: self._set_bubble_text(str(partial)),
            thinking_text="VIVY 正在阅读文档并给创作建议（流式）...",
        )

    def _clear_loaded_document(self):
        self._touch()
        self._loaded_doc_path = None
        self._set_status_text("已清除已读取文档。")

    def _reset_user(self):
        self._touch()
        self.user_id = str(uuid.uuid4())
        USER_ID_FILE.write_text(self.user_id, encoding="utf-8")
        self._set_status_text("已重置本机 user_id。重新初始化中…")
        QTimer.singleShot(300, self._init_session)

    def mousePressEvent(self, event: QMouseEvent):
        self._touch()
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        self._touch()
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            self.move(new_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._touch()
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_interaction_ts = time.time()
            self._set_idle_collapsed(not self._idle_collapsed)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


def run_flask_background(port: int):
    def _target():
        init_db()
        flask_app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)

    t = threading.Thread(target=_target, daemon=True)
    t.start()


def wait_server_ready(base_url: str, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(base_url + "/", timeout=1.5)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _save_env_value(key: str, value: str):
    lines = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

    new_line = f"{key}={value}"
    replaced = False
    out_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped.startswith(f"{key}="):
            out_lines.append(new_line)
            replaced = True
        else:
            out_lines.append(line)

    if not replaced:
        out_lines.append(new_line)

    ENV_FILE.write_text("\n".join(out_lines).strip() + "\n", encoding="utf-8")
    os.environ[key] = value


def _ensure_api_key(parent=None) -> bool:
    existing = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if existing:
        return True

    key, ok = QInputDialog.getText(
        parent,
        "首次配置 API Key",
        "检测到未配置 DeepSeek API Key，请输入（会保存到 .env）：",
        QLineEdit.EchoMode.Password,
    )
    if not ok:
        return False

    new_key = (key or "").strip()
    if not new_key:
        return False

    _save_env_value("DEEPSEEK_API_KEY", new_key)
    return True


def main():
    load_dotenv(PROJECT_DIR / ".env")
    port = int(os.getenv("FLASK_PORT", "5000"))
    base_url = f"http://127.0.0.1:{port}"

    qt_app = QApplication(sys.argv)
    if not _ensure_api_key():
        QMessageBox.information(None, "提示", "未配置 API Key，本次将以离线兜底模式运行。可右键桌宠 -> 设置 API Key。")

    # Start embedded Flask backend
    run_flask_background(port)

    if not wait_server_ready(base_url, timeout=12):
        print("Flask 后端启动超时，请检查端口占用或配置")

    pet = DesktopPet(api_base=base_url)
    pet.show()
    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()
