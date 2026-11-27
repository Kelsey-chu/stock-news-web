#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股 + 港股 完整分析網站
升級：新聞交叉對比、公司營收/基本面、競爭公司新聞
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
    "AAPL": "蘋果",
    "MSFT": "微軟",
    "NVDA": "輝達",
    "TSLA": "特斯拉",
    "GOOGL": "Alphabet",
    "AMZN": "亞馬遜",
    "META": "Meta",
    "AMD": "超微",
    "TSM": "台積電 (TSMC)",
    "VOO": "Vanguard S&P 500 ETF",
    "IVV": "iShares Core S&P 500 ETF",
    "SPY": "SPDR S&P 500 ETF",
    "QQQ": "Invesco QQQ",
    "AVGO": "博通",
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

# 競爭對手對照表
COMPETITORS = {
    "NVDA": ["AMD", "AVGO", "TSM", "INTC", "ASML"],
    "AMD": ["NVDA", "INTC", "AVGO", "TSM"],
    "TSM": ["NVDA", "ASML", "INTC", "AMAT", "Samsung"],
    "AAPL": ["MSFT", "GOOGL", "AMZN", "META"],
    "MSFT": ["AAPL", "GOOGL", "AMZN", "ORCL"],
    "GOOGL": ["MSFT", "AAPL", "META", "AMZN"],
    "AMZN": ["AAPL", "MSFT", "GOOGL", "WMT"],
    "META": ["GOOGL", "SNAP", "PINS"],
    "TSLA": ["GM", "F", "RIVN", "NIO"],
    "AVGO": ["NVDA", "QCOM", "TXN", "MRVL"],
    "ASML": ["AMAT", "LRCX", "KLAC"],
    "INTC": ["AMD", "NVDA", "TSM"],
    "QCOM": ["AVGO", "TXN", "MRVL"],
    "MU": ["WDC", "STX"],
    "PLTR": ["SNOW", "DDOG", "MDB"],
    "0700.HK": ["9988.HK", "BABA", "JD", "PDD"],
    "9988.HK": ["0700.HK", "JD", "PDD", "AMZN"],
    "3690.HK": ["0700.HK", "9988.HK"],
    "1810.HK": ["AAPL", "SSNLF"],
}

HOURS_BACK = 72

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
            "currency": "HKD" if symbol.endswith(".HK") else "USD",
            "prev_close": round(prev_close, 2)
        }
    except Exception as e:
        logger.error(f"股價錯誤 {symbol}: {e}")
        return {"symbol": symbol, "error": str(e)}

def get_company_info(symbol):
    """以正式財報為核心：抓取多季營收/淨利並做簡單對比分析"""
    is_hk = symbol.endswith(".HK")
    if is_hk:
        official_url = "https://www.hkexnews.hk/search/titlesearch.xhtml?lang=zh"
        official_label = "港交所披露易（官方公告）"
    else:
        official_url = f"https://www.sec.gov/edgar/search/#/category=custom&entityName={symbol}"
        official_label = "SEC EDGAR（美國證交會正式財報）"

    result = {
        "longName": symbol,
        "currency": "HKD" if is_hk else "USD",
        "official_url": official_url,
        "official_label": official_label,
        "website": None,
        "quarterly": [],
        "annual": [],
        "analysis_text": [],
        "error": None
    }

    try:
        ticker = yf.Ticker(symbol)

        # 名稱
        try:
            info = ticker.info or {}
            result["longName"] = info.get("longName") or info.get("shortName") or symbol
            result["website"] = info.get("website")
            result["currency"] = info.get("currency") or result["currency"]
        except Exception:
            info = {}

        def extract_financials(df, max_cols=4):
            rows = []
            if df is None or getattr(df, "empty", True):
                return rows
            cols = list(df.columns)[:max_cols]
            for col in cols:
                date_str = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)[:10]
                rev = net = gross = op = None
                for key in df.index:
                    k = str(key).lower()
                    try:
                        val = df.loc[key, col]
                        if val is None or str(val) == "nan":
                            continue
                        val = float(val)
                    except Exception:
                        continue
                    if rev is None and ("total revenue" in k or k == "revenue" or "operating revenue" in k):
                        rev = val
                    if net is None and "net income" in k:
                        net = val
                    if gross is None and "gross profit" in k:
                        gross = val
                    if op is None and ("operating income" in k or "operating income loss" in k):
                        op = val
                rows.append({
                    "date": date_str,
                    "revenue": rev,
                    "net_income": net,
                    "gross_profit": gross,
                    "operating_income": op
                })
            return rows

        # 季報
        try:
            result["quarterly"] = extract_financials(ticker.quarterly_financials, 5)
        except Exception as e:
            logger.warning(f"季報 {symbol}: {e}")

        # 年報
        try:
            result["annual"] = extract_financials(ticker.financials, 3)
        except Exception as e:
            logger.warning(f"年報 {symbol}: {e}")

        # ===== 根據財報產生詳細文字分析 =====
        analysis = []
        q = result["quarterly"]

        if len(q) >= 2:
            latest = q[0]
            prev = q[1]
            analysis.append(f"最新一季（約 {latest['date']}）與前一季（約 {prev['date']}）對比：")

            if latest.get("revenue") is not None and prev.get("revenue") is not None and prev["revenue"] != 0:
                chg = (latest["revenue"] - prev["revenue"]) / abs(prev["revenue"]) * 100
                direction = "成長" if chg > 0 else "下滑"
                analysis.append(f"・營收較前季{direction} {abs(chg):.1f}%（{format_number(prev['revenue'])} → {format_number(latest['revenue'])}）。")
            elif latest.get("revenue") is not None:
                analysis.append(f"・最新一季營收約 {format_number(latest['revenue'])}。")

            if latest.get("net_income") is not None and prev.get("net_income") is not None:
                if prev["net_income"] != 0:
                    chg = (latest["net_income"] - prev["net_income"]) / abs(prev["net_income"]) * 100
                    direction = "成長" if chg > 0 else "下滑"
                    analysis.append(f"・淨利較前季{direction} {abs(chg):.1f}%（{format_number(prev['net_income'])} → {format_number(latest['net_income'])}）。")
                else:
                    analysis.append(f"・最新一季淨利約 {format_number(latest['net_income'])}。")
            elif latest.get("net_income") is not None:
                analysis.append(f"・最新一季淨利約 {format_number(latest['net_income'])}。")

            # 毛利率粗估
            if latest.get("revenue") and latest.get("gross_profit") and latest["revenue"] != 0:
                gm = latest["gross_profit"] / latest["revenue"] * 100
                analysis.append(f"・最新一季毛利率約 {gm:.1f}%。")

        if len(q) >= 4:
            # 與一年前同季比（第4筆）
            yoy = q[3]
            latest = q[0]
            if latest.get("revenue") and yoy.get("revenue") and yoy["revenue"] != 0:
                chg = (latest["revenue"] - yoy["revenue"]) / abs(yoy["revenue"]) * 100
                direction = "成長" if chg > 0 else "下滑"
                analysis.append(f"・與約一年前同季相比，營收{direction} {abs(chg):.1f}%。")
            if latest.get("net_income") is not None and yoy.get("net_income") is not None and yoy["net_income"] != 0:
                chg = (latest["net_income"] - yoy["net_income"]) / abs(yoy["net_income"]) * 100
                direction = "成長" if chg > 0 else "下滑"
                analysis.append(f"・與約一年前同季相比，淨利{direction} {abs(chg):.1f}%。")

        # 年報趨勢
        a = result["annual"]
        if len(a) >= 2 and a[0].get("revenue") and a[1].get("revenue") and a[1]["revenue"] != 0:
            chg = (a[0]["revenue"] - a[1]["revenue"]) / abs(a[1]["revenue"]) * 100
            direction = "成長" if chg > 0 else "下滑"
            analysis.append(f"・最近兩個完整會計年度，營收{direction} {abs(chg):.1f}%。")

        if not analysis:
            analysis.append("目前無法從 Yahoo Finance 取得足夠的季報/年報數字進行對比。")
            analysis.append("請點下方官方連結，直接查看公司向監管機構申報的原始財報。")
            result["error"] = "財報數字不完整（港股或部分美股較常見）"
        else:
            analysis.append("以上數字來自公司正式財報彙整，建議再對照官方原始文件確認。")
            analysis.append(
                f'資料連結：Yahoo Finance 財報頁 '
                f'(<a href="https://finance.yahoo.com/quote/{symbol}/financials" target="_blank" rel="noopener" '
                f'style="color:#60a5fa; text-decoration:underline;">開啟 {symbol} Financials</a>)'
            )

        result["analysis_text"] = analysis

    except Exception as e:
        logger.error(f"公司財報錯誤 {symbol}: {e}")
        result["error"] = str(e)
        result["analysis_text"] = ["取得財報資料時發生錯誤，請改以官方連結查看。"]

    return result



