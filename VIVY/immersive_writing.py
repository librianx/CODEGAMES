"""VIVY 沉浸写作窗口：大屏编辑 + /api/office_passage_stream 辅助。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

import requests
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QKeySequence, QShortcut, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from creative_assist import OFFICE_CONTEXT_MAX, OFFICE_PASSAGE_MAX

if TYPE_CHECKING:
    from desktop_pet import DesktopPet

PROJECT_DIR = Path(__file__).resolve().parent
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


class ImmersiveWritingWindow(QWidget):
    """仅在 VIVY 内：大屏沉浸写作。"""

    def __init__(self, pet: "DesktopPet"):
        super().__init__(None, Qt.WindowType.Window)
        self._pet = pet
        self._current_path: Path | None = None
        self._dirty = False
        self._suppress_dirty = False

        self.setWindowTitle("VIVY 沉浸写作")
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
        self.btn_open = mk_btn("打开", self._open_file)
        self._recent_menu = QMenu(self)
        self._recent_menu.aboutToShow.connect(self._fill_recent_menu)
        self.btn_recent = QPushButton("最近")
        self.btn_recent.setObjectName("imBarBtn")
        self.btn_recent.setMenu(self._recent_menu)
        self.btn_new = mk_btn("新建", self._new_file)
        self.btn_save = mk_btn("保存", self._save_file)
        self.btn_save_as = mk_btn("另存为", self._save_as)
        self.btn_restore_bak = mk_btn("恢复备份", self._restore_autosave)
        self.btn_full = mk_btn("全屏", self._toggle_fullscreen)
        self.btn_focus = mk_btn("专注", self._toggle_focus_assist)
        self.btn_find = mk_btn("查找", self._find_in_text)
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
        self.spin_goal.setSpecialValueText("目标字数")
        self.spin_goal.setValue(0)
        self.spin_goal.setMaximumWidth(100)
        self.spin_goal.setToolTip("0=不显示目标")
        self.spin_goal.valueChanged.connect(lambda _v: self._refresh_word_count())
        bar1.addWidget(self.spin_goal)
        self.lbl_count = QLabel("0 字")
        self.lbl_count.setObjectName("imBarLbl")
        bar1.addWidget(self.lbl_count)
        self.btn_close = mk_btn("收起", self._close_safe)
        bar1.addWidget(self.btn_close)
        root.addLayout(bar1)

        bar2 = QHBoxLayout()
        bar2.setSpacing(6)
        self.btn_polish = mk_btn("润色", lambda: self._assist("polish"))
        self.btn_continue = mk_btn("续写", lambda: self._assist("continue"))
        self.btn_critique = mk_btn("点评", lambda: self._assist("critique"))
        self.btn_improve = mk_btn("加强", lambda: self._assist("improve"))
        self.btn_custom = mk_btn("自定义…", self._assist_custom)
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
            "在此专注写作…\n"
            "快捷键：Ctrl+S / O / N / F，F11 全屏，Esc 退出全屏。\n"
            "选中一段再点润色等；未选则对全文。"
        )
        self.editor.textChanged.connect(self._on_text_changed)
        ef = QFont(self.editor.font())
        ef.setPointSize(15)
        ef.setFamilies(
            ["Microsoft YaHei UI", "微软雅黑", "PingFang SC", "Source Han Sans SC", "sans-serif"]
        )
        self.editor.setFont(ef)

        self.assist = QTextEdit()
        self.assist.setObjectName("imWriteAssist")
        self.assist.setReadOnly(True)
        self.assist.setPlaceholderText("VIVY 输出。可复制或插入正文。")
        self.assist.setMinimumHeight(100)
        self.assist.setMaximumHeight(180)
        aw.addWidget(self.assist)

        assist_btns = QHBoxLayout()
        assist_btns.setSpacing(6)
        self.btn_copy_assist = mk_btn("复制输出", self._copy_assist)
        self.btn_insert_assist = mk_btn("插入到光标", self._insert_assist_at_cursor)
        self.btn_replace_assist = mk_btn("替换选区", self._replace_selection_with_assist)
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
            self.lbl_count.setText(f"{n} / {g} 字")
            if n >= g:
                self.lbl_count.setStyleSheet("color: #7fe8b0; font-weight: 600;")
            else:
                self.lbl_count.setStyleSheet("")
        else:
            self.lbl_count.setText(f"{n} 字")
            self.lbl_count.setStyleSheet("")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.btn_full.setText("全屏")
        else:
            self.showFullScreen()
            self.btn_full.setText("退出全屏")

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        r = QMessageBox.question(
            self,
            "沉浸写作",
            "有未保存的修改，确定收起窗口吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return r == QMessageBox.StandardButton.Yes

    def _confirm_discard_open(self) -> bool:
        if not self._dirty:
            return True
        r = QMessageBox.question(
            self,
            "沉浸写作",
            "未保存的修改将丢失，确定打开新文件吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return r == QMessageBox.StandardButton.Yes

    def _fill_recent_menu(self) -> None:
        self._recent_menu.clear()
        paths = _immersive_load_recent(14)
        if not paths:
            a = self._recent_menu.addAction("（暂无最近文件）")
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
        self.setWindowTitle("VIVY 沉浸写作")
        self._refresh_word_count()

    def _load_document_from_path(self, path: str) -> None:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            QMessageBox.warning(self, "沉浸写作", f"无法读取：{e}")
            return
        self._suppress_dirty = True
        self.editor.setPlainText(text)
        self._suppress_dirty = False
        self._dirty = False
        self._current_path = Path(path)
        _immersive_push_recent(path)
        self.setWindowTitle(f"VIVY 沉浸写作 — {Path(path).name}")
        self._refresh_word_count()

    def _open_file(self):
        if self._dirty and not self._confirm_discard_open():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "打开文本",
            str(PROJECT_DIR),
            "Markdown / 文本 (*.md *.txt);;All (*.*)",
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
            self.lbl_autosave.setText(time.strftime("%H:%M 备份"))
        except OSError:
            self.lbl_autosave.setText("备份失败")

    def _restore_autosave(self) -> None:
        if not IMMERSIVE_AUTOSAVE_PATH.exists():
            QMessageBox.information(self, "沉浸写作", "暂无自动备份（.vivy_immersive_autosave.md）。")
            return
        if self._dirty:
            r = QMessageBox.question(
                self,
                "沉浸写作",
                "当前内容未保存，用备份覆盖吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                return
        try:
            text = IMMERSIVE_AUTOSAVE_PATH.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            QMessageBox.warning(self, "沉浸写作", f"读取备份失败：{e}")
            return
        self._suppress_dirty = True
        self.editor.setPlainText(text)
        self._suppress_dirty = False
        self._dirty = True
        self._current_path = None
        self.setWindowTitle("VIVY 沉浸写作（从备份恢复）")
        self._refresh_word_count()
        self._pet._set_status_text("已从自动备份恢复，建议另存为正式文件。")

    def _toggle_focus_assist(self) -> None:
        self._focus_assist_hidden = not self._focus_assist_hidden
        self.assist_wrap.setVisible(not self._focus_assist_hidden)
        self.btn_focus.setText("显示辅助区" if self._focus_assist_hidden else "专注")

    def _find_in_text(self) -> None:
        needle, ok = QInputDialog.getText(self, "查找", "查找内容：")
        if not ok or not needle:
            return
        if not self.editor.find(needle):
            self.editor.moveCursor(QTextCursor.MoveOperation.Start)
            if not self.editor.find(needle):
                QMessageBox.information(self, "查找", "未找到匹配内容。")

    def _on_escape(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.btn_full.setText("全屏")

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
            "自定义指令",
            "希望 VIVY 对当前选区（若无选区则对全文）做什么？",
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
                self._pet._set_status_text("沉浸写作已保存。")
            except OSError as e:
                QMessageBox.warning(self, "沉浸写作", f"保存失败：{e}")
            return
        self._save_as()

    def _save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "另存为",
            str(PROJECT_DIR / "草稿.md"),
            "Markdown (*.md);;文本 (*.txt);;All (*.*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(self.editor.toPlainText(), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "沉浸写作", f"保存失败：{e}")
            return
        self._current_path = Path(path)
        self._dirty = False
        _immersive_push_recent(path)
        self.setWindowTitle(f"VIVY 沉浸写作 — {Path(path).name}")
        self._pet._set_status_text("沉浸写作已另存为。")

    def _assist(self, action: str, goal: str | None = None):
        pet = self._pet
        if pet._busy:
            return
        passage, context_excerpt = self._passage_and_context()
        if not passage:
            QMessageBox.information(self, "沉浸写作", "先写一些内容，或选中一段后再请求辅助。")
            return
        if len(passage) > OFFICE_PASSAGE_MAX:
            passage = passage[:OFFICE_PASSAGE_MAX]

        def _request_stream():
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
            self.assist.setPlainText(f"请求失败：{msg}")

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
            thinking_text="VIVY 沉浸写作辅助中…",
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
