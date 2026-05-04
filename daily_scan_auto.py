#!/usr/bin/env python3
"""
反量化博弈策略 — 每日自动扫描
核心信号：MA20即将突破 + MACD即将金叉
质量打分 + 每日Top3精选
"""
import pandas as pd
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np

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

def calc_indicators(df):
    """计算技术指标"""
    df['MA5'] = df['close'].rolling(5).mean()
    df['MA10'] = df['close'].rolling(10).mean()
    df['MA20'] = df['close'].rolling(20).mean()
    df['MA60'] = df['close'].rolling(60).mean()
    
    ema_fast = df['close'].ewm(span=12).mean()
    ema_slow = df['close'].ewm(span=26).mean()
    df['DIF'] = ema_fast - ema_slow
    df['DEA'] = df['DIF'].ewm(span=9).mean()
    df['MACD'] = 2 * (df['DIF'] - df['DEA'])
    
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift(1))
    low_close = np.abs(df['low'] - df['close'].shift(1))
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    
    df['VOL_MA20'] = df['volume'].rolling(20).mean()
    return df

def score_signal(df, i):
    """信号质量打分（满分10分）"""
    cur = df.iloc[i]
    score = 0
    reasons = []
    
    # 1. DIF位置 (0-3分)
    dif_gap = cur['DIF'] - cur['DEA']
    if 0 <= dif_gap <= 0.05:
        score += 3; reasons.append(f"DIF刚金叉+3")
    elif 0.05 < dif_gap <= 0.1:
        score += 2.5; reasons.append(f"DIF金叉稳定+2.5")
    elif dif_gap < 0 and dif_gap >= -0.05:
        score += 2; reasons.append(f"DIF即将金叉+2")
    elif dif_gap > 0:
        score += 1.5; reasons.append(f"金叉持续+1.5")
    # else: dif_gap < -0.05, 距离较远，不给分
    
    # 2. MA20位置 (0-1.5分)
    pct = (cur['close'] - cur['MA20']) / cur['MA20'] * 100
    if pct < -0.5:
        score += 1.5; reasons.append(f"MA20下方+1.5")
    elif pct < 0:
        score += 1; reasons.append(f"MA20附近+1")
    elif pct < 1:
        score += 0.5; reasons.append(f"MA20上方+0.5")
    
    # 3. 缩量 (0-2分)
    vol_ratio = cur['volume'] / cur['VOL_MA20'] if cur['VOL_MA20'] > 0 else 99
    if vol_ratio < 0.6:
        score += 2; reasons.append(f"极度缩量+2")
    elif vol_ratio < 0.8:
        score += 1.5; reasons.append(f"明显缩量+1.5")
    elif vol_ratio < 1.0:
        score += 1; reasons.append(f"略微缩量+1")
    
    # 4. MA20斜率 (0-1分)
    ma20_trend = (cur['MA20'] - df.iloc[i-5]['MA20']) / df.iloc[i-5]['MA20'] * 100
    if ma20_trend > 0.5:
        score += 1; reasons.append(f"MA20上行+1")
    elif ma20_trend > 0:
        score += 0.5; reasons.append(f"MA20走平+0.5")
    
    # 5. 均线收敛 (0-1.5分)
    ma5_ma20_gap = abs(cur['MA5'] - cur['MA20']) / cur['MA20'] * 100
    if ma5_ma20_gap < 2:
        score += 1.5; reasons.append(f"均线收敛+1.5")
    elif ma5_ma20_gap < 3:
        score += 1; reasons.append(f"均线接近+1")
    
    # 6. 近期企稳 (0-1分)
    up_count = sum(1 for j in range(i-2, i+1) if df.iloc[j]['close'] > df.iloc[j-1]['close'])
    if up_count >= 2:
        score += 1; reasons.append(f"近期企稳+1")
    elif up_count >= 1:
        score += 0.5; reasons.append(f"近期震荡+0.5")
    
    return round(score, 1), ' / '.join(reasons)

def detect_signals(df):
    """检测MA20+MACD信号"""
    if len(df) < 30:
        return []
    
    df = calc_indicators(df)
    signals = []
    
    for i in range(25, len(df)-2):  # 留3天给买入+验证
        cur = df.iloc[i]
        prev = df.iloc[i-1]
        
        # 基础条件：MA20附近
        pct = (cur['close'] - cur['MA20']) / cur['MA20']
        if abs(pct) > 0.02:
            continue
        
        # MA20不向下
        ma20_trend = (cur['MA20'] - df.iloc[i-5]['MA20']) / df.iloc[i-5]['MA20']
        if ma20_trend < -0.005:
            continue
        
        # MACD准备金叉
        dif_gap = cur['DIF'] - cur['DEA']
        gap_narrowing = dif_gap > (prev['DIF'] - prev['DEA'])
        close_to_golden = abs(dif_gap) < 0.3
        macd_shortening = (prev['MACD'] < 0 and cur['MACD'] > prev['MACD']) or \
                          (prev['MACD'] >= 0 and cur['MACD'] >= prev['MACD'])
        if not (gap_narrowing and (close_to_golden or macd_shortening)):
            continue
        
        # 安全过滤
        if cur['volume'] > cur['VOL_MA20'] * 3 and cur['VOL_MA20'] > 0:
            continue
        if cur['close'] < 2:
            continue
        if cur['ATR'] / cur['close'] > 0.06:
            continue
        
        # 打分
        qscore, reasons = score_signal(df, i)
        
        signals.append({
            'signal_date': df.index[i].strftime('%Y-%m-%d'),
            'close': round(cur['close'], 2),
            'ma20': round(cur['MA20'], 2),
            'pct_to_ma20': round(pct * 100, 2),
            'dif_gap': round(dif_gap, 3),
            'vol_ratio': round(cur['volume'] / cur['VOL_MA20'], 2),
            'quality': qscore,
            'reasons': reasons,
        })
    
    return signals

