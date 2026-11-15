# -*- coding: utf-8 -*-
"""
analysis.py —— 天气数据清洗与可视化模块
=======================================

功能说明
--------
1. 使用 pandas 读取 spider.py 抓取的 CSV 数据；
2. 完成数据清洗：日期格式转换、缺失值处理、异常值剔除、新增月份字段；
3. 使用 pyecharts 生成四张交互式图表，并输出汇总看板页面。

输出文件（均位于 html/ 目录）
----------------------------
- temperature_line.html  高低温双折线图（支持缩放、悬浮提示）
- monthly_avg_bar.html   月度平均气温柱状图
- weather_pie.html       天气类型分布饼图
- humidity_line.html     相对湿度变化折线图
- dashboard.html         汇总看板（整合以上四张图表）

运行方式
--------
    python analysis.py
（运行前请先执行 python spider.py 生成数据文件）
"""

from __future__ import annotations

import logging
from string import Template

import pandas as pd
from pyecharts import options as opts
from pyecharts.charts import Bar, Line, Pie
from pyecharts.globals import ThemeType

from spider import BASE_DIR, CITY, OUTPUT_CSV

# ============================== 全局配置 ==============================

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("analysis")

# 输出目录与文件名
HTML_DIR = BASE_DIR / "html"
TEMP_FILE = "temperature_line.html"
MONTH_FILE = "monthly_avg_bar.html"
WEATHER_FILE = "weather_pie.html"
HUMIDITY_FILE = "humidity_line.html"
DASHBOARD_FILE = "dashboard.html"

# 图表配色
COLOR_MAX = "#FF6B6B"        # 最高气温
COLOR_MIN = "#4DABF7"        # 最低气温
COLOR_BAR = "#FFA94D"        # 月度柱状图
COLOR_HUMIDITY = "#20C997"   # 湿度
PIE_COLORS = [
    "#FF6B6B", "#FFA94D", "#FFD43B", "#69DB7C", "#38D9A9",
    "#4DABF7", "#748FFC", "#DA77F2", "#F783AC", "#ADB5BD",
]

# 需要做数值转换与异常检测的列，以及各自的物理合理范围
NUMERIC_COLUMNS = ["最高气温", "最低气温", "相对湿度"]
PHYSICAL_BOUNDS = {
    "最高气温": (-30, 60),
    "最低气温": (-40, 60),
    "相对湿度": (0, 100),
}
# IQR 异常值系数：天气数据中的寒潮、高温属于真实极端值，
# 使用 3.0 倍 IQR 只剔除明显异常的离群点，避免误删真实天气记录
IQR_MULTIPLIER = 3.0


# ============================== 数据读取与清洗 ==============================

