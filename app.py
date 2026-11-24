#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股 + 港股 完整分析網站
功能：側邊欄、走勢圖、詳細新聞分析（正反角度）、多時間週期資料
"""

from flask import Flask, render_template_string, jsonify, request
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
import yfinance as yf
import logging
from functools import lru_cache
import os

app = Flask(__name__)

# ==================== 設定 ====================
US_WATCHLIST = {
    # 原本的科技股
    "AAPL": "蘋果",
    "MSFT": "微軟",
    "NVDA": "輝達",
    "TSLA": "特斯拉",
    "GOOGL": "Alphabet",
    "AMZN": "亞馬遜",
    "META": "Meta",
    "AMD": "超微",
    
    # 先前加入的
    "TSM": "台積電 (TSMC)",
    "VOO": "Vanguard S&P 500 ETF",
    "IVV": "iShares Core S&P 500 ETF",
    "SPY": "SPDR S&P 500 ETF",
    "QQQ": "Invesco QQQ",
    "AVGO": "博通",
    
    # 新增的
    "ASML": "艾司摩爾",
    "MU": "美光科技",
    "PLTR": "Palantir",
    "SOXX": "iShares 半導體 ETF",
    "SMH": "VanEck 半導體 ETF",
    "ARM": "Arm Holdings",
    "INTC": "英特爾",
    "QCOM": "高通",
    "AMAT": "應用材料",
    "LRCX": "Lam Research",
    "KLAC": "KLA",
    "TXN": "德州儀器"
}

HK_WATCHLIST = {
    "0700.HK": "騰訊控股",
    "9988.HK": "阿里巴巴",
    "3690.HK": "美團",
    "1810.HK": "小米集團",
    "9618.HK": "京東集團",
    "0005.HK": "匯豐控股",
    "1299.HK": "友邦保險",
    "2318.HK": "中國平安"
}

HOURS_BACK = 48

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== 工具函式 ====================

def get_stock_price(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d")
        if hist.empty or len(hist) < 2:
            return {"symbol": symbol, "error": "無資料"}
        latest = hist.iloc[-1]
        prev = hist.iloc[-2]
        current = float(latest["Close"])
        prev_close = float(prev["Close"])
        change = current - prev_close
        change_pct = (change / prev_close) * 100 if prev_close else 0
        return {
            "symbol": symbol,
            "price": round(current, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "open": round(float(latest["Open"]), 2),
            "high": round(float(latest["High"]), 2),
            "low": round(float(latest["Low"]), 2),
            "volume": int(latest["Volume"]),
            "currency": "HKD" if symbol.endswith(".HK") else "USD"
        }
    except Exception as e:
        logger.error(f"股價錯誤 {symbol}: {e}")
        return {"symbol": symbol, "error": str(e)}

def get_history(symbol, period="1mo"):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period)
        if hist.empty:
            return []
        data = []
        for idx, row in hist.iterrows():
            data.append({
                "date": idx.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"])
            })
        return data
    except Exception as e:
        logger.error(f"歷史資料錯誤 {symbol}: {e}")
        return []

def fetch_us_news(symbol):
    url = f"https://api.quotelinks.com/v1/news/{symbol}"
    req = urllib.request.Request(url, headers={"User-Agent": "StockNewsFull/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.load(r)
            return data.get("items", [])
    except Exception as e:
        logger.error(f"美股新聞錯誤 {symbol}: {e}")
        return []

def fetch_hk_news(symbol):
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news or []
        result = []
        for n in news[:8]:
            result.append({
                "id": n.get("uuid", str(time.time())),
                "title": n.get("title", "無標題"),
                "url": n.get("link", "#"),
                "published": datetime.fromtimestamp(
                    n.get("providerPublishTime", time.time()), tz=timezone.utc
                ).isoformat(),
                "publisher": {"name": n.get("publisher", "Yahoo Finance")},
                "symbols": [symbol],
                "tags": [],
                "sentiment": {"score": 0.0, "label": "neutral"}
            })
        return result
    except Exception as e:
        logger.error(f"港股新聞錯誤 {symbol}: {e}")
        return []

def analyze_news(item, market="US"):
    title = item.get("title", "")
    tags = item.get("tags", [])
    sent = item.get("sentiment", {})
    score = sent.get("score", 0)
    label = sent.get("label", "neutral")

    if score >= 0.35:
        sentiment_text = "偏正面"
        overall = "這則新聞整體偏向正面，短期可能對股價有支撐作用。"
    elif score <= -0.35:
        sentiment_text = "偏負面"
        overall = "這則新聞整體偏向負面，短期可能對股價造成壓力。"
    else:
        sentiment_text = "中性"
        overall = "這則新聞情緒偏向中性，對股價的直接影響可能有限。"

    positive_points = []
    if "earnings" in tags or "guidance" in tags:
        positive_points.append("若財報或展望優於預期，可能吸引買盤進場。")
    if "analyst_rating" in tags:
        positive_points.append("分析師上調評級通常會帶動短期資金流入。")
    if "product_launch" in tags or "buyback" in tags or "dividend" in tags:
        positive_points.append("新產品、庫藏股或股息增加，長期有利公司價值。")
    if not positive_points:
        positive_points.append("市場可能解讀為公司營運穩定或有潛在利多。")

    negative_points = []
    if "earnings" in tags or "guidance" in tags:
        negative_points.append("若數據不如預期，可能引發獲利了結或賣壓。")
    if "analyst_rating" in tags:
        negative_points.append("下調評級可能引發短期拋售情緒。")
    if score < 0:
        negative_points.append("負面情緒可能放大市場波動，尤其在成交量放大時。")
    if not negative_points:
        negative_points.append("需留意消息是否已提前反映在股價中，避免追高殺低。")

    impact = []
    if abs(score) >= 0.4:
        impact.append("情緒較強烈，短線波動可能加大。")
    else:
        impact.append("情緒溫和，影響可能較為有限。")
    impact.append("建議同時觀察成交量與大盤走勢，判斷資金真實態度。")
    impact.append("單一新聞不應作為唯一進出依據，需結合基本面與技術面。")

    return {
        "sentiment_text": sentiment_text,
        "overall": overall,
        "positive": positive_points,
        "negative": negative_points,
        "impact": impact,
        "score": score,
        "label": label
    }

@lru_cache(maxsize=2)
def get_dashboard_data(cache_key):
    price_info = {}
    for sym in list(US_WATCHLIST.keys()) + list(HK_WATCHLIST.keys()):
        price_info[sym] = get_stock_price(sym)
        time.sleep(0.15)
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "price_info": price_info
    }

@lru_cache(maxsize=4)
def get_news_data(market, cache_key):
    since = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    all_news = []
    seen = set()
    watchlist = US_WATCHLIST if market == "US" else HK_WATCHLIST

    for symbol in watchlist:
        if market == "US":
            items = fetch_us_news(symbol)
            time.sleep(0.5)
        else:
            items = fetch_hk_news(symbol)
            time.sleep(0.3)

        for item in items:
            try:
                pub = datetime.fromisoformat(item["published"].replace("Z", "+00:00"))
            except:
                continue
            if pub < since or item["id"] in seen:
                continue
            seen.add(item["id"])
            item["market"] = market
            item["main_symbol"] = symbol
            item["analysis"] = analyze_news(item, market)
            all_news.append(item)

    all_news.sort(key=lambda x: x["published"], reverse=True)
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "news": all_news[:40]
    }

SIDEBAR = """
<div class="sidebar">
    <div class="logo">美股・港股分析</div>
    <nav>
        <a href="/" class="nav-item">總覽儀表板</a>
        <div class="nav-group">股票</div>
        <a href="/stocks/us" class="nav-item sub">美股</a>
        <a href="/stocks/hk" class="nav-item sub">港股</a>
        <div class="nav-group">新聞資訊</div>
        <a href="/news/us" class="nav-item sub">美股新聞</a>
        <a href="/news/hk" class="nav-item sub">港股新聞</a>
        <div class="nav-group">說明</div>
        <a href="/about" class="nav-item sub">關於本站</a>
    </nav>
    <div class="sidebar-footer">資料僅供參考<br>不構成投資建議</div>
