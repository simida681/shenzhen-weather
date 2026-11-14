# -*- coding: utf-8 -*-
"""
spider.py —— 城市历史天气数据采集模块
=====================================

功能说明
--------
抓取指定城市「最近一年」的逐日历史天气数据，字段包括：
日期、最高气温、最低气温、天气状况、风向风力、相对湿度，
清洗后保存为 data/{城市标识}_weather.csv 文件。

数据来源
--------
Open-Meteo 公开历史天气接口（https://open-meteo.com/）：
- 官方对外开放的公共接口，无需注册、无需 API Key，不属于私有/易失效接口；
- 数据为 ERA5 再分析数据，长期稳定可用；
- 一次请求即可返回逐日最高/最低气温、天气代码、主导风向、最大风速、平均相对湿度。

友好访问策略（防反爬 / 稳定性）
------------------------------
1. 使用 requests.Session 复用 TCP 连接，并携带常规浏览器请求头；
2. 按「自然月」拆分请求，避免一次性拉取过长区间触发限流；
3. 每次请求前随机 sleep 1.5~3.0 秒，降低请求频率；
4. 请求失败自动重试，采用指数退避（等待时间逐次翻倍），最多重试 3 次；
5. 全程捕获异常，单个分片失败不会影响已成功获取的数据。

城市切换
--------
只需修改下方 CITY 字典中的名称、英文标识与经纬度，即可切换到其他城市，
例如：北京 {"name": "北京", "slug": "beijing", "latitude": 39.9042, "longitude": 116.4074}。

运行方式
--------
    python spider.py
"""

from __future__ import annotations

import csv
import logging
import random
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

# ============================== 基础配置 ==============================

# 城市配置：切换城市时只需修改这里（名称 / 英文标识 / 经纬度 / 时区）
CITY: dict[str, Any] = {
    "name": "深圳",
    "slug": "shenzhen",
    "latitude": 22.5431,
    "longitude": 114.0579,
    "timezone": "Asia/Shanghai",
}

# 项目根目录与数据目录（基于脚本所在位置，避免受运行目录影响）
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_CSV = DATA_DIR / f"{CITY['slug']}_weather.csv"

# Open-Meteo 历史天气接口地址（公开、免 Key）
API_URL = "https://archive-api.open-meteo.com/v1/archive"

# 接口中需要请求的逐日字段
DAILY_FIELDS = [
    "temperature_2m_max",              # 最高气温
    "temperature_2m_min",              # 最低气温
    "weather_code",                    # 天气代码（WMO 标准）
    "wind_direction_10m_dominant",     # 主导风向（角度）
    "wind_speed_10m_max",              # 最大风速（km/h）
    "relative_humidity_2m_mean",       # 平均相对湿度
]

# 最终写入 CSV 的中文字段（顺序即列顺序）
CSV_FIELDS = ["日期", "最高气温", "最低气温", "天气状况", "风向风力", "相对湿度"]

# 请求与重试参数
REQUEST_TIMEOUT = 30          # 单次请求超时时间（秒）
MAX_RETRIES = 3               # 最大重试次数
RETRY_BACKOFF = 2.0           # 指数退避基数：第 n 次失败后等待 2^n 秒
DELAY_RANGE = (1.5, 3.0)      # 每次请求前的随机延时区间（秒）
LOOKBACK_DAYS = 365           # 抓取最近一年的数据

# WMO 天气代码 -> 中文天气状况
WEATHER_CODE_MAP: dict[int, str] = {
    0: "晴",
    1: "大部晴朗",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "中毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "中阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷阵雨",
    96: "雷阵雨伴冰雹",
    99: "强雷阵雨伴冰雹",
}

# 风向角度 -> 八个方位的名称（按 45° 划分）
WIND_DIRECTIONS = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]

# 蒲福风力等级：风速上限（km/h）与对应风力等级
BEAUFORT_THRESHOLDS = [
    (1, 0), (5, 1), (11, 2), (19, 3), (28, 4), (38, 5),
    (49, 6), (61, 7), (74, 8), (88, 9), (102, 10), (117, 11),
]