def format_number(n):
    if n is None:
        return "—"
    try:
        n = float(n)
        if abs(n) >= 1e12:
            return f"{n/1e12:.2f}T"
        if abs(n) >= 1e9:
            return f"{n/1e9:.2f}B"
        if abs(n) >= 1e6:
            return f"{n/1e6:.2f}M"
        return f"{n:,.2f}"
    except:
        return "—"

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

def get_performance(symbol):
    result = {}
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="2y")
        if hist.empty or len(hist) < 5:
            return {}
        closes = hist["Close"]
        latest = float(closes.iloc[-1])

        def calc_return(days):
            if len(closes) <= days:
                return None
            past = float(closes.iloc[-days-1])
            return round((latest - past) / past * 100, 2)

        result = {
            "1d": calc_return(1),
            "1w": calc_return(5),
            "1mo": calc_return(21),
            "3mo": calc_return(63),
            "6mo": calc_return(126),
            "1y": calc_return(252),
        }
    except Exception as e:
        logger.error(f"績效計算錯誤 {symbol}: {e}")
    return result


def get_competitor_performance(symbol):
    """同業競爭對手表現：股價漲跌與多時間報酬率對比"""
    comps = COMPETITORS.get(symbol, [])[:5]
    symbols = [symbol] + [c for c in comps if c != symbol]
    rows = []
    for sym in symbols:
        try:
            price = get_stock_price(sym)
            perf = get_performance(sym)
            name = US_WATCHLIST.get(sym) or HK_WATCHLIST.get(sym) or sym
            if "error" in price:
                rows.append({"symbol": sym, "name": name, "is_self": sym == symbol, "error": True})
            else:
                rows.append({
                    "symbol": sym,
                    "name": name,
                    "is_self": sym == symbol,
                    "price": price.get("price"),
                    "change_pct": price.get("change_pct"),
                    "currency": price.get("currency", "USD"),
                    "r1w": perf.get("1w"),
                    "r1mo": perf.get("1mo"),
                    "r3mo": perf.get("3mo"),
                    "r1y": perf.get("1y"),
                    "error": False,
                })
            time.sleep(0.08)
        except Exception as e:
            logger.warning("競爭對手資料 %s: %s", sym, e)
    return rows


