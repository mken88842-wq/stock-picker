#!/usr/bin/env python3
import pandas as pd
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

WECHAT_KEY = os.environ.get("SERVER_CHAN_KEY", "")
OUTPUT_DIR = Path(__file__).parent / "public"
LOG_FILE = Path(__file__).parent / "scan.log"

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def send_wechat(title, content):
    if not WECHAT_KEY:
        log("错误: 未配置 SERVER_CHAN_KEY")
        return False
    try:
        data = urllib.parse.urlencode({"title": title, "desp": content}).encode('utf-8')
        req = urllib.request.Request(f"https://sctapi.ftqq.com/{WECHAT_KEY}.send", data=data, method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read().decode())
            log(f"微信返回: {result}")
            return result.get("code") == 0
    except Exception as e:
        log(f"微信异常: {e}")
        return False

def main():
    log("=" * 40)
    log(f"SERVER_CHAN_KEY={'已设置' if WECHAT_KEY else '未设置'}")
    
    # 一启动就发测试消息
    if WECHAT_KEY:
        log("发送启动测试消息...")
        ok = send_wechat("三阴不破阳 - 扫描启动", f"时间: {datetime.now().strftime('%H:%M')}\n脚本已启动!")
        log(f"测试消息: {'成功' if ok else '失败'}")
    else:
        log("警告: 没有 SERVER_CHAN_KEY，无法发送微信")
    
    try:
        import akshare as ak
    except ImportError:
        os.system("pip install akshare -q")
        import akshare as ak
    
    log("获取A股数据...")
    stock_list = ak.stock_zh_a_spot_em()
    tickers = stock_list['代码'].head(200).tolist()
    
    end = datetime.now()
    start = end - timedelta(days=45)
    start_s, end_s = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    
    all_data = []
    for idx, code in enumerate(tickers):
        if idx % 50 == 0:
            log(f"下载: {idx}/{len(tickers)}...")
        try:
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_s, end_date=end_s, adjust="qfq")
            if df is None or len(df) < 15:
                continue
            df = df.rename(columns={'日期': 'time', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'volume'})
            name = stock_list[stock_list['代码'] == code]['名称'].values[0]
            df['thsname_cn'] = name
            df['thscode'] = f"{code}.SZ" if code.startswith(('0', '3')) else f"{code}.SH"
            all_data.append(df[['time', 'open', 'high', 'low', 'close', 'volume', 'thsname_cn', 'thscode']])
        except:
            continue
    
    combined = pd.concat(all_data, ignore_index=True)
    log(f"获取完成: {combined['thscode'].nunique()} 只")
    
    # 选股
    log("运行选股算法...")
    all_results = []
    for ticker in combined['thscode'].unique():
        stock_df = combined[combined['thscode'] == ticker][['time', 'open', 'high', 'low', 'close', 'volume', 'thsname_cn', 'thscode']]
        stock_df = stock_df.sort_values('time').reset_index(drop=True)
        if len(stock_df) < 7:
            continue
        stock_df['prev_close'] = stock_df['close'].shift(1)
        stock_df['change_pct'] = (stock_df['close'] - stock_df['prev_close']) / stock_df['prev_close']
        
        for i in range(1, len(stock_df) - 3):
            if stock_df.loc[i, 'change_pct'] < 0.095:
                continue
            yang_low = stock_df.loc[i, 'low']
            yang_date = stock_df.loc[i, 'time']
            for dur in range(3, 6):
                if i + dur >= len(stock_df):
                    continue
                valid = True
                for j in range(1, dur + 1):
                    idx = i + j
                    if idx >= len(stock_df) or stock_df.loc[idx, 'close'] >= stock_df.loc[idx, 'open']:
                        valid = False
                        break
                if not valid:
                    continue
                
                sig_idx = i + dur + 1
                name = str(stock_df['thsname_cn'].iloc[0])
                ticker_code = str(stock_df['thscode'].iloc[0])
                if sig_idx < len(stock_df):
                    sig = stock_df.loc[sig_idx]
                    if sig['close'] > sig['open']:
                        all_results.append({"ticker": ticker_code, "name": name, "type": "complete", "yang_day": str(yang_date), "signal_day": str(sig['time']), "support_price": round(float(yang_low), 2)})
                    else:
                        all_results.append({"ticker": ticker_code, "name": name, "type": "potential", "yang_day": str(yang_date), "support_price": round(float(yang_low), 2)})
                else:
                    all_results.append({"ticker": ticker_code, "name": name, "type": "potential", "yang_day": str(yang_date), "support_price": round(float(yang_low), 2)})
    
    complete = [r for r in all_results if r["type"] == "complete"]
    potential = [r for r in all_results if r["type"] == "potential"]
    log(f"结果: {len(complete)} 完整, {len(potential)} 潜在")
    
    # 保存
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_DIR / "scan_results.json", "w") as f:
        json.dump(all_results, f, ensure_ascii=False)
    
    # 发最终结果
    lines = [f"扫描 **{combined['thscode'].nunique()}** 只A股\n完整: **{len(complete)}** | 潜在: **{len(potential)}**\n"]
    if complete:
        lines.append("### 完整形态\n")
        for c in complete:
            lines.append(f"**{c['name']}** ({c['ticker']})\n涨停: {c['yang_day']} | 信号: {c['signal_day']}\n")
    if not complete:
        lines.append("本次未发现完整形态。\n")
    lines.append("\n---\n*免责声明：本模型仅供学习研究，不构成投资建议*")
    
    send_wechat(f"三阴不破阳 - {datetime.now().strftime('%m-%d %H:%M')} 结果", "\n".join(lines))
    log("扫描完成!")

if __name__ == "__main__":
    main()