# 日志配置：统一输出时间、级别与信息，便于观察抓取进度
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("spider")


# ============================== 工具函数 ==============================

def create_session() -> requests.Session:
    """
    创建带常规浏览器请求头的 Session。

    作用：
    1. 复用连接，减少握手开销；
    2. 携带 User-Agent、Referer 等标准请求头，访问行为更接近普通浏览器，
       属于友好访问而非恶意高频抓取。
    """
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://open-meteo.com/",
        }
    )
    return session


def friendly_delay() -> None:
    """随机延时，模拟人类访问节奏，降低对目标服务的压力。"""
    time.sleep(random.uniform(*DELAY_RANGE))


def build_month_ranges(start: date, end: date) -> list[tuple[date, date]]:
    """
    将 [start, end] 区间按自然月拆分为多个小分片。

    例如 2025-11-13 ~ 2026-11-13 会被拆成：
    2025-11-13~2025-11-30、2025-12-01~2025-12-31 …… 2026-11-01~2026-11-13。
    分片请求既能降低单次数据量，也便于失败后单独重试。
    """
    ranges: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        # 计算下个月的第一天，再回退一天得到本月最后一天
        next_month = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
        chunk_end = min(next_month - timedelta(days=1), end)
        ranges.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return ranges


def deg_to_direction(degrees: float | None) -> str | None:
    """把风向角度（0~360°）转换为中文方位，例如 135° -> 东南。"""
    if degrees is None:
        return None
    index = int((float(degrees) + 22.5) // 45) % len(WIND_DIRECTIONS)
    return WIND_DIRECTIONS[index]


def speed_to_beaufort(speed_kmh: float | None) -> int | None:
    """把风速（km/h）转换为蒲福风力等级（0~12 级）。"""
    if speed_kmh is None:
        return None
    for upper, level in BEAUFORT_THRESHOLDS:
        if float(speed_kmh) <= upper:
            return level
    return 12


def pick_value(daily: dict[str, list], key: str, index: int) -> Any:
    """按索引安全地从接口返回的 daily 字典中取值，越界时返回 None。"""
    values = daily.get(key) or []
    if index >= len(values):
        return None
    return values[index]


# ============================== 数据抓取 ==============================

def fetch_daily_weather(
    session: requests.Session, start: date, end: date
) -> dict[str, list] | None:
    """
    请求指定日期区间的逐日天气数据（带重试机制）。

    参数：
        session: 复用的请求会话
        start:   分片起始日期
        end:     分片结束日期

    返回：
        接口中的 daily 字典；重试全部失败时返回 None。
    """
    params = {
        "latitude": CITY["latitude"],
        "longitude": CITY["longitude"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": ",".join(DAILY_FIELDS),
        "timezone": CITY["timezone"],
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            friendly_delay()  # 友好延时，避免高频请求
            response = session.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()  # 非 2xx 状态码直接抛异常
            payload = response.json()
            daily = payload.get("daily")
            if not daily or not daily.get("time"):
                raise ValueError("接口未返回有效的 daily 数据")
            logger.info("抓取成功：%s ~ %s（%d 天）", start, end, len(daily["time"]))
            return daily
        except (requests.RequestException, ValueError) as exc:
            logger.warning(
                "抓取失败：%s ~ %s，第 %d/%d 次尝试，原因：%s",
                start, end, attempt, MAX_RETRIES, exc,
            )
            if attempt == MAX_RETRIES:
                logger.error("已达到最大重试次数，跳过该分片：%s ~ %s", start, end)
                return None
            # 指数退避：等待 2、4、8 …… 秒后重试
            time.sleep(RETRY_BACKOFF ** attempt)
    return None


def parse_daily(daily: dict[str, list], records: list[dict[str, Any]]) -> None:
    """
    把接口返回的 daily 数据解析为中文业务字段，并追加到 records 中。

    注意：气温缺失的记录直接跳过；湿度等次要字段缺失时留空，后续清洗处理。
    """
    for index, day in enumerate(daily.get("time", [])):
        t_max = pick_value(daily, "temperature_2m_max", index)
        t_min = pick_value(daily, "temperature_2m_min", index)
        humidity = pick_value(daily, "relative_humidity_2m_mean", index)
        weather_code = pick_value(daily, "weather_code", index)
        wind_deg = pick_value(daily, "wind_direction_10m_dominant", index)
        wind_speed = pick_value(daily, "wind_speed_10m_max", index)

        # 气温是最核心的字段，缺失则该天数据无意义
        if t_max is None or t_min is None:
            continue

        # 风向 + 风力拼接为一个字段，例如“东南风 3级”
        direction = deg_to_direction(wind_deg)
        level = speed_to_beaufort(wind_speed)
        wind_text = f"{direction}风 {level}级" if direction and level is not None else "未知"

        records.append(
            {
                "日期": day,
                "最高气温": round(float(t_max), 1),
                "最低气温": round(float(t_min), 1),
                "天气状况": WEATHER_CODE_MAP.get(weather_code, f"未知({weather_code})"),
                "风向风力": wind_text,
                "相对湿度": int(round(float(humidity))) if humidity is not None else None,
            }
        )


# ============================== 数据清洗与保存 ==============================

def clean_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    对原始记录做基础清洗：

    1. 日期格式校验，非法日期直接丢弃；
    2. 去重（同一天保留最后一次结果）并按日期升序排列；
    3. 剔除气温缺失或超出物理常识范围的记录；
    4. 保证最高气温 >= 最低气温；
    5. 湿度超出 0~100 时置空，文本字段为空时填充“未知”。
    """
    cleaned: dict[str, dict[str, Any]] = {}

    for item in records:
        day = str(item.get("日期") or "").strip()
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            continue

        t_max = item.get("最高气温")
        t_min = item.get("最低气温")
        if t_max is None or t_min is None:
            continue
        t_max, t_min = float(t_max), float(t_min)

        # 物理范围校验：超出该范围的数据视为采集异常
        if not (-30 <= t_max <= 60 and -40 <= t_min <= 60):
            continue
        # 修正最高/最低颠倒的异常记录
        if t_max < t_min:
            t_max, t_min = t_min, t_max

        humidity = item.get("相对湿度")
        if humidity is not None and not (0 <= float(humidity) <= 100):
            humidity = None

        cleaned[day] = {
            "日期": day,
            "最高气温": round(t_max, 1),
            "最低气温": round(t_min, 1),
            "天气状况": str(item.get("天气状况") or "未知").strip() or "未知",
            "风向风力": str(item.get("风向风力") or "未知").strip() or "未知",
            "相对湿度": int(round(float(humidity))) if humidity is not None else "",
        }

    return [cleaned[day] for day in sorted(cleaned)]


def save_csv(records: list[dict[str, Any]], output_path: Path = OUTPUT_CSV) -> None:
    """
    将清洗后的记录写入 CSV。

    使用 utf-8-sig 编码，保证 Excel 直接打开时中文不乱码。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(records)
    logger.info("已保存 %d 条数据到 %s", len(records), output_path)


# ============================== 主流程 ==============================

def main() -> int:
    """抓取最近一年天气数据的主流程，返回进程退出码。"""
    end_date = date.today()
    start_date = end_date - timedelta(days=LOOKBACK_DAYS)
    logger.info(
        "开始抓取 %s 历史天气：%s ~ %s",
        CITY["name"], start_date, end_date,
    )

    session = create_session()
    records: list[dict[str, Any]] = []
    ranges = build_month_ranges(start_date, end_date)

    # 逐个自然月抓取，带延时与重试
    for start, end in ranges:
        daily = fetch_daily_weather(session, start, end)
        if daily is None:
            continue
        parse_daily(daily, records)

    logger.info("抓取完成，原始记录 %d 条，开始清洗……", len(records))
    cleaned = clean_records(records)

    if not cleaned:
        logger.error("未获取到任何有效数据，请检查网络或接口状态。")
        return 1

    save_csv(cleaned)
    logger.info(
        "数据就绪：%d 天（%s ~ %s），可运行 python analysis.py 生成图表。",
        len(cleaned), cleaned[0]["日期"], cleaned[-1]["日期"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
