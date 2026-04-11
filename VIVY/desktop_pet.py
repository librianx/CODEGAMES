import json
import os
import sys
import uuid
import threading
import time
import math
import random
import base64
import mimetypes
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
)
from PyQt6.QtGui import (
    QAction,
    QFont,
    QKeySequence,
    QMouseEvent,
    QMovie,
    QImageReader,
    QShortcut,
    QTextCursor,
)
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
    QFormLayout,
    QGraphicsOpacityEffect,
    QPlainTextEdit,
    QSplitter,
    QSpinBox,
)

# Import existing Flask backend
from app import app as flask_app
from db import init_db
from creative_assist import OFFICE_CONTEXT_MAX, OFFICE_PASSAGE_MAX, load_document_text
from speech import set_speech_token, speak_text_async, stop_speaking, trigger_windows_voice_typing

try:
    from command_effect_stable import CommandEffect
except Exception:
    CommandEffect = None

try:
    from urban_inspiration import UrbanInspirationPanel, generate_inspiration_short, generate_inspiration
except Exception:
    UrbanInspirationPanel = None
    generate_inspiration_short = None
    generate_inspiration = None


CREATIVE_DOC_SUFFIXES = frozenset({".txt", ".md", ".docx", ".pdf"})

IMPROV_SKETCH_MESSAGE = (
    "即兴写一个脑洞小短剧：共 3 到 5 行，格式用“场景一句”和“角色名：台词”交替；"
    "要有一个无厘头误会，结尾用一句反转或金句收束；不要写作课讲解，不要正式标题以外的套话。"
)

_FALLBACK_INSPIRATION_POOL = [
    "地铁玻璃里映出两个人影，其中一个比真人慢了半拍。",
    "深夜便利店的自动门每隔十分钟自己开一次，但监控里门外没有人。",
    "一封写给三天后的短信准时送达，而发件箱里并没有发送记录。",
    "雨停后，天桥上出现一串没有尽头的湿脚印，方向却是通往天空。",
]
PROJECT_DIR = Path(__file__).resolve().parent
USER_ID_FILE = PROJECT_DIR / ".desktop_user_id"
GIF_PATH = PROJECT_DIR / "static" / "images" / "VIVYfirst.gif"
PNG_FALLBACK_PATH = PROJECT_DIR / "static" / "images" / "VIVYstatr.png"
ENV_FILE = PROJECT_DIR / ".env"
DOMAIN_SOUND_PATH = PROJECT_DIR / "static" / "sounds" / "domain_expand.wav"
SONG_DIR = PROJECT_DIR / "song"
SONG_SUFFIXES = {".wav"}
SING_TRIGGER_KEYWORDS = (
    "唱歌", "唱首歌", "唱一首", "给我唱", "你唱", "来首歌", "来一首歌",
    "放首歌", "播放歌曲", "哄我", "安慰我", "我心情不好", "我很难过"
)
STOP_SONG_TRIGGER_KEYWORDS = ("停止唱歌", "别唱了", "停歌", "停止播放", "暂停歌曲")
IMMERSIVE_AUTOSAVE_PATH = PROJECT_DIR / ".vivy_immersive_autosave.md"
IMMERSIVE_RECENT_FILE = PROJECT_DIR / ".immersive_recent.json"


