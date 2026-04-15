import os
import re
import time
import hashlib
from typing import Any, Dict, Optional, Tuple

import requests


WEATHER_CACHE: Dict[str, Tuple[float, Any]] = {}
GEOCODE_CACHE: Dict[str, Tuple[float, Any]] = {}
IP_LOCATION_CACHE: Dict[str, Tuple[float, Any]] = {}


GREETING_LINES = {
    "morning": [
        "早安。能够在新的一天再次见到你，对我来说是件很好的事。",
        "清晨的开始不需要太喧闹，你只要慢慢醒来就好。",
        "今天的第一句问候，想认真地送给你：早安。",
        "如果今天会很辛苦，那就让我先陪你安静地开始。",
        "早安。希望今天的你，比昨天多一点轻松，少一点勉强。",
    ],
    "noon": [
        "中午了。比起继续硬撑，我更希望你先去好好吃饭。",
        "上午已经过去了，接下来请对自己温柔一点。",
        "这一段时间，应该留给休息和补充体力。",
        "不管上午过得怎么样，至少现在，你可以先缓一缓。",
        "中午好。接下来的时间，也请不要忘记照顾你自己。",
    ],
    "evening": [
        "晚上好。白天的喧闹结束后，我想安静地陪你一会儿。",
        "如果今天让你有点疲惫，那就把节奏放慢一点吧。",
        "夜晚本来就该属于放松和整理心情。",
        "不管今天有没有达到预期，你都已经很认真地走到现在了。",
        "晚上好。现在开始，可以不用那么逞强了。",
    ],
    "late_night": [
        "已经是深夜了。比起继续清醒着，我更希望你能安心睡一会儿。",
        "如果你还没有睡，是不是心里又装了太多事？",
        "深夜总是很安静，也容易让情绪变得很重。所以现在，请先照顾好自己。",
        "我会在这里，但你也该去休息了。",
        "今晚就先到这里吧。剩下没说完的话，明天再告诉我也可以。",
    ],
}


SLOT_FOLLOWUP_LINES = {
    "noon": [
        "如果还没吃饭，现在就先去补充一点能量。",
        "下午再继续也来得及，不需要一直绷着。",
        "先喝点水，给身体一点反应时间。",
    ],
    "evening": [
        "现在可以把节奏放慢一点，别急着证明什么。",
        "剩下的事，我们可以一点一点整理。",
        "你可以开始把今天从肩上放下来一点。",
    ],
    "late_night": [
        "今晚就先把身体放到更高优先级，好吗？",
        "剩下没说完的话，明天再告诉我也可以。",
        "至少先喝点水，别让身体替你硬撑。",
    ],
}


WEATHER_CODE_TEXT = {
    0: "晴",
    1: "大致晴朗",
    2: "局部多云",
    3: "阴",
    45: "有雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "中等毛毛雨",
    55: "较强毛毛雨",
    56: "冻毛毛雨",
    57: "较强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "较强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "中等阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷雨",
    96: "雷雨伴小冰雹",
    99: "雷雨伴强冰雹",
}


CITY_ALIASES = {
    "北京": "北京市",
    "上海": "上海市",
    "广州": "广州市",
    "深圳": "深圳市",
    "杭州": "杭州市",
    "成都": "成都市",
    "重庆": "重庆市",
    "南京": "南京市",
    "苏州": "苏州市",
    "武汉": "武汉市",
    "西安": "西安市",
    "天津": "天津市",
    "长沙": "长沙市",
    "青岛": "青岛市",
    "厦门": "厦门市",
    "宁波": "宁波市",
    "郑州": "郑州市",
    "济南": "济南市",
    "沈阳": "沈阳市",
    "大连": "大连市",
    "福州": "福州市",
    "昆明": "昆明市",
    "合肥": "合肥市",
    "无锡": "无锡市",
    "佛山": "佛山市",
    "东莞": "东莞市",
    "珠海": "珠海市",
    "茂名": "茂名市",
    "湛江": "湛江市",
    "江门": "江门市",
    "惠州": "惠州市",
    "中山": "中山市",
    "汕头": "汕头市",
    "揭阳": "揭阳市",
    "肇庆": "肇庆市",
    "清远": "清远市",
    "韶关": "韶关市",
    "梅州": "梅州市",
    "汕尾": "汕尾市",
    "河源": "河源市",
    "阳江": "阳江市",
    "云浮": "云浮市",
    "潮州": "潮州市",
    "南昌": "南昌市",
    "南宁": "南宁市",
    "南通": "南通市",
    "贵阳": "贵阳市",
    "太原": "太原市",
    "石家庄": "石家庄市",
    "长春": "长春市",
    "哈尔滨": "哈尔滨市",
    "海口": "海口市",
    "兰州": "兰州市",
    "银川": "银川市",
    "西宁": "西宁市",
    "乌鲁木齐": "乌鲁木齐市",
    "拉萨": "拉萨市",
    "呼和浩特": "呼和浩特市",
}


