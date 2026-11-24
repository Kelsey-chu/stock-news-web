#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股 + 港股 公開新聞分析網站
"""

from flask import Flask, render_template_string, jsonify
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
import yfinance as yf
import logging
from functools import lru_cache
import os

app = Flask(__name__)

# ==================== 設定區 ====================
US_WATCHLIST = ["AAPL", "MSFT", "NVDA", "TSLA", "GOOGL", "AMZN", "META", "AMD"]
HK_WATCHLIST = [
    "0700.HK",   # 騰訊
    "9988.HK",   # 阿里巴巴
    "3690.HK",   # 美團
    "1810.HK",   # 小米
    "9618.HK",   # 京東
    "0005.HK",   # 匯豐
    "1299.HK",   # 友邦
    "2318.HK",   # 中國平安
]

HOURS_BACK = 36

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== 新聞影響規則 ====================
IMPACT_RULES = {
    "earnings": {
        "positive": "財報優於預期，通常利好股價",
        "negative": "財報遜於預期，通常利空股價",
        "neutral": "財報大致符合預期，影響相對有限"
    },
    "guidance": {
        "positive": "公司上調未來展望，屬強烈正面訊號",
        "negative": "公司下調未來展望，屬強烈負面訊號",
        "neutral": "維持原先展望"
    },
    "analyst_rating": {
        "positive": "分析師上調評級或目標價，短期偏多",
        "negative": "分析師下調評級或目標價，短期偏空",
        "neutral": "維持評級，影響較小"
    },
    "merger_acquisition": {
        "positive": "併購消息（尤其被併方）通常利好",
        "negative": "併購破局或條件不利，可能利空",
        "neutral": "併購進度更新"
    },
    "product_launch": {
        "positive": "新產品發表順利，中長期偏多",
        "negative": "產品延遲或評價不佳，偏空",
        "neutral": "產品相關消息"
    },
    "dividend": {
        "positive": "增加股息或特別股息，對股價偏多",
        "negative": "削減股息，通常利空",
        "neutral": "股息政策相關"
    },
    "buyback": {
        "positive": "庫藏股計畫，支撐股價",
        "negative": "取消或減少庫藏股，偏空",
        "neutral": "庫藏股相關"
    },
    "insider_trade": {
        "positive": "內部人大量買進，屬正面訊號",
        "negative": "內部人大量賣出，需提高警覺",
        "neutral": "內部人交易"
    },
    "default": {
        "positive": "整體情緒偏正面",
        "negative": "整體情緒偏負面",
        "neutral": "情緒中性"
    }
}

def get_impact_and_notes(tags, sentiment_label, score, title):
    main_tag = tags[0] if tags else "default"
    rule = IMPACT_RULES.get(main_tag, IMPACT_RULES["default"])
    impact = rule.get(sentiment_label, rule["neutral"])

    if score >= 0.4:
        summary = f"這則新聞情緒明顯偏多（分數 {score:+.2f}），市場可能解讀為正面發展。"
    elif score <= -0.4:
        summary = f"這則新聞情緒明顯偏空（分數 {score:+.2f}），需留意短期股價壓力。"
    else:
        summary = f"這則新聞情緒中性偏弱（分數 {score:+.2f}），對股價影響可能有限。"

    notes = []
    if "earnings" in tags or "guidance" in tags:
        notes.append("財報／展望類消息對股價影響通常較大，建議關注後續法人評論。")
    if "analyst_rating" in tags:
        notes.append("分析師評級變動後，短期可能出現跟風買賣，波動會放大。")
    if "insider_trade" in tags:
        notes.append("內部人交易僅供參考，需結合公司基本面與產業趨勢判斷。")
    if abs(score) < 0.15:
        notes.append("情緒分數接近零，實際影響可能不大，勿過度解讀。")
    if not notes:
        notes.append("建議搭配成交量與技術面一起觀察，避免單一新聞過度反應。")

    return {
        "impact": impact,
        "summary": summary,
        "notes": notes
    }

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

def fetch_us_news(symbol):
    url = f"https://api.quotelinks.com/v1/news/{symbol}"
    req = urllib.request.Request(url, headers={"User-Agent": "StockNewsPublic/1.0"})
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
        for n in news[:6]:
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

# 快取 10 分鐘，避免每次刷新都狂打 API
@lru_cache(maxsize=1)
def collect_all_data_cached(cache_key):
    return _collect_all_data()

def _collect_all_data():
    since = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    all_news = []
    price_info = {}
    seen = set()

    for symbol in US_WATCHLIST:
        price_info[symbol] = get_stock_price(symbol)
        time.sleep(0.2)
        items = fetch_us_news(symbol)
        time.sleep(0.5)
        for item in items:
            try:
                pub = datetime.fromisoformat(item["published"].replace("Z", "+00:00"))
            except:
                continue
            if pub < since or item["id"] in seen:
                continue
            seen.add(item["id"])
            item["market"] = "US"
            item["main_symbol"] = symbol
            sent = item.get("sentiment", {})
            item["analysis"] = get_impact_and_notes(
                item.get("tags", []),
                sent.get("label", "neutral"),
                sent.get("score", 0),
                item.get("title", "")
            )
            all_news.append(item)

    for symbol in HK_WATCHLIST:
        price_info[symbol] = get_stock_price(symbol)
        time.sleep(0.25)
        items = fetch_hk_news(symbol)
        for item in items:
            try:
                pub = datetime.fromisoformat(item["published"].replace("Z", "+00:00"))
            except:
                continue
            if pub < since or item["id"] in seen:
                continue
            seen.add(item["id"])
            item["market"] = "HK"
            item["main_symbol"] = symbol
            item["analysis"] = get_impact_and_notes(
                item.get("tags", []),
                "neutral",
                0.0,
                item.get("title", "")
            )
            all_news.append(item)

    all_news.sort(key=lambda x: x["published"], reverse=True)
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "price_info": price_info,
        "news": all_news
    }

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>美股・港股 每日新聞分析</title>
    <meta name="description" content="即時美股與港股新聞、股價漲跌與情緒影響分析">
    <style>
        :root {
            --bg: #0f172a;
            --card: #1e293b;
            --border: #334155;
            --text: #e2e8f0;
            --muted: #94a3b8;
            --green: #22c55e;
            --red: #ef4444;
            --blue: #3b82f6;
            --yellow: #eab308;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans TC", sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 20px;
        }
        .container { max-width: 1100px; margin: 0 auto; }
        header {
            text-align: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border);
        }
        h1 { font-size: 1.8rem; margin-bottom: 8px; }
        .subtitle { color: var(--muted); font-size: 0.95rem; }
        .refresh-btn {
            background: var(--blue);
            color: white;
            border: none;
            padding: 10px 22px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 0.95rem;
            margin-top: 15px;
            transition: background 0.2s;
        }
        .refresh-btn:hover { background: #2563eb; }
        .section-title {
            font-size: 1.3rem;
            margin: 30px 0 15px;
            padding-left: 10px;
            border-left: 4px solid var(--blue);
        }
        .price-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(155px, 1fr));
            gap: 12px;
            margin-bottom: 20px;
        }
        .price-card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 14px;
            text-align: center;
        }
        .price-card .symbol { font-weight: 600; font-size: 1.05rem; }
        .price-card .price { font-size: 1.2rem; margin: 6px 0; }
        .up { color: var(--green); }
        .down { color: var(--red); }
        .flat { color: var(--muted); }
        .news-card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .news-card h3 {
            font-size: 1.12rem;
            margin-bottom: 10px;
            line-height: 1.4;
        }
        .news-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            font-size: 0.85rem;
            color: var(--muted);
            margin-bottom: 12px;
        }
        .tag {
            background: #334155;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.8rem;
        }
        .sentiment-pos { color: var(--green); }
        .sentiment-neg { color: var(--red); }
        .sentiment-neu { color: var(--yellow); }
        .analysis-box {
            background: #0f172a;
            border-left: 4px solid var(--blue);
            padding: 14px 16px;
            margin-top: 16px;
            border-radius: 0 8px 8px 0;
        }
        .analysis-box h4 {
            font-size: 0.95rem;
            margin-bottom: 8px;
            color: #93c5fd;
        }
        .analysis-box ul {
            margin-left: 18px;
            font-size: 0.9rem;
            color: var(--muted);
        }
        .analysis-box li { margin-bottom: 4px; }
        .link-btn {
            display: inline-block;
            margin-top: 12px;
            color: var(--blue);
            text-decoration: none;
            font-size: 0.9rem;
        }
        .link-btn:hover { text-decoration: underline; }
        .loading {
            text-align: center;
            padding: 60px 20px;
            color: var(--muted);
        }
        footer {
            text-align: center;
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid var(--border);
            color: var(--muted);
            font-size: 0.85rem;
        }
        @media (max-width: 600px) {
            h1 { font-size: 1.4rem; }
            .price-grid { grid-template-columns: repeat(2, 1fr); }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>美股・港股 每日新聞分析</h1>
            <div class="subtitle">即時股價 + 新聞情緒影響分析</div>
            <div class="subtitle" id="update-time">載入中...</div>
            <button class="refresh-btn" onclick="location.reload()">重新整理資料</button>
        </header>

        <div id="content">
            <div class="loading">正在抓取最新股價與新聞，請稍候（約 15~30 秒）...</div>
        </div>

        <footer>
            資料來源：Quotelinks（美股新聞）、Yahoo Finance（港股新聞與股價）<br>
            本分析僅供參考，不構成任何投資建議
        </footer>
    </div>

    <script>
        async function loadData() {
            try {
                const res = await fetch('/api/data');
                const data = await res.json();
                render(data);
            } catch (e) {
                document.getElementById('content').innerHTML = 
                    '<div class="loading">載入失敗，請稍後再試或重新整理</div>';
            }
        }

        function render(data) {
            document.getElementById('update-time').textContent = 
                '最後更新：' + data.generated_at;

            let html = '';

            html += '<div class="section-title">今日股價總覽</div>';
            html += '<div class="price-grid">';

            const symbols = Object.keys(data.price_info);
            for (const sym of symbols) {
                const p = data.price_info[sym];
                if (p.error) {
                    html += `<div class="price-card"><div class="symbol">${sym}</div><div>無資料</div></div>`;
                    continue;
                }
                const cls = p.change_pct > 0 ? 'up' : (p.change_pct < 0 ? 'down' : 'flat');
                const arrow = p.change_pct > 0 ? '▲' : (p.change_pct < 0 ? '▼' : '─');
                const curr = p.currency === 'HKD' ? 'HK$' : '$';
                html += `
                    <div class="price-card">
                        <div class="symbol">${sym}</div>
                        <div class="price">${curr}${p.price}</div>
                        <div class="${cls}">${arrow} ${p.change_pct > 0 ? '+' : ''}${p.change_pct}%</div>
                    </div>`;
            }
            html += '</div>';

            html += '<div class="section-title">最新新聞與影響分析</div>';

            if (!data.news || data.news.length === 0) {
                html += '<div class="loading">目前沒有符合條件的新聞</div>';
            } else {
                for (const n of data.news) {
                    const sent = n.sentiment || {};
                    const label = sent.label || 'neutral';
                    const score = sent.score || 0;
                    const sentClass = label === 'positive' ? 'sentiment-pos' : 
                                     (label === 'negative' ? 'sentiment-neg' : 'sentiment-neu');
                    const sentText = label === 'positive' ? '偏多' : 
                                    (label === 'negative' ? '偏空' : '中性');
                    const analysis = n.analysis || {};
                    const tags = (n.tags || []).map(t => `<span class="tag">${t}</span>`).join(' ');

                    html += `
                    <div class="news-card">
                        <h3>${n.title}</h3>
                        <div class="news-meta">
                            <span>${n.published?.slice(0,16).replace('T',' ') || ''} UTC</span>
                            <span>${n.publisher?.name || '未知來源'}</span>
                            <span>${n.main_symbol}（${n.market}）</span>
                            <span class="${sentClass}">情緒：${sentText} (${score > 0 ? '+' : ''}${score.toFixed(2)})</span>
                            ${tags}
                        </div>
                        <a class="link-btn" href="${n.url}" target="_blank" rel="noopener">閱讀原文 →</a>
                        
                        <div class="analysis-box">
                            <h4>📌 重點整合</h4>
                            <p style="margin-bottom:10px; font-size:0.95rem;">${analysis.summary || ''}</p>
                            <p style="margin-bottom:8px;"><strong>股價影響判斷：</strong>${analysis.impact || ''}</p>
                            <h4 style="margin-top:12px;">⚠️ 投資人注意事項</h4>
                            <ul>
                                ${(analysis.notes || []).map(note => `<li>${note}</li>`).join('')}
                            </ul>
                        </div>
                    </div>`;
                }
            }

            document.getElementById('content').innerHTML = html;
        }

        loadData();
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/data")
def api_data():
    # 每 10 分鐘更新一次快取
    cache_key = datetime.now().strftime("%Y%m%d%H%M")[:11]  # 精確到 10 分鐘
    data = collect_all_data_cached(cache_key)
    return jsonify(data)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