def generate_stock_analysis(symbol, name, performance, news_list=None):
    """結合價格走勢 + 近期新聞，產生更詳細的走勢解釋（含新聞連結）"""
    p = performance or {}
    news_list = news_list or []
    r1d = p.get("1d")
    r1w = p.get("1w")
    r1mo = p.get("1mo")
    r1y = p.get("1y")

    recent_parts = []
    if r1d is not None:
        if r1d > 2: recent_parts.append(f"近1日大漲 {r1d}%")
        elif r1d > 0.5: recent_parts.append(f"近1日上漲 {r1d}%")
        elif r1d < -2: recent_parts.append(f"近1日大跌 {r1d}%")
        elif r1d < -0.5: recent_parts.append(f"近1日下跌 {r1d}%")
        else: recent_parts.append(f"近1日幾乎持平（{r1d}%）")

    if r1w is not None:
        if r1w > 5: recent_parts.append(f"近1週累積上漲 {r1w}%，短期動能偏強")
        elif r1w < -5: recent_parts.append(f"近1週累積下跌 {r1w}%，短期出現調整")
        elif abs(r1w) <= 2: recent_parts.append(f"近1週波動不大（{r1w}%）")

    if r1mo is not None:
        if r1mo > 10: recent_parts.append(f"近1個月大漲 {r1mo}%，中期趨勢偏多")
        elif r1mo < -10: recent_parts.append(f"近1個月下跌 {r1mo}%，中期出現明顯回檔")

    recent_summary = "；".join(recent_parts) if recent_parts else "近期波動相對有限。"

    bullish_news = []
    bearish_news = []
    neutral_news = []
    themes = set()

    for n in news_list:
        a = n.get("analysis", {})
        score = a.get("score", 0)
        label = a.get("label", "neutral")
        title = n.get("title", "")
        url = n.get("url", "#")
        item = {"title": title, "url": url, "score": score}

        if label == "positive" or score > 0.25:
            bullish_news.append(item)
        elif label == "negative" or score < -0.25:
            bearish_news.append(item)
        else:
            neutral_news.append(item)

        t = title.lower()
        if any(k in t for k in ["earnings", "revenue", "財報", "營收", "獲利", "profit"]):
            themes.add("財報/營收")
        if any(k in t for k in ["upgrade", "downgrade", "rating", "目標價", "評級", "price target"]):
            themes.add("分析師評級")
        if any(k in t for k in ["ai", "chip", "semiconductor", "gpu", "半導體", "輝達", "nvidia", "hbm"]):
            themes.add("AI/半導體")
        if any(k in t for k in ["guidance", "outlook", "展望", "預估", "forecast"]):
            themes.add("未來展望")
        if any(k in t for k in ["buyback", "dividend", "回購", "股息"]):
            themes.add("回購/股息")
        if any(k in t for k in ["lawsuit", "probe", "investigation", "監管", "訴訟"]):
            themes.add("監管/訴訟風險")

    def news_link(item, idx):
        """產生可點擊的新聞條目 HTML"""
        title = item.get("title") or "（無標題）"
        url = item.get("url") or "#"
        score = item.get("score", 0)
        return (
            f'{idx}. <a href="{url}" target="_blank" rel="noopener" '
            f'style="color:#60a5fa; text-decoration:underline;">{title}</a>'
            f'（情緒 {score:+.2f}）'
        )

    reasons = []
    price_up = (r1d is not None and r1d > 1) or (r1w is not None and r1w > 3)
    price_down = (r1d is not None and r1d < -1) or (r1w is not None and r1w < -3)

    # 股價資料來源說明
    reasons.append(
        f'【股價資料來源】Yahoo Finance 歷史報價 '
        f'(<a href="https://finance.yahoo.com/quote/{symbol}" target="_blank" rel="noopener" '
        f'style="color:#60a5fa; text-decoration:underline;">查看 {symbol} 頁面</a>)'
    )

    if price_up:
        reasons.append(f"【價格表現】近期股價偏強（近1日 {r1d if r1d is not None else '—'}%，近1週 {r1w if r1w is not None else '—'}%）。")
        if bullish_news:
            reasons.append("【新聞交叉｜偏多】以下正面新聞可能支撐上漲（點標題可看原文）：")
            for i, bn in enumerate(bullish_news[:4], 1):
                reasons.append("　" + news_link(bn, i))
            reasons.append("這些正面消息可能吸引買盤，或讓市場重新評估公司前景。")
        if bearish_news:
            reasons.append("【需注意｜偏空新聞】上漲同時仍有負面消息：")
            for i, bn in enumerate(bearish_news[:3], 1):
                reasons.append("　" + news_link(bn, i))
            reasons.append("若後續負面消息發酵，短線可能出現獲利了結。")
        if not bullish_news and not bearish_news:
            reasons.append("【新聞交叉】近期沒有特別強烈的正負面新聞，上漲可能更多來自大盤或產業資金流向。")

    elif price_down:
        reasons.append(f"【價格表現】近期股價偏弱（近1日 {r1d if r1d is not None else '—'}%，近1週 {r1w if r1w is not None else '—'}%）。")
        if bearish_news:
            reasons.append("【新聞交叉｜偏空】以下負面新聞可能造成賣壓（點標題可看原文）：")
            for i, bn in enumerate(bearish_news[:4], 1):
                reasons.append("　" + news_link(bn, i))
            reasons.append("負面消息容易引發賣壓或避險情緒，尤其在成交量放大時影響更明顯。")
        if bullish_news:
            reasons.append("【需注意｜仍有偏多新聞】：")
            for i, bn in enumerate(bullish_news[:3], 1):
                reasons.append("　" + news_link(bn, i))
            reasons.append("若這些正面因素後續被市場重新重視，可能出現技術性反彈。")
        if not bullish_news and not bearish_news:
            reasons.append("【新聞交叉】近期新聞情緒不明顯，下跌可能更多來自大盤回檔、獲利了結或資金輪動。")

    else:
        reasons.append("【價格表現】近期股價波動相對有限，方向尚未明確。")
        if bullish_news or bearish_news:
            reasons.append("【新聞交叉】價格震盪但新聞面並非完全平靜：")
            if bullish_news:
                reasons.append("　偏多：" + news_link(bullish_news[0], 1))
            if bearish_news:
                reasons.append("　偏空：" + news_link(bearish_news[0], 1))
            reasons.append("多空消息並存，可能是造成股價上不來、下不去的原因之一。")
        else:
            reasons.append("【新聞交叉】近期相關新聞不多或情緒中性，市場觀望氣氛濃厚。")

    if themes:
        reasons.append(f"【主要新聞主題】目前市場較關注：{'、'.join(themes)}。這些主題的後續發展，將持續影響短線走勢。")

    reasons.append("【綜合建議】單一新聞不應作為進出唯一依據，建議搭配成交量、大盤氣氛與公司基本面一起判斷。所有新聞標題均可點擊查看原文。")

    short_term = []
    if price_up and bullish_news and not bearish_news:
        short_term.append("新聞面偏多且股價同步走強，短期動能仍有機會延續，但需留意是否已反應過度。")
    elif price_down and bearish_news and not bullish_news:
        short_term.append("新聞面偏空且股價同步走弱，短期仍有壓力，可觀察是否出現止跌訊號或利空出盡。")
    elif price_up and bearish_news:
        short_term.append("股價上漲但存在負面新聞，短期可能震盪加大，建議觀察能否消化利空。")
    elif price_down and bullish_news:
        short_term.append("股價下跌但有正面新聞支撐，短期或有反彈機會，需確認買盤是否真正進場。")
    else:
        short_term.append("目前多空消息與價格表現較為糾結，短期可能以震盪為主。")
    short_term.append("重點觀察：後續是否有重要財報、分析師報告或產業新聞出現。")

    long_term = []
    if r1y is not None and r1y > 30:
        long_term.append("過去一年漲幅顯著，長期趨勢仍偏多，但需留意估值與獲利了結壓力。")
    elif r1y is not None and r1y < -20:
        long_term.append("過去一年表現偏弱，若基本面未明顯惡化，長期有機會出現價值修復。")
    else:
        long_term.append("長期表現大致中性，未來仍取決於公司基本面、產業周期與總體經濟。")
    if themes:
        long_term.append(f"目前新聞反覆提到「{'、'.join(list(themes)[:3])}」，這些因素的長期演變，將是影響未來走勢的重要變數。")
    long_term.append("建議持續追蹤財報、競爭對手動態與產業資本支出變化。")

    return {
        "recent_summary": recent_summary,
        "reasons": reasons,
        "short_term": short_term,
        "long_term": long_term,
        "performance": p
    }



def get_daily_closes(symbols, days=10):
    result = {}
    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1mo")
            if hist.empty: continue
            closes = []
            for idx, row in hist.tail(days).iterrows():
                closes.append({"date": idx.strftime("%Y-%m-%d"), "close": round(float(row["Close"]), 2)})
            result[symbol] = closes
            time.sleep(0.1)
        except Exception as e:
            logger.error(f"每日收盤錯誤 {symbol}: {e}")
    return result

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
                "published": datetime.fromtimestamp(n.get("providerPublishTime", time.time()), tz=timezone.utc).isoformat(),
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

def get_stock_news(symbol, limit=5):
    is_hk = symbol.endswith(".HK")
    items = fetch_hk_news(symbol) if is_hk else fetch_us_news(symbol)
    since = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    result = []
    seen = set()
    for item in items:
        try:
            pub = datetime.fromisoformat(item["published"].replace("Z", "+00:00"))
        except:
            continue
        if pub < since or item.get("id") in seen:
            continue
        seen.add(item.get("id"))
        item["analysis"] = analyze_news(item, "HK" if is_hk else "US")
        result.append(item)
        if len(result) >= limit:
            break
    return result