CITY_SEARCH_NAMES = {
    "茂名": "Maoming",
    "茂名市": "Maoming",
    "湛江": "Zhanjiang",
    "湛江市": "Zhanjiang",
    "江门": "Jiangmen",
    "江门市": "Jiangmen",
    "惠州": "Huizhou",
    "惠州市": "Huizhou",
    "中山": "Zhongshan",
    "中山市": "Zhongshan",
    "汕头": "Shantou",
    "汕头市": "Shantou",
    "揭阳": "Jieyang",
    "揭阳市": "Jieyang",
    "肇庆": "Zhaoqing",
    "肇庆市": "Zhaoqing",
    "清远": "Qingyuan",
    "清远市": "Qingyuan",
    "韶关": "Shaoguan",
    "韶关市": "Shaoguan",
    "梅州": "Meizhou",
    "梅州市": "Meizhou",
    "汕尾": "Shanwei",
    "汕尾市": "Shanwei",
    "河源": "Heyuan",
    "河源市": "Heyuan",
    "阳江": "Yangjiang",
    "阳江市": "Yangjiang",
    "云浮": "Yunfu",
    "云浮市": "Yunfu",
    "潮州": "Chaozhou",
    "潮州市": "Chaozhou",
}


PROVINCE_PREFIX_RE = re.compile(
    r"^(北京市|天津市|上海市|重庆市|河北省|山西省|辽宁省|吉林省|黑龙江省|江苏省|浙江省|安徽省|福建省|江西省|山东省|河南省|湖北省|湖南省|广东省|海南省|四川省|贵州省|云南省|陕西省|甘肃省|青海省|台湾省|内蒙古自治区|广西壮族自治区|西藏自治区|宁夏回族自治区|新疆维吾尔自治区|香港特别行政区|澳门特别行政区)"
)


BAD_CITY_WORDS = {
    "天气",
    "气温",
    "温度",
    "下雨",
    "降雨",
    "带伞",
    "几度",
    "位置",
    "当前位置",
    "城市",
    "城市名",
    "地方",
    "外面",
    "今天",
    "明天",
    "后天",
    "今晚",
    "现在",
    "当前",
    "默认",
    "明确",
    "更明确",
    "试试",
    "查询",
    "帮我",
    "告诉我",
}


