# 深圳近一年天气数据分析与可视化

一个可直接运行的 Python 数据分析小项目：抓取城市近一年逐日历史天气数据，使用 pandas 完成清洗与统计，再通过 pyecharts 生成四张交互式图表和一个汇总看板页面。

项目默认城市为 **深圳**，城市名称与经纬度集中在 `spider.py` 的 `CITY` 配置中，修改后即可复用到其他城市。

## 项目结构

```
shenzhen-weather-analysis/
├── .github/
│   └── workflows/
│       └── update-weather.yml # GitHub Actions：每日自动更新数据与图表
├── spider.py                  # 爬虫：抓取、清洗并保存天气数据
├── analysis.py                # 分析：pandas 清洗 + pyecharts 可视化 + 看板
├── data/
│   └── shenzhen_weather.csv   # 抓取结果（本次已生成，可直接查看）
├── html/
│   ├── temperature_line.html  # 高低温双折线图
│   ├── monthly_avg_bar.html   # 月度平均气温柱状图
│   ├── weather_pie.html       # 天气类型分布饼图
│   ├── humidity_line.html     # 相对湿度变化折线图
│   └── dashboard.html         # 汇总看板（整合以上四张图）
├── requirements.txt
├── README.md
└── .gitignore
```

## 数据来源与字段说明

数据来自 [Open-Meteo](https://open-meteo.com/) 的公开历史天气接口（`archive-api.open-meteo.com`）：

- 官方对外开放的公共接口，**无需注册、无需 API Key**；
- 不属于私有或易失效接口，长期稳定；
- 数据为 ERA5 再分析数据，按经纬度返回逐日统计值。

| CSV 字段 | 含义 | 接口来源 |
| --- | --- | --- |
| 日期 | 日期（YYYY-MM-DD） | `time` |
| 最高气温 | 当日最高气温（℃） | `temperature_2m_max` |
| 最低气温 | 当日最低气温（℃） | `temperature_2m_min` |
| 天气状况 | 中文天气描述 | `weather_code`，按 WMO 天气代码映射 |
| 风向风力 | 如“东南风 3级” | 主导风向角度 → 八方位；最大风速 → 蒲福风力等级 |
| 相对湿度 | 当日平均相对湿度（%） | `relative_humidity_2m_mean` |

抓取策略：按自然月拆分请求，每次请求前随机延时 1.5~3.0 秒，失败后按指数退避重试（最多 3 次），并对日期、气温范围、重复日期等做基础清洗。

## 环境安装

建议 Python 3.9 及以上版本（开发环境为 Python 3.12）。

```bash
# 1. 进入项目目录
cd shenzhen-weather-analysis

# 2. 创建并激活虚拟环境（可选，但推荐）
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt
```

## 运行步骤

```bash
# 第一步：抓取最近一年数据，结果保存到 data/shenzhen_weather.csv
python spider.py

# 第二步：数据清洗并生成图表与看板，输出到 html/ 目录
python analysis.py
```

运行完成后直接双击 `html/dashboard.html` 即可查看看板。由于图表使用 pyecharts 默认 CDN 加载 ECharts，打开页面时需要联网。

> 抓取过程包含友好延时，13 个自然月分片大约需要半分钟，属于正常现象。

## 数据自动更新

项目通过 `.github/workflows/update-weather.yml` 实现无人值守更新：

| 项目 | 说明 |
| --- | --- |
| 触发方式 | 每天北京时间 **09:20** 自动运行一次，也可在 Actions 页面手动触发 |
| 执行内容 | `python spider.py` 重新抓取最近一年数据 → `python analysis.py` 重建全部图表与看板 |
| 结果处理 | 若 `data/` 或 `html/` 有变化，自动提交并推送到 `main` 分支 |
| 看板标识 | 看板首张指标卡片显示「数据更新于」，用于确认数据新鲜度 |

因此仓库中的 CSV 与图表始终是最近一次自动运行的结果，无需本地手动操作。

## 在线预览（GitHub Pages）

1. 打开仓库 **Settings → Pages**；
2. **Source** 选择 `Deploy from a branch`；
3. **Branch** 选择 `main`，目录选择 `/(root)`，点击 **Save**；
4. 部署完成后访问：

```
https://<你的用户名>.github.io/shenzhen-weather/html/dashboard.html
```

> 自动更新任务提交后，Pages 会随之重新部署，页面数据随之刷新。

## 图表说明

| 文件 | 图表 | 说明 |
| --- | --- | --- |
| `temperature_line.html` | 高低温双折线图 | 最高/最低气温随日期变化；支持滚轮与底部滑块缩放，悬浮可同时查看两条曲线数值，并标注全年极值点 |
| `monthly_avg_bar.html` | 月度平均气温柱状图 | 月平均气温 = 当月每日 (最高 + 最低) / 2 的均值；虚线为全年平均气温 |
| `weather_pie.html` | 天气类型分布饼图 | 环形图展示各天气类型天数占比；类型过多时仅展示前 8 类，其余合并为“其他” |
| `humidity_line.html` | 相对湿度变化折线图 | 面积折线展示全年湿度走势，标注平均湿度，支持缩放与悬浮提示 |
| `dashboard.html` | 汇总看板 | 顶部 9 个关键指标卡片（含数据更新时间），下方整合四张图表；图表通过 iframe 引用同目录文件 |

> 注意：`dashboard.html` 与另外四个 HTML 文件必须保持在同一个 `html/` 目录下，移动时请一并移动。

## 项目结论

基于本次实际抓取的 366 天数据（2025-11-13 ~ 2026-11-13），主要结论如下：

1. **全年温暖，夏长冬短。** 年平均气温约 **24.1 ℃**，最热月份为 **2026-08（月均 28.8 ℃）**，最冷月份为 **2026-01（月均 16.6 ℃）**，月均温差约 12.2 ℃。
2. **极端气温出现在盛夏与年初。** 极端最高气温 **35.7 ℃**（2026-08-09），极端最低气温 **7.0 ℃**（2026-01-07），全年气温整体波动平缓。
3. **降水频繁、湿度偏高。** 天气状况含“雨”的天数达 **233 天（约 63.7%）**，全年平均相对湿度 **76.4%**；4—9 月月均湿度维持在 **82%~87%**，是典型的华南湿热气候。
4. **阴雨天气占主导。** 出现最多的天气类型为 **阴（103 天，28.1%）**，其次为小毛毛雨（92 天）和中雨（60 天）；晴朗类天气（晴 + 大部晴朗）合计仅 14 天。
5. **湿度与气温同向变化。** 湿度在春季后随气温升高而上升，7 月达到峰值（月均 87.4%），进入 11 月后随气温回落而下降。

> 说明：以上结论基于 ERA5 再分析数据，与气象站单点观测可能存在细微差异，仅用于学习与数据分析演示。

## 切换其他城市

只需修改 `spider.py` 顶部的 `CITY` 字典：

```python
CITY = {
    "name": "北京",
    "slug": "beijing",
    "latitude": 39.9042,
    "longitude": 116.4074,
    "timezone": "Asia/Shanghai",
}
```

`analysis.py` 会自动复用该配置，输出文件也会随之变为 `data/beijing_weather.csv` 及对应图表标题。

## 说明与许可

- 本项目仅用于 Python 爬虫、pandas 数据处理与 pyecharts 可视化的学习演示；
- 请遵守数据提供方的使用条款，控制请求频率，不要将数据用于商业用途；
- 图表依赖 CDN 资源，离线环境可将 ECharts 静态文件下载到本地并修改 pyecharts 的 `ONLINE_HOST` 配置。