def load_data(path=OUTPUT_CSV) -> pd.DataFrame:
    """
    读取爬虫输出的 CSV 文件。

    utf-8-sig 与 spider.py 保存时的编码保持一致，避免中文列名出现 BOM 乱码。
    """
    if not path.exists():
        raise FileNotFoundError(
            f"未找到数据文件：{path}\n请先运行 python spider.py 抓取数据。"
        )
    df = pd.read_csv(path, encoding="utf-8-sig")
    logger.info("读取原始数据 %d 条：%s", len(df), path)
    return df


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    数据清洗主流程：

    1. 日期格式转换：统一转为 datetime，无法解析的日期直接丢弃；
    2. 去重排序：同一天只保留一条，并按日期升序排列；
    3. 缺失值处理：数值列按时间线性插值，文本列前后向填充；
    4. 异常值剔除：先用物理范围过滤，再用 IQR 法则识别并置为缺失后插值；
    5. 新增月份字段：用于月度统计与图表横轴。
    """
    df = df.copy()

    # ---------- 1. 日期格式转换 ----------
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    invalid_dates = int(df["日期"].isna().sum())
    if invalid_dates:
        logger.warning("丢弃日期无法解析的记录 %d 条", invalid_dates)
    df = df.dropna(subset=["日期"])

    # ---------- 2. 去重与排序 ----------
    before = len(df)
    df = df.drop_duplicates(subset=["日期"]).sort_values("日期")
    if before != len(df):
        logger.warning("去除重复日期记录 %d 条", before - len(df))

    # 以日期为索引，便于按时间插值
    df = df.set_index("日期")

    # ---------- 3. 数值转换 ----------
    for column in NUMERIC_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    # 文本字段统一填充为空值标识
    for column in ["天气状况", "风向风力"]:
        df[column] = df[column].astype("string").str.strip().fillna("未知")

    # ---------- 4. 物理范围过滤 ----------
    for column, (low, high) in PHYSICAL_BOUNDS.items():
        mask = df[column].notna() & ~df[column].between(low, high)
        if mask.any():
            logger.warning("剔除 %s 超出物理范围的记录 %d 条", column, int(mask.sum()))
            df.loc[mask, column] = float("nan")

    # ---------- 5. IQR 异常值检测 ----------
    # IQR 法则：超出 [Q1-3*IQR, Q3+3*IQR] 视为异常；同时受物理范围约束
    for column, (low, high) in PHYSICAL_BOUNDS.items():
        q1, q3 = df[column].quantile([0.25, 0.75])
        iqr = q3 - q1
        lower_limit = max(low, q1 - IQR_MULTIPLIER * iqr)
        upper_limit = min(high, q3 + IQR_MULTIPLIER * iqr)
        mask = df[column].notna() & ~df[column].between(lower_limit, upper_limit)
        if mask.any():
            logger.warning("IQR 检测到 %s 异常值 %d 条，已置为缺失", column, int(mask.sum()))
            df.loc[mask, column] = float("nan")

    # ---------- 6. 缺失值填补 ----------
    missing_before = int(df[NUMERIC_COLUMNS].isna().sum().sum())
    if missing_before:
        logger.info("数值缺失 %d 个，使用按时间线性插值填补", missing_before)
    df[NUMERIC_COLUMNS] = df[NUMERIC_COLUMNS].interpolate(
        method="time", limit_direction="both"
    )
    # 最后兜底：极端情况下（整列缺失）用列均值填充
    df[NUMERIC_COLUMNS] = df[NUMERIC_COLUMNS].fillna(df[NUMERIC_COLUMNS].mean())

    # 文本列缺失值前后向填充
    for column in ["天气状况", "风向风力"]:
        df[column] = df[column].ffill().bfill().fillna("未知")

    # ---------- 7. 逻辑修正 ----------
    # 最高气温必须不小于最低气温，若颠倒则交换
    swapped = df["最高气温"] < df["最低气温"]
    if swapped.any():
        logger.warning("修正最高/最低气温颠倒的记录 %d 条", int(swapped.sum()))
        temp = df.loc[swapped, "最高气温"].copy()
        df.loc[swapped, "最高气温"] = df.loc[swapped, "最低气温"]
        df.loc[swapped, "最低气温"] = temp

    # 保留一位小数，便于阅读
    df["最高气温"] = df["最高气温"].round(1)
    df["最低气温"] = df["最低气温"].round(1)
    df["相对湿度"] = df["相对湿度"].round(0)

    # ---------- 8. 新增月份字段 ----------
    df["月份"] = df.index.strftime("%Y-%m")

    df = df.reset_index()
    logger.info(
        "清洗完成：有效记录 %d 条（%s ~ %s）",
        len(df),
        df["日期"].min().strftime("%Y-%m-%d"),
        df["日期"].max().strftime("%Y-%m-%d"),
    )
    return df


# ============================== 图表构建 ==============================

def _date_range_text(df: pd.DataFrame) -> str:
    """返回用于图表副标题的统计区间文本。"""
    return (
        f"{df['日期'].min().strftime('%Y-%m-%d')} ~ "
        f"{df['日期'].max().strftime('%Y-%m-%d')}"
    )


def _base_title(name: str, df: pd.DataFrame) -> opts.TitleOpts:
    """统一风格的图表标题：居中标题 + 统计区间副标题。"""
    return opts.TitleOpts(
        title=name,
        subtitle=f"统计区间：{_date_range_text(df)}",
        pos_left="center",
        pos_top="2%",
        title_textstyle_opts=opts.TextStyleOpts(font_size=18, color="#1F2937"),
        subtitle_textstyle_opts=opts.TextStyleOpts(font_size=12, color="#6B7280"),
    )


def _render_chart(chart, filename: str) -> None:
    """
    渲染图表并补充页面样式。

    pyecharts 默认在 body 上保留浏览器默认外边距，图表宽度设为 100% 时
    嵌入 iframe 会出现多余的滚动条，因此渲染后统一注入零边距样式。
    """
    path = HTML_DIR / filename
    chart.render(str(path))

    html = path.read_text(encoding="utf-8")
    page_style = "<style>html, body { margin: 0; padding: 0; overflow: hidden; }</style>"
    if "</head>" in html:
        html = html.replace("</head>", f"    {page_style}\n</head>", 1)
        path.write_text(html, encoding="utf-8")

    logger.info("已生成图表：%s", path)


def build_temperature_chart(df: pd.DataFrame) -> None:
    """
    图 1：高低温双折线图。

    - 最高气温与最低气温两条折线对比展示；
    - 支持鼠标滚轮缩放（inside）与底部滑块缩放（slider）；
    - 悬浮提示按日期同时显示两条曲线的数值。
    """
    dates = df["日期"].dt.strftime("%Y-%m-%d").tolist()

    line = (
        Line(init_opts=opts.InitOpts(
            theme=ThemeType.LIGHT, width="100%", height="500px",
            page_title=f"{CITY['name']}高低温变化",
        ))
        .add_xaxis(dates)
        .add_yaxis(
            "最高气温",
            df["最高气温"].tolist(),
            is_smooth=True,
            is_symbol_show=False,
            color=COLOR_MAX,
            markpoint_opts=opts.MarkPointOpts(
                data=[opts.MarkPointItem(type_="max", name="最高")],
                symbol_size=52,
            ),
        )
        .add_yaxis(
            "最低气温",
            df["最低气温"].tolist(),
            is_smooth=True,
            is_symbol_show=False,
            color=COLOR_MIN,
            markpoint_opts=opts.MarkPointOpts(
                data=[opts.MarkPointItem(type_="min", name="最低")],
                symbol_size=52,
            ),
        )
        .set_global_opts(
            title_opts=_base_title(f"{CITY['name']}近一年最高/最低气温变化", df),
            tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross"),
            legend_opts=opts.LegendOpts(pos_top="13%"),
            toolbox_opts=opts.ToolboxOpts(
                is_show=True,
                feature={"saveAsImage": {}, "dataZoom": {}},
            ),
            xaxis_opts=opts.AxisOpts(
                type_="category",
                name="日期",
                boundary_gap=False,
                axislabel_opts=opts.LabelOpts(rotate=0),
            ),
            yaxis_opts=opts.AxisOpts(
                type_="value",
                name="气温 (℃)",
                splitline_opts=opts.SplitLineOpts(is_show=True),
            ),
            datazoom_opts=[
                opts.DataZoomOpts(type_="inside", range_start=0, range_end=100),
                opts.DataZoomOpts(
                    type_="slider", pos_bottom="2%", height=20,
                    range_start=0, range_end=100,
                ),
            ],
        )
    )
    # pyecharts 2.x 的 set_global_opts 不支持 grid_opts，直接写入 ECharts 原生配置
    line.options["grid"] = {"top": "22%", "bottom": "18%", "left": "7%", "right": "4%"}
    _render_chart(line, TEMP_FILE)


def build_monthly_chart(df: pd.DataFrame) -> None:
    """
    图 2：月度平均气温柱状图。

    月平均气温 = 当月每日 (最高气温 + 最低气温) / 2 的平均值，
    并用标记线展示全年平均气温，便于对比各月冷暖。
    """
    monthly_df = df.assign(
        月均温=(df["最高气温"] + df["最低气温"]) / 2
    )
    monthly_avg = monthly_df.groupby("月份")["月均温"].mean().round(1)

    bar = (
        Bar(init_opts=opts.InitOpts(
            theme=ThemeType.LIGHT, width="100%", height="500px",
            page_title=f"{CITY['name']}月度平均气温",
        ))
        .add_xaxis(monthly_avg.index.tolist())
        .add_yaxis(
            "月平均气温",
            monthly_avg.tolist(),
            color=COLOR_BAR,
            label_opts=opts.LabelOpts(is_show=True, position="top", formatter="{c}℃"),
            itemstyle_opts=opts.ItemStyleOpts(border_radius=[6, 6, 0, 0]),
            markline_opts=opts.MarkLineOpts(
                data=[opts.MarkLineItem(type_="average", name="全年平均")],
                label_opts=opts.LabelOpts(formatter="全年平均 {c}℃"),
            ),
        )
        .set_global_opts(
            title_opts=_base_title(f"{CITY['name']}近一年月度平均气温", df),
            tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="shadow"),
            legend_opts=opts.LegendOpts(pos_top="13%"),
            toolbox_opts=opts.ToolboxOpts(
                is_show=True, feature={"saveAsImage": {}}
            ),
            xaxis_opts=opts.AxisOpts(
                type_="category",
                name="月份",
                axislabel_opts=opts.LabelOpts(rotate=30),
            ),
            yaxis_opts=opts.AxisOpts(
                type_="value",
                name="气温 (℃)",
                splitline_opts=opts.SplitLineOpts(is_show=True),
            ),
        )
    )
    # 直接写入 ECharts 原生 grid 配置，避免标题/图例与绘图区重叠
    bar.options["grid"] = {"top": "22%", "bottom": "12%", "left": "7%", "right": "4%"}
    _render_chart(bar, MONTH_FILE)


def build_weather_pie(df: pd.DataFrame) -> None:
    """
    图 3：天气类型分布饼图（环形图）。

    天气类型较多的城市只展示占比前 8 的类型，其余合并为“其他”，
    避免图例过长影响阅读。
    """
    counts = df["天气状况"].value_counts()
    top_counts = counts.head(8)
    if len(counts) > 8:
        top_counts = pd.concat([top_counts, pd.Series({"其他": counts.iloc[8:].sum()})])
    data_pair = [[str(name), int(value)] for name, value in top_counts.items()]

    pie = (
        Pie(init_opts=opts.InitOpts(
            theme=ThemeType.LIGHT, width="100%", height="500px",
            page_title=f"{CITY['name']}天气类型分布",
        ))
        .add(
            series_name="天气类型",
            data_pair=data_pair,
            radius=["38%", "66%"],
            center=["50%", "55%"],
            label_opts=opts.LabelOpts(formatter="{b}: {d}%", font_size=12),
        )
        .set_colors(PIE_COLORS)
        .set_global_opts(
            title_opts=_base_title(f"{CITY['name']}近一年天气类型分布", df),
            tooltip_opts=opts.TooltipOpts(trigger="item", formatter="{b}<br/>天数：{c} 天<br/>占比：{d}%"),
            legend_opts=opts.LegendOpts(
                type_="scroll", orient="horizontal", pos_bottom="0%", pos_left="center"
            ),
        )
    )
    _render_chart(pie, WEATHER_FILE)


def build_humidity_chart(df: pd.DataFrame) -> None:
    """
    图 4：相对湿度变化折线图。

    采用面积折线图展示全年湿度走势，并标注全年平均湿度，
    支持缩放与悬浮查看每日具体数值。
    """
    dates = df["日期"].dt.strftime("%Y-%m-%d").tolist()

    line = (
        Line(init_opts=opts.InitOpts(
            theme=ThemeType.LIGHT, width="100%", height="500px",
            page_title=f"{CITY['name']}相对湿度变化",
        ))
        .add_xaxis(dates)
        .add_yaxis(
            "相对湿度",
            df["相对湿度"].tolist(),
            is_smooth=True,
            is_symbol_show=False,
            color=COLOR_HUMIDITY,
            areastyle_opts=opts.AreaStyleOpts(opacity=0.3, color=COLOR_HUMIDITY),
            markline_opts=opts.MarkLineOpts(
                data=[opts.MarkLineItem(type_="average", name="平均湿度")],
                label_opts=opts.LabelOpts(formatter="平均 {c}%"),
            ),
        )
        .set_global_opts(
            title_opts=_base_title(f"{CITY['name']}近一年相对湿度变化", df),
            tooltip_opts=opts.TooltipOpts(
                trigger="axis",
                axis_pointer_type="cross",
                formatter="{b}<br/>相对湿度：{c}%",
            ),
            legend_opts=opts.LegendOpts(pos_top="13%"),
            toolbox_opts=opts.ToolboxOpts(
                is_show=True,
                feature={"saveAsImage": {}, "dataZoom": {}},
            ),
            xaxis_opts=opts.AxisOpts(
                type_="category",
                name="日期",
                boundary_gap=False,
                axislabel_opts=opts.LabelOpts(rotate=0),
            ),
            yaxis_opts=opts.AxisOpts(
                type_="value",
                name="相对湿度 (%)",
                min_=0,
                max_=100,
                splitline_opts=opts.SplitLineOpts(is_show=True),
            ),
            datazoom_opts=[
                opts.DataZoomOpts(type_="inside", range_start=0, range_end=100),
                opts.DataZoomOpts(
                    type_="slider", pos_bottom="2%", height=20,
                    range_start=0, range_end=100,
                ),
            ],
        )
    )
    # 直接写入 ECharts 原生 grid 配置，为底部缩放滑块预留空间
    line.options["grid"] = {"top": "22%", "bottom": "18%", "left": "7%", "right": "4%"}
    _render_chart(line, HUMIDITY_FILE)


# ============================== 汇总看板 ==============================

def collect_stats(df: pd.DataFrame) -> dict[str, str]:
    """
    计算看板顶部关键指标。

    返回：
        dict，包含统计指标文本，供看板与终端摘要复用。
    """
    monthly_df = df.assign(月均温=(df["最高气温"] + df["最低气温"]) / 2)
    monthly_avg = monthly_df.groupby("月份")["月均温"].mean()

    max_row = df.loc[df["最高气温"].idxmax()]
    min_row = df.loc[df["最低气温"].idxmin()]
    weather_counts = df["天气状况"].value_counts()
    top_weather = str(weather_counts.index[0])
    top_ratio = weather_counts.iloc[0] / len(df) * 100

    return {
        "days": f"{len(df)} 天",
        "avg_temp": f"{((df['最高气温'] + df['最低气温']) / 2).mean():.1f} ℃",
        "max_temp": f"{max_row['最高气温']:.1f} ℃",
        "max_temp_date": max_row["日期"].strftime("%Y-%m-%d"),
        "min_temp": f"{min_row['最低气温']:.1f} ℃",
        "min_temp_date": min_row["日期"].strftime("%Y-%m-%d"),
        "avg_humidity": f"{df['相对湿度'].mean():.1f} %",
        "rainy_days": f"{int(df['天气状况'].str.contains('雨').sum())} 天",
        "top_weather": top_weather,
        "top_weather_ratio": f"占比 {top_ratio:.1f}%",
        "hottest_month": monthly_avg.idxmax(),
        "hottest_month_temp": f"月均 {monthly_avg.max():.1f} ℃",
        "coldest_month": monthly_avg.idxmin(),
        "coldest_month_temp": f"月均 {monthly_avg.min():.1f} ℃",
    }


# 看板 HTML 模板：$xxx 为占位符，由 string.Template 替换
DASHBOARD_TEMPLATE = Template(
    """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>$title</title>