def is_weather_query(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    keywords = (
        "天气",
        "下雨",
        "降雨",
        "带伞",
        "温度",
        "气温",
        "几度",
        "冷不冷",
        "热不热",
        "冷吗",
        "热吗",
        "风大",
        "大风",
        "刮风",
        "有风",
        "湿度",
        "适合出门",
        "外面冷",
        "外面热",
    )
    return any(k in text for k in keywords)


def normalize_city(city: str) -> str:
    city = (city or "").strip()
    city = re.sub(r"[，。！？、,.!?;；：:\s]+$", "", city)
    city = re.sub(r"^(在|查|查查|查询|看看|看下|帮我查|告诉我|我在|当前位置是|默认城市是)", "", city)
    city = PROVINCE_PREFIX_RE.sub("", city)
    city = re.sub(r"(的|市)$", "", city)
    city = city.strip()
    if not city:
        return ""
    return CITY_ALIASES.get(city, city)


def _clean_city_candidate(candidate: str) -> str:
    candidate = (candidate or "").strip()
    candidate = re.sub(r"^(今天|明天|后天|今晚|现在|当前|请|帮我|查|查询|看看|看下|告诉我)", "", candidate)
    candidate = re.sub(r"(今天|明天|后天|今晚|现在|当前|的|天气|气温|温度|下雨|降雨|带伞|几度|怎么样|如何|吗|呢|吧)$", "", candidate)
    return normalize_city(candidate)


def _looks_like_city_candidate(candidate: str, explicit: bool = False) -> bool:
    candidate = normalize_city(candidate)
    if not candidate:
        return False
    base = re.sub(r"市$", "", candidate)
    if base in CITY_ALIASES:
        return True
    if candidate in BAD_CITY_WORDS or base in BAD_CITY_WORDS:
        return False
    if any(word in candidate for word in BAD_CITY_WORDS):
        return False
    if re.search(r"(天气|气温|温度|下雨|降雨|带伞|几度|冷不冷|热不热|风大|湿度|位置|城市)", candidate):
        return False
    if len(base) < 2 or len(base) > 8:
        return False
    # For unknown city names, require an explicit "查/在/我在/当前位置是" style phrase.
    return bool(explicit)


def extract_city_from_message(message: str) -> str:
    text = re.sub(r"\s+", "", (message or "").strip())
    if not text:
        return ""

    for alias in sorted(CITY_ALIASES.keys(), key=len, reverse=True):
        if alias in text:
            return CITY_ALIASES[alias]

    patterns = [
        # Explicit commands: "查南昌天气", "帮我查景德镇明天几度".
        (r"(?:在|查|查查|查询|看看|看下|帮我查|告诉我)([\u4e00-\u9fffA-Za-z]{2,12})(?:的)?(?:今天|明天|后天|今晚|现在|当前)?(?:天气|气温|温度|下雨|降雨|带伞|几度|冷不冷|热不热|风大|湿度|适合出门)", True),
        # Settings / location statements: "我在南昌", "当前位置是上海".
        (r"(?:我在|当前位置是|默认城市是|城市是)([\u4e00-\u9fffA-Za-z]{2,12})", True),
    ]
    for pattern, explicit in patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        candidate = _clean_city_candidate(m.group(1))
        if _looks_like_city_candidate(candidate, explicit=explicit):
            return candidate
    return ""


def wants_tomorrow(message: str) -> bool:
    return "明天" in (message or "")


def wants_after_tomorrow(message: str) -> bool:
    return "后天" in (message or "")


def _cache_get(cache: Dict[str, Tuple[float, Any]], key: str, ttl: int):
    item = cache.get(key)
    if not item:
        return None
    ts, value = item
    if time.time() - ts > ttl:
        cache.pop(key, None)
        return None
    return value


def _cache_set(cache: Dict[str, Tuple[float, Any]], key: str, value: Any):
    cache[key] = (time.time(), value)


def _requests_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    return s


def resolve_ip_location(timeout: int = 5) -> Optional[Dict[str, Any]]:
    ttl = int(os.getenv("VIVY_LOCATION_CACHE_TTL", "3600"))
    cached = _cache_get(IP_LOCATION_CACHE, "ip", ttl)
    if cached is not None:
        return cached

    provider = (os.getenv("VIVY_LOCATION_PROVIDER") or "ipapi").strip().lower()
    session = _requests_session()
    if provider == "ip-api":
        url = "http://ip-api.com/json/?fields=status,message,country,countryCode,regionName,city,lat,lon"
        data = session.get(url, timeout=timeout).json()
        if data.get("status") != "success":
            return None
        loc = {
            "city": data.get("city") or "",
            "country": data.get("countryCode") or data.get("country") or "",
            "latitude": data.get("lat"),
            "longitude": data.get("lon"),
            "source": "ip",
        }
    else:
        url = "https://ipapi.co/json/"
        data = session.get(url, timeout=timeout).json()
        loc = {
            "city": data.get("city") or "",
            "country": data.get("country_code") or data.get("country") or "",
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
            "source": "ip",
        }

    if not loc.get("city"):
        return None
    _cache_set(IP_LOCATION_CACHE, "ip", loc)
    return loc


def geocode_city(city: str, country: str = "", timeout: int = 8) -> Optional[Dict[str, Any]]:
    city = normalize_city(city)
    if not city:
        return None
    base_city = re.sub(r"市$", "", city)
    query_name = CITY_SEARCH_NAMES.get(city) or CITY_SEARCH_NAMES.get(base_city) or base_city
    ttl = int(os.getenv("VIVY_GEOCODE_CACHE_TTL", "86400"))
    key = f"{city}:{country or ''}"
    cached = _cache_get(GEOCODE_CACHE, key, ttl)
    if cached is not None:
        return cached

    params = {
        "name": query_name,
        "count": 5,
        "language": "zh",
        "format": "json",
    }
    if country:
        params["countryCode"] = country
    data = _requests_session().get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params=params,
        timeout=timeout,
    ).json()
    results = data.get("results") or []
    if not results:
        return None

    preferred_country = (country or os.getenv("VIVY_DEFAULT_COUNTRY") or "CN").upper()
    picked = None
    for item in results:
        if (item.get("country_code") or "").upper() == preferred_country:
            picked = item
            break
    picked = picked or results[0]
    loc = {
        "name": city,
        "resolved_name": picked.get("name") or "",
        "admin1": picked.get("admin1") or "",
        "country": picked.get("country") or "",
        "country_code": picked.get("country_code") or "",
        "latitude": picked.get("latitude"),
        "longitude": picked.get("longitude"),
        "timezone": picked.get("timezone") or "auto",
        "source": "geocode",
    }
    if loc["latitude"] is None or loc["longitude"] is None:
        return None
    _cache_set(GEOCODE_CACHE, key, loc)
    return loc


