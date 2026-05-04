#!/usr/bin/env python3
"""
三阴不破阳 - 每日自动扫描脚本
"""
import pandas as pd
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ============ 配置 ============
CONFIG = {
    "zhangting_threshold": 0.095,
    "volume_shrink_ratio": 0.90,
    "volume_expand_ratio": 1.05,
    "min_yin_days": 3,
    "max_yin_days": 5,
}
WECHAT_KEY = os.environ.get("SERVER_CHAN_KEY", "")
DATA_DIR = Path(__file__).parent
OUTPUT_DIR = DATA_DIR / "public"
LOG_FILE = DATA_DIR / "scan.log"

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def find_patterns(df_stock):
    df = df_stock.sort_values('time').reset_index(drop=True)
    if len(df) < 7: return [], []
    df['prev_close'] = df['close'].shift(1)
    df['change_pct'] = (df['close'] - df['prev_close']) / df['prev_close']
    
    complete, potential = [], []
    zt, vs, ve = CONFIG["zhangting_threshold"], CONFIG["volume_shrink_ratio"], CONFIG["volume_expand_ratio"]
    
    for i in range(1, len(df) - 3):
        if df.loc[i, 'change_pct'] < zt: continue
        yang_low, yang_vol, yang_date = df.loc[i, 'low'], df.loc[i, 'volume'], df.loc[i, 'time']
        
        for dur in range(CONFIG["min_yin_days"], CONFIG["max_yin_days"] + 1):
            if i + dur >= len(df): continue
            valid = True
            for j in range(1, dur + 1):
                idx = i + j
                if idx >= len(df) or df.loc[idx, 'close'] >= df.loc[idx, 'open'] or df.loc[idx, 'volume'] >= yang_vol * vs or df.loc[idx, 'low'] < yang_low:
                    valid = False; break
            if not valid: continue
            
            sig_idx = i + dur + 1
            name = str(df_stock['thsname_cn'].iloc[0]) if 'thsname_cn' in df_stock.columns else ticker
            ticker = str(df_stock['thscode'].iloc[0]) if 'thscode' in df_stock.columns else "unknown"
            
            if sig_idx < len(df):
                avg_yin = df.loc[i+1:i+dur, 'volume'].mean()
                sig = df.loc[sig_idx]
                if sig['close'] > sig['open'] and sig['volume'] >= avg_yin * ve:
                    complete.append({
                        "ticker": ticker, "name": name, "type": "complete",
                        "yang_day": yang_date, "yang_close": round(df.loc[i, 'close'], 2),
                        "yin_days": dur, "signal_day": sig['time'], "signal_close": round(sig['close'], 2),
                        "support_price": round(yang_low, 2)
                    })
                else:
                    potential.append({
                        "ticker": ticker, "name": name, "type": "potential",
                        "yang_day": yang_date, "support_price": round(yang_low, 2), "yin_days": dur
                    })
            else:
                potential.append({
                    "ticker": ticker, "name": name, "type": "potential",
                    "yang_day": yang_date, "support_price": round(yang_low, 2), "yin_days": dur
                })
    return complete, potential

def main():
    log("=" * 40); log("扫描开始"); log("=" * 40)
    csv_files = list(DATA_DIR.glob("*.csv"))
    if not csv_files:
        log("错误: 没有找到CSV文件!"); sys.exit(1)
    
    dfs = []
    for f in csv_files:
        df = pd.read_csv(f).dropna(subset=['open','high','low','close','volume'])
        dfs.append(df)
    combined = pd.concat(dfs, ignore_index=True)
    log(f"加载 {len(csv_files)} 个文件, {combined['thscode'].nunique()} 只股票")
    
    all_results = []
    for ticker in combined['thscode'].unique():
        stock_df = combined[combined['thscode'] == ticker][['time','open','high','low','close','volume','thsname_cn','thscode']].copy()
        c, p = find_patterns(stock_df)
        all_results.extend(c); all_results.extend(p)
    
    complete = [r for r in all_results if r["type"] == "complete"]
    potential = [r for r in all_results if r["type"] == "potential"]
    log(f"结果: {len(complete)} 完整, {len(potential)} 潜在")
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_DIR / "scan_results.json", "w") as f:
        json.dump(all_results, f, ensure_ascii=False)
    
    stocks = [{"ticker": t, "name": combined[combined['thscode']==t].iloc[0]['thsname_cn']} for t in combined['thscode'].unique()]
    with open(OUTPUT_DIR / "a_stock_list.json", "w") as f:
        json.dump(stocks, f, ensure_ascii=False)
    
    # 微信通知
    if WECHAT_KEY and complete:
        import urllib.request, urllib.parse
        title = f"三阴不破阳 - {datetime.now().strftime('%m-%d')} 发现{len(complete)}只"
        lines = [f"扫描{len(stocks)}只A股，发现 {len(complete)} 只完整形态：\n"]
        for c in complete:
            lines.append(f"【{c['name']}】{c['ticker']}\n涨停: {c['yang_day']} 信号: {c['signal_day']}\n支撑: {c['support_price']}\n---")
        content = "\n".join(lines)
        data = urllib.parse.urlencode({"title": title, "desp": content}).encode()
        req = urllib.request.Request(f"https://sctapi.ftqq.com/{WECHAT_KEY}.send", data=data, method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                log("微信推送成功")
        except Exception as e:
            log(f"微信推送失败: {e}")
    elif not WECHAT_KEY:
        log("未配置 SERVER_CHAN_KEY，跳过微信通知")
    else:
        log("未发现完整形态，不发送通知")
    
    for c in complete: log(f"[完整] {c['name']}({c['ticker']}): {c['yang_day']} -> {c['signal_day']}")
    log("扫描完成!")

if __name__ == "__main__":
    main()