<style>
    :root {
        --bg: #f4f7fb;
        --card: #ffffff;
        --text: #1f2937;
        --muted: #6b7280;
        --primary: #2563eb;
        --shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
    }
    * { box-sizing: border-box; }
    body {
        margin: 0;
        font-family: "Microsoft YaHei", "PingFang SC", "Helvetica Neue", Arial, sans-serif;
        background: var(--bg);
        color: var(--text);
    }
    .hero {
        padding: 48px 24px 40px;
        text-align: center;
        color: #fff;
        background: linear-gradient(135deg, #0f2b5b 0%, #1e40af 45%, #3b82f6 100%);
        box-shadow: 0 12px 30px rgba(30, 64, 175, 0.25);
    }
    .hero h1 {
        margin: 0 0 12px;
        font-size: 32px;
        letter-spacing: 2px;
        font-weight: 600;
    }
    .hero p {
        margin: 0;
        font-size: 14px;
        opacity: 0.85;
        letter-spacing: 0.5px;
    }
    main {
        max-width: 1400px;
        margin: 0 auto;
        padding: 32px 24px 56px;
    }
    .kpis {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 16px;
        margin-bottom: 28px;
    }
    .kpi {
        background: var(--card);
        border-radius: 14px;
        padding: 18px 20px;
        box-shadow: var(--shadow);
        border-top: 3px solid var(--primary);
        display: flex;
        flex-direction: column;
        gap: 6px;
    }
    .kpi-label {
        font-size: 13px;
        color: var(--muted);
        letter-spacing: 0.5px;
    }
    .kpi-value {
        font-size: 24px;
        font-weight: 700;
        color: #0f2b5b;
        line-height: 1.2;
    }
    .kpi-sub {
        font-size: 12px;
        color: #9ca3af;
    }
    .charts {
        display: grid;
        grid-template-columns: 1fr;
        gap: 24px;
    }
    .card {
        background: var(--card);
        border-radius: 16px;
        padding: 8px;
        box-shadow: var(--shadow);
        overflow: hidden;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .card:hover {
        transform: translateY(-3px);
        box-shadow: 0 16px 38px rgba(15, 23, 42, 0.12);
    }
    .card iframe {
        display: block;
        width: 100%;
        height: 520px;
        border: none;
        border-radius: 12px;
        background: #fff;
    }
    footer {
        margin-top: 36px;
        text-align: center;
        font-size: 12px;
        color: var(--muted);
        line-height: 1.8;
    }
    @media (min-width: 1100px) {
        .charts { grid-template-columns: 1fr 1fr; }
        .wide { grid-column: 1 / -1; }
    }
    @media (max-width: 640px) {
        .hero h1 { font-size: 24px; }
        .card iframe { height: 460px; }
        main { padding: 20px 12px 40px; }
    }
</style>
</head>
<body>
<header class="hero">
    <h1>$title</h1>
    <p>$subtitle</p>
</header>
<main>
    <section class="kpis">
$kpi_cards
    </section>
    <section class="charts">
        <div class="card wide">
            <iframe src="$temp_file" title="高低温双折线图" loading="lazy"></iframe>
        </div>
        <div class="card">
            <iframe src="$month_file" title="月度平均气温柱状图" loading="lazy"></iframe>
        </div>
        <div class="card">
            <iframe src="$weather_file" title="天气类型分布饼图" loading="lazy"></iframe>
        </div>
        <div class="card wide">
            <iframe src="$humidity_file" title="相对湿度变化折线图" loading="lazy"></iframe>
        </div>
    </section>
    <footer>
        数据来源：Open-Meteo 公开历史天气接口 · 可视化引擎：PyECharts<br>
        看板与四张图表文件需保持在同一 html 目录下 · 生成时间：$generated_at
    </footer>
</main>
</body>
</html>
"""
)


def build_dashboard(df: pd.DataFrame) -> None:
    """
    生成汇总看板 dashboard.html。

    看板由「顶部标题区 + 关键指标卡片 + 四张图表」组成，
    图表通过 iframe 引用同目录下已生成的独立 HTML 文件，便于单独分享与维护。
    """
    stats = collect_stats(df)
    # 本次生成时间：GitHub Actions 运行环境为 UTC，这里统一换算为北京时间
    generated_at = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d %H:%M")

    # 关键指标卡片：(指标名, 指标值, 补充说明)
    cards = [
        ("数据更新于", generated_at, "北京时间 · 每日自动刷新"),
        ("有效数据天数", stats["days"], "已完成清洗"),
        ("年平均气温", stats["avg_temp"], "（最高 + 最低）/ 2"),
        ("极端最高气温", stats["max_temp"], stats["max_temp_date"]),
        ("极端最低气温", stats["min_temp"], stats["min_temp_date"]),
        ("平均相对湿度", stats["avg_humidity"], "全年平均"),
        ("雨天日数", stats["rainy_days"], "天气状况含“雨”"),
        ("最常见天气", stats["top_weather"], stats["top_weather_ratio"]),
        ("最热月份", stats["hottest_month"], stats["hottest_month_temp"]),
    ]
    kpi_cards = "\n".join(
        f'        <div class="kpi">\n'
        f'            <span class="kpi-label">{label}</span>\n'
        f'            <span class="kpi-value">{value}</span>\n'
        f'            <span class="kpi-sub">{sub}</span>\n'
        f'        </div>'
        for label, value, sub in cards
    )

    html = DASHBOARD_TEMPLATE.substitute(
        title=f"{CITY['name']} · 近一年天气数据可视化看板",
        subtitle=(
            f"统计区间：{_date_range_text(df)} ｜ 数据来源：Open-Meteo 公开历史天气接口"
        ),
        kpi_cards=kpi_cards,
        temp_file=TEMP_FILE,
        month_file=MONTH_FILE,
        weather_file=WEATHER_FILE,
        humidity_file=HUMIDITY_FILE,
        generated_at=generated_at,
    )

    dashboard_path = HTML_DIR / DASHBOARD_FILE
    dashboard_path.write_text(html, encoding="utf-8")
    logger.info("已生成汇总看板：%s", dashboard_path)


# ============================== 主流程 ==============================

def main() -> int:
    """分析主流程：读取数据 -> 清洗 -> 生成图表 -> 生成看板。"""
    HTML_DIR.mkdir(parents=True, exist_ok=True)

    try:
        raw_df = load_data()
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1

    df = clean_data(raw_df)

    # 依次生成四张独立图表
    build_temperature_chart(df)
    build_monthly_chart(df)
    build_weather_pie(df)
    build_humidity_chart(df)

    # 生成汇总看板
    build_dashboard(df)

    # 终端输出关键结论，便于快速查看本次分析结果
    stats = collect_stats(df)
    logger.info(
        "分析结果：共 %s，年平均气温 %s，极端最高 %s（%s），极端最低 %s（%s），"
        "平均湿度 %s，雨天 %s，最常见天气 %s（%s），最热月份 %s（%s）。",
        stats["days"], stats["avg_temp"], stats["max_temp"], stats["max_temp_date"],
        stats["min_temp"], stats["min_temp_date"], stats["avg_humidity"],
        stats["rainy_days"], stats["top_weather"], stats["top_weather_ratio"],
        stats["hottest_month"], stats["hottest_month_temp"],
    )
    logger.info("全部图表已生成，直接双击 html/dashboard.html 即可查看看板。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