def _immersive_load_recent(max_n: int = 10) -> list[str]:
    if not IMMERSIVE_RECENT_FILE.exists():
        return []
    try:
        data = json.loads(IMMERSIVE_RECENT_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        out = []
        for x in data:
            p = Path(str(x))
            if p.is_file():
                out.append(str(p.resolve()))
            if len(out) >= max_n:
                break
        return out
    except Exception:
        return []


def _immersive_push_recent(path: str, max_n: int = 10) -> None:
    try:
        p = str(Path(path).resolve())
    except OSError:
        return
    cur = []
    if IMMERSIVE_RECENT_FILE.exists():
        try:
            old = json.loads(IMMERSIVE_RECENT_FILE.read_text(encoding="utf-8"))
            if isinstance(old, list):
                cur = [str(Path(x).resolve()) for x in old if Path(x).is_file()]
        except Exception:
            cur = []
    lst = [p] + [x for x in cur if x != p]
    lst = lst[:max_n]
    try:
        IMMERSIVE_RECENT_FILE.write_text(json.dumps(lst, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    p = 1.0 - t
    return 1.0 - p * p * p


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, 'true' if default else 'false')).strip().lower()
    return raw in ('1', 'true', 'yes', 'y', 'on')


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


class RequestCancelled(RuntimeError):
    pass


class ChatDropFrame(QFrame):
    """宸︿晶瀵硅瘽鍖哄鍣細鍒涗綔褰㈡€佷笅鍙嫋鍏ユ枃妗ｈЕ鍙戜笌鑿滃崟鐩稿悓鐨勮鍙栨祦绋嬨€?"""
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
                self.pet._set_status_text("浠呮敮鎸佹嫋鍏?.txt銆?md銆?docx銆?pdf")
            event.ignore()
            return
        event.acceptProposedAction()
        self.pet._start_creative_doc_stream(paths[0], "")


class CreativeDomainEffects(QWidget):
    """Creative domain visual effects with an intro completion signal."""

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

        # 鈥斺€?鎵╂暎娉紙搴忚疮鍦嗙幆鏀惧ぇ骞舵贰鍑猴級鈥斺€?
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

        # 鈥斺€?鑳屾櫙棰嗗煙鍏夋檿锛堥殢 settle 鎵╁ぇ骞跺畾鏍硷級鈥斺€?
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

        # 鈥斺€?鏍稿績鍦堬紙鎵胯浇 UI 鐨勫渾褰㈣竟鐣屾彁绀猴級鈥斺€?
        core_r = min(w, h) * 0.47
        core_pen = QPen(QColor(140, 230, 255, int(110 * o * max(0.35, settle))))
        core_pen.setWidthF(1.4)
        p.setPen(core_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(cx - core_r, cy - core_r, 2 * core_r, 2 * core_r))

        # 鈥斺€?瑁呴グ绾匡細鐭垝娌垮懆鍚戞紓绉?鈥斺€?
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
            # 鈥斺€?鏃嬭浆铏氱嚎鐜?鈥斺€?
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

            # 鈥斺€?鍏竟褰?鈥斺€?
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

            # 鈥斺€?鏀惧皠绾?鈥斺€?
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

        # 鈥斺€?绮掑瓙鍏夌偣 鈥斺€?
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
    """涓棿鍒楋細棰嗗煙缁樺眰 + 澶村儚 + 搴曢儴棰嗗煙蹇嵎鎸夐挳銆?"""
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


class ImmersiveWritingWindow(QWidget):
    """浠呭湪 VIVY 鍐咃細澶у睆娌夋蹈鍐欎綔锛岃皟鐢ㄦ湰鏈?/api/office_passage_stream锛屼笉渚濊禆 Office 鎻掍欢銆?"""
    def __init__(self, pet: "DesktopPet"):
        super().__init__(None, Qt.WindowType.Window)
        self._pet = pet
        self._current_path: Path | None = None
        self._dirty = False
        self._suppress_dirty = False

        self.setWindowTitle("VIVY 娌夋蹈鍐欎綔")
        self.setMinimumSize(640, 420)
        self.resize(960, 680)
        self.setStyleSheet(
            """
            ImmersiveWritingWindow {
                background: #080e14;
            }
            QPlainTextEdit#imWriteEditor {
                background: #0c141d;
                color: #e8f4ff;
                border: 1px solid rgba(60, 160, 210, 100);
                border-radius: 10px;
                padding: 18px 22px;
                font-size: 15px;
                selection-background-color: rgba(55, 214, 255, 120);
            }
            QTextEdit#imWriteAssist {
                background: #0a1018;
                color: #c5e8ff;
                border: 1px solid rgba(80, 200, 255, 80);
                border-radius: 8px;
                padding: 10px;
                font-size: 12px;
            }
            QPushButton#imBarBtn {
                background: rgba(32, 120, 168, 200);
                border: 1px solid rgba(120, 220, 255, 150);
                border-radius: 7px;
                padding: 5px 12px;
                color: #f2fbff;
                font-size: 12px;
            }
            QPushButton#imBarBtn:hover { background: rgba(48, 150, 200, 220); }
            QPushButton#imBarBtn:disabled { background: rgba(40, 60, 80, 150); color: #8899aa; }
            QLabel#imBarLbl { color: #9ecfe8; font-size: 12px; }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        def mk_btn(text, slot):
            b = QPushButton(text)
            b.setObjectName("imBarBtn")
            b.clicked.connect(slot)
            return b

        bar1 = QHBoxLayout()
        bar1.setSpacing(6)
        self.btn_open = mk_btn("鎵撳紑", self._open_file)
        self._recent_menu = QMenu(self)
        self._recent_menu.aboutToShow.connect(self._fill_recent_menu)
        self.btn_recent = QPushButton("鏈€杩?)
        self.btn_recent.setObjectName("imBarBtn")
        self.btn_recent.setMenu(self._recent_menu)
        self.btn_new = mk_btn("鏂板缓", self._new_file)
        self.btn_save = mk_btn("淇濆瓨", self._save_file)
        self.btn_save_as = mk_btn("鍙﹀瓨涓?, self._save_as)
        self.btn_restore_bak = mk_btn("鎭㈠澶囦唤", self._restore_autosave)
        self.btn_full = mk_btn("鍏ㄥ睆", self._toggle_fullscreen)
        self.btn_focus = mk_btn("涓撴敞", self._toggle_focus_assist)
        self.btn_find = mk_btn("鏌ユ壘", self._find_in_text)
        for w in (
            self.btn_open,
            self.btn_recent,
            self.btn_new,
            self.btn_save,
            self.btn_save_as,
            self.btn_restore_bak,
            self.btn_full,
            self.btn_focus,
            self.btn_find,
        ):
            bar1.addWidget(w)
        bar1.addStretch(1)
        self.lbl_autosave = QLabel("")
        self.lbl_autosave.setObjectName("imBarLbl")
        self.lbl_autosave.setStyleSheet("color: rgba(150,200,220,160); font-size: 11px;")
        bar1.addWidget(self.lbl_autosave)
        self.spin_goal = QSpinBox()
        self.spin_goal.setRange(0, 200_000)
        self.spin_goal.setSpecialValueText("鐩爣瀛楁暟")
        self.spin_goal.setValue(0)
        self.spin_goal.setMaximumWidth(100)
        self.spin_goal.setToolTip("0=涓嶆樉绀虹洰鏍囷紱璁句负瀛楁暟鍚庨《鏍忔樉绀鸿繘搴?)
        self.spin_goal.valueChanged.connect(lambda _v: self._refresh_word_count())
        bar1.addWidget(self.spin_goal)
        self.lbl_count = QLabel("0 瀛?)
        self.lbl_count.setObjectName("imBarLbl")
        bar1.addWidget(self.lbl_count)
        self.btn_close = mk_btn("鏀惰捣", self._close_safe)
        bar1.addWidget(self.btn_close)
        root.addLayout(bar1)

        bar2 = QHBoxLayout()
        bar2.setSpacing(6)
        self.btn_polish = mk_btn("娑﹁壊", lambda: self._assist("polish"))
        self.btn_continue = mk_btn("缁啓", lambda: self._assist("continue"))
        self.btn_critique = mk_btn("鐐硅瘎", lambda: self._assist("critique"))
        self.btn_improve = mk_btn("鍔犲己", lambda: self._assist("improve"))
        self.btn_custom = mk_btn("鑷畾涔夆€?, self._assist_custom)
        self._assist_buttons = [
            self.btn_polish,
            self.btn_continue,
            self.btn_critique,
            self.btn_improve,
            self.btn_custom,
        ]
        for w in self._assist_buttons:
            bar2.addWidget(w)
        bar2.addStretch(1)
        root.addLayout(bar2)

        self.assist_wrap = QWidget()
        aw = QVBoxLayout(self.assist_wrap)
        aw.setContentsMargins(0, 0, 0, 0)
        aw.setSpacing(4)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("imWriteEditor")
        self.editor.setPlaceholderText(
            "鍦ㄦ涓撴敞鍐欎綔鈥n"
            "蹇嵎閿細Ctrl+S 淇濆瓨锛孋trl+O 鎵撳紑锛孋trl+N 鏂板缓锛孋trl+F 鏌ユ壘锛孎11 鍏ㄥ睆锛孍sc 閫€鍑哄叏灞忋€俓n"
            "閫変腑涓€娈靛啀鐐广€屾鼎鑹?/ 鍔犲己銆嶇瓑锛涙湭閫夊垯瀵瑰叏鏂囷紙杩囬暱浼氭埅鏂級銆?
        )
        self.editor.textChanged.connect(self._on_text_changed)
        ef = QFont(self.editor.font())
        ef.setPointSize(15)
        ef.setFamilies(
            ["Microsoft YaHei UI", "寰蒋闆呴粦", "PingFang SC", "Source Han Sans SC", "sans-serif"]
        )
        self.editor.setFont(ef)

        self.assist = QTextEdit()
        self.assist.setObjectName("imWriteAssist")
        self.assist.setReadOnly(True)
        self.assist.setPlaceholderText("VIVY 杈撳嚭鍦ㄦ銆傚彲鐢ㄤ笅鏂规寜閽彃鍏ユ鏂囨垨鏇挎崲閫夊尯銆?)
        self.assist.setMinimumHeight(100)
        self.assist.setMaximumHeight(180)
        aw.addWidget(self.assist)

        assist_btns = QHBoxLayout()
        assist_btns.setSpacing(6)
        self.btn_copy_assist = mk_btn("澶嶅埗杈撳嚭", self._copy_assist)
        self.btn_insert_assist = mk_btn("鎻掑叆鍒板厜鏍?, self._insert_assist_at_cursor)
        self.btn_replace_assist = mk_btn("鏇挎崲閫夊尯", self._replace_selection_with_assist)
        self._assist_output_buttons = [self.btn_copy_assist, self.btn_insert_assist, self.btn_replace_assist]
        for w in self._assist_output_buttons:
            assist_btns.addWidget(w)
        assist_btns.addStretch(1)
        aw.addLayout(assist_btns)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.editor)
        splitter.addWidget(self.assist_wrap)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        self._focus_assist_hidden = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(45_000)
        self._autosave_timer.timeout.connect(self._autosave_tick)
        self._autosave_timer.start()

        QShortcut(QKeySequence.StandardKey.Save, self, self._save_file)
        QShortcut(QKeySequence.StandardKey.Open, self, self._open_file)
        QShortcut(QKeySequence.StandardKey.New, self, self._new_file)
        QShortcut(QKeySequence.StandardKey.Find, self, self._find_in_text)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, self._toggle_fullscreen)
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._on_escape)
        esc.setContext(Qt.ShortcutContext.WindowShortcut)

    def set_assist_busy(self, busy: bool):
        for b in self._assist_buttons:
            b.setDisabled(busy)
        for b in self._assist_output_buttons:
            b.setDisabled(busy)

    def _on_text_changed(self):
        if self._suppress_dirty:
            return
        self._dirty = True
        self._refresh_word_count()

    def _refresh_word_count(self):
        t = self.editor.toPlainText()
        n = len(t.replace("\n", "").replace("\r", ""))
        g = self.spin_goal.value()
        if g > 0:
            self.lbl_count.setText(f"{n} / {g} 瀛?)
            if n >= g:
                self.lbl_count.setStyleSheet("color: #7fe8b0; font-weight: 600;")
            else:
                self.lbl_count.setStyleSheet("")
        else:
            self.lbl_count.setText(f"{n} 瀛?)
            self.lbl_count.setStyleSheet("")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.btn_full.setText("鍏ㄥ睆")
        else:
            self.showFullScreen()
            self.btn_full.setText("閫€鍑哄叏灞?)

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        r = QMessageBox.question(
            self,
            "娌夋蹈鍐欎綔",
            "鏈夋湭淇濆瓨鐨勪慨鏀癸紝纭畾鏀惰捣绐楀彛鍚楋紵",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return r == QMessageBox.StandardButton.Yes

    def _confirm_discard_open(self) -> bool:
        if not self._dirty:
            return True
        r = QMessageBox.question(
            self,
            "娌夋蹈鍐欎綔",
            "鏈繚瀛樼殑淇敼灏嗕涪澶憋紝纭畾鎵撳紑鏂版枃浠跺悧锛?,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return r == QMessageBox.StandardButton.Yes

    def _fill_recent_menu(self) -> None:
        self._recent_menu.clear()
        paths = _immersive_load_recent(14)
        if not paths:
            a = self._recent_menu.addAction("锛堟殏鏃犳渶杩戞枃浠讹級")
            a.setEnabled(False)
            return
        for p in paths:
            act = self._recent_menu.addAction(Path(p).name)
            act.triggered.connect(lambda *_, path=p: self._open_recent(path))

    def _open_recent(self, path: str) -> None:
        if self._dirty and not self._confirm_discard_open():
            return
        self._load_document_from_path(path)

    def _new_file(self) -> None:
        if self._dirty and not self._confirm_discard_open():
            return
        self._suppress_dirty = True
        self.editor.clear()
        self._suppress_dirty = False
        self._dirty = False
        self._current_path = None
        self.setWindowTitle("VIVY 娌夋蹈鍐欎綔")
        self._refresh_word_count()

    def _load_document_from_path(self, path: str) -> None:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            QMessageBox.warning(self, "娌夋蹈鍐欎綔", f"鏃犳硶璇诲彇锛歿e}")
            return
        self._suppress_dirty = True
        self.editor.setPlainText(text)
        self._suppress_dirty = False
        self._dirty = False
        self._current_path = Path(path)
        _immersive_push_recent(path)
        self.setWindowTitle(f"VIVY 娌夋蹈鍐欎綔 鈥?{Path(path).name}")
        self._refresh_word_count()

    def _open_file(self):
        if self._dirty and not self._confirm_discard_open():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "鎵撳紑鏂囨湰",
            str(PROJECT_DIR),
            "Markdown / 鏂囨湰 (*.md *.txt);;All (*.*)",
        )
        if not path:
            return
        self._load_document_from_path(path)

    def _autosave_tick(self) -> None:
        text = self.editor.toPlainText()
        if not text.strip():
            return
        try:
            IMMERSIVE_AUTOSAVE_PATH.write_text(text, encoding="utf-8")
            self.lbl_autosave.setText(time.strftime("%H:%M 澶囦唤"))
        except OSError:
            self.lbl_autosave.setText("澶囦唤澶辫触")

    def _restore_autosave(self) -> None:
        if not IMMERSIVE_AUTOSAVE_PATH.exists():
            QMessageBox.information(self, "娌夋蹈鍐欎綔", "鏆傛棤鑷姩澶囦唤锛?vivy_immersive_autosave.md锛夈€?)
            return
        if self._dirty:
            r = QMessageBox.question(
                self,
                "娌夋蹈鍐欎綔",
                "褰撳墠鍐呭鏈繚瀛橈紝鐢ㄥ浠借鐩栧悧锛?,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                return
        try:
            text = IMMERSIVE_AUTOSAVE_PATH.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            QMessageBox.warning(self, "娌夋蹈鍐欎綔", f"璇诲彇澶囦唤澶辫触锛歿e}")
            return
        self._suppress_dirty = True
        self.editor.setPlainText(text)
        self._suppress_dirty = False
        self._dirty = True
        self._current_path = None
        self.setWindowTitle("VIVY 娌夋蹈鍐欎綔锛堜粠澶囦唤鎭㈠锛?)
        self._refresh_word_count()
        self._pet._set_status_text("宸蹭粠鑷姩澶囦唤鎭㈠锛屽缓璁彟瀛樹负姝ｅ紡鏂囦欢銆?)

    def _toggle_focus_assist(self) -> None:
        self._focus_assist_hidden = not self._focus_assist_hidden
        self.assist_wrap.setVisible(not self._focus_assist_hidden)
        self.btn_focus.setText("鏄剧ず杈呭姪鍖? if self._focus_assist_hidden else "涓撴敞")

    def _find_in_text(self) -> None:
        needle, ok = QInputDialog.getText(self, "鏌ユ壘", "鏌ユ壘鍐呭锛?)
        if not ok or not needle:
            return
        if not self.editor.find(needle):
            self.editor.moveCursor(QTextCursor.MoveOperation.Start)
            if not self.editor.find(needle):
                QMessageBox.information(self, "鏌ユ壘", "鏈壘鍒板尮閰嶅唴瀹广€?)

    def _on_escape(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.btn_full.setText("鍏ㄥ睆")

    def _copy_assist(self) -> None:
        t = self.assist.toPlainText().strip()
        if t:
            QApplication.clipboard().setText(t)

    def _insert_assist_at_cursor(self) -> None:
        t = self.assist.toPlainText().strip()
        if not t:
            return
        cur = self.editor.textCursor()
        cur.insertText("\n" + t + "\n")

    def _replace_selection_with_assist(self) -> None:
        t = self.assist.toPlainText().strip()
        if not t:
            return
        cur = self.editor.textCursor()
        if cur.hasSelection():
            cur.insertText(t)
        else:
            self._insert_assist_at_cursor()

    def _assist_custom(self) -> None:
        text, ok = QInputDialog.getMultiLineText(
            self,
            "鑷畾涔夋寚浠?,
            "甯屾湜 VIVY 瀵瑰綋鍓嶉€夊尯锛堣嫢鏃犻€夊尯鍒欏鍏ㄦ枃锛夊仛浠€涔堬紵",
        )
        if ok and (text or "").strip():
            self._assist("free", goal=(text or "").strip())

    def _passage_and_context(self) -> tuple[str, str]:
        cur = self.editor.textCursor()
        full = self.editor.toPlainText()
        if cur.hasSelection():
            passage = cur.selectedText().replace("\u2029", "\n").strip()
            start = min(cur.selectionStart(), cur.anchor())
            ctx_start = max(0, start - 1200)
            context = full[ctx_start:start]
        else:
            passage = full.strip()
            context = ""
        if context:
            context = context[-OFFICE_CONTEXT_MAX:]
        return passage, context

    def _save_file(self):
        text = self.editor.toPlainText()
        if self._current_path is not None:
            try:
                self._current_path.write_text(text, encoding="utf-8")
                self._dirty = False
                _immersive_push_recent(str(self._current_path))
                self._pet._set_status_text("娌夋蹈鍐欎綔宸蹭繚瀛樸€?)
            except OSError as e:
                QMessageBox.warning(self, "娌夋蹈鍐欎綔", f"淇濆瓨澶辫触锛歿e}")
            return
        self._save_as()

    def _save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "鍙﹀瓨涓?,
            str(PROJECT_DIR / "鑽夌.md"),
            "Markdown (*.md);;鏂囨湰 (*.txt);;All (*.*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(self.editor.toPlainText(), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "娌夋蹈鍐欎綔", f"淇濆瓨澶辫触锛歿e}")
            return
        self._current_path = Path(path)
        self._dirty = False
        _immersive_push_recent(path)
        self.setWindowTitle(f"VIVY 娌夋蹈鍐欎綔 鈥?{Path(path).name}")
        self._pet._set_status_text("娌夋蹈鍐欎綔宸插彟瀛樹负銆?)

    def _assist(self, action: str, goal: str | None = None):
        pet = self._pet
        if pet._busy:
            return
        passage, context_excerpt = self._passage_and_context()
        if not passage:
            QMessageBox.information(self, "娌夋蹈鍐欎綔", "鍏堝啓涓€浜涘唴瀹癸紝鎴栭€変腑涓€娈靛悗鍐嶈姹傝緟鍔┿€?)
            return
        if len(passage) > OFFICE_PASSAGE_MAX:
            passage = passage[:OFFICE_PASSAGE_MAX]

        def _request_stream():
            import json

            url = f"{pet.api_base}/api/office_passage_stream"
            payload = {
                "user_id": pet.user_id,
                "passage": passage,
                "action": action,
                "goal": (goal or "").strip(),
                "context_excerpt": context_excerpt or "",
            }
            resp = requests.post(url, json=payload, timeout=30, stream=True)
            resp.raise_for_status()
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
            pass

        def _err_stream(msg):
            self.assist.setPlainText(f"璇锋眰澶辫触锛歿msg}")

        def _consume(progress_callback=None):
            for partial in _request_stream():
                if progress_callback is not None:
                    progress_callback(partial)
            return {"ok": True}

        self.assist.clear()
        pet._run_async(
            _consume,
            _ok_stream,
            _err_stream,
            on_progress=lambda p: self.assist.setPlainText(str(p)),
            thinking_text="VIVY 娌夋蹈鍐欎綔杈呭姪涓€?,
        )

    def _close_safe(self):
        if not self._confirm_discard():
            return
        self.hide()

    def closeEvent(self, event):
        if not self._confirm_discard():
            event.ignore()
            return
        self.hide()
        event.ignore()


class DesktopPet(QWidget):
    def __init__(self, api_base: str):
        super().__init__()
        self.api_base = api_base.rstrip("/")
        self.user_id = self._load_or_create_user_id()

        self._dragging = False
        self._drag_offset = QPoint()
        self._drag_started_while_collapsed = False
        self.latest_reply = ""
        self.thread_pool = QThreadPool.globalInstance()
        self._busy = False
        self.current_interest_signal = ""
        self.chat_mode = "chat"
        self._loaded_doc_path: str | None = None
        self._immersive_writing_window: ImmersiveWritingWindow | None = None

        # voice
        self.auto_voice_reply = _env_bool('VIVY_AUTO_VOICE_REPLY', True)
        self.stream_tts_enabled = _env_bool('VIVY_STREAM_TTS', True)
        self._stream_tts_buffer = ''
        self._request_serial = 0
        self._active_chat_request_token = 0
        self._chat_request_interruptible = False
        self._keep_input_enabled_while_busy = False

        # local song player
        self._song_is_playing = False
        self._pending_song_path: Path | None = None
        self._song_delay_timer = QTimer(self)
        self._song_delay_timer.setSingleShot(True)
        self._song_delay_timer.timeout.connect(self._play_pending_song_now)

        # idle / wander
        self._idle_collapsed = False
        self._last_interaction_ts = time.time()
        self._idle_timeout_s = int(os.getenv("VIVY_IDLE_TIMEOUT", "18"))
        self._expanded_size = QSize(620, 332)
        self._expanded_size_with_memory = QSize(784, 348)
        self._collapsed_size = QSize(292, 302)

        # Keep the long-lived reply text separate from temporary status prompts.
        self._bubble_main_text = "VIVY 鍚姩涓?.."
        self._bubble_restore_timer = QTimer(self)
        self._bubble_restore_timer.setSingleShot(True)
        self._bubble_restore_timer.timeout.connect(self._restore_bubble_main_text)

        self._build_ui()
        self._init_session()
        self._start_idle_watch()

    def _build_ui(self):
        self.setWindowTitle("VIVY 妗屽疇")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 绋冲畾鐗堬細榛樿鍏抽棴鎸夐挳绾ч€忔槑鍔ㄧ敾锛屽噺灏戦€忔槑涓荤獥涓婄殑鍒嗗眰鏇存柊鍘嬪姏銆?
        self._use_button_opacity_fx = _env_bool("VIVY_SAFE_BUTTON_FADE", False)

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
        self.bubble_text.setPlainText(self._bubble_main_text)
        self.bubble_text.setAcceptDrops(False)
        bubble_layout.addWidget(self.bubble_text)

        bubble_action_row = QHBoxLayout()
        bubble_action_row.setSpacing(6)
        self.btn_copy_reply = QPushButton("澶嶅埗鍥炲")
        self.btn_copy_reply.clicked.connect(self._copy_latest_reply)
        bubble_action_row.addWidget(self.btn_copy_reply)
        bubble_action_row.addStretch(1)
        bubble_layout.addLayout(bubble_action_row)

        self.controls_layout.addWidget(self.bubble)

        if UrbanInspirationPanel is not None:
            self._urban_inspiration_panel = UrbanInspirationPanel(
                self.controls_wrap,
                interval_ms=int(os.getenv("VIVY_INSPIRATION_INTERVAL_MS", "30000")),
            )
            self._urban_inspiration_panel.hide()
            self.controls_layout.addWidget(self._urban_inspiration_panel)
        else:
            self._urban_inspiration_panel = None
        self._urban_inspiration_space_added = False
        self._urban_inspiration_extra_h = int(os.getenv("VIVY_INSPIRATION_PANEL_H", "58"))

        self.options_wrap = QFrame()
        self.options_layout = QVBoxLayout(self.options_wrap)
        self.options_layout.setContentsMargins(0, 0, 0, 0)
        self.options_layout.setSpacing(6)
        self.controls_layout.addWidget(self.options_wrap)
        self.options_wrap.hide()

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumHeight(180)
        self.image_label.setMaximumHeight(260)
        self.image_label.setMinimumWidth(150)
        self.image_label.setMaximumWidth(220)
        self.image_label.setStyleSheet("background: transparent;")
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

        self.btn_domain_doc = QPushButton("璇绘枃妗?)
        self.btn_domain_doc.setObjectName("vivyDomainBtn")
        self.btn_domain_doc.setToolTip("涓庡彸閿彍鍗曠浉鍚岀殑鏂囨。鍒涗綔杈呭姪")
        self.btn_domain_doc.clicked.connect(self._load_document_for_creative)

        self.btn_domain_spark = QPushButton("鍒涗綔鐏垫劅")
        self.btn_domain_spark.setObjectName("vivyDomainBtn")
        self.btn_domain_spark.clicked.connect(
            lambda: self._send_message("缁欐垜涓€涓畝鐭殑鍒涗綔鐏垫劅锛屽甫涓€鐐圭敾闈㈡劅銆?)
        )

        self.btn_domain_clear = QPushButton("娓呭弬鑰?)
        self.btn_domain_clear.setObjectName("vivyDomainBtn")
        self.btn_domain_clear.setToolTip("娓呴櫎宸茶鍙栫殑鏂囨。鍙傝€冪姸鎬?)
        self.btn_domain_clear.clicked.connect(self._clear_loaded_document)

        self.btn_domain_immerse = QPushButton("娌夋蹈鍐欎綔")
        self.btn_domain_immerse.setObjectName("vivyDomainBtn")
        self.btn_domain_immerse.setToolTip("鎵撳紑澶у睆涓撴敞鍐欎綔绐楀彛锛堜粎 VIVY 鍐咃級")
        self.btn_domain_immerse.clicked.connect(self._open_immersive_writing)

        self._creative_domain_buttons = [
            self.btn_domain_doc,
            self.btn_domain_spark,
            self.btn_domain_clear,
            self.btn_domain_immerse,
        ]
        if self._use_button_opacity_fx:
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
        self.avatar_dock.setMinimumWidth(150 + 48)
        self.avatar_dock.setMaximumWidth(220 + 48)
        self._sync_avatar_dock_heights()

        self._command_effect = CommandEffect(self, target_text="鍒涗綔妯″紡") if CommandEffect else None
        self._command_effect_w = int(os.getenv("VIVY_COMMAND_FX_W", "220"))
        self._command_effect_h = int(os.getenv("VIVY_COMMAND_FX_H", "44"))
        self._command_effect_margin_top = int(os.getenv("VIVY_COMMAND_FX_MARGIN_TOP", "14"))

        self._inspiration_effect = CommandEffect(self, target_text="") if CommandEffect else None
        self._inspiration_effect_w = int(os.getenv("VIVY_INSP_FX_W", "360"))
        self._inspiration_effect_h = int(os.getenv("VIVY_INSP_FX_H", "88"))
        self._inspiration_effect_margin_top = int(os.getenv("VIVY_INSP_FX_MARGIN_TOP", "14"))
        self._last_inspiration_overlay_text = ""

        # Overlay effects should never block UI interactions (e.g. memory panel).
        for _eff in (self._command_effect, self._inspiration_effect):
            if _eff is None:
                continue
            try:
                _eff.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            except Exception:
                pass

        # quick actions
        action_row = QHBoxLayout()
        action_row.setSpacing(6)

        self.btn_inspiration = QPushButton("浠婃棩鐏垫劅")
        self.btn_inspiration.setToolTip("鑾峰彇浠婂ぉ鐨勫啿娴闂?/ 鐏垫劅鍒嗕韩")
        self.btn_inspiration.clicked.connect(self._on_today_inspiration)
        action_row.addWidget(self.btn_inspiration)

        self.btn_improv_sketch = QPushButton("鑴戞礊鐭墽")
        self.btn_improv_sketch.setToolTip("鍗冲叴鐢熸垚涓€娈垫棤鍘樺ご灏忓墽鍦?)
        self.btn_improv_sketch.clicked.connect(lambda: self._send_message(IMPROV_SKETCH_MESSAGE))
        action_row.addWidget(self.btn_improv_sketch)

        self.btn_question = QPushButton("鎹釜闂")
        self.btn_question.clicked.connect(lambda: self._send_message("鎹釜闂"))
        action_row.addWidget(self.btn_question)

        self.btn_mode = QPushButton("褰㈡€侊細鏅€?)
        self.btn_mode.clicked.connect(self._toggle_chat_mode)
        action_row.addWidget(self.btn_mode)

        self.controls_layout.addLayout(action_row)

        # user input row
        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText("鍜?VIVY 璇寸偣浠€涔?..")
        # typing/focus should count as interaction (prevent idle collapse while composing)
        self.input_edit.textChanged.connect(lambda: self._touch())
        self.input_edit.cursorPositionChanged.connect(lambda: self._touch())
        self.input_edit.selectionChanged.connect(lambda: self._touch())
        self.input_edit.installEventFilter(self)
        self.input_edit.setAcceptDrops(False)
        self.input_edit.setMinimumHeight(46)
        self.input_edit.setMaximumHeight(86)
        input_row.addWidget(self.input_edit, 1)

        # image attach (vision)
        self._chat_image_path = None
        self.btn_image = QPushButton("馃柤")
        self.btn_image.setToolTip("閫夋嫨涓€寮犲浘鐗囷紝璁?VIVY 璇嗗埆/鐞嗚В锛堝彂閫佹椂灏嗚蛋闈炴祦寮忥級")
        self.btn_image.clicked.connect(self._pick_chat_image)
        input_row.addWidget(self.btn_image)

        self.btn_image_clear = QPushButton("鉁?)
        self.btn_image_clear.setToolTip("娓呴櫎宸查€夋嫨鐨勫浘鐗?)
        self.btn_image_clear.clicked.connect(self._clear_chat_image)
        self.btn_image_clear.setEnabled(False)
        input_row.addWidget(self.btn_image_clear)

        self.btn_send = QPushButton("鍙戦€?)
        self.btn_send.clicked.connect(self._send_from_input)
        input_row.addWidget(self.btn_send)

        self.btn_voice = QPushButton("馃帳璇煶")
        self.btn_voice.clicked.connect(self._start_voice_input)
        input_row.addWidget(self.btn_voice)

        self.btn_voice_toggle = QPushButton()
        self.btn_voice_toggle.clicked.connect(self._toggle_auto_voice_reply)
        input_row.addWidget(self.btn_voice_toggle)
        self._update_voice_toggle_button()

        interest_row = QHBoxLayout()
        interest_row.setSpacing(6)
        self.interest_label = QLabel("鍏磋叮锛氭湭閫夋嫨")
        self.interest_label.setStyleSheet(
            "background: rgba(39, 160, 209, 190); "
            "border: 1px solid rgba(121, 228, 255, 180); "
            "border-radius: 8px; padding: 4px 8px; color: #f3fcff;"
        )
        interest_row.addWidget(self.interest_label)

        self.btn_interest_yes = QPushButton("鎰熷叴瓒?)
        self.btn_interest_yes.clicked.connect(lambda: self._set_interest_signal("interested"))
        interest_row.addWidget(self.btn_interest_yes)

        self.btn_interest_no = QPushButton("涓嶆劅鍏磋叮")
        self.btn_interest_no.clicked.connect(lambda: self._set_interest_signal("not_interested"))
        interest_row.addWidget(self.btn_interest_no)

        self.btn_interest_clear = QPushButton("娓呴櫎")
        self.btn_interest_clear.clicked.connect(lambda: self._set_interest_signal(""))
        interest_row.addWidget(self.btn_interest_clear)
        self.controls_layout.addLayout(interest_row)

        # Keep the input box at the bottom of the chat area.
        self.controls_layout.addStretch(1)
        self.controls_layout.addLayout(input_row)

        self.memory_wrap = QFrame()
        self.memory_wrap.setObjectName("memoryWrap")
        memory_layout = QVBoxLayout(self.memory_wrap)
        memory_layout.setContentsMargins(8, 8, 8, 8)
        memory_layout.setSpacing(8)

        self.memory_title = QLabel("璁板繂妯″潡锛堝彲缂栬緫锛?)
        memory_layout.addWidget(self.memory_title)

        self.memory_help = QLabel(
            "蹇€熶笂鎵嬶細鈶犲厛鍒锋柊 鈶″啀淇敼 鈶㈡渶鍚庝繚瀛?
        )
        self.memory_help.setWordWrap(True)
        self.memory_help.setObjectName("memoryHelp")
        memory_layout.addWidget(self.memory_help)

        guide_row = QHBoxLayout()
        self.btn_memory_guide = QPushButton("鎿嶄綔鎸囧崡")
        self.btn_memory_guide.clicked.connect(self._show_memory_guide)
        guide_row.addWidget(self.btn_memory_guide)
        self.btn_memory_template = QPushButton("濉叆绀轰緥JSON")
        self.btn_memory_template.clicked.connect(self._apply_memory_json_template)
        guide_row.addWidget(self.btn_memory_template)
        memory_layout.addLayout(guide_row)

        self.label_summary = QLabel("鐭憳瑕?summary锛?鍙ワ紝璁板綍鏈€杩戠姸鎬侊級")
        memory_layout.addWidget(self.label_summary)
        self.memory_summary = QTextEdit()
        self.memory_summary.setPlaceholderText("summary锛氱煭鎽樿锛堝缓璁?鍙ワ級")
        self.memory_summary.setMinimumHeight(40)
        self.memory_summary.setMaximumHeight(64)
        memory_layout.addWidget(self.memory_summary)

        self.label_summary_long = QLabel("闀挎憳瑕?summary_long锛堥暱鏈熷亸濂?鐩爣/杈圭晫锛?)
        memory_layout.addWidget(self.label_summary_long)
        self.memory_summary_long = QTextEdit()
        self.memory_summary_long.setPlaceholderText("summary_long锛氶暱鎽樿锛堥暱鏈熷亸濂?杩戞湡鐩爣/杈圭晫锛?)
        self.memory_summary_long.setMinimumHeight(54)
        self.memory_summary_long.setMaximumHeight(82)
        memory_layout.addWidget(self.memory_summary_long)

        self.label_prefs = QLabel("鍋忓ソ preferences JSON锛堝繀椤绘槸鍚堟硶 JSON锛?)
        memory_layout.addWidget(self.label_prefs)
        self.memory_prefs = QTextEdit()
        self.memory_prefs.setPlaceholderText("preferences JSON锛堣淇濇寔鍚堟硶JSON鏍煎紡锛?)
        self.memory_prefs.setMinimumHeight(54)
        self.memory_prefs.setMaximumHeight(90)
        memory_layout.addWidget(self.memory_prefs)

        self.label_turns = QLabel("鏈€杩戝璇濆洖鍚堬紙鍙寜 ID 鍒犻櫎鍗曟潯锛?)
        memory_layout.addWidget(self.label_turns)
        self.memory_turns = QTextEdit()
        self.memory_turns.setPlaceholderText("recent conversation turns")
        self.memory_turns.setReadOnly(True)
        self.memory_turns.setMinimumHeight(64)
        self.memory_turns.setMaximumHeight(100)
        memory_layout.addWidget(self.memory_turns)

        turn_del_row = QHBoxLayout()
        self.turn_id_input = QLineEdit()
        self.turn_id_input.setPlaceholderText("杈撳叆鍥炲悎ID鍒犻櫎锛屽 123")
        turn_del_row.addWidget(self.turn_id_input)
        self.btn_turn_delete = QPushButton("鍒犻櫎鍥炲悎")
        self.btn_turn_delete.clicked.connect(self._delete_turn_by_id)
        turn_del_row.addWidget(self.btn_turn_delete)
        memory_layout.addLayout(turn_del_row)

        memory_btns = QHBoxLayout()
        self.btn_memory_refresh = QPushButton("鍒锋柊璁板繂")
        self.btn_memory_refresh.clicked.connect(lambda: self._refresh_memory(silent=False))
        memory_btns.addWidget(self.btn_memory_refresh)
        self.btn_memory_save = QPushButton("淇濆瓨璁板繂")
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
            QLineEdit, QPlainTextEdit {
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

    def _target_avatar_size(self) -> QSize:
        """鏍规嵁褰撳墠澶村儚鍖哄煙锛岀粰鍑烘洿绋冲Ε鐨勯珮璐ㄩ噺缂╂斁鐩爣灏哄銆?"""
        if getattr(self, "image_label", None) is None:
            return QSize(220, 260)
        w = max(self.image_label.minimumWidth(), self.image_label.width(), 150)
        h = max(self.image_label.minimumHeight(), self.image_label.height(), 180)
        return QSize(min(w, self.image_label.maximumWidth()), min(h, self.image_label.maximumHeight()))

    def _set_avatar_pixmap_high_quality(self, pix):
        if pix is None or pix.isNull():
            return
        target = self._target_avatar_size()
        scaled = pix.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)

    def _update_avatar_frame(self, *_args):
        if not hasattr(self, "movie") or self.movie is None:
            return
        pix = self.movie.currentPixmap()
        if pix.isNull():
            return
        self._set_avatar_pixmap_high_quality(pix)

    def _setup_avatar(self):
        # 浼樺厛浣跨敤 GIF锛涗笉鐩存帴璁?QMovie 鍦?QLabel 鍐呴儴缂╂斁锛?
        # 鑰屾槸閫愬抚鍙栧嚭鍚庣敤 SmoothTransformation 缂╂斁锛岀敾璐ㄦ洿绋炽€?
        self.movie = None
        self._avatar_source_size = None

        if GIF_PATH.exists():
            self.movie = QMovie(str(GIF_PATH))
            self.movie.setCacheMode(QMovie.CacheMode.CacheAll)
            self.movie.setBackgroundColor(Qt.GlobalColor.transparent)

            reader = QImageReader(str(GIF_PATH))
            src_size = reader.size()
            if src_size.isValid():
                self._avatar_source_size = src_size

            self.movie.frameChanged.connect(self._update_avatar_frame)
            self.movie.start()
            self._update_avatar_frame()
            return

        # Fallback to PNG if GIF is missing
        from PyQt6.QtGui import QPixmap

        pix = QPixmap(str(PNG_FALLBACK_PATH))
        if not pix.isNull():
            self._set_avatar_pixmap_high_quality(pix)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "movie") and self.movie is not None:
            self._update_avatar_frame()
        try:
            if getattr(self, "_command_effect", None) is not None and self.chat_mode == "creative":
                self._play_overlay_effect(
                    self._command_effect,
                    "鍒涗綔妯″紡",
                    self._command_effect_w,
                    self._command_effect_h,
                    self._command_effect_margin_top,
                )
            if getattr(self, "_inspiration_effect", None) is not None and self._inspiration_effect.isVisible():
                self._show_overlay_text_static(
                    self._inspiration_effect,
                    getattr(self, "_last_inspiration_overlay_text", ""),
                    self._inspiration_effect_w,
                    self._inspiration_effect_h,
                    self._inspiration_effect_margin_top,
                    stack_above_command=(self.chat_mode == "creative"),
                )
        except Exception:
            pass

    def _load_or_create_user_id(self) -> str:
        if USER_ID_FILE.exists():
            value = USER_ID_FILE.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = str(uuid.uuid4())
        USER_ID_FILE.write_text(value, encoding="utf-8")
        return value

    def _restore_bubble_main_text(self):
        self.bubble_text.setPlainText(self._bubble_main_text)

    def _set_bubble_text(self, text: str, kind: str = "reply", restore_after_ms: int = 2200):
        clean_text = (text or "").strip()

        if kind == "status":
            self.bubble_text.setPlainText(clean_text)
            if self._bubble_main_text:
                self._bubble_restore_timer.start(max(250, int(restore_after_ms)))
            return

        self._bubble_restore_timer.stop()

        if kind == "reply":
            self.latest_reply = clean_text

        # reply/thinking become the new main bubble content until another reply arrives.
        self._bubble_main_text = clean_text
        self.bubble_text.setPlainText(clean_text)

    def _set_status_text(self, text: str):
        self._set_bubble_text(text, kind="status")

    def _next_request_token(self) -> int:
        self._request_serial += 1
        return self._request_serial

    def _is_chat_request_current(self, token: int) -> bool:
        return bool(token) and self._chat_request_interruptible and token == self._active_chat_request_token

    def _begin_chat_request(self) -> int:
        token = self._next_request_token()
        self._active_chat_request_token = token
        self._chat_request_interruptible = True
        self._stream_tts_buffer = ""
        stop_speaking()
        set_speech_token(token)
        return token

    def _finish_chat_request(self, token: int):
        if token == self._active_chat_request_token:
            self._chat_request_interruptible = False
            self._keep_input_enabled_while_busy = False

    def _interrupt_active_chat_request(self):
        if not self._chat_request_interruptible:
            return False
        token = self._next_request_token()
        self._active_chat_request_token = token
        self._chat_request_interruptible = False
        self._stream_tts_buffer = ""
        self._keep_input_enabled_while_busy = False
        stop_speaking()
        set_speech_token(token)
        self._set_busy(False)
        return True

    def _set_busy(self, busy: bool, thinking_text: str | None = "VIVY 鎬濊€冧腑..."):
        self._busy = busy
        self.btn_inspiration.setDisabled(busy)
        if hasattr(self, "btn_improv_sketch"):
            self.btn_improv_sketch.setDisabled(busy)
        self.btn_question.setDisabled(busy)
        keep_input_enabled = busy and self._keep_input_enabled_while_busy
        self.btn_send.setDisabled(busy and not keep_input_enabled)
        self.input_edit.setDisabled(busy and not keep_input_enabled)
        if hasattr(self, "btn_voice"):
            self.btn_voice.setDisabled(busy)
        if hasattr(self, "btn_voice_toggle"):
            self.btn_voice_toggle.setDisabled(False)
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
            self._keep_input_enabled_while_busy = False
            self._touch()

    def _run_async(
        self,
        fn,
        on_success,
        on_error,
        on_progress=None,
        thinking_text: str | None = "VIVY 鎬濊€冧腑...",
        stale_check=None,
        keep_input_enabled: bool = False,
    ):
        if self._busy:
            self._set_status_text("褰撳墠浠嶅湪澶勭悊涓婁竴璇锋眰锛岃绋嶅€欏啀璇曘€?)
            return
        self._touch()
        self._keep_input_enabled_while_busy = bool(keep_input_enabled)
        self._set_busy(True, thinking_text=thinking_text)
        worker = RequestWorker(fn)
        worker.signals.finished.connect(
            lambda data: None if stale_check and stale_check() else self._on_worker_success(data, on_success)
        )
        worker.signals.error.connect(
            lambda err: None if stale_check and stale_check() else self._on_worker_error(err, on_error)
        )
        if on_progress is not None:
            worker.signals.progress.connect(
                lambda payload: None if stale_check and stale_check() else on_progress(payload)
            )
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


    def _safe_generate_inspiration(self) -> str:
        max_chars = max(28, int(os.getenv("VIVY_INSP_MAX_CHARS", "56")))
        try:
            if generate_inspiration is not None:
                val = (generate_inspiration() or "").strip()
                if val:
                    return val
            if generate_inspiration_short is not None:
                val = (generate_inspiration_short(max_chars=max_chars) or "").strip()
                if val:
                    return val
        except Exception:
            pass
        return random.choice(_FALLBACK_INSPIRATION_POOL)

    def _play_overlay_effect(self, effect, text: str, width: int, height: int, margin_top: int, stack_above_command: bool = False):
        if effect is None or self._idle_collapsed:
            return
        try:
            if hasattr(effect, "target_text"):
                effect.target_text = text
            elif hasattr(effect, "set_target_text"):
                effect.set_target_text(text)

            anchor = self.image_label
            dx = max(0, (anchor.width() - width) // 2)
            dy = -height - max(0, margin_top)

            if stack_above_command and getattr(self, "_command_effect", None) is not None:
                ce = self._command_effect
                try:
                    if ce.isVisible():
                        dy = -(int(getattr(ce, "height")()) + max(0, self._command_effect_margin_top) + 8 + height)
                except Exception:
                    pass

            if hasattr(effect, "follow_master"):
                effect.follow_master(anchor, dx, dy, width, height)
            if hasattr(effect, "start_effect"):
                effect.start_effect()
        except Exception:
            pass

    def _show_overlay_text_static(self, effect, text: str, width: int, height: int, margin_top: int, stack_above_command: bool = False):
        if effect is None or self._idle_collapsed:
            return
        clean_text = (text or "").strip()
        if not clean_text:
            return
        try:
            if hasattr(effect, "set_target_text"):
                effect.set_target_text(clean_text)
            elif hasattr(effect, "target_text"):
                effect.target_text = clean_text

            actual_w, actual_h = int(width), int(height)
            if hasattr(effect, "fit_to_text"):
                try:
                    actual_w, actual_h = effect.fit_to_text(
                        max_text_width=max(int(width), 420),
                        max_outer_height=max(int(height), 180),
                        min_outer_width=max(220, min(int(width), 280)),
                    )
                except Exception:
                    actual_w, actual_h = int(width), int(height)

            anchor = self.image_label
            dx = max(0, (anchor.width() - actual_w) // 2)
            dy = -actual_h - max(0, int(margin_top))

            if stack_above_command and getattr(self, "_command_effect", None) is not None:
                ce = self._command_effect
                try:
                    if ce.isVisible():
                        dy = -(int(getattr(ce, "height")()) + max(0, self._command_effect_margin_top) + 8 + actual_h)
                except Exception:
                    pass

            if hasattr(effect, "set_offset_from_master"):
                effect.set_offset_from_master(anchor, dx, dy)
            elif hasattr(effect, "follow_master"):
                effect.follow_master(anchor, dx, dy, actual_w, actual_h)

            if hasattr(effect, "_timer"):
                effect._timer.stop()
            if hasattr(effect, "_scan_timer"):
                try:
                    effect._scan_timer.start()
                except Exception:
                    pass
            if hasattr(effect, "_label"):
                effect._label.setText(clean_text)
            effect.show()
            effect.raise_()
        except Exception:
            pass

    def _stop_command_effect(self):
        ce = getattr(self, "_command_effect", None)
        if ce is not None and hasattr(ce, "stop_effect"):
            try:
                ce.stop_effect()
            except Exception:
                pass

    def _stop_inspiration_effect(self):
        insp = getattr(self, "_inspiration_effect", None)
        if insp is not None and hasattr(insp, "stop_effect"):
            try:
                insp.stop_effect()
            except Exception:
                pass

    def _start_command_effect_if_needed(self):
        if self.chat_mode != "creative" or self._idle_collapsed:
            return
        self._play_overlay_effect(
            self._command_effect,
            "鍒涗綔妯″紡",
            self._command_effect_w,
            self._command_effect_h,
            self._command_effect_margin_top,
        )

    def _on_today_inspiration(self):
        self._touch()
        # 缁熶竴璧板悗绔粨鏋勫寲鐏垫劅鎺ュ彛锛屼繚鎸佲€溿€愪粖鏃ュ啿娴闂汇€戝彂鐜?鑱旀兂/閭€璇封€濇牱寮忋€?
        self._send_message("浠婂ぉ鏈変粈涔堢伒鎰?)

    def _copy_latest_reply(self):
        text = self.bubble_text.textCursor().selectedText().strip() or self.latest_reply
        if text:
            QApplication.clipboard().setText(text)
            self.btn_copy_reply.setText("宸插鍒?)
            QTimer.singleShot(800, lambda: self.btn_copy_reply.setText("澶嶅埗鍥炲"))

    def _update_voice_toggle_button(self):
        if hasattr(self, "btn_voice_toggle"):
            self.btn_voice_toggle.setText(f"璇煶鍥炴斁锛歿'寮€' if self.auto_voice_reply else '鍏?}")

    def _toggle_auto_voice_reply(self):
        self._touch()
        self.auto_voice_reply = not self.auto_voice_reply
        self._update_voice_toggle_button()
        self._set_status_text(f"璇煶鍥炴斁宸瞷'寮€鍚? if self.auto_voice_reply else '鍏抽棴'}銆?)

    def _start_voice_input(self):
        self._touch()

        dlg = QDialog(self)
        dlg.setWindowTitle("语音输入")
        dlg.setModal(True)
        dlg.resize(460, 300)
        dlg.setStyleSheet(
            """
            QDialog {
                background: rgba(9, 18, 28, 238);
                border: 1px solid rgba(121, 226, 255, 160);
                border-radius: 12px;
            }
            QLabel {
                color: #f3fcff;
            }
            QTextEdit {
                background: rgba(17, 28, 40, 230);
                color: #f3fcff;
                border: 1px solid rgba(93, 204, 240, 180);
                border-radius: 10px;
                padding: 8px;
                selection-background-color: rgba(77, 216, 255, 120);
            }
            QPushButton {
                background: #2b9ec5;
                border: 1px solid #7be2ff;
                border-radius: 8px;
                color: #f3fcff;
                padding: 6px 12px;
                font-size: 13px;
            }
            QPushButton:disabled {
                background: rgba(43, 158, 197, 120);
                color: rgba(243, 252, 255, 150);
            }
            """
        )

        layout = QVBoxLayout(dlg)
        tip = QLabel("点击“开始听写”后直接说话。VIVY 会调用 Windows 自带语音听写；如果没有自动弹出，请手动按 Win+H。")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        edit = QTextEdit()
        edit.setPlaceholderText("系统听写的文字会出现在这里，你也可以手动修改后再发送。")
        layout.addWidget(edit, 1)

        button_row = QHBoxLayout()
        btn_listen = QPushButton("开始听写")
        btn_send = QPushButton("发送")
        btn_cancel = QPushButton("取消")
        button_row.addWidget(btn_listen)
        button_row.addStretch(1)
        button_row.addWidget(btn_send)
        button_row.addWidget(btn_cancel)
        layout.addLayout(button_row)

        def _sync_send_state():
            btn_send.setEnabled(bool(edit.toPlainText().strip()))

        def _start_dictation():
            dlg.raise_()
            dlg.activateWindow()
            edit.setFocus()
            cursor = edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            edit.setTextCursor(cursor)
            try:
                trigger_windows_voice_typing()
                self._set_status_text("请直接说话，系统会把听写内容输入到窗口里。")
            except Exception as e:
                self._set_status_text(f"无法启动系统语音听写：{e}")
                QMessageBox.information(
                    dlg,
                    "语音输入",
                    "没有自动弹出系统语音听写。你可以手动按 Win+H 后继续说话。",
                )

        def _accept():
            if not edit.toPlainText().strip():
                self._set_status_text("还没有收到语音输入内容。")
                return
            dlg.accept()

        btn_listen.clicked.connect(_start_dictation)
        btn_send.clicked.connect(_accept)
        btn_cancel.clicked.connect(dlg.reject)
        edit.textChanged.connect(_sync_send_state)
        _sync_send_state()
        QTimer.singleShot(150, _start_dictation)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        text = edit.toPlainText().strip()
        if not text:
            return
        self.input_edit.setPlainText(text)
        self.input_edit.setFocus()
        self._send_from_input()

    def _maybe_speak_reply(self, text: str, token: int | None = None):
        spoken = (text or '').strip()
        if not spoken or not self.auto_voice_reply:
            return
        speak_text_async(spoken, token=token)

    def _split_stream_tts_units(self, buffer_text: str, flush: bool = False):
        text = buffer_text or ''
        if not text:
            return [], ''
        strong_seps = '銆傦紒锛??\n'
        out = []
        start = 0
        i = 0
        while i < len(text):
            ch = text[i]
            if ch in strong_seps:
                end = i + 1
                while end < len(text) and text[end] in '鈥濄€嶃€忥級銆?':
                    end += 1
                unit = text[start:end].strip()
                if unit:
                    out.append(unit)
                start = end
            i += 1
        remain = text[start:].strip()
        if flush and remain:
            out.append(remain)
            remain = ''
        return out, remain

    def _on_stream_tts_progress(self, payload, token: int | None = None):
        if token is not None and not self._is_chat_request_current(token):
            return
        if isinstance(payload, dict):
            partial = str(payload.get('partial') or '')
            delta = str(payload.get('delta') or '')
        else:
            partial = str(payload or '')
            delta = ''
        if partial:
            self._set_bubble_text(partial)
        if not (self.auto_voice_reply and self.stream_tts_enabled and delta):
            return
        self._stream_tts_buffer += delta
        units, remain = self._split_stream_tts_units(self._stream_tts_buffer, flush=False)
        self._stream_tts_buffer = remain
        for unit in units:
            self._maybe_speak_reply(unit, token=token)

    def _spoken_text_from_messages(self, messages):
        for msg in messages or []:
            mtype = msg.get('type')
            if mtype == 'chat':
                return (msg.get('text') or '').strip()
            if mtype == 'inspiration':
                return (
                    f"浠婃棩鍐叉氮瑙侀椈銆傚彂鐜帮細{msg.get('discovery', '')}銆?
                    f"鑱旀兂锛歿msg.get('vivy_association', '')}銆?
                    f"{msg.get('invitation_question', '')}"
                ).strip()
        return ''

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
            dlg.setWindowTitle("VIVY 鎹釜闂")
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
            label = QLabel(question or "璇烽€夋嫨涓€涓洖绛旓細")
            label.setWordWrap(True)
            layout.addWidget(label)

            listw = QListWidget()
            for opt in options:
                item = QListWidgetItem(opt.get("label", "閫夐」"))
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
            btn = QPushButton(opt.get("label", "閫夐」"))
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
            self.interest_label.setText("鍏磋叮锛氭劅鍏磋叮")
        elif self.current_interest_signal == "not_interested":
            self.interest_label.setText("鍏磋叮锛氫笉鎰熷叴瓒?)
        else:
            self.interest_label.setText("鍏磋叮锛氭湭閫夋嫨")

    def _sync_avatar_dock_heights(self):
        extra = 58 if self.chat_mode == "creative" else 0
        self.avatar_dock.setMinimumHeight(230 + extra)
        self.avatar_dock.setMaximumHeight(300 + extra)
        self._apply_window_size()

    def _on_creative_domain_intro_finished(self):
        if self.chat_mode != "creative":
            return
        self.creative_actions.show()
        self.avatar_dock.updateGeometry()
        if not self._use_button_opacity_fx:
            QTimer.singleShot(0, self._start_command_effect_if_needed)
            return
        if getattr(self, "_creative_domain_intro_instant", False):
            for b in self._creative_domain_buttons:
                eff = b.graphicsEffect()
                if isinstance(eff, QGraphicsOpacityEffect):
                    eff.setOpacity(1.0)
            QTimer.singleShot(0, self._start_command_effect_if_needed)
            return
        stagger = 95
        for i, b in enumerate(self._creative_domain_buttons):
            eff = b.graphicsEffect()
            if not isinstance(eff, QGraphicsOpacityEffect):
                continue
            anim = QPropertyAnimation(eff, b"opacity", self)
            anim.setDuration(360)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutBack)
            QTimer.singleShot(50 + i * stagger, anim.start)
        QTimer.singleShot(0, self._start_command_effect_if_needed)

    def _finish_hide_creative_actions(self):
        if self.chat_mode != "creative":
            self.creative_actions.hide()

    def _hide_creative_domain_buttons(self, animated: bool):
        if not self._use_button_opacity_fx:
            self.creative_actions.hide()
            return
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
            self.btn_mode.setText("褰㈡€侊細鍒涗綔")
            self.controls_wrap.setStyleSheet(
                "#vivyChatColumn { border-left: 2px solid rgba(100, 220, 255, 120); "
                "border-radius: 10px; padding-left: 6px; background: rgba(6, 22, 36, 85); }"
            )
            self._creative_domain_intro_instant = not mode_changed
            self._sync_avatar_dock_heights()
            self.domain_aura.set_creative_active(True, animate=mode_changed)
        else:
            self.btn_mode.setText("褰㈡€侊細鏅€?)
            self.controls_wrap.setStyleSheet("")
            self._hide_creative_domain_buttons(animated=mode_changed)
            self.domain_aura.set_creative_active(False, animate=mode_changed)
            self._stop_command_effect()
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
            self._set_status_text("褰㈡€佸凡鍒囨崲銆?)

        def _err(error_msg):
            self._set_status_text(f"鍒囨崲褰㈡€佸け璐ワ細{error_msg}")

        self._run_async(_request, _ok, _err, thinking_text="姝ｅ湪鍒囨崲褰㈡€?..")

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
                self._set_status_text("璁板繂妯″潡宸插埛鏂般€?)

        def _err(error_msg):
            if not silent:
                self._set_status_text(f"璇诲彇璁板繂澶辫触锛歿error_msg}")

        if silent:
            # silent refresh should never disable the UI
            self._run_background(_request, on_success=_ok, on_error=_err)
        else:
            self._run_async(_request, _ok, _err, thinking_text="姝ｅ湪璇诲彇璁板繂...")

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
            self._set_status_text("璁板繂妯″潡宸蹭繚瀛樸€?)

        def _err(error_msg):
            self._set_status_text(f"淇濆瓨璁板繂澶辫触锛歿error_msg}")

        self._run_async(_request, _ok, _err, thinking_text="姝ｅ湪淇濆瓨璁板繂...")

    def _show_memory_guide(self):
        self._touch()
        text = (
            "鏈€绠€鍗曠殑浣跨敤娴佺▼锛歕n\n"
            "1) 鐐瑰嚮鈥滃埛鏂拌蹇嗏€漒n"
            "   鍏堟媺鍙栨暟鎹簱閲岀殑鏈€鏂板唴瀹癸紝閬垮厤瑕嗙洊鏃ф暟鎹€俓n\n"
            "2) 鎸夐渶淇敼\n"
            "   - summary锛氬啓 1 鍙ユ渶杩戠姸鎬乗n"
            "   - summary_long锛氬啓闀挎湡鍋忓ソ/鐩爣/杈圭晫\n"
            "   - preferences JSON锛氬繀椤讳繚鎸佸悎娉?JSON\n\n"
            "3) 鐐瑰嚮鈥滀繚瀛樿蹇嗏€漒n"
            "   淇濆瓨鍚?VIVY 鍚庣画瀵硅瘽浼氭寜鏂拌蹇嗗伐浣溿€俓n\n"
            "4) 绠＄悊鍘嗗彶鍥炲悎\n"
            "   鍦ㄢ€滄渶杩戝璇濆洖鍚堚€濋噷鐪?[ID]锛岃緭鍏?ID 鍚庣偣鈥滃垹闄ゅ洖鍚堚€濄€?
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("璁板繂妯″潡鎿嶄綔鎸囧崡")
        box.setText(text)
        # On translucent/dark UI, the default QMessageBox theme can make text unreadable.
        box.setStyleSheet(
            """
            QMessageBox {
                background: rgba(10, 18, 26, 245);
                color: #eaf8ff;
            }
            QMessageBox QLabel {
                color: #eaf8ff;
                font-size: 12px;
            }
            QMessageBox QPushButton {
                background: rgba(39, 160, 209, 210);
                border: 1px solid rgba(121, 228, 255, 180);
                border-radius: 8px;
                padding: 5px 10px;
                color: #f3fcff;
            }
            QMessageBox QPushButton:hover {
                background: rgba(57, 186, 241, 230);
            }
            """
        )
        box.exec()

    def _apply_memory_json_template(self):
        self._touch()
        template = {
            "chat_mode": "chat",
            "topic_bias": "鍒涗綔",
            "humor_level": "涓?,
            "comfort_style": "闄即",
            "last_interest_signal": "interested"
        }
        import json

        self.memory_prefs.setPlainText(json.dumps(template, ensure_ascii=False, indent=2))
        self._set_status_text("宸插～鍏ョず渚?JSON锛屽彲鎸夐渶淇敼鍚庝繚瀛樸€?)

    def _delete_turn_by_id(self):
        self._touch()
        text = (self.turn_id_input.text() or "").strip()
        if not text:
            self._set_status_text("璇疯緭鍏ヨ鍒犻櫎鐨勫洖鍚圛D銆?)
            return
        try:
            turn_id = int(text)
        except Exception:
            self._set_status_text("鍥炲悎ID蹇呴』鏄暟瀛椼€?)
            return

        def _request():
            return self._request_json(
                "/api/memory/delete_turn",
                {"user_id": self.user_id, "turn_id": turn_id},
            )

        def _ok(_data):
            self.turn_id_input.clear()
            self._set_status_text(f"宸插垹闄ゅ洖鍚?{turn_id}銆?)
            self._refresh_memory(silent=True)

        def _err(error_msg):
            self._set_status_text(f"鍒犻櫎澶辫触锛歿error_msg}")

        self._run_async(_request, _ok, _err, thinking_text="姝ｅ湪鍒犻櫎鍥炲悎...")

    def _set_api_key_interactive(self):
        self._touch()
        current = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
        key, ok = QInputDialog.getText(
            self,
            "閰嶇疆 DeepSeek API Key",
            "璇疯緭鍏?DeepSeek API Key锛堜細淇濆瓨鍒?.env锛夛細",
            QLineEdit.EchoMode.Password,
            current,
        )
        if not ok:
            return

        new_key = (key or "").strip()
        if not new_key:
            QMessageBox.warning(self, "鎻愮ず", "API Key 涓嶈兘涓虹┖銆?)
            return

        _save_env_value("DEEPSEEK_API_KEY", new_key)
        self._set_status_text("API Key 宸蹭繚瀛橈紝鍚庣画璇锋眰灏嗕娇鐢ㄦ柊閰嶇疆銆?)

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
                    msg.get("question", "鎴戞兂鏇翠簡瑙ｄ綘涓€鐐广€?),
                    msg.get("options", []),
                )
            elif mtype == "inspiration":
                self._hide_options()
                text = (
                    "銆愪粖鏃ュ啿娴闂汇€慭n"
                    f"鍙戠幇锛歿msg.get('discovery', '')}\n"
                    f"鑱旀兂锛歿msg.get('vivy_association', '')}\n"
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
            self._set_status_text(f"鍚姩澶辫触锛歿error_msg}")

        self._run_async(_request, _ok, _err, thinking_text="VIVY 姝ｅ湪鎺ュ叆鏃堕棿绾?..")

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
            self._set_status_text(f"璁板綍鍋忓ソ澶辫触锛歿error_msg}")

        self._run_async(_request, _ok, _err, thinking_text="VIVY 姝ｅ湪璁颁綇浣犵殑鍋忓ソ...")

    def _is_sing_request(self, text: str) -> bool:
        t = (text or "").strip().lower()
        if not t:
            return False
        return any(k in t for k in SING_TRIGGER_KEYWORDS)


    def _list_song_files(self) -> list[Path]:
        if not SONG_DIR.is_dir():
            return []
        songs = []
        for p in SONG_DIR.iterdir():
            if p.is_file() and p.suffix.lower() in SONG_SUFFIXES:
                songs.append(p)
        return songs


    def _stop_song(self):
        try:
            if self._song_delay_timer.isActive():
                self._song_delay_timer.stop()
        except Exception:
            pass

        self._pending_song_path = None

        try:
            import winsound

            winsound.PlaySound(None, 0)
        except Exception:
            pass
        self._song_is_playing = False
        try:
            self._set_status_text("宸插仠姝㈠敱姝屻€?)
        except Exception:
            pass


    def _estimate_tts_wait_ms(self, text: str) -> int:
        clean = (text or "").strip()
        if not clean:
            return 900

        ms = 850 + len(clean) * 170
        ms += 350
        return max(1400, min(ms, 5000))


    def _play_pending_song_now(self):
        song = self._pending_song_path
        self._pending_song_path = None
        if not song:
            return

        try:
            import winsound

            winsound.PlaySound(str(song.resolve()), winsound.SND_FILENAME | winsound.SND_ASYNC)
            self._song_is_playing = True
            self._set_status_text(f"姝ｅ湪鎾斁锛歿song.name}")
        except Exception as e:
            self._song_is_playing = False
            self._set_status_text(f"鎾斁姝屾洸澶辫触锛歿e}")
            self._set_bubble_text("鎶辨瓑锛岃繖棣栨瓕鐜板湪娌¤兘椤哄埄鎾斁銆傝纭姝屾洸鏄?WAV 鏍煎紡銆?)


    def _make_song_intro_text(self, request_text: str) -> str:
        t = (request_text or "").strip().lower()
        if any(k in t for k in ("闅捐繃", "浼ゅ績", "浣庤惤", "蹇冩儏涓嶅ソ", "瀹夋叞", "鍝勬垜")):
            return "鍒€ワ紝鎴戝湪銆傛垜缁欎綘鍞变竴棣栨瓕锛屽ソ鍚楋紵"
        if any(k in t for k in ("娣卞", "鏅氫笂", "澶滈噷", "鐫′笉鐫€", "澶辩湢")):
            return "杩樻病鐫″悧銆傞偅灏辫璁╂垜缁欎綘鍞变竴棣栨瓕鍚с€?
        if any(k in t for k in ("鍞辨瓕", "鍞遍姝?, "鍞变竴棣?, "缁欐垜鍞?, "浣犲敱")):
            return "鎴戝湪銆傞偅灏辫鎴戠粰鎮ㄥ敱涓€棣栨瓕鍚с€?
        return "鍡紝鎴戠粰浣犲敱涓€棣栥€傛瓕"


    def _speak_then_play_song(self, song: Path, intro_text: str):
        self._set_bubble_text(intro_text)
        self._set_status_text(f"宸查€変腑姝屾洸锛歿song.name}")

        if self.auto_voice_reply:
            self._maybe_speak_reply(intro_text)
            self._pending_song_path = song
            wait_ms = self._estimate_tts_wait_ms(intro_text)
            self._song_delay_timer.start(wait_ms)
        else:
            self._pending_song_path = song
            self._play_pending_song_now()


    def _play_random_song(self, request_text: str = "") -> bool:
        songs = self._list_song_files()
        if not songs:
            self._set_bubble_text("鎴戞兂鍞辩粰浣犲惉锛屼絾褰撳墠姝屽崟鐩綍閲屾病鏈夊彲鎾斁鐨?WAV 姝屾洸鏂囦欢銆?)
            self._set_status_text(f"鏈壘鍒?WAV 姝屾洸锛歿SONG_DIR}")
            return False

        song = random.choice(songs)
        intro_text = self._make_song_intro_text(request_text)

        try:
            self._stop_song()
            self._speak_then_play_song(song, intro_text)
            return True
        except Exception as e:
            self._song_is_playing = False
            self._set_status_text(f"鎾斁姝屾洸澶辫触锛歿e}")
            self._set_bubble_text("鎶辨瓑锛岃繖棣栨瓕鐜板湪娌¤兘椤哄埄鎾斁銆傝纭姝屾洸鏄?WAV 鏍煎紡銆?)
            return False


    def _try_handle_local_song_request(self, text: str) -> bool:
        t = (text or "").strip().lower()

        if any(k in t for k in STOP_SONG_TRIGGER_KEYWORDS):
            self._stop_song()
            self._set_bubble_text("濂斤紝鎴戝厛鍋滀笅銆?)
            return True

        if not self._is_sing_request(text):
            return False

        self._play_random_song(text)
        return True


    def _send_from_input(self):
        self._touch()
        text = (self.input_edit.toPlainText() or "").strip()
        if not text:
            return
        self.input_edit.setPlainText("")
        self._send_message(text)

    def _pick_chat_image(self):
        self._touch()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "閫夋嫨鍥剧墖",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All Files (*)",
        )
        if not path:
            return
        p = Path(path)
        if not p.exists() or not p.is_file():
            self._set_status_text("閫夋嫨鍥剧墖澶辫触锛氭枃浠朵笉瀛樺湪銆?)
            return
        max_bytes = int(os.getenv("VIVY_IMAGE_MAX_BYTES", "3000000"))
        try:
            size = p.stat().st_size
        except Exception:
            size = 0
        if size and size > max_bytes:
            self._set_status_text(f"鍥剧墖澶ぇ锛歿size} bytes锛堜笂闄?{max_bytes}锛夈€?)
            return

        self._chat_image_path = str(p)
        self.btn_image.setText("馃柤鉁?)
        self.btn_image_clear.setEnabled(True)
        self._set_status_text(f"宸查€夋嫨鍥剧墖锛歿p.name}")

    def _clear_chat_image(self, silent: bool = False):
        self._chat_image_path = None
        if hasattr(self, "btn_image"):
            self.btn_image.setText("馃柤")
        if hasattr(self, "btn_image_clear"):
            self.btn_image_clear.setEnabled(False)
        if not silent:
            self._set_status_text("宸叉竻闄ゅ浘鐗囥€?)

    def _build_chat_image_payload(self) -> dict | None:
        path = getattr(self, "_chat_image_path", None)
        if not path:
            return None
        p = Path(path)
        if not p.exists() or not p.is_file():
            return None

        max_bytes = int(os.getenv("VIVY_IMAGE_MAX_BYTES", "3000000"))
        try:
            raw = p.read_bytes()
        except Exception:
            return None
        if not raw or len(raw) > max_bytes:
            return None

        mime, _enc = mimetypes.guess_type(str(p))
        mime = (mime or "image/png").lower()
        if not mime.startswith("image/"):
            mime = "image/png"
        b64 = base64.b64encode(raw).decode("ascii")
        return {"data": b64, "mime": mime}

    def _send_message(self, text: str):
        self._touch()

        if self._try_handle_local_song_request(text):
            return

        if self._busy:
            if self._chat_request_interruptible:
                self._interrupt_active_chat_request()
            else:
                self._set_status_text("褰撳墠浠嶅湪澶勭悊涓婁竴璇锋眰锛岃绋嶅€欏啀璇曘€?)
                return

        request_token = self._begin_chat_request()
        is_stale = lambda: not self._is_chat_request_current(request_token)

        # Commands that require structured messages should use non-stream endpoint.
        lower = (text or "").lower()
        has_image = bool(getattr(self, "_chat_image_path", None))
        is_structured = has_image or ("鎹釜闂" in text) or ("浜嗚В鎴? in text) or ("鐏垫劅" in text) or ("鍐叉氮" in lower)

        if is_structured:
            def _request():
                if is_stale():
                    raise RequestCancelled("cancelled")
                payload = {
                    "user_id": self.user_id,
                    "message": text,
                    "interest_signal": self.current_interest_signal or None,
                    "chat_mode": self.chat_mode,
                }
                if has_image:
                    img_payload = self._build_chat_image_payload()
                    if img_payload:
                        payload["image"] = img_payload
                return self._request_json(
                    "/api/message",
                    payload,
                    timeout=25,
                )

            def _ok(data):
                self._finish_chat_request(request_token)
                messages = data.get("messages", [])
                self._handle_messages(messages)
                spoken = self._spoken_text_from_messages(messages)
                if spoken:
                    self._maybe_speak_reply(spoken, token=request_token)
                self._refresh_memory(silent=True)
                if has_image:
                    self._clear_chat_image(silent=True)

            def _err(error_msg):
                self._finish_chat_request(request_token)
                self._set_status_text(f"璇锋眰澶辫触锛歿error_msg}")

            self._run_async(
                _request,
                _ok,
                _err,
                thinking_text="VIVY 鎬濊€冧腑...",
                stale_check=is_stale,
                keep_input_enabled=True,
            )
            return

        def _request_stream():
            if is_stale():
                raise RequestCancelled("cancelled")
            url = f"{self.api_base}/api/message_stream"
            payload = {
                "user_id": self.user_id,
                "message": text,
                "interest_signal": self.current_interest_signal or None,
                "chat_mode": self.chat_mode,
            }
            with requests.post(url, json=payload, timeout=30, stream=True) as resp:
                resp.raise_for_status()

                import json

                assembled = ""
                self._stream_tts_buffer = ""
                for raw in resp.iter_lines(decode_unicode=True):
                    if is_stale():
                        raise RequestCancelled("cancelled")
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
                        # 杩斿洖鈥滃綋鍓嶇疮璁℃枃鏈€濆拰鈥滄湰娆″閲忊€濓紝鐢ㄤ簬 UI 瀹炴椂鍒锋柊鍜屾祦寮忔挱鎶?                        yield {"partial": assembled, "delta": delta}

            return assembled

        def _ok_stream(data):
            self._finish_chat_request(request_token)
            reply_text = ((data or {}).get("text") or "").strip()
            if self.auto_voice_reply:
                if self.stream_tts_enabled:
                    units, _remain = self._split_stream_tts_units(self._stream_tts_buffer, flush=True)
                    self._stream_tts_buffer = ""
                    for unit in units:
                        self._maybe_speak_reply(unit, token=request_token)
                elif reply_text:
                    self._maybe_speak_reply(reply_text, token=request_token)
            self._refresh_memory(silent=True)

        def _err_stream(error_msg):
            self._finish_chat_request(request_token)
            self._stream_tts_buffer = ""
            self._set_status_text(f"璇锋眰澶辫触锛歿error_msg}")

        def _consume(progress_callback=None):
            final_text = ""
            for payload in _request_stream():
                if is_stale():
                    raise RequestCancelled("cancelled")
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
            on_progress=lambda payload: self._on_stream_tts_progress(payload, token=request_token),
            thinking_text="VIVY 鎬濊€冧腑锛堟祦寮忚緭鍑猴級...",
            stale_check=is_stale,
            keep_input_enabled=True,
        )

    def _touch(self):
        self._last_interaction_ts = time.time()

    def _avatar_anchor_global(self) -> QPoint:
        """鍙栦汉鐗╁浘鐗囧簳閮ㄤ腑鐐逛綔涓哄睆骞曢敋鐐广€?"""
        if getattr(self, "image_label", None) is None:
            return self.mapToGlobal(self.rect().center())

        local_pt = QPoint(self.image_label.width() // 2, self.image_label.height())
        return self.image_label.mapToGlobal(local_pt)

    def _move_window_to_keep_anchor(self, old_anchor: QPoint):
        """绐楀彛灏哄/甯冨眬鍙樺寲鍚庯紝鏍℃浣嶇疆锛屼繚鎸佷汉鐗╅敋鐐逛笉婕傜Щ銆?"""
        new_anchor = self._avatar_anchor_global()
        dx = old_anchor.x() - new_anchor.x()
        dy = old_anchor.y() - new_anchor.y()
        if dx or dy:
            self.move(self.x() + dx, self.y() + dy)

    def _set_idle_collapsed(self, collapsed: bool):
        if collapsed == self._idle_collapsed:
            return

        old_anchor = self._avatar_anchor_global()

        self._idle_collapsed = collapsed
        self.controls_wrap.setVisible(not collapsed)

        # 搴曢儴鍒涗綔棰嗗煙鎸夐挳鎸傚湪 avatar_dock 涓嬶紝涓嶅睘浜?controls_wrap锛?
        # 鏀惰捣鏃跺繀椤婚澶栭殣钘忥紝鍚﹀垯浼氭畫鐣欏湪浜虹墿澶栦晶銆?
        if hasattr(self, "creative_actions"):
            if collapsed:
                self.creative_actions.hide()
            else:
                if self.chat_mode == "creative":
                    self.creative_actions.show()
                else:
                    self.creative_actions.hide()

        if collapsed:
            self.options_wrap.hide()
            self.memory_wrap.hide()

        self._apply_window_size()
        QApplication.processEvents()
        self._move_window_to_keep_anchor(old_anchor)

    def _apply_window_size(self):
        if self._idle_collapsed:
            size = self._collapsed_size
        else:
            mw = getattr(self, "memory_wrap", None)
            memory_visible = mw is not None and mw.isVisible()
            size = self._expanded_size_with_memory if memory_visible else self._expanded_size
        self.setMinimumSize(size)
        self.setMaximumSize(size)
        self.resize(size)

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
        # Any key/mouse interaction in input should prevent idle collapse.
        try:
            if obj is self.input_edit:
                et = event.type()
                # Enter to send, Shift+Enter to newline (for multiline input).
                if et == event.Type.KeyPress:
                    try:
                        key = event.key()
                        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                            mods = event.modifiers()
                            if mods & Qt.KeyboardModifier.ShiftModifier:
                                return False  # allow newline
                            self._send_from_input()
                            return True  # consume
                    except Exception:
                        pass
                # KeyPress/KeyRelease/MouseButtonPress/FocusIn all imply interaction
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
        act_reset = QAction("閲嶇疆鏈満鐢ㄦ埛ID", self)
        act_set_api_key = QAction("璁剧疆 API Key", self)
        act_toggle_memory = QAction("鏄剧ず/闅愯棌璁板繂妯″潡", self)
        act_toggle_idle = QAction("鍒囨崲寰呮満鏀惰捣", self)
        act_set_idle_timeout = QAction("璁剧疆寰呮満鏃堕暱", self)
        act_load_doc = QAction("璇诲彇鏂囨。锛堝垱浣滆緟鍔╋級", self)
        act_clear_doc = QAction("娓呴櫎宸茶鍙栨枃妗?, self)
        act_immersive = QAction("娌夋蹈鍐欎綔绐楀彛鈥?, self)
        act_stop_song = QAction("鍋滄鍞辨瓕", self)
        act_quit = QAction("閫€鍑?VIVY", self)

        act_reset.triggered.connect(self._reset_user)
        act_set_api_key.triggered.connect(self._set_api_key_interactive)
        act_toggle_memory.triggered.connect(self._toggle_memory_panel)
        act_toggle_idle.triggered.connect(lambda: self._set_idle_collapsed(not self._idle_collapsed))
        act_set_idle_timeout.triggered.connect(self._set_idle_timeout_interactive)
        act_load_doc.triggered.connect(self._load_document_for_creative)
        act_clear_doc.triggered.connect(self._clear_loaded_document)
        act_immersive.triggered.connect(self._open_immersive_writing)
        act_stop_song.triggered.connect(lambda: (self._stop_song(), self._set_bubble_text("濂斤紝鎴戝厛鍋滀笅銆?)))
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
        menu.addAction(act_stop_song)
        menu.addSeparator()
        menu.addAction(act_quit)
        menu.exec(event.globalPos())

    def _toggle_memory_panel(self):
        self.memory_wrap.setVisible(not self.memory_wrap.isVisible())
        # Keep main panel size stable regardless of memory panel visibility.
        self._apply_window_size()


    def _get_themed_int(self, title: str, label: str, value: int, minimum: int, maximum: int, step: int = 1):
        dlg = QInputDialog(self)
        dlg.setInputMode(QInputDialog.InputMode.IntInput)
        dlg.setWindowTitle(title)
        dlg.setLabelText(label)
        dlg.setIntRange(int(minimum), int(maximum))
        dlg.setIntStep(int(step))
        dlg.setIntValue(int(value))
        dlg.setOkButtonText("\u786e\u5b9a")
        dlg.setCancelButtonText("\u53d6\u6d88")
        dlg.setStyleSheet(
            """
            QInputDialog, QInputDialog QWidget {
                background: rgba(10, 18, 26, 245);
                color: #eaf8ff;
                font-size: 12px;
            }
            QInputDialog QLabel {
                color: #dff6ff;
                font-size: 12px;
                font-weight: 600;
            }
            QInputDialog QSpinBox {
                background: rgba(10, 18, 26, 220);
                border: 1px solid rgba(78, 208, 255, 180);
                border-radius: 8px;
                padding: 4px 8px;
                color: #f2fbff;
                selection-background-color: rgba(55, 214, 255, 160);
            }
            QInputDialog QSpinBox::up-button,
            QInputDialog QSpinBox::down-button {
                width: 18px;
                border: 0;
                background: rgba(39, 160, 209, 190);
                border-radius: 6px;
                margin: 2px;
            }
            QInputDialog QSpinBox::up-button:hover,
            QInputDialog QSpinBox::down-button:hover {
                background: rgba(57, 186, 241, 210);
            }
            QInputDialog QPushButton {
                background: rgba(39, 160, 209, 190);
                border: 1px solid rgba(121, 228, 255, 180);
                border-radius: 8px;
                padding: 5px 10px;
                color: #f3fcff;
                min-width: 72px;
            }
            QInputDialog QPushButton:hover {
                background: rgba(57, 186, 241, 210);
            }
            """
        )

        spin = dlg.findChild(QSpinBox)
        if spin is not None:
            line_edit = spin.lineEdit()
            if line_edit is not None:
                line_edit.setStyleSheet(
                    "background: transparent; border: 0; color: #f2fbff; selection-background-color: rgba(55, 214, 255, 160);"
                )
                line_edit.selectAll()

        ok = dlg.exec() == QDialog.DialogCode.Accepted
        return dlg.intValue(), ok


    def _set_idle_timeout_interactive(self):
        self._touch()
        value, ok = self._get_themed_int(
            "\u8bbe\u7f6e\u5f85\u673a\u65f6\u957f",
            "\u591a\u5c11\u79d2\u4e0d\u64cd\u4f5c\u540e\u8fdb\u5165\u5f85\u673a\uff1a",
            int(self._idle_timeout_s),
            5,
            3600,
            1,
        )
        if not ok:
            return

        self._idle_timeout_s = int(value)
        _save_env_value("VIVY_IDLE_TIMEOUT", str(self._idle_timeout_s))
        self._set_status_text(f"\u5f85\u673a\u65f6\u957f\u5df2\u8bbe\u7f6e\u4e3a {self._idle_timeout_s} \u79d2\u3002")


    def _load_document_for_creative(self):
        self._touch()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "閫夋嫨瑕佽鍙栫殑鏂囨。",
            str(PROJECT_DIR),
            "Documents (*.txt *.md *.docx *.pdf);;All Files (*.*)",
        )
        if not path:
            return

        goal, ok = QInputDialog.getText(
            self,
            "鍒涗綔鐩爣锛堝彲閫夛級",
            "浣犲笇鏈?VIVY 浠庤繖涓枃妗ｉ噷甯綘鍋氫粈涔堬紵锛堝彲鐣欑┖锛?,
            QLineEdit.EchoMode.Normal,
            "",
        )
        if not ok:
            return

        self._start_creative_doc_stream(path, (goal or "").strip())

    def _start_creative_doc_stream(self, path: str, goal: str = ""):
        """鑿滃崟銆岃鍙栨枃妗ｃ€嶄笌鍒涗綔褰㈡€佷笅鎷栨斁鍏辩敤锛氭祦寮忚皟鐢?creative_doc_stream銆?"""
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
            self._set_status_text(f"鏂囨。鍒涗綔杈呭姪澶辫触锛歿error_msg}")

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
            thinking_text="VIVY 姝ｅ湪闃呰鏂囨。骞剁粰鍒涗綔寤鸿锛堟祦寮忥級...",
        )

    def _clear_loaded_document(self):
        self._touch()
        self._loaded_doc_path = None
        self._set_status_text("宸叉竻闄ゅ凡璇诲彇鏂囨。銆?)

    def _open_immersive_writing(self):
        self._touch()
        if self._immersive_writing_window is None:
            self._immersive_writing_window = ImmersiveWritingWindow(self)
        self._immersive_writing_window.show()
        self._immersive_writing_window.raise_()
        self._immersive_writing_window.activateWindow()

    def _reset_user(self):
        self._touch()
        self.user_id = str(uuid.uuid4())
        USER_ID_FILE.write_text(self.user_id, encoding="utf-8")
        self._set_status_text("宸查噸缃湰鏈?user_id銆傞噸鏂板垵濮嬪寲涓€?)
        QTimer.singleShot(300, self._init_session)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._drag_started_while_collapsed = bool(self._idle_collapsed)
            self._last_interaction_ts = time.time()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            self.move(new_pos)
            self._last_interaction_ts = time.time()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def moveEvent(self, event):
        super().moveEvent(event)
        try:
            if getattr(self, "_command_effect", None) is not None and self.chat_mode == "creative":
                self._play_overlay_effect(
                    self._command_effect,
                    "鍒涗綔妯″紡",
                    self._command_effect_w,
                    self._command_effect_h,
                    self._command_effect_margin_top,
                )
            if getattr(self, "_inspiration_effect", None) is not None and self._inspiration_effect.isVisible():
                self._show_overlay_text_static(
                    self._inspiration_effect,
                    getattr(self, "_last_inspiration_overlay_text", ""),
                    self._inspiration_effect_w,
                    self._inspiration_effect_h,
                    self._inspiration_effect_margin_top,
                    stack_above_command=(self.chat_mode == "creative"),
                )
        except Exception:
            pass

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._last_interaction_ts = time.time()
            self._drag_started_while_collapsed = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

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
        "棣栨閰嶇疆 API Key",
        "妫€娴嬪埌鏈厤缃?DeepSeek API Key锛岃杈撳叆锛堜細淇濆瓨鍒?.env锛夛細",
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
        QMessageBox.information(None, "鎻愮ず", "鏈厤缃?API Key锛屾湰娆″皢浠ョ绾垮厹搴曟ā寮忚繍琛屻€傚彲鍙抽敭妗屽疇 -> 璁剧疆 API Key銆?)

    # Start embedded Flask backend
    run_flask_background(port)

    if not wait_server_ready(base_url, timeout=12):
        print("Flask 鍚庣鍚姩瓒呮椂锛岃妫€鏌ョ鍙ｅ崰鐢ㄦ垨閰嶇疆")

    pet = DesktopPet(api_base=base_url)
    pet.show()
    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()