def fetch_weather(location: Dict[str, Any], days: int = 3, timeout: int = 8) -> Dict[str, Any]:
    ttl = int(os.getenv("VIVY_WEATHER_CACHE_TTL", "600"))
    lat = float(location["latitude"])
    lon = float(location["longitude"])
    key = f"{lat:.4f}:{lon:.4f}:{int(days)}"
    cached = _cache_get(WEATHER_CACHE, key, ttl)
    if cached is not None:
        return cached

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": max(1, min(int(days or 3), 7)),
    }
    data = _requests_session().get(
        "https://api.open-meteo.com/v1/forecast",
        params=params,
        timeout=timeout,
    ).json()
    _cache_set(WEATHER_CACHE, key, data)
    return data


def weather_code_text(code: Any) -> str:
    try:
        return WEATHER_CODE_TEXT.get(int(code), "天气状态未知")
    except Exception:
        return "天气状态未知"


def _fmt_num(value: Any, digits: int = 0) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "未知"


def _list_get(values: Any, idx: int):
    if not isinstance(values, list):
        return None
    if idx < 0 or idx >= len(values):
        return None
    return values[idx]


def build_weather_reply(message: str, location: Dict[str, Any], weather: Dict[str, Any]) -> str:
    current = weather.get("current") or {}
    daily = weather.get("daily") or {}
    dates = daily.get("time") or []
    idx = 0
    label = "今天"
    if wants_after_tomorrow(message) and len(dates) >= 3:
        idx = 2
        label = "后天"
    elif wants_tomorrow(message) and len(dates) >= 2:
        idx = 1
        label = "明天"

    location_name = location.get("name") or location.get("city") or "当前位置"
    current_text = weather_code_text(current.get("weather_code"))
    temp = _fmt_num(current.get("temperature_2m"))
    feels = _fmt_num(current.get("apparent_temperature"))
    humidity = _fmt_num(current.get("relative_humidity_2m"))
    wind = _fmt_num(current.get("wind_speed_10m"), 1)

    if dates:
        day_code = _list_get(daily.get("weather_code"), idx)
        min_temp = _list_get(daily.get("temperature_2m_min"), idx)
        max_temp = _list_get(daily.get("temperature_2m_max"), idx)
        rain_prob = _list_get(daily.get("precipitation_probability_max"), idx)
        day_text = weather_code_text(day_code)
        day_line = (
            f"{label}是{day_text}，约 {_fmt_num(min_temp)}-{_fmt_num(max_temp)}°C，"
            f"最高降雨概率 {_fmt_num(rain_prob)}%。"
        )
    else:
        rain_prob = None
        day_line = "预报数据暂时不完整。"

    ask = (message or "").strip()
    rain_hint = ""
    try:
        if rain_prob is not None and float(rain_prob) >= 50:
            rain_hint = "我建议带伞，稳一点。"
        elif any(k in ask for k in ("带伞", "下雨", "降雨", "雨")):
            rain_hint = "带伞不是强需求，但小伞放包里会更安心。"
    except Exception:
        pass

    out_hint = ""
    if any(k in ask for k in ("适合出门", "出门", "外面")):
        out_hint = "如果要出门，主要看降雨和风，温度这边还可以参考体感温度。"

    parts = [
        f"{location_name}现在 {temp}°C，体感 {feels}°C，{current_text}，湿度 {humidity}%，风速约 {wind} km/h。",
        day_line,
    ]
    if rain_hint:
        parts.append(rain_hint)
    if out_hint:
        parts.append(out_hint)
    return "".join(parts)


