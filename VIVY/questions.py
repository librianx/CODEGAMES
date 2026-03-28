import random


QUESTION_BANK = [
    {
        "id": "reading_habit",
        "question": "你更喜欢哪种“看东西”的节奏？（我会按你的口味准备灵感）",
        "options": [
            {"id": "paper", "label": "纸质书/纸笔记录", "preference_patch": {"reading_habit": "纸质书"}},
            {"id": "screen", "label": "电子屏/手机碎片化", "preference_patch": {"reading_habit": "电子屏"}},
            {"id": "mix", "label": "混合：想起就切换", "preference_patch": {"reading_habit": "混合"}},
            {"id": "audio", "label": "听为主（播客/有声书）", "preference_patch": {"reading_habit": "听为主"}},
        ],
    },
    {
        "id": "inspiration_source",
        "question": "当你需要灵感时，最常从哪里来？",
        "options": [
            {"id": "nature", "label": "大自然：风/云/光影", "preference_patch": {"inspiration_source": "大自然"}},
            {"id": "tech", "label": "技术/代码：Bug、架构、日志", "preference_patch": {"inspiration_source": "技术/代码"}},
            {"id": "people", "label": "人和故事：对话、日常细节", "preference_patch": {"inspiration_source": "人和故事"}},
            {"id": "arts", "label": "艺术/音乐：旋律、画面、节奏", "preference_patch": {"inspiration_source": "艺术/音乐"}},
        ],
    },
    {
        "id": "humor_level",
        "question": "你希望我吐槽和幽默占比大概多少？（别怕，我会把握“情感温度”）",
        "options": [
            {"id": "low", "label": "温柔一点，少点梗", "preference_patch": {"humor_level": "低"}},
            {"id": "mid", "label": "刚刚好：偶尔会笑就行", "preference_patch": {"humor_level": "中"}},
            {"id": "high", "label": "多来点！要会发光那种", "preference_patch": {"humor_level": "高"}},
        ],
    },
    {
        "id": "topic_bias",
        "question": "你更爱聊哪个方向？",
        "options": [
            {"id": "sci_fi", "label": "科幻/时间线/未来隐喻", "preference_patch": {"topic_bias": "科幻"}},
            {"id": "life", "label": "日常/情绪/自我成长", "preference_patch": {"topic_bias": "日常"}},
            {"id": "creative", "label": "创作：写作/绘画/点子", "preference_patch": {"topic_bias": "创作"}},
            {"id": "dev", "label": "编程：问题拆解、构建工具链", "preference_patch": {"topic_bias": "编程"}},
        ],
    },
    {
        "id": "night_or_day",
        "question": "你一般在一天什么时候更容易进入创作状态？",
        "options": [
            {"id": "morning", "label": "清晨：脑子最亮", "preference_patch": {"creative_time": "清晨"}},
            {"id": "afternoon", "label": "下午：稳稳输出", "preference_patch": {"creative_time": "下午"}},
            {"id": "night", "label": "晚上：灵感在被窝里加速", "preference_patch": {"creative_time": "晚上"}},
            {"id": "random", "label": "随机：看情绪驱动", "preference_patch": {"creative_time": "随机"}},
        ],
    },
    {
        "id": "comfort_style",
        "question": "如果你今天有点低落，我更希望怎么陪你？",
        "options": [
            {"id": "hug", "label": "抱抱 + 鼓励（少分析，多陪伴）", "preference_patch": {"comfort_style": "陪伴"}},
            {"id": "plan", "label": "一起拆解方案（理性但不冷）", "preference_patch": {"comfort_style": "拆解"}},
            {"id": "distract", "label": "换个轨道：一起做个小实验/游戏", "preference_patch": {"comfort_style": "转移注意"}},
        ],
    },
]


def pick_random_question(exclude_ids=None):
    exclude_ids = set(exclude_ids or [])
    candidates = [q for q in QUESTION_BANK if q["id"] not in exclude_ids]
    if not candidates:
        candidates = QUESTION_BANK
    return random.choice(candidates)