</div>
"""

BASE_STYLE = """
:root {
    --bg: #0f172a; --sidebar: #1e293b; --card: #1e293b; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --green: #22c55e; --red: #ef4444;
    --blue: #3b82f6; --yellow: #eab308; --accent: #60a5fa;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans TC", sans-serif; background: var(--bg); color: var(--text); display: flex; min-height: 100vh; }
.sidebar { width: 240px; background: var(--sidebar); border-right: 1px solid var(--border); padding: 24px 0; position: fixed; height: 100vh; overflow-y: auto; }
.logo { font-size: 1.15rem; font-weight: 700; padding: 0 20px 24px; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
.nav-group { padding: 16px 20px 6px; font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.nav-item { display: block; padding: 10px 20px; color: var(--text); text-decoration: none; font-size: 0.95rem; transition: background 0.15s; }
.nav-item:hover, .nav-item.active { background: #334155; color: var(--accent); }
.nav-item.sub { padding-left: 32px; font-size: 0.9rem; }
.sidebar-footer { position: absolute; bottom: 20px; left: 0; right: 0; text-align: center; font-size: 0.75rem; color: var(--muted); padding: 0 12px; }
.main { margin-left: 240px; flex: 1; padding: 28px 32px; max-width: 1100px; }
h1 { font-size: 1.7rem; margin-bottom: 8px; }
.subtitle { color: var(--muted); margin-bottom: 24px; }
.section-title { font-size: 1.25rem; margin: 28px 0 14px; padding-left: 10px; border-left: 4px solid var(--blue); }
.price-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
.price-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px; text-align: center; text-decoration: none; color: inherit; transition: border-color 0.15s; }
.price-card:hover { border-color: var(--blue); }
.price-card .symbol { font-weight: 600; font-size: 1rem; }
.price-card .name { font-size: 0.8rem; color: var(--muted); margin-top: 2px; }
.price-card .price { font-size: 1.15rem; margin: 8px 0 4px; }
.up { color: var(--green); } .down { color: var(--red); } .flat { color: var(--muted); }
.news-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 18px; }
.news-card h3 { font-size: 1.1rem; margin-bottom: 10px; line-height: 1.4; }
.news-meta { display: flex; flex-wrap: wrap; gap: 10px; font-size: 0.85rem; color: var(--muted); margin-bottom: 12px; }
.tag { background: #334155; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; }
.sentiment-pos { color: var(--green); } .sentiment-neg { color: var(--red); } .sentiment-neu { color: var(--yellow); }
.analysis-box { background: #0f172a; border-left: 4px solid var(--blue); padding: 14px 16px; margin-top: 14px; border-radius: 0 8px 8px 0; }
.analysis-box h4 { font-size: 0.92rem; margin: 12px 0 6px; color: #93c5fd; }
.analysis-box h4:first-child { margin-top: 0; }
.analysis-box ul { margin-left: 18px; font-size: 0.9rem; color: var(--muted); }
.analysis-box li { margin-bottom: 4px; }
.link-btn { display: inline-block; margin-top: 10px; color: var(--blue); text-decoration: none; font-size: 0.9rem; }
.link-btn:hover { text-decoration: underline; }
.chart-container { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 24px; }
.period-btns { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.period-btn { background: #334155; border: none; color: var(--text); padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 0.85rem; }
.period-btn.active, .period-btn:hover { background: var(--blue); }
.stock-header { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 16px; margin-bottom: 20px; }
.stock-price-big { font-size: 2rem; font-weight: 700; }
.loading { text-align: center; padding: 40px; color: var(--muted); }
table.data-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; margin-top: 12px; }
table.data-table th, table.data-table td { padding: 8px 12px; border-bottom: 1px solid var(--border); text-align: right; }
table.data-table th { color: var(--muted); font-weight: 500; }
table.data-table td:first-child, table.data-table th:first-child { text-align: left; }
@media (max-width: 800px) { .sidebar { width: 100%; height: auto; position: relative; } .main { margin-left: 0; padding: 20px; } body { flex-direction: column; } }
"""

def render_page(title, page, content, extra_js=""):
    html = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} | 美股・港股分析</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <style>{BASE_STYLE}</style>
</head>
<body>
    {SIDEBAR}
    <div class="main">{content}</div>
    <script>{extra_js}</script>
</body>
</html>"""
    return html

@app.route("/")
def home():
    cache_key = datetime.now().strftime("%Y%m%d%H%M")[:11]
    data = get_dashboard_data(cache_key)
    price_info = data["price_info"]

    us_cards = ""
    for sym, name in US_WATCHLIST.items():
        p = price_info.get(sym, {})
        if "error" in p:
            us_cards += f'<a class="price-card" href="/stock/{sym}"><div class="symbol">{sym}</div><div class="name">{name}</div><div>無資料</div></a>'
        else:
            cls = "up" if p["change_pct"] > 0 else ("down" if p["change_pct"] < 0 else "flat")
            arrow = "▲" if p["change_pct"] > 0 else ("▼" if p["change_pct"] < 0 else "─")
            us_cards += f'<a class="price-card" href="/stock/{sym}"><div class="symbol">{sym}</div><div class="name">{name}</div><div class="price">${p["price"]}</div><div class="{cls}">{arrow} {p["change_pct"]:+.2f}%</div></a>'

    hk_cards = ""
    for sym, name in HK_WATCHLIST.items():
        p = price_info.get(sym, {})
        if "error" in p:
            hk_cards += f'<a class="price-card" href="/stock/{sym}"><div class="symbol">{sym}</div><div class="name">{name}</div><div>無資料</div></a>'
        else:
            cls = "up" if p["change_pct"] > 0 else ("down" if p["change_pct"] < 0 else "flat")
            arrow = "▲" if p["change_pct"] > 0 else ("▼" if p["change_pct"] < 0 else "─")
            hk_cards += f'<a class="price-card" href="/stock/{sym}"><div class="symbol">{sym}</div><div class="name">{name}</div><div class="price">HK${p["price"]}</div><div class="{cls}">{arrow} {p["change_pct"]:+.2f}%</div></a>'

    content = f"""
    <h1>總覽儀表板</h1>
    <div class="subtitle">最後更新：{data["generated_at"]} ｜ 點擊股票可查看詳細走勢與資料</div>
    <div class="section-title">美股</div>
    <div class="price-grid">{us_cards}</div>
    <div class="section-title">港股</div>
    <div class="price-grid">{hk_cards}</div>
    """
    return render_page("總覽", "home", content)

@app.route("/stocks/us")
def stocks_us():
    cache_key = datetime.now().strftime("%Y%m%d%H%M")[:11]
    data = get_dashboard_data(cache_key)
    price_info = data["price_info"]
    cards = ""
    for sym, name in US_WATCHLIST.items():
        p = price_info.get(sym, {})
        if "error" in p:
            cards += f'<a class="price-card" href="/stock/{sym}"><div class="symbol">{sym}</div><div class="name">{name}</div><div>無資料</div></a>'
        else:
            cls = "up" if p["change_pct"] > 0 else ("down" if p["change_pct"] < 0 else "flat")
            arrow = "▲" if p["change_pct"] > 0 else ("▼" if p["change_pct"] < 0 else "─")
            cards += f'<a class="price-card" href="/stock/{sym}"><div class="symbol">{sym}</div><div class="name">{name}</div><div class="price">${p["price"]}</div><div class="{cls}">{arrow} {p["change_pct"]:+.2f}%</div></a>'
    content = f'<h1>美股列表</h1><div class="subtitle">點擊任一股票可查看完整走勢圖與詳細數據</div><div class="price-grid">{cards}</div>'
    return render_page("美股", "stocks-us", content)

@app.route("/stocks/hk")
def stocks_hk():
    cache_key = datetime.now().strftime("%Y%m%d%H%M")[:11]
    data = get_dashboard_data(cache_key)
    price_info = data["price_info"]
    cards = ""
    for sym, name in HK_WATCHLIST.items():
        p = price_info.get(sym, {})
        if "error" in p:
            cards += f'<a class="price-card" href="/stock/{sym}"><div class="symbol">{sym}</div><div class="name">{name}</div><div>無資料</div></a>'
        else:
            cls = "up" if p["change_pct"] > 0 else ("down" if p["change_pct"] < 0 else "flat")
            arrow = "▲" if p["change_pct"] > 0 else ("▼" if p["change_pct"] < 0 else "─")
            cards += f'<a class="price-card" href="/stock/{sym}"><div class="symbol">{sym}</div><div class="name">{name}</div><div class="price">HK${p["price"]}</div><div class="{cls}">{arrow} {p["change_pct"]:+.2f}%</div></a>'
    content = f'<h1>港股列表</h1><div class="subtitle">點擊任一股票可查看完整走勢圖與詳細數據</div><div class="price-grid">{cards}</div>'
    return render_page("港股", "stocks-hk", content)

@app.route("/stock/<symbol>")
def stock_detail(symbol):
    name = US_WATCHLIST.get(symbol) or HK_WATCHLIST.get(symbol) or symbol
    price = get_stock_price(symbol)
    is_hk = symbol.endswith(".HK")
    curr = "HK$" if is_hk else "$"

    if "error" in price:
        price_html = "<div>無法取得即時價格</div>"
    else:
        cls = "up" if price["change_pct"] > 0 else ("down" if price["change_pct"] < 0 else "flat")
        arrow = "▲" if price["change_pct"] > 0 else ("▼" if price["change_pct"] < 0 else "─")
        price_html = f'<div><div class="stock-price-big">{curr}{price["price"]}</div><div class="{cls}" style="font-size:1.2rem;">{arrow} {price["change"]:+.2f} ({price["change_pct"]:+.2f}%)</div><div style="margin-top:8px; color:var(--muted); font-size:0.9rem;">開 {curr}{price["open"]} ｜ 高 {curr}{price["high"]} ｜ 低 {curr}{price["low"]} ｜ 量 {price["volume"]:,}</div></div>'

    content = f"""
    <div class="stock-header">
        <div><h1>{symbol} <span style="font-size:1.1rem; color:var(--muted); font-weight:400;">{name}</span></h1><div class="subtitle">完整走勢與歷史數據</div></div>
        {price_html}
    </div>
    <div class="chart-container">
        <div class="period-btns">
            <button class="period-btn active" data-period="5d">5日</button>
            <button class="period-btn" data-period="1mo">1個月</button>
            <button class="period-btn" data-period="3mo">3個月</button>
            <button class="period-btn" data-period="6mo">6個月</button>
            <button class="period-btn" data-period="1y">1年</button>
            <button class="period-btn" data-period="2y">2年</button>
        </div>
        <canvas id="priceChart" height="120"></canvas>
    </div>
    <div class="section-title">歷史數據表（最近資料）</div>
    <div id="data-table-container" class="loading">載入中...</div>
    """

    extra_js = f"""
    const symbol = "{symbol}";
    let chart = null;
    async function loadChart(period) {{
        document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
        document.querySelector(`[data-period="${{period}}"]`).classList.add('active');
        const res = await fetch(`/api/history/${{symbol}}?period=${{period}}`);
        const data = await res.json();
        const labels = data.map(d => d.date);
        const closes = data.map(d => d.close);
        if (chart) chart.destroy();
        const ctx = document.getElementById('priceChart').getContext('2d');
        chart = new Chart(ctx, {{
            type: 'line',
            data: {{ labels: labels, datasets: [{{ label: '收盤價', data: closes, borderColor: '#3b82f6', backgroundColor: 'rgba(59, 130, 246, 0.1)', fill: true, tension: 0.1, pointRadius: 0 }}] }},
            options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }}, scales: {{ x: {{ ticks: {{ color: '#94a3b8', maxTicksLimit: 8 }}, grid: {{ color: '#334155' }} }}, y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }} }} }}
        }});
        let table = '<table class="data-table"><thead><tr><th>日期</th><th>開盤</th><th>最高</th><th>最低</th><th>收盤</th><th>成交量</th></tr></thead><tbody>';
        const recent = data.slice(-15).reverse();
        for (const d of recent) {{ table += `<tr><td>${{d.date}}</td><td>${{d.open}}</td><td>${{d.high}}</td><td>${{d.low}}</td><td>${{d.close}}</td><td>${{d.volume.toLocaleString()}}</td></tr>`; }}
        table += '</tbody></table>';
        document.getElementById('data-table-container').innerHTML = table;
    }}
    document.querySelectorAll('.period-btn').forEach(btn => {{ btn.addEventListener('click', () => loadChart(btn.dataset.period)); }});
    loadChart('1mo');
    """
    return render_page(f"{symbol} 詳情", "stocks-us" if not is_hk else "stocks-hk", content, extra_js)

@app.route("/news/us")
def news_us():
    cache_key = datetime.now().strftime("%Y%m%d%H%M")[:11]
    data = get_news_data("US", cache_key)
    return render_news_page("美股新聞", "news-us", data)

@app.route("/news/hk")
def news_hk():
    cache_key = datetime.now().strftime("%Y%m%d%H%M")[:11]
    data = get_news_data("HK", cache_key)
    return render_news_page("港股新聞", "news-hk", data)

def render_news_page(title, page, data):
    news_html = ""
    if not data["news"]:
        news_html = '<div class="loading">目前沒有符合條件的新聞</div>'
    else:
        for n in data["news"]:
            a = n.get("analysis", {})
            sent_class = "sentiment-pos" if a.get("label") == "positive" else ("sentiment-neg" if a.get("label") == "negative" else "sentiment-neu")
            tags = "".join([f'<span class="tag">{t}</span>' for t in n.get("tags", [])])
            pos_list = "".join([f"<li>{p}</li>" for p in a.get("positive", [])])
            neg_list = "".join([f"<li>{p}</li>" for p in a.get("negative", [])])
            impact_list = "".join([f"<li>{p}</li>" for p in a.get("impact", [])])
            news_html += f"""
            <div class="news-card">
                <h3>{n.get("title", "")}</h3>
                <div class="news-meta">
                    <span>{n.get("published", "")[:16].replace("T", " ")} UTC</span>
                    <span>{n.get("publisher", {}).get("name", "未知")}</span>
                    <span>{n.get("main_symbol", "")}</span>
                    <span class="{sent_class}">情緒：{a.get("sentiment_text", "中性")} ({a.get("score", 0):+.2f})</span>
                    {tags}
                </div>
                <a class="link-btn" href="{n.get("url", "#")}" target="_blank" rel="noopener">閱讀原文 →</a>
                <div class="analysis-box">
                    <h4>📌 重點整合</h4>
                    <p style="font-size:0.95rem; margin-bottom:8px;">{a.get("overall", "")}</p>
                    <h4>正面角度</h4><ul>{pos_list}</ul>
                    <h4>風險 / 負面角度</h4><ul>{neg_list}</ul>
                    <h4>可能對市場的影響</h4><ul>{impact_list}</ul>
                </div>
            </div>"""
    content = f'<h1>{title}</h1><div class="subtitle">最後更新：{data["generated_at"]} ｜ 來源盡量選用大型與相對中性的媒體</div>{news_html}'
    return render_page(title, page, content)

@app.route("/about")
def about():
    content = """
    <h1>關於本站</h1>
    <div class="subtitle">美股與港股綜合分析工具</div>
    <div class="section-title">資料來源</div>
    <ul style="margin-left:20px; color:var(--muted); line-height:1.8;">
        <li>股價與歷史走勢：Yahoo Finance（透過 yfinance）</li>
        <li>美股新聞：Quotelinks（彙整多家媒體，含情緒標籤）</li>
        <li>港股新聞：Yahoo Finance 新聞彙整</li>
    </ul>
    <div class="section-title">分析說明</div>
    <p style="color:var(--muted); line-height:1.7; margin-bottom:12px;">
        每則新聞都會提供「重點整合」、「正面角度」、「風險/負面角度」以及「可能對市場的影響」。
        分析是根據新聞標題、情緒分數與常見市場邏輯自動產生，僅供參考，不構成投資建議。
    </p>
    <div class="section-title">注意事項</div>
    <ul style="margin-left:20px; color:var(--muted); line-height:1.8;">
        <li>本站資料有短時間快取，可能與即時行情有數分鐘落差。</li>
        <li>新聞來源來自公開彙整，無法完全保證每則都來自「完全中立」的媒體。</li>
        <li>投資有風險，請自行判斷並承擔責任。</li>
    </ul>
    """
    return render_page("關於", "about", content)

@app.route("/api/history/<symbol>")
def api_history(symbol):
    period = request.args.get("period", "1mo")
    data = get_history(symbol, period)
    return jsonify(data)

@app.route("/api/dashboard")
def api_dashboard():
    cache_key = datetime.now().strftime("%Y%m%d%H%M")[:11]
    return jsonify(get_dashboard_data(cache_key))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