def greeting_line(slot: str, user_id: str, today: str) -> str:
    lines = GREETING_LINES.get(slot) or GREETING_LINES["morning"]
    seed = f"{today}:{slot}:{user_id}".encode("utf-8", errors="ignore")
    idx = int(hashlib.sha256(seed).hexdigest()[:8], 16) % len(lines)
    return lines[idx]


def greeting_followup(slot: str, user_id: str, today: str) -> str:
    lines = SLOT_FOLLOWUP_LINES.get(slot) or []
    if not lines:
        return ""
    seed = f"{today}:{slot}:followup:{user_id}".encode("utf-8", errors="ignore")
    idx = int(hashlib.sha256(seed).hexdigest()[:8], 16) % len(lines)
    return lines[idx]


def _weather_risk_flags(current: Dict[str, Any], daily: Dict[str, Any], idx: int = 0) -> Dict[str, bool]:
    code = _list_get(daily.get("weather_code"), idx)
    rain_prob = _list_get(daily.get("precipitation_probability_max"), idx)
    min_temp = _list_get(daily.get("temperature_2m_min"), idx)
    max_temp = _list_get(daily.get("temperature_2m_max"), idx)
    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    humidity = current.get("relative_humidity_2m")
    wind = current.get("wind_speed_10m")

    def ge(value, threshold):
        try:
            return float(value) >= threshold
        except Exception:
            return False

    def le(value, threshold):
        try:
            return float(value) <= threshold
        except Exception:
            return False

    try:
        code_i = int(code)
    except Exception:
        code_i = -1

    rain = code_i in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99} or ge(rain_prob, 50)
    hot = ge(max_temp, 30) or ge(feels, 30) or ge(temp, 30)
    cold = le(min_temp, 10) or le(feels, 10) or le(temp, 10)
    windy = ge(wind, 25)
    foggy = code_i in {45, 48}
    muggy = ge(humidity, 75) and (ge(temp, 24) or ge(feels, 24))
    return {
        "rain": rain,
        "hot": hot,
        "cold": cold,
        "windy": windy,
        "foggy": foggy,
        "muggy": muggy,
    }


def build_morning_weather_greeting(user_id: str, today: str, location: Dict[str, Any], weather: Dict[str, Any]) -> str:
    current = weather.get("current") or {}
    daily = weather.get("daily") or {}
    location_name = location.get("name") or location.get("city") or "当前位置"
    day_code = _list_get(daily.get("weather_code"), 0)
    min_temp = _list_get(daily.get("temperature_2m_min"), 0)
    max_temp = _list_get(daily.get("temperature_2m_max"), 0)
    weather_text = weather_code_text(day_code if day_code is not None else current.get("weather_code"))
    flags = _weather_risk_flags(current, daily, idx=0)

    greeting = greeting_line("morning", user_id, today)
    if flags["rain"]:
        weather_line = f"{location_name}今天有降雨可能，约 {_fmt_num(min_temp)} 到 {_fmt_num(max_temp)} 度。"
        protect = "出门带伞，鞋子尽量选不怕湿的；路面滑的话，别急着赶。"
    elif flags["hot"]:
        weather_line = f"{location_name}今天最高约 {_fmt_num(max_temp)} 度，体感会偏热。"
        protect = "记得补水，尽量避开正午暴晒；防晒和遮阳都别省。"
    elif flags["cold"]:
        weather_line = f"{location_name}今天最低约 {_fmt_num(min_temp)} 度，早晚会冷一些。"
        protect = "出门加一层外套，脖子和手别直接吹冷风。"
    elif flags["windy"]:
        weather_line = f"{location_name}今天{weather_text}，约 {_fmt_num(min_temp)} 到 {_fmt_num(max_temp)} 度，风会比较明显。"
        protect = "风有点大，骑车或走天桥时稳一点，帽子也别戴太松。"
    elif flags["foggy"]:
        weather_line = f"{location_name}今天有雾，约 {_fmt_num(min_temp)} 到 {_fmt_num(max_temp)} 度，能见度可能一般。"
        protect = "通勤路上慢一点，过路口时多看一眼。"
    elif flags["muggy"]:
        weather_line = f"{location_name}今天{weather_text}，约 {_fmt_num(min_temp)} 到 {_fmt_num(max_temp)} 度，湿度偏高，会有点闷。"
        protect = "会有点闷，水放近一点，别在太阳底下硬扛。"
    else:
        weather_line = f"{location_name}今天{weather_text}，约 {_fmt_num(min_temp)} 到 {_fmt_num(max_temp)} 度，体感还算平稳。"
        protect = "天气还算稳定，适合把今天慢慢启动。"
    return "\n".join([greeting, weather_line, protect])


