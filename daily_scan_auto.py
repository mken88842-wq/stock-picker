#!/usr/bin/env python3
import pandas as pd
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

CONFIG = {"zhangting_threshold": 0.095, "volume_shrink_ratio": 0.90, "volume_expand_ratio": 1.05, "min_yin_days": 3, "max_yin_days": 5}
WECHAT_KEY = os.environ.get("SERVER_CHAN_KEY", "")
OUTPUT_DIR = Path(__file__).parent / "public"
LOG_FILE = Path(__file__).parent / "scan.log"

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def fetch_data_akshare(max_stocks=500):
    try:
        import akshare as ak
    except ImportError:
        os.system("pip install akshare -q")
        import akshare as ak
    
    log("获取A股列表...")
    stock_list = ak.stock_zh_a_spot_em()
    tickers = stock_list['代码'].head(max_stocks).tolist()
    
    end = datetime.now()
    start = end - timedelta(days=60)
    start_s, end_s = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    
    all_data = []
    for idx, code in enumerate(tickers):
        if idx % 50 == 0:
            log(f"下载: {idx}/{len(tickers)}...")
        try:
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_s, end_date=end_s, adjust="qfq")
            if df is None or len(df) < 10:
                continue
            df = df.rename(columns={'日期': 'time', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'volume'})
            name = stock_list[stock_list['代码'] == code]['名称'].values[0] if len(stock_list[stock_list['代码'] == code]) > 0 else code
            df['thsname_cn'] = name
            df['thscode'] = f"{code}.SZ" if code.startswith(('0', '3')) else f"{code}.SH"
            all_data.append(df[['time', 'open', 'high', 'low', 'close', 'volume', 'thsname_cn', 'thscode']])
        except:
            continue
    
    if all_data:
        combined = pd.concat(all_data, ignore_index=True)
        log(f"获取完成: {combined['thscode'].nunique()} 只")
        return combined
    else:
        log("AKShare 失败"); sys.exit(1)

def find_patterns(df_stock):
    df = df_stock.sort_values('time').reset_index(drop=True)
    if len(df) < 7:
        return [], []
    df['prev_close'] = df['close'].shift(1)
    df['change_pct'] = (df['close'] - df['prev_close']) / df['prev_close']
    complete, potential = [], []
    zt, vs, ve = CONFIG["zhangting_threshold"], CONFIG["volume_shrink_ratio"], CONFIG["volume_expand_ratio"]
    
    for i in range(1, len(df) - 3):
        if df.loc[i, 'change_pct'] < zt:
            continue
        yang_low, yang_vol, yang_date = df.loc[i, 'low'], df.loc[i, 'volume'], df.loc[i, 'time']
        for dur in range(CONFIG["min_yin_days"], CONFIG["max_yin_days"] + 1):
            if i + dur >= len(df):
                continue
            valid = True
            for j in range(1, dur + 1):
                idx = i + j
                if idx >= len(df) or df.loc[idx, 'close'] >= df.loc[idx, 'open'] or df.loc[idx, 'volume'] >= yang_vol * vs or df.loc[idx, 'low'] < yang_low:
                    valid = False
                    break
            if not valid:
                continue
            sig_idx = i + dur + 1
            ticker = str(df_stock['thscode'].iloc[0])
            name = str(df_stock['thsname_cn'].iloc[0])
            if sig_idx < len(df):
                avg_yin = df.loc[i+1:i+dur, 'volume'].mean()
                sig = df.loc[sig_idx]
                if sig['close'] > sig['open'] and sig['volume'] >= avg_yin * ve:
                    complete.append({"ticker": ticker, "name": name, "type": "complete", "yang_day": str(yang_date), "yang_close": round(float(df.loc[i, 'close']), 2), "yin_days": dur, "signal_day": str(sig['time']), "signal_close": round(float(sig['close']), 2), "support_price": round(float(yang_low), 2)})
                else:
                    potential.append({"ticker": ticker, "name": name, "type": "potential", "yang_day": str(yang_date), "support_price": round(float(yang_low), 2), "yin_days": dur})
            else:
                potential.append({"ticker": ticker, "name": name, "type": "potential", "yang_day": str(yang_date), "support_price": round(float(yang_low), 2), "yin_days": dur})
    return complete, potential

