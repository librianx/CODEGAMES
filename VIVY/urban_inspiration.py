"""「都市指令」风格的无序短文本生成：多词库、多模式、随机后缀，供「今日灵感」使用。

环境变量（可选）：
- VIVY_INSPIRATION_INTERVAL_MS：自动刷新间隔，毫秒，默认 30000，最小 5000。
- VIVY_INSPIRATION_PANEL_H：首次展开面板时为窗口增加的纵向高度，默认 58。
"""

from __future__ import annotations

import random
import string
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QTextEdit, QVBoxLayout, QWidget


class UrbanInspirationGenerator:
    """多词库随机组合，模式不可预测。"""

    NOUNS = (
        # 抽象/系统
        "边界",
        "索引",
        "回响",
        "回路",
        "接口",
        "备份",
        "裂口",
        "信标",
        "闸门",
        "刻度",
        "余温",
        "遗言",
        # 具体可写的物
        "旧伞",
        "空瓶",
        "纸条",
        "车票",
        "钥匙",
        "口袋表",
        "录音带",
        "玻璃渣",
        "潮湿的火柴",
        "被涂抹的照片",
        "沾着油墨的手指",
        "一盏坏掉的路灯",
        "没有寄出的信",
        "一段误删的语音",
        # 人与关系
        "陌生人",
        "搭档",
        "债主",
        "证人",
        "失踪者",
        "替身",
        "收件人",
        "告密者",
        "守门人",
        "临时家人",
    )
    VERBS = (
        # 行动/叙事驱动
        "偷走",
        "递交",
        "藏起",
        "交换",
        "跟踪",
        "误认",
        "拆开",
        "抹去",
        "修复",
        "点燃",
        "捡起",
        "删掉",
        "复制",
        "覆盖",
        "说服",
        "背叛",
        "原谅",
        "逃离",
        "返回",
        "等待",
        "追问",
        # 保留一点“系统感”
        "同步",
        "校准",
        "锁定",
    )
    ADJECTIVES = (
        "不可逆的",
        "锈蚀的",
        "潮湿的",
        "冰冷的",
        "发烫的",
        "过期的",
        "虚假的",
        "透明的",
        "刺眼的",
        "黯淡的",
        "断续的",
        "轻声的",
        "没说出口的",
        "来不及的",
    )
    CONNECTORS = (
        "直至",
        "除非",
        "因而",
        "与此同时",
        "在那之后",
        "若且唯若",
        "作为代价",
        "无需辩解",
        "在雨停之前",
        "当信号复位",
    )
    CONTEXT = (
        "雨后的站台",
        "凌晨的便利店",
        "楼梯间的回声",
        "电梯停在半层",
        "灯箱广告闪烁",
        "路口红灯太久",
        "走廊尽头的门",
        "玻璃窗的倒影",
        "手机只剩 1%",
        "一条没发出的消息",
    )
    FRAGMENTS = (
        "你以为你在救人，其实在自救",
        "把真话说成玩笑，再看谁先崩",
        "别回头，那里有人在学你走路",
        "他递来的东西很轻，却压得你喘不过气",
        "你听见自己的名字从别人口中被改写",
        "这次不要解释，直接做选择",
        "灯灭的一刻，所有人都变得诚实",
        "你欠的不是钱，是一句没说完的话",
    )

    EMOTIONS = (
        "焦躁",
        "愧疚",
        "亢奋",
        "麻木",
        "羞耻",
        "安心",
        "恐惧",
        "释然",
    )
    CAMERA = (
        "特写",
        "中景",
        "远景",
        "俯拍",
        "仰拍",
        "跟拍",
        "定格",
    )
    CONSTRAINTS = (
        "全段只用短句",
        "不要出现“我”",
        "每句话都带一个物件",
        "结尾必须反转",
        "对话里不许解释背景",
        "只写动作，不写心理",
        "只写心理，不写动作",
    )

    def __init__(self, rng: Optional[random.Random] = None):
        self._rng = rng or random.Random()

    def _pick(self, seq: tuple[str, ...]) -> str:
        return self._rng.choice(seq)

    def _suffix(self) -> str:
        r = self._rng
        roll = r.randint(0, 6)
        if roll == 0:
            return f"[0x{r.randint(0, 0xFFFF):04X}]"
        if roll == 1:
            return f"_{r.randint(0, 999):03d}"
        if roll == 2:
            return f" §{r.choice('△□◇')}"
        if roll == 3:
            return f"::{r.choice('ABGHXZ')}{r.randint(10, 99)}"
        if roll == 4:
            bits = "".join(r.choice(string.ascii_uppercase + string.digits) for _ in range(4))
            return f" [{bits}]"
        if roll == 5:
            return " // PENDING"
        return ""

    def _gibberish_token(self) -> str:
        return "".join(self._rng.choice("█▓░▀▄▚╳∷◎") for _ in range(self._rng.randint(2, 5)))

    def _noise_word(self) -> str:
        if self._rng.random() < 0.35:
            return self._gibberish_token()
        return self._pick(self.NOUNS)

    def _mode_imperative(self) -> str:
        v, n = self._pick(self.VERBS), self._pick(self.NOUNS)
        head = self._rng.choice((f"{v}{n}", f"现在去{v}{n}", f"别{v}{n}", f"把{n}{v}"))
        ctx = self._rng.choice(("", f"（{self._pick(self.CONTEXT)}）"))
        return f"{head}{ctx}{self._suffix()}"

    def _mode_fragment(self) -> str:
        return (
            f"{self._pick(self.ADJECTIVES)}{self._pick(self.NOUNS)}，"
            f"{self._pick(self.CONNECTORS)}，{self._pick(self.FRAGMENTS)}{self._suffix()}"
        )

    def _mode_poetic(self) -> str:
        a, b, c = self._pick(self.NOUNS), self._pick(self.VERBS), self._pick(self.CONTEXT)
        return f"{c}。\n{self._pick(self.ADJECTIVES)}{a}，{b}。\n{self._pick(self.FRAGMENTS)}{self._suffix()}"

    def _mode_ledger(self) -> str:
        st = self._rng.choice(("SEED", "CUT", "SHIFT", "ECHO", "NOTE"))
        return f"[{st}] {self._pick(self.CONTEXT)} · {self._pick(self.NOUNS)} · {self._pick(self.VERBS)}{self._suffix()}"

    def _mode_corrupt(self) -> str:
        base = self._mode_fragment().replace("\n", "")
        if self._rng.random() < 0.5:
            i = self._rng.randint(1, max(1, len(base) - 1))
            tok = self._gibberish_token()
            base = base[:i] + tok + base[i:]
        return base + self._rng.choice((" …", " ——", ""))

    def _mode_scene_hook(self) -> str:
        return (
            f"{self._pick(self.CONTEXT)}："
            f"{self._pick(self.ADJECTIVES)}{self._pick(self.NOUNS)}，"
            f"{self._pick(self.FRAGMENTS)}"
        )

    def _mode_character_drive(self) -> str:
        who = self._rng.choice(("他", "她", "你", "他们", "她们"))
        want = self._rng.choice(("想要", "必须", "发誓要", "不允许自己再"))
        goal = self._rng.choice(
            (
                f"{self._pick(self.VERBS)}{self._pick(self.NOUNS)}",
                f"把{self._pick(self.NOUNS)}带回去",
                f"证明{self._pick(self.NOUNS)}不是假的",
            )
        )
        block = self._rng.choice(
            (
                f"但{self._pick(self.NOUNS)}先开口了",
                f"可{self._pick(self.CONTEXT)}不允许",
                f"偏偏在{self._pick(self.CONTEXT)}失手",
            )
        )
        return f"{who}{want}{goal}，{block}{self._suffix()}"

    def _mode_camera_prompt(self) -> str:
        cam = self._pick(self.CAMERA)
        return (
            f"{cam}：{self._pick(self.CONTEXT)}，"
            f"{self._pick(self.VERBS)}{self._pick(self.NOUNS)}；"
            f"情绪={self._pick(self.EMOTIONS)}{self._suffix()}"
        )

    def _mode_constraint(self) -> str:
        return (
            f"写作约束：{self._pick(self.CONSTRAINTS)}；"
            f"主题：{self._pick(self.NOUNS)} / {self._pick(self.CONTEXT)}{self._suffix()}"
        )

    def generate(self) -> str:
        modes = (
            self._mode_imperative,
            self._mode_fragment,
            self._mode_poetic,
            self._mode_ledger,
            self._mode_corrupt,
            self._mode_scene_hook,
            self._mode_character_drive,
            self._mode_camera_prompt,
            self._mode_constraint,
        )
        fn = self._rng.choice(modes)
        return fn()

    def generate_short(self, max_chars: int = 22) -> str:
        """生成更适合「逐字落屏」的一行短句。"""
        raw = self.generate().replace("\n", " / ").strip()
        # 去掉末尾太重的句号，避免像“正式句子”
        while raw.endswith(("。", "！", "？", ".", "!", "?")):
            raw = raw[:-1].strip()
        if len(raw) <= max_chars:
            return raw
        # 简单裁切：保留头部主体 + 结尾随机后缀感
        tail = ""
        if " " in raw[-8:]:
            tail = raw.split()[-1]
        cut = max(8, max_chars - (len(tail) + (1 if tail else 0)))
        out = raw[:cut].rstrip("，,;；:：") + (" " + tail if tail else "")
        return out[:max_chars]