def cross_compare_news(news_list):
    """對多則新聞做交叉對比摘要"""
    if not news_list:
        return {
            "summary": "目前沒有足夠新聞可進行交叉對比。",
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "key_themes": []
        }

    bullish = 0
    bearish = 0
    neutral = 0
    themes = []

    for n in news_list:
        a = n.get("analysis", {})
        label = a.get("label", "neutral")
        score = a.get("score", 0)
        if label == "positive" or score > 0.2:
            bullish += 1
        elif label == "negative" or score < -0.2:
            bearish += 1
        else:
            neutral += 1

        title = n.get("title", "").lower()
        if any(k in title for k in ["earnings", "revenue", "財報", "營收", "獲利"]):
            themes.append("財報/營收相關")
        if any(k in title for k in ["upgrade", "downgrade", "rating", "目標價", "評級"]):
            themes.append("分析師評級")
        if any(k in title for k in ["ai", "chip", "semiconductor", "半導體", "輝達", "nvidia"]):
            themes.append("AI/半導體題材")
        if any(k in title for k in ["guidance", "outlook", "展望", "預估"]):
            themes.append("未來展望")

    themes = list(dict.fromkeys(themes))[:4]  # 去重並限制數量

    total = len(news_list)
    if bullish > bearish and bullish >= total * 0.4:
        overall = f"近期 {total} 則新聞中，偏多情緒佔比較高（正面 {bullish}、負面 {bearish}、中性 {neutral}），整體新聞氣氛偏樂觀。"
    elif bearish > bullish and bearish >= total * 0.4:
        overall = f"近期 {total} 則新聞中，偏空情緒佔比較高（正面 {bullish}、負面 {bearish}、中性 {neutral}），整體新聞氣氛偏謹慎。"
    else:
        overall = f"近期 {total} 則新聞情緒較為分歧（正面 {bullish}、負面 {bearish}、中性 {neutral}），市場觀點尚未形成明顯共識。"

    if themes:
        overall += f" 主要討論主題包括：{'、'.join(themes)}。"

    overall += " 建議將新聞情緒與股價走勢、成交量一起交叉驗證，避免單一消息過度解讀。"

    return {
        "summary": overall,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": neutral,
        "key_themes": themes
    }

@lru_cache(maxsize=2)
def get_dashboard_data(cache_key):
    price_info = {}
    all_symbols = list(US_WATCHLIST.keys()) + list(HK_WATCHLIST.keys())
    for sym in all_symbols:
        price_info[sym] = get_stock_price(sym)
        time.sleep(0.1)

    valid = [v for v in price_info.values() if "error" not in v]
    gainers = sorted(valid, key=lambda x: x["change_pct"], reverse=True)[:8]
    losers = sorted(valid, key=lambda x: x["change_pct"])[:8]

    up_count = sum(1 for v in valid if v["change_pct"] > 0)
    down_count = sum(1 for v in valid if v["change_pct"] < 0)
    flat_count = len(valid) - up_count - down_count
    avg_change = sum(v["change_pct"] for v in valid) / len(valid) if valid else 0

    summary_parts = []
    if avg_change > 0.5:
        summary_parts.append(f"今日整體偏多，平均漲幅約 {avg_change:.2f}%。")
    elif avg_change < -0.5:
        summary_parts.append(f"今日整體偏空，平均跌幅約 {avg_change:.2f}%。")
    else:
        summary_parts.append(f"今日整體震盪，平均變化約 {avg_change:.2f}%。")
    summary_parts.append(f"上漲 {up_count} 檔、下跌 {down_count} 檔、持平 {flat_count} 檔。")

    if gainers:
        top = gainers[0]
        name = US_WATCHLIST.get(top["symbol"]) or HK_WATCHLIST.get(top["symbol"]) or top["symbol"]
        summary_parts.append(f"漲幅最大：{top['symbol']}（{name}）{top['change_pct']:+.2f}%。")
    if losers:
        bottom = losers[0]
        name = US_WATCHLIST.get(bottom["symbol"]) or HK_WATCHLIST.get(bottom["symbol"]) or bottom["symbol"]
        summary_parts.append(f"跌幅最大：{bottom['symbol']}（{name}）{bottom['change_pct']:+.2f}%。")

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "price_info": price_info,
        "gainers": gainers,
        "losers": losers,
        "daily_summary": " ".join(summary_parts)
    }

@lru_cache(maxsize=4)
def get_news_data(market, cache_key):
    since = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    all_news = []
    seen = set()
    watchlist = US_WATCHLIST if market == "US" else HK_WATCHLIST

    for symbol in watchlist:
        items = fetch_us_news(symbol) if market == "US" else fetch_hk_news(symbol)
        time.sleep(0.35)
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
    return {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "news": all_news[:40]}

SIDEBAR = """
<div class="sidebar">
    <div class="logo">美股・港股分析</div>
    <nav>
        <a href="/" class="nav-item">總覽儀表板</a>
        <div class="nav-group">股票</div>
        <a href="/stocks/us" class="nav-item sub">美股</a>
        <a href="/stocks/hk" class="nav-item sub">港股</a>
        <a href="/daily-closes" class="nav-item sub">每日收盤紀錄</a>
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
.perf-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(100px, 1fr)); gap: 10px; margin: 16px 0; }
.perf-item { background: #0f172a; border: 1px solid var(--border); border-radius: 8px; padding: 10px; text-align: center; }
.perf-item .label { font-size: 0.8rem; color: var(--muted); }
.perf-item .value { font-size: 1.1rem; font-weight: 600; margin-top: 4px; }
.summary-box { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 24px; }
.summary-box h3 { font-size: 1.1rem; margin-bottom: 12px; color: #93c5fd; }
.summary-box p, .summary-box li { color: var(--muted); line-height: 1.7; font-size: 0.95rem; }
.summary-box ul { margin-left: 18px; margin-top: 6px; }
.rank-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }
.rank-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }
.rank-card h3 { font-size: 1.05rem; margin-bottom: 12px; }
.rank-item { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 0.9rem; }
.rank-item:last-child { border-bottom: none; }
.rank-item a { color: var(--text); text-decoration: none; }
.rank-item a:hover { color: var(--accent); }
.daily-summary-box { background: linear-gradient(135deg, #1e293b, #0f172a); border: 1px solid var(--border); border-radius: 12px; padding: 18px 20px; margin-bottom: 24px; line-height: 1.7; }
.info-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; margin-bottom: 20px; }
.info-item { background: #0f172a; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.info-item .label { font-size: 0.8rem; color: var(--muted); }
.info-item .value { font-size: 1.05rem; font-weight: 600; margin-top: 4px; }
@media (max-width: 800px) {
    .sidebar { width: 100%; height: auto; position: relative; }
    .main { margin-left: 0; padding: 20px; }
    body { flex-direction: column; }
    .rank-grid { grid-template-columns: 1fr; }
}
"""

def render_page(title, page, content, extra_js="", auto_refresh=False):
    refresh_meta = '<meta http-equiv="refresh" content="180">' if auto_refresh else ""
    refresh_note = (
        '<div style="font-size:0.8rem;color:var(--muted);margin-bottom:12px;">'
        '⏱ 本頁約每 3 分鐘自動重新整理以更新行情（亦可手動重新整理）</div>'
        if auto_refresh else ""
    )
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {refresh_meta}
    <title>{title} | 美股・港股分析</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <style>{BASE_STYLE}</style>