def main():
    log("=" * 50)
    log("反量化博弈策略 — 每日扫描启动")
    log(f"SERVER_CHAN_KEY={'已设置' if WECHAT_KEY else '未设置'}")
    log("=" * 50)
    
    if WECHAT_KEY:
        ok = send_wechat("反量化策略 - 扫描启动", f"时间: {datetime.now().strftime('%H:%M')}\n脚本已启动")
        log(f"启动测试: {'成功' if ok else '失败'}")
    
    # 安装依赖
    try:
        import akshare as ak
    except ImportError:
        os.system("pip install akshare -q")
        import akshare as ak
    
    log("获取A股列表...")
    stock_list = ak.stock_zh_a_spot_em()
    log(f"获取到 {len(stock_list)} 只")
    
    # 下载最近90天数据（每批50只）
    tickers = stock_list['代码'].tolist()
    SCAN_COUNT = min(len(tickers), 500)  # 扫描500只（可调整）
    
    end = datetime.now()
    start = end - timedelta(days=90)
    start_s, end_s = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    
    log(f"下载 {SCAN_COUNT} 只股票数据 ({start_s}~{end_s})...")
    
    all_data = []
    for idx in range(SCAN_COUNT):
        code = tickers[idx]
        if idx % 50 == 0:
            log(f"下载: {idx}/{SCAN_COUNT}...")
        try:
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_s, end_date=end_s, adjust="qfq")
            if df is None or len(df) < 30:
                continue
            name = stock_list[stock_list['代码'] == code]['名称'].values[0]
            df['代码'] = code
            df['名称'] = name
            all_data.append(df)
        except:
            continue
    
    if not all_data:
        log("下载失败，无数据")
        send_wechat("反量化策略 - 错误", "数据下载失败，请检查网络")
        return
    
    log(f"下载完成: {len(all_data)} 只, 开始分析...")
    
    # 分析每只股票
    today_signals = []
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    for df in all_data:
        code = df['代码'].iloc[0]
        name = df['名称'].iloc[0]
        
        df_p = df.rename(columns={'日期': 'time', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'volume'})
        df_p['time'] = pd.to_datetime(df_p['time'])
        df_p = df_p.sort_values('time').set_index('time')
        
        signals = detect_signals(df_p)
        
        # 只看今天的信号
        for s in signals:
            if s['signal_date'] == today_str:
                s['code'] = code
                s['name'] = name
                today_signals.append(s)
    
    log(f"今日信号: {len(today_signals)} 个")
    
    # 按质量分排序
    today_signals.sort(key=lambda x: x['quality'], reverse=True)
    
    # 准备推送内容
    output_signals = today_signals[:5]  # Top 5
    
    lines = [
        f"📊 **反量化博弈策略**",
        f"扫描 {SCAN_COUNT} 只A股",
        f"今日发现 **{len(today_signals)}** 个信号",
        f"",
    ]
    
    if output_signals:
        lines.append(f"🔥 **TOP {len(output_signals)} 精选信号**")
        lines.append(f"---")
        
        for i, s in enumerate(output_signals):
            medals = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣']
            medal = medals[i] if i < 5 else '  '
            q = s['quality']
            star = '⭐' * int(q / 2.5) + f" ({q}/10)"
            
            lines.append(f"{medal} **{s['name']}** ({s['code']})")
            lines.append(f"  质量: {star}")
            lines.append(f"  收盘: {s['close']} | MA20: {s['ma20']} | 偏离: {s['pct_to_ma20']:+.2f}%")
            lines.append(f"  DIF-DEA: {s['dif_gap']:+.3f} | 量比: {s['vol_ratio']:.2f}")
            lines.append(f"  要点: {s['reasons']}")
            lines.append(f"  操作: 明天开盘买入 | 持有5天 | 止损-5%")
            lines.append(f"---")
        
        lines.append(f"💡 **使用指南**")
        lines.append(f"1. 同仓位不超过2-3只")
        lines.append(f"2. 每只持有5天，到期不管盈亏卖出")
        lines.append(f"3. 触-5%止损提前卖出")
        lines.append(f"4. 每天新信号替换到期信号")
        
        # 保存信号
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(OUTPUT_DIR / "anti_quant_signals.json", "w", encoding="utf-8") as f:
            json.dump(output_signals, f, ensure_ascii=False, indent=2)
        
        # 发送微信
        title = f"反量化策略 {today_str} — {len(today_signals)}个信号"
        content = "\n".join(lines)
        send_wechat(title, content)
    else:
        lines.append("❌ 今日无信号。可能原因：")
        lines.append("- 市场处于极端行情")
        lines.append("- 信号条件过严")
        lines.append("- 数据不足")
        send_wechat(f"反量化策略 {today_str} — 无信号", "\n".join(lines))
    
    log("扫描完成!")

if __name__ == "__main__":
    main()