def generate_inspiration(rng: Optional[random.Random] = None) -> str:
    """生成一条「都市指令」式短文本。"""
    return UrbanInspirationGenerator(rng=rng).generate()


def generate_inspiration_short(rng: Optional[random.Random] = None, max_chars: int = 22) -> str:
    """生成一条更短的一行灵感。"""
    return UrbanInspirationGenerator(rng=rng).generate_short(max_chars=max_chars)


class UrbanInspirationPanel(QFrame):
    """半透明黑底 + 深蓝等宽字，模拟 CRT；支持定时与手动刷新。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        interval_ms: int = 30_000,
        generator: Optional[UrbanInspirationGenerator] = None,
    ):
        super().__init__(parent)
        self.setObjectName("urbanInspirationPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._generator = generator or UrbanInspirationGenerator()

        self._text = QTextEdit(self)
        self._text.setObjectName("urbanInspirationText")
        self._text.setReadOnly(True)
        self._text.setAcceptRichText(False)
        self._text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._text.setMinimumHeight(44)
        self._text.setMaximumHeight(68)

        mono = QFont("Consolas")
        if not mono.exactMatch():
            mono = QFont("Courier New")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(9)
        self._text.setFont(mono)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.addWidget(self._text)

        self._timer = QTimer(self)
        self._timer.setInterval(max(5_000, interval_ms))
        self._timer.timeout.connect(self._on_timer)

        self.setStyleSheet(
            """
            QFrame#urbanInspirationPanel {
                background-color: rgba(0, 0, 0, 185);
                border: 1px solid rgba(80, 120, 180, 140);
                border-radius: 6px;
            }
            QTextEdit#urbanInspirationText {
                background-color: transparent;
                border: none;
                color: #2a5a9c;
                text-shadow: 0 0 2px rgba(42, 90, 156, 0.4);
                selection-background-color: rgba(60, 100, 160, 120);
            }
            """
        )

    def _on_timer(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self._text.setPlainText(self._generator.generate())

    def manual_refresh(self) -> None:
        """手动刷新（重新随机一条）。"""
        self.refresh()

    def set_auto_refresh(self, enabled: bool) -> None:
        if enabled:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def auto_refresh_interval_ms(self) -> int:
        return self._timer.interval()

    def set_auto_refresh_interval_ms(self, ms: int) -> None:
        self._timer.setInterval(max(5_000, int(ms)))
        if self._timer.isActive():
            self._timer.stop()
            self._timer.start()


__all__ = [
    "UrbanInspirationGenerator",
    "UrbanInspirationPanel",
    "generate_inspiration",
    "generate_inspiration_short",
]