def save_price_json(combined_df):
    price_dir = OUTPUT_DIR / "price_data"
    price_dir.mkdir(parents=True, exist_ok=True)
    for ticker in combined_df['thscode'].unique():
        stock_df = combined_df[combined_df['thscode'] == ticker].sort_values('time')
        records = [{"time": str(r['time']), "open": float(r['open']), "high": float(r['high']), "low": float(r['low']), "close": float(r['close']), "volume": float(r['volume'])} for _, r in stock_df.iterrows()]
        with open(price_dir / f"{ticker}.json", "w") as f:
            json.dump(records, f)

def send_wechat(complete, potential, total):
    if not WECHAT_KEY:
        log("未配置微信"); return
    import urllib.request, urllib.parse
    title = f"三阴不破阳 - {datetime.now().strftime('%m-%d')} 扫描{total}只"
    lines = [f"扫描 **{total}** 只A股 | 完整: **{len(complete)}** | 潜在: **{len(potential)}**\n"]
    if complete:
        lines.append("### 完整形态\n")
        for c in complete:
            lines.append(f"**{c['name']}** ({c['ticker']})\n涨停: {c['yang_day']} | 信号: {c['signal_day']} | 支撑: {c['support_price']}\n")
    if potential:
        lines.append("### 潜在形态\n")
        for p in potential[:5]:
            lines.append(f"**{p['name']}** ({p['ticker']})\n涨停: {p['yang_day']} | 缩量: {p['yin_days']}天 | 支撑: {p['support_price']}\n")
    lines.append("\n---\n*免责声明：本模型仅供学习研究，不构成投资建议*")
    try:
        data = urllib.parse.urlencode({"title": title, "desp": "\n".join(lines)}).encode('utf-8')
        req = urllib.request.Request(f"https://sctapi.ftqq.com/{WECHAT_KEY}.send", data=data, method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        with urllib.request.urlopen(req, timeout=15) as r:
            log("微信推送成功")
    except Exception as e:
        log(f"微信异常: {e}")

def main():
    log("=" * 40); log("扫描开始"); log("=" * 40)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    log("获取数据...")
    combined = fetch_data_akshare(max_stocks=500)
    
    log("运行算法...")
    all_results = []
    tickers = combined['thscode'].unique()
    for idx, ticker in enumerate(tickers):
        if idx % 100 == 0:
            log(f"进度: {idx}/{len(tickers)}...")
        stock_df = combined[combined['thscode'] == ticker][['time', 'open', 'high', 'low', 'close', 'volume', 'thsname_cn', 'thscode']]
        c, p = find_patterns(stock_df)
        all_results.extend(c); all_results.extend(p)
    
    complete = [r for r in all_results if r["type"] == "complete"]
    potential = [r for r in all_results if r["type"] == "potential"]
    log(f"结果: {len(complete)} 完整, {len(potential)} 潜在")
    
    with open(OUTPUT_DIR / "scan_results.json", "w") as f:
        json.dump(all_results, f, ensure_ascii=False)
    
    stocks = [{"ticker": t, "name": str(combined[combined['thscode']==t].iloc[0]['thsname_cn'])} for t in tickers]
    with open(OUTPUT_DIR / "a_stock_list.json", "w") as f:
        json.dump(stocks, f, ensure_ascii=False)
    
    save_price_json(combined)
    
    if complete or potential:
        send_wechat(complete, potential, len(tickers))
    else:
        log("无形态，不通知")
    
    for c in complete:
        log(f"[完整] {c['name']}({c['ticker']}): {c['yang_day']} -> {c['signal_day']}")
    log("扫描完成!")

if __name__ == "__main__":
    main()