</head>
<body>
    {SIDEBAR}
    <div class="main">{refresh_note}{content}</div>
    <script>{extra_js}</script>
</body>
</html>"""

@app.route("/")
def home():
    cache_key = datetime.now().strftime("%Y%m%d%H%M")
    data = get_dashboard_data(cache_key)
    price_info = data["price_info"]

    summary_html = f'<div class="daily-summary-box"><strong>📅 今日市場摘要</strong><br>{data["daily_summary"]}</div>'

    def render_rank(items, title):
        html = f'<div class="rank-card"><h3>{title}</h3>'
        for i, p in enumerate(items, 1):
            name = US_WATCHLIST.get(p["symbol"]) or HK_WATCHLIST.get(p["symbol"]) or ""
            cls = "up" if p["change_pct"] > 0 else "down"
            curr = "HK$" if p["symbol"].endswith(".HK") else "$"
            html += f'<div class="rank-item"><div><a href="/stock/{p["symbol"]}">{i}. {p["symbol"]}</a> <span style="color:var(--muted);font-size:0.8rem;">{name}</span></div><div class="{cls}">{p["change_pct"]:+.2f}% <span style="color:var(--muted);font-size:0.85rem;">{curr}{p["price"]}</span></div></div>'
        html += "</div>"
        return html

    rank_html = f'<div class="rank-grid">{render_rank(data["gainers"], "📈 今日漲幅榜")}{render_rank(data["losers"], "📉 今日跌幅榜")}</div>'

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
    <div class="subtitle">最後更新：{data["generated_at"]}</div>
    {summary_html}{rank_html}
    <div class="section-title">美股</div><div class="price-grid">{us_cards}</div>
    <div class="section-title">港股</div><div class="price-grid">{hk_cards}</div>
    """
    return render_page("總覽", "home", content, auto_refresh=True)

@app.route("/daily-closes")
def daily_closes():
    key_symbols = ["NVDA", "AAPL", "TSLA", "TSM", "0700.HK", "9988.HK", "SPY", "QQQ"]
    closes_data = get_daily_closes(key_symbols, days=12)
    all_dates = set()
    for sym_data in closes_data.values():
        for d in sym_data: all_dates.add(d["date"])
    dates = sorted(all_dates, reverse=True)[:12]

    table = '<table class="data-table"><thead><tr><th>日期</th>'
    for sym in key_symbols:
        name = US_WATCHLIST.get(sym) or HK_WATCHLIST.get(sym) or sym
        table += f'<th>{sym}<br><span style="font-size:0.75rem;color:var(--muted)">{name}</span></th>'
    table += '</tr></thead><tbody>'
    for date in dates:
        table += f'<tr><td>{date}</td>'
        for sym in key_symbols:
            price = "—"
            for d in closes_data.get(sym, []):
                if d["date"] == date: price = d["close"]; break
            table += f'<td>{price}</td>'
        table += '</tr>'
    table += '</tbody></table>'

    content = f"""
    <h1>每日收盤紀錄</h1>
    <div class="subtitle">最近約 12 個交易日的收盤價</div>
    {table}
    """
    return render_page("每日收盤紀錄", "daily-closes", content)

@app.route("/stocks/us")
def stocks_us():
    cache_key = datetime.now().strftime("%Y%m%d%H%M")
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
    return render_page("美股", "stocks-us", f'<h1>美股列表</h1><div class="price-grid">{cards}</div>')

@app.route("/stocks/hk")
def stocks_hk():
    cache_key = datetime.now().strftime("%Y%m%d%H%M")
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
    return render_page("港股", "stocks-hk", f'<h1>港股列表</h1><div class="price-grid">{cards}</div>')