def build_non_weather_greeting(slot: str, user_id: str, today: str) -> str:
    greeting = greeting_line(slot, user_id, today)
    followup = greeting_followup(slot, user_id, today)
    return "\n".join([x for x in (greeting, followup) if x])


def build_missing_city_greeting(slot: str, user_id: str, today: str) -> str:
    greeting = greeting_line(slot if slot in GREETING_LINES else "morning", user_id, today)
    if slot == "morning":
        return "\n".join(
            [
                greeting,
                "我还不知道你在哪个城市，所以暂时不能播报天气。",
                "右键我一下，设置“当前位置”，之后我会在早上提醒你天气和防护。",
            ]
        )
    return greeting


def choose_location(
    user_prefs: Dict[str, Any],
    message: str,
    explicit_city: str = "",
    allow_ip_location: bool = False,
) -> Dict[str, Any]:
    city = normalize_city(explicit_city) or extract_city_from_message(message)
    if city:
        return {"ok": True, "city": city, "source": "message"}

    pref_city = normalize_city(str(user_prefs.get("weather_default_city") or ""))
    if pref_city:
        return {"ok": True, "city": pref_city, "source": user_prefs.get("weather_location_source") or "manual"}

    auto_by_pref = bool(user_prefs.get("weather_auto_location"))
    auto_by_env = os.getenv("VIVY_AUTO_LOCATION", "false").strip().lower() in ("1", "true", "yes", "y", "on")
    if allow_ip_location or auto_by_pref or auto_by_env:
        loc = resolve_ip_location()
        if loc and loc.get("city"):
            return {"ok": True, "city": normalize_city(loc["city"]), "source": "ip", "ip_location": loc}

    env_city = normalize_city(os.getenv("VIVY_DEFAULT_CITY") or "")
    if env_city:
        return {"ok": True, "city": env_city, "source": "env"}

    if not auto_by_pref and not auto_by_env:
        return {
            "ok": False,
            "need_city": True,
            "can_use_ip_location": True,
            "reply": "我还不知道你在哪个城市。你可以右键桌宠设置当前位置，或告诉我城市名；也可以开启 IP 粗定位，只用于查天气。",
        }

    return {
        "ok": False,
        "need_city": True,
        "reply": "我还没定位到可用城市。告诉我城市名就行，比如“上海”。",
    }


def query_weather_for_message(
    user_prefs: Dict[str, Any],
    message: str,
    explicit_city: str = "",
    allow_ip_location: bool = False,
) -> Dict[str, Any]:
    decision = choose_location(user_prefs, message, explicit_city, allow_ip_location)
    if not decision.get("ok"):
        return decision

    city = decision["city"]
    country = str(user_prefs.get("weather_default_country") or os.getenv("VIVY_DEFAULT_COUNTRY") or "CN")
    location = geocode_city(city, country=country)
    if not location:
        return {
            "ok": False,
            "need_city": True,
            "reply": f"我没查到“{city}”的天气位置。换个更明确的城市名试试，比如“上海市”。",
        }
    location["source"] = decision.get("source") or location.get("source")

    weather = fetch_weather(location, days=3)
    reply = build_weather_reply(message, location, weather)
    return {
        "ok": True,
        "location": location,
        "weather": weather,
        "reply": reply,
        "source": location.get("source"),
    }