@app.route("/stock/<symbol>")
def stock_detail(symbol):
    name = US_WATCHLIST.get(symbol) or HK_WATCHLIST.get(symbol) or symbol
    price = get_stock_price(symbol)
    is_hk = symbol.endswith(".HK")
    curr = "HK$" if is_hk else "$"

    performance = get_performance(symbol)
    stock_news = get_stock_news(symbol, limit=6)
    analysis = generate_stock_analysis(symbol, name, performance, news_list=stock_news)
    company = get_company_info(symbol)
    cross = cross_compare_news(stock_news)

    # 價格區塊
    if "error" in price:
        price_html = "<div>無法取得即時價格</div>"
    else:
        cls = "up" if price["change_pct"] > 0 else ("down" if price["change_pct"] < 0 else "flat")
        arrow = "▲" if price["change_pct"] > 0 else ("▼" if price["change_pct"] < 0 else "─")
        price_html = (
            f'<div><div class="stock-price-big">{curr}{price["price"]}</div>'
            f'<div class="{cls}" style="font-size:1.2rem;">{arrow} {price["change"]:+.2f} ({price["change_pct"]:+.2f}%)</div>'
            f'<div style="margin-top:8px; color:var(--muted); font-size:0.9rem;">'
            f'開 {curr}{price["open"]} ｜ 高 {curr}{price["high"]} ｜ 低 {curr}{price["low"]} ｜ 量 {price["volume"]:,}'
            f'</div></div>'
        )

    # 同業競爭對手表現對比
    comp_rows = get_competitor_performance(symbol)
    competitor_perf_html = ""
    if comp_rows and len(comp_rows) > 1:
        parts = []
        parts.append('<table class="data-table"><thead><tr><th>代號</th><th>名稱</th><th>現價</th><th>今日漲跌</th><th>1週</th><th>1月</th><th>3月</th><th>1年</th></tr></thead><tbody>')
        for row in comp_rows:
            if row.get("error"):
                parts.append(
                    f'<tr><td><a href="/stock/{row["symbol"]}" style="color:var(--accent);">{row["symbol"]}</a></td>'
                    f'<td>{row["name"]}</td><td colspan="6">無資料</td></tr>'
                )
                continue
            chg = row.get("change_pct") or 0
            cls = "up" if chg > 0 else ("down" if chg < 0 else "flat")
            curr_sym = "HK$" if row.get("currency") == "HKD" else "$"
            self_mark = ' <span style="color:#eab308;font-size:0.75rem;">本股</span>' if row.get("is_self") else ""

            def _pct(v):
                if v is None:
                    return "—"
                c = "up" if v > 0 else ("down" if v < 0 else "flat")
                return f'<span class="{c}">{v:+.2f}%</span>'

            parts.append(
                f'<tr><td><a href="/stock/{row["symbol"]}" style="color:var(--accent);">{row["symbol"]}</a>{self_mark}</td>'
                f'<td>{row["name"]}</td><td>{curr_sym}{row.get("price", "—")}</td>'
                f'<td class="{cls}">{chg:+.2f}%</td>'
                f'<td>{_pct(row.get("r1w"))}</td><td>{_pct(row.get("r1mo"))}</td>'
                f'<td>{_pct(row.get("r3mo"))}</td><td>{_pct(row.get("r1y"))}</td></tr>'
            )
        parts.append("</tbody></table>")
        parts.append(
            f'<p style="margin-top:10px; font-size:0.85rem; color:var(--muted);">'
            f'資料來源：Yahoo Finance。可點代號進入該股詳情。'
            f' <a href="https://finance.yahoo.com/quote/{symbol}" target="_blank" rel="noopener" style="color:#60a5fa;">Yahoo {symbol}</a></p>'
        )
        competitor_perf_html = "".join(parts)
    else:
        competitor_perf_html = '<p style="color:var(--muted);">此股票尚未設定同業對手帳號，或暫時無法取得對比資料。</p>'

    # 競爭公司新聞
    comps = COMPETITORS.get(symbol, [])[:3]
    competitor_news_html = ""
    for comp in comps:
        c_news = get_stock_news(comp, limit=2)
        comp_name = US_WATCHLIST.get(comp) or HK_WATCHLIST.get(comp) or comp
        if c_news:
            competitor_news_html += f'<h4 style="margin:16px 0 8px; color:#93c5fd;">{comp}（{comp_name}）</h4>'
            for n in c_news:
                title = n.get("title", "")
                pub = n.get("published", "")[:16].replace("T", " ")
                publisher = n.get("publisher", {}).get("name", "")
                url = n.get("url", "#")
                competitor_news_html += (
                    f'<div style="margin-bottom:12px; padding-bottom:12px; border-bottom:1px solid var(--border);">'
                    f'<div style="font-size:0.95rem;">{title}</div>'
                    f'<div style="font-size:0.8rem; color:var(--muted); margin:4px 0;">{pub} ｜ {publisher}</div>'
                    f'<a class="link-btn" href="{url}" target="_blank" rel="noopener">閱讀原文 →</a>'
                    f'</div>'
                )

    # ===== 財報詳細分析區塊 =====
    qlist = company.get("quarterly") or []
    alist = company.get("annual") or []
    analysis_lines = company.get("analysis_text") or []

    # 季報表
    quarterly_html = ""
    if qlist:
        quarterly_html = '<table class="data-table"><thead><tr><th>報告期</th><th>營收</th><th>毛利</th><th>營業利益</th><th>淨利</th></tr></thead><tbody>'
        for q in qlist:
            quarterly_html += f"<tr><td>{q.get('date','—')}</td><td>{format_number(q.get('revenue'))}</td><td>{format_number(q.get('gross_profit'))}</td><td>{format_number(q.get('operating_income'))}</td><td>{format_number(q.get('net_income'))}</td></tr>"
        quarterly_html += '</tbody></table>'
    else:
        quarterly_html = '<p style="color:var(--muted); font-size:0.9rem;">暫無季報數字可顯示。</p>'

    # 年報表
    annual_html = ""
    if alist:
        annual_html = '<table class="data-table" style="margin-top:12px;"><thead><tr><th>會計年度</th><th>營收</th><th>毛利</th><th>營業利益</th><th>淨利</th></tr></thead><tbody>'
        for a in alist:
            annual_html += f"<tr><td>{a.get('date','—')}</td><td>{format_number(a.get('revenue'))}</td><td>{format_number(a.get('gross_profit'))}</td><td>{format_number(a.get('operating_income'))}</td><td>{format_number(a.get('net_income'))}</td></tr>"
        annual_html += '</tbody></table>'

    analysis_html = "".join([f"<li>{line}</li>" for line in analysis_lines])

    fundamentals_html = f"""
    <div class="summary-box" style="margin-bottom:16px;">
        <p style="font-size:0.9rem; color:var(--muted); margin-bottom:10px;">
            以下分析根據公司向監管機構申報的正式財報彙整（美股 SEC、港股聯交所）。
            數字可能有延遲，請以官方原始文件為最終依據。
            {('<br><span style="color:#fbbf24;">⚠ ' + str(company.get("error")) + '</span>') if company.get("error") else ""}
        </p>
        <div style="display:flex; flex-wrap:wrap; gap:12px; margin-bottom:8px;">
            <a class="link-btn" href="{company.get('official_url', '#')}" target="_blank" rel="noopener">📄 {company.get('official_label', '官方財報')}</a>
            {"<a class='link-btn' href='" + company['website'] + "' target='_blank' rel='noopener'>🏢 公司官網</a>" if company.get("website") else ""}
        </div>
    </div>

    <div class="summary-box">
        <h3>📊 財報趨勢分析</h3>
        <ul style="margin-left:18px; color:var(--muted); line-height:1.8;">{analysis_html}</ul>
    </div>

    <div class="section-title" style="margin-top:8px;">最近幾季財報對比</div>
    {quarterly_html}

    <div class="section-title" style="margin-top:20px;">最近年度財報</div>
    {annual_html if annual_html else '<p style="color:var(--muted); font-size:0.9rem;">暫無年度財報數字可顯示。</p>'}
    """

    perf_html = '<div class="perf-grid">'
    for key, label in [("1d","1日"),("1w","1週"),("1mo","1月"),("3mo","3月"),("6mo","6月"),("1y","1年")]:
        val = analysis["performance"].get(key)
        if val is None:
            perf_html += f'<div class="perf-item"><div class="label">{label}</div><div class="value">—</div></div>'
        else:
            cls = "up" if val > 0 else ("down" if val < 0 else "flat")
            perf_html += f'<div class="perf-item"><div class="label">{label}</div><div class="value {cls}">{val:+.2f}%</div></div>'
    perf_html += '</div>'

    reasons_html = "".join([f"<li style=\"line-height:1.7;\">{r}</li>" for r in analysis["reasons"]])
    short_html = "".join([f"<li>{s}</li>" for s in analysis["short_term"]])
    long_html = "".join([f"<li>{l}</li>" for l in analysis["long_term"]])

    # 新聞交叉對比
    cross_html = f"""
    <div class="summary-box">
        <h3>🔍 新聞交叉對比摘要</h3>
        <p>{cross["summary"]}</p>
        <div style="margin-top:12px; font-size:0.9rem; color:var(--muted);">
            正面：{cross["bullish_count"]} ｜ 負面：{cross["bearish_count"]} ｜ 中性：{cross["neutral_count"]}
            {" ｜ 主題：" + "、".join(cross["key_themes"]) if cross["key_themes"] else ""}
        </div>
    </div>
    """

    # 個股新聞
    news_html = ""
    if not stock_news:
        news_html = '<div class="loading">目前沒有找到相關近期新聞</div>'
    else:
        for n in stock_news:
            a = n.get("analysis", {})
            sent_class = "sentiment-pos" if a.get("label") == "positive" else ("sentiment-neg" if a.get("label") == "negative" else "sentiment-neu")
            pos_list = "".join([f"<li>{p}</li>" for p in a.get("positive", [])])
            neg_list = "".join([f"<li>{p}</li>" for p in a.get("negative", [])])
            impact_list = "".join([f"<li>{p}</li>" for p in a.get("impact", [])])
            news_html += f"""
            <div class="news-card">
                <h3>{n.get("title", "")}</h3>
                <div class="news-meta">
                    <span>{n.get("published", "")[:16].replace("T", " ")} UTC</span>
                    <span>{n.get("publisher", {}).get("name", "未知")}</span>
                    <span class="{sent_class}">情緒：{a.get("sentiment_text", "中性")} ({a.get("score", 0):+.2f})</span>
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

    content = f"""
    <div class="stock-header">
        <div>
            <h1>{symbol} <span style="font-size:1.1rem; color:var(--muted); font-weight:400;">{name}</span></h1>
            <div class="subtitle">完整分析：走勢、基本面、新聞交叉對比、競爭對手</div>
        </div>
        {price_html}
    </div>

    <div class="section-title">公司財報詳細分析</div>
    {fundamentals_html}

    <div class="section-title">不同時間報酬率</div>
    {perf_html}

    <div class="chart-container">
        <div class="period-btns" style="margin-bottom:10px;">
            <button class="period-btn" data-period="5d">5日</button>
            <button class="period-btn active" data-period="1mo">1個月</button>
            <button class="period-btn" data-period="3mo">3個月</button>
            <button class="period-btn" data-period="6mo">6個月</button>
            <button class="period-btn" data-period="1y">1年</button>
            <button class="period-btn" data-period="2y">2年</button>
        </div>
        <div class="period-btns" style="margin-bottom:14px;">
            <button type="button" class="period-btn chart-type-btn active" data-chart="candle">日K（K棒）</button>
            <button type="button" class="period-btn chart-type-btn" data-chart="line">折線圖</button>
        </div>
        <div class="period-btns" style="margin-bottom:14px; gap:6px;">
            <span style="color:var(--muted); font-size:0.85rem; align-self:center; margin-right:4px;">指標：</span>
            <button type="button" class="period-btn ind-btn active" data-ind="ma5">MA5</button>
            <button type="button" class="period-btn ind-btn active" data-ind="ma20">MA20</button>
            <button type="button" class="period-btn ind-btn active" data-ind="ma60">MA60</button>
            <button type="button" class="period-btn ind-btn" data-ind="bb">布林通道</button>
        </div>
        <div id="klineChart" style="width:100%; height:380px;"></div>
        <p style="font-size:0.8rem; color:var(--muted); margin-top:8px;">滑鼠移上可看開高低收；滾輪可縮放，拖曳可平移。預設為日K線。</p>
    </div>

    <div class="summary-box">
        <h3>📊 近期走勢總結</h3>
        <p>{analysis["recent_summary"]}</p>
        <h3 style="margin-top:18px;">可能造成此走勢的原因</h3>
        <ul>{reasons_html}</ul>
        <h3 style="margin-top:18px;">短期展望（1天～1週）</h3>
        <ul>{short_html}</ul>
        <h3 style="margin-top:18px;">長期展望（一季～一年）</h3>
        <ul>{long_html}</ul>
    </div>

    {cross_html}

    <div class="section-title">相關新聞分析</div>
    {news_html}

    <div class="section-title">同業競爭對手表現對比</div>
    <div class="summary-box">
        {competitor_perf_html}
    </div>

    <div class="section-title">競爭公司近期新聞</div>
    <div class="summary-box">
        {competitor_news_html if competitor_news_html else '<p style="color:var(--muted);">目前沒有找到主要競爭對手的近期新聞，或該股票尚未設定競爭對手帳號。</p>'}
    </div>

    <div class="section-title">歷史數據表</div>
    <div id="data-table-container" class="loading">載入中...</div>
    """

    extra_js = f"""
    const symbol = "{symbol}";
    let chart = null;
    let candleSeries = null;
    let lineSeries = null;
    let volumeSeries = null;
    let ma5Series = null, ma20Series = null, ma60Series = null;
    let bbUpper = null, bbMid = null, bbLower = null;
    let currentPeriod = "1mo";
    let currentType = "candle";
    let lastData = [];
    const indOn = {{ ma5: true, ma20: true, ma60: true, bb: false }};

    function sma(values, period) {{
        const out = [];
        for (let i = 0; i < values.length; i++) {{
            if (i + 1 < period) {{ out.push(null); continue; }}
            let sum = 0;
            for (let j = i - period + 1; j <= i; j++) sum += values[j];
            out.push(sum / period);
        }}
        return out;
    }}

    function bollinger(values, period, mult) {{
        const mid = sma(values, period);
        const upper = [], lower = [];
        for (let i = 0; i < values.length; i++) {{
            if (mid[i] == null) {{ upper.push(null); lower.push(null); continue; }}
            let sum = 0;
            for (let j = i - period + 1; j <= i; j++) {{
                const d = values[j] - mid[i];
                sum += d * d;
            }}
            const std = Math.sqrt(sum / period);
            upper.push(mid[i] + mult * std);
            lower.push(mid[i] - mult * std);
        }}
        return {{ mid, upper, lower }};
    }}

    function toLineData(dates, values) {{
        const out = [];
        for (let i = 0; i < dates.length; i++) {{
            if (values[i] == null) continue;
            out.push({{ time: dates[i], value: +values[i].toFixed(4) }});
        }}
        return out;
    }}

    function ensureChart() {{
        if (chart) return;
        const el = document.getElementById("klineChart");
        chart = LightweightCharts.createChart(el, {{
            layout: {{
                background: {{ type: "solid", color: "#1e293b" }},
                textColor: "#94a3b8"
            }},
            grid: {{
                vertLines: {{ color: "#334155" }},
                horzLines: {{ color: "#334155" }}
            }},
            crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
            rightPriceScale: {{ borderColor: "#334155" }},
            timeScale: {{ borderColor: "#334155", timeVisible: true }}
        }});
        candleSeries = chart.addCandlestickSeries({{
            upColor: "#22c55e", downColor: "#ef4444",
            borderUpColor: "#22c55e", borderDownColor: "#ef4444",
            wickUpColor: "#22c55e", wickDownColor: "#ef4444"
        }});
        lineSeries = chart.addLineSeries({{ color: "#3b82f6", lineWidth: 2, visible: false }});
        ma5Series = chart.addLineSeries({{ color: "#fbbf24", lineWidth: 1.5, title: "MA5" }});
        ma20Series = chart.addLineSeries({{ color: "#a78bfa", lineWidth: 1.5, title: "MA20" }});
        ma60Series = chart.addLineSeries({{ color: "#38bdf8", lineWidth: 1.5, title: "MA60" }});
        bbUpper = chart.addLineSeries({{ color: "rgba(148,163,184,0.7)", lineWidth: 1, lineStyle: 2, title: "BB上" }});
        bbMid = chart.addLineSeries({{ color: "rgba(148,163,184,0.5)", lineWidth: 1, title: "BB中" }});
        bbLower = chart.addLineSeries({{ color: "rgba(148,163,184,0.7)", lineWidth: 1, lineStyle: 2, title: "BB下" }});
        volumeSeries = chart.addHistogramSeries({{
            priceFormat: {{ type: "volume" }},
            priceScaleId: "",
            scaleMargins: {{ top: 0.82, bottom: 0 }}
        }});
        chart.priceScale("").applyOptions({{ scaleMargins: {{ top: 0.82, bottom: 0 }} }});
        window.addEventListener("resize", () => chart.applyOptions({{ width: el.clientWidth }}));
    }}

    function updateIndicators(data) {{
        const dates = data.map(d => d.date);
        const closes = data.map(d => d.close);
        const m5 = sma(closes, 5);
        const m20 = sma(closes, 20);
        const m60 = sma(closes, 60);
        const bb = bollinger(closes, 20, 2);
        ma5Series.setData(toLineData(dates, m5));
        ma20Series.setData(toLineData(dates, m20));
        ma60Series.setData(toLineData(dates, m60));
        bbUpper.setData(toLineData(dates, bb.upper));
        bbMid.setData(toLineData(dates, bb.mid));
        bbLower.setData(toLineData(dates, bb.lower));
        ma5Series.applyOptions({{ visible: !!indOn.ma5 }});
        ma20Series.applyOptions({{ visible: !!indOn.ma20 }});
        ma60Series.applyOptions({{ visible: !!indOn.ma60 }});
        bbUpper.applyOptions({{ visible: !!indOn.bb }});
        bbMid.applyOptions({{ visible: !!indOn.bb }});
        bbLower.applyOptions({{ visible: !!indOn.bb }});
    }}

    function applyData(data, type) {{
        ensureChart();
        lastData = data;
        const candles = data.map(d => ({{
            time: d.date, open: d.open, high: d.high, low: d.low, close: d.close
        }}));
        const line = data.map(d => ({{ time: d.date, value: d.close }}));
        const vols = data.map(d => ({{
            time: d.date,
            value: d.volume,
            color: d.close >= d.open ? "rgba(34,197,94,0.45)" : "rgba(239,68,68,0.45)"
        }}));
        candleSeries.setData(candles);
        lineSeries.setData(line);
        volumeSeries.setData(vols);
        if (type === "candle") {{
            candleSeries.applyOptions({{ visible: true }});
            lineSeries.applyOptions({{ visible: false }});
        }} else {{
            candleSeries.applyOptions({{ visible: false }});
            lineSeries.applyOptions({{ visible: true }});
        }}
        updateIndicators(data);
        chart.timeScale().fitContent();
        let table = '<table class="data-table"><thead><tr><th>日期</th><th>開盤</th><th>最高</th><th>最低</th><th>收盤</th><th>成交量</th></tr></thead><tbody>';
        data.slice(-20).reverse().forEach(d => {{
            table += `<tr><td>${{d.date}}</td><td>${{d.open}}</td><td>${{d.high}}</td><td>${{d.low}}</td><td>${{d.close}}</td><td>${{d.volume.toLocaleString()}}</td></tr>`;
        }});
        table += "</tbody></table>";
        document.getElementById("data-table-container").innerHTML = table;
    }}

    async function loadChart(period) {{
        currentPeriod = period;
        document.querySelectorAll(".period-btn[data-period]").forEach(b => b.classList.remove("active"));
        const pb = document.querySelector(`[data-period="${{period}}"]`);
        if (pb) pb.classList.add("active");
        const res = await fetch(`/api/history/${{symbol}}?period=${{period}}`);
        const data = await res.json();
        if (!data || !data.length) {{
            document.getElementById("data-table-container").innerHTML = '<div class="loading">無歷史資料</div>';
            return;
        }}
        applyData(data, currentType);
    }}

    function setChartType(type) {{
        currentType = type;
        document.querySelectorAll(".chart-type-btn").forEach(b => b.classList.remove("active"));
        const btn = document.querySelector(`[data-chart="${{type}}"]`);
        if (btn) btn.classList.add("active");
        if (lastData.length) applyData(lastData, type);
    }}

    function toggleInd(key) {{
        indOn[key] = !indOn[key];
        const btn = document.querySelector(`[data-ind="${{key}}"]`);
        if (btn) btn.classList.toggle("active", indOn[key]);
        if (lastData.length) updateIndicators(lastData);
    }}

    document.querySelectorAll(".period-btn[data-period]").forEach(btn => {{
        btn.addEventListener("click", () => loadChart(btn.dataset.period));
    }});
    document.querySelectorAll(".chart-type-btn").forEach(btn => {{
        btn.addEventListener("click", () => setChartType(btn.dataset.chart));
    }});
    document.querySelectorAll(".ind-btn").forEach(btn => {{
        btn.addEventListener("click", () => toggleInd(btn.dataset.ind));
    }});

    (function() {{
        const s = document.createElement("script");
        s.src = "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js";
        s.onload = () => loadChart("1mo");
        document.head.appendChild(s);
    }})();
    """
    return render_page(f"{symbol} 詳情", "stocks-us" if not is_hk else "stocks-hk", content, extra_js, auto_refresh=True)

@app.route("/news/us")
def news_us():
    cache_key = datetime.now().strftime("%Y%m%d%H%M")
    return render_news_page("美股新聞", "news-us", get_news_data("US", cache_key))

@app.route("/news/hk")
def news_hk():
    cache_key = datetime.now().strftime("%Y%m%d%H%M")
    return render_news_page("港股新聞", "news-hk", get_news_data("HK", cache_key))

def render_news_page(title, page, data):
    news_html = ""
    if not data["news"]:
        news_html = '<div class="loading">目前沒有符合條件的新聞</div>'
    else:
        for n in data["news"]:
            a = n.get("analysis", {})
            sent_class = "sentiment-pos" if a.get("label") == "positive" else ("sentiment-neg" if a.get("label") == "negative" else "sentiment-neu")
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
                </div>
                <a class="link-btn" href="{n.get("url", "#")}" target="_blank" rel="noopener">閱讀原文 →</a>
                <div class="analysis-box">
                    <h4>📌 重點整合</h4><p style="font-size:0.95rem; margin-bottom:8px;">{a.get("overall", "")}</p>
                    <h4>正面角度</h4><ul>{pos_list}</ul>
                    <h4>風險 / 負面角度</h4><ul>{neg_list}</ul>
                    <h4>可能對市場的影響</h4><ul>{impact_list}</ul>
                </div>
            </div>"""
    return render_page(title, page, f'<h1>{title}</h1><div class="subtitle">最後更新：{data["generated_at"]}</div>{news_html}')

@app.route("/about")
def about():
    content = """
    <h1>關於本站</h1>
    <div class="section-title">功能</div>
    <p style="color:var(--muted); line-height:1.8;">
        今日漲跌榜、每日摘要、每日收盤紀錄、個股走勢圖、公司基本面與營收、新聞交叉對比、競爭公司新聞、短長期展望分析。
        所有內容僅供參考，不構成投資建議。
    </p>
    """
    return render_page("關於", "about", content)

@app.route("/api/history/<symbol>")
def api_history(symbol):
    return jsonify(get_history(symbol, request.args.get("period", "1mo")))

@app.route("/api/dashboard")
def api_dashboard():
    return jsonify(get_dashboard_data(datetime.now().strftime("%Y%m%d%H%M")))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
