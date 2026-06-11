import os
import requests
from datetime import datetime, timedelta

# =========================================================================
# [1] 실시간 API 데이터 수집 모듈 (MEXC + Fear&Greed + CoinMetrics Free API)
# =========================================================================

def fetch_mvrv_ratio():
    """
    CoinMetrics 커뮤니티 API를 활용하여 무료로 MVRV Ratio를 수집합니다.
    API Key 불필요. 깃허브 서버리스 환경에 맞춰 CSV 캐싱 없이 최근 5일 데이터 실시간 스나이핑.
    """
    try:
        now = datetime.utcnow()
        start_str = (now - timedelta(days=5)).strftime('%Y-%m-%d')
        url = f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=CapMVRVCur&start_time={start_str}&frequency=1d"
        res = requests.get(url, timeout=10)
        
        if res.status_code == 200:
            data = res.json().get('data', [])
            if data:
                # 데이터 배열 중 가장 마지막(최신) 일자의 MVRV 반환
                return float(data[-1].get('CapMVRVCur', 1.0))
        else:
            print(f"MVRV 수집 에러 (Status {res.status_code})")
    except Exception as e:
        print(f"MVRV Ratio 통신 에러: {e}")
    return 1.0 # 에러 시 안전값

def fetch_fear_and_greed_index():
    try:
        res = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        if res.status_code == 200:
            return int(res.json()['data'][0]['value'])
    except: pass
    return 50

def fetch_market_data():
    market_data = {
        'price': 0.0,
        'funding_rate_annual': 0.0,
        'bid_depth': 0.0,
        'atr_15m_avg': 0.0,
        'max_tr_15m': 0.0, 
        'vwap': 0.0,
        'is_sweep_candle': False,
        'stablecoin_peg': 1.0,
        'rsi_1d': 50.0,
        'price_to_ma20_ratio': 0.0,
        'mvrv_ratio': 1.0,          # 코인메트릭스 MVRV Ratio
        'fear_greed_index': 50
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    }
    
    try:
        market_data['fear_greed_index'] = fetch_fear_and_greed_index()
        market_data['mvrv_ratio'] = fetch_mvrv_ratio() # 독립 수집 모듈 가동
        
        # 1. 가격 및 펀딩비 (MEXC)
        ticker_url = "https://contract.mexc.com/api/v1/contract/ticker?symbol=BTC_USDT"
        res_ticker = requests.get(ticker_url, headers=headers, timeout=10)
        if res_ticker.status_code == 200:
            ticker_data = res_ticker.json().get('data', {})
            market_data['price'] = float(ticker_data.get('lastPrice', 0))
            market_data['funding_rate_annual'] = float(ticker_data.get('fundingRate', 0)) * 3 * 365 * 100
        
        # 2. 오더북 뎁스 (MEXC)
        depth_url = "https://contract.mexc.com/api/v1/contract/depth/BTC_USDT?limit=50"
        depth_res = requests.get(depth_url, headers=headers, timeout=10)
        if depth_res.status_code == 200:
            bids = depth_res.json().get('data', {}).get('bids', [])
            market_data['bid_depth'] = sum([float(b[1]) * 0.0001 for b in bids if len(b) > 1])
        
        # 3. 15분봉 미시구조 (블랙스완 및 구조대)
        klines_15m_url = "https://contract.mexc.com/api/v1/contract/kline/BTC_USDT?interval=Min15&limit=100"
        k15_res = requests.get(klines_15m_url, headers=headers, timeout=10)
        if k15_res.status_code == 200:
            k15_data = k15_res.json().get('data', {})
            closes = k15_data.get('close', [])
            if len(closes) > 2:
                times, opens, highs, lows, vols = k15_data['time'], k15_data['open'], k15_data['high'], k15_data['low'], k15_data['vol']
                tr_list = []
                typical_price_vol, total_vol = 0, 0
                now_utc_date = datetime.utcnow().date()
                
                recent_start_idx = max(1, len(closes) - 14)
                for i in range(recent_start_idx, len(closes)):
                    high, low, close_prev = float(highs[i]), float(lows[i]), float(closes[i-1])
                    tr_list.append(max(high - low, abs(high - close_prev), abs(low - close_prev)))
                
                for i in range(1, len(closes)):
                    ts = float(times[i])
                    if ts > 1e11: ts = ts / 1000
                    candle_date = datetime.utcfromtimestamp(ts).date()
                    if candle_date == now_utc_date:
                        high, low, close_curr, vol = float(highs[i]), float(lows[i]), float(closes[i]), float(vols[i])
                        typical_price_vol += ((high + low + close_curr) / 3) * vol
                        total_vol += vol
                        
                market_data['atr_15m_avg'] = sum(tr_list) / len(tr_list) if tr_list else 0
                market_data['max_tr_15m'] = max(tr_list) if tr_list else 0
                market_data['vwap'] = typical_price_vol / total_vol if total_vol > 0 else market_data['price']
                
                body = abs(float(closes[-2]) - float(opens[-2]))
                lower_wick = min(float(opens[-2]), float(closes[-2])) - float(lows[-2])
                if lower_wick > (body * 2) and lower_wick > (market_data['price'] * 0.002): 
                    market_data['is_sweep_candle'] = True

        # 4. 일봉 매크로 지표 (RSI 및 이격도)
        klines_1d_url = "https://contract.mexc.com/api/v1/contract/kline/BTC_USDT?interval=Day1&limit=100"
        k1d_res = requests.get(klines_1d_url, headers=headers, timeout=10)
        if k1d_res.status_code == 200:
            k1d_data = k1d_res.json().get('data', {})
            closes_1d = [float(c) for c in k1d_data.get('close', [])]
            
            if len(closes_1d) >= 20:
                ma20 = sum(closes_1d[-20:]) / 20
                market_data['price_to_ma20_ratio'] = (market_data['price'] - ma20) / ma20
                
                if len(closes_1d) >= 15:
                    diffs = [closes_1d[i] - closes_1d[i-1] for i in range(1, len(closes_1d))]
                    gains = [d if d > 0 else 0 for d in diffs]
                    losses = [abs(d) if d < 0 else 0 for d in diffs]
                    
                    avg_gain = sum(gains[:14]) / 14
                    avg_loss = sum(losses[:14]) / 14
                    
                    for i in range(14, len(diffs)):
                        avg_gain = (avg_gain * 13 + gains[i]) / 14
                        avg_loss = (avg_loss * 13 + losses[i]) / 14
                        
                    if avg_loss == 0: market_data['rsi_1d'] = 100.0
                    else: market_data['rsi_1d'] = 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))

        # 5. 스테이블코인 페깅
        peg_url = "https://api.mexc.com/api/v3/ticker/price?symbol=USDCUSDT"
        peg_res = requests.get(peg_url, headers=headers, timeout=10)
        if peg_res.status_code == 200:
            market_data['stablecoin_peg'] = float(peg_res.json().get('price', 1.0))

    except Exception as e:
        print(f"시장 데이터 수집 에러: {e}")
        
    return market_data

# =========================================================================
# [2] 하이브리드 전략 엔진
# =========================================================================

def analyze_strategy(market):
    score = 0
    
    # 1. MVRV Ratio (역사적 고점 3.0, 강세장 확장 2.4 기준)
    if market['mvrv_ratio'] >= 3.0: score += 20
    elif market['mvrv_ratio'] >= 2.4: score += 10
    
    # 2. 공포 탐욕 지수
    if market['fear_greed_index'] >= 85: score += 20
    elif market['fear_greed_index'] >= 75: score += 10
    
    # 3. 파생 펀딩비 과열
    if market['funding_rate_annual'] > 50.0: score += 20
    elif market['funding_rate_annual'] > 20.0: score += 10
    
    # 4. 일봉 매크로 RSI
    if market['rsi_1d'] > 80.0: score += 20
    elif market['rsi_1d'] > 70.0: score += 10
    
    # 5. 일봉 이평선 이격도
    if market['price_to_ma20_ratio'] > 0.20: score += 20 
    elif market['price_to_ma20_ratio'] > 0.10: score += 10 

    is_blackswan = False
    if market['stablecoin_peg'] < 0.985: is_blackswan = True
    if market['bid_depth'] < 20: is_blackswan = True
    if market['max_tr_15m'] > (market['price'] * 0.05): is_blackswan = True

    rescue_triggers = 0
    if market['funding_rate_annual'] < -50.0: rescue_triggers += 1
    if market['is_sweep_candle']: rescue_triggers += 1
    if market['price'] > market['vwap']: rescue_triggers += 1
    
    is_rescue = (rescue_triggers >= 2)

    if is_blackswan:
        if is_rescue: return 'C', score
        return 'B', score
    return 'A', score

# =========================================================================
# [3] 동적 텔레그램 메시지 발송
# =========================================================================

def get_strategy_message(scenario_type, btc_price, score, market):
    
    mvrv = market['mvrv_ratio']
    if mvrv >= 3.0: mvrv_stat = f"🔴 위험 (MVRV 비율 {mvrv:.2f} 역사적 고평가)"
    elif mvrv >= 2.4: mvrv_stat = f"🟠 경고 (MVRV 비율 {mvrv:.2f} 강세장 후반)"
    else: mvrv_stat = f"🟢 안전 (MVRV 비율 {mvrv:.2f} 정상 궤도)"
    
    fgi = market['fear_greed_index']
    if fgi >= 85: fgi_stat = f"🔴 위험 (극단적 탐욕 {fgi})"
    elif fgi >= 75: fgi_stat = f"🟠 경고 (탐욕 진입 {fgi})"
    else: fgi_stat = f"🟢 안전 (중립 또는 공포 {fgi})"

    fr = market['funding_rate_annual']
    if fr > 50.0: fr_stat = f"🔴 위험 (연환산 {fr:.1f}% 과열)"
    elif fr > 20.0: fr_stat = f"🟠 경고 (연환산 {fr:.1f}% 누적)"
    else: fr_stat = f"🟢 안전 (정상 펀딩비)"

    rsi = market['rsi_1d']
    if rsi > 80.0: rsi_stat = f"🔴 위험 (1D RSI {rsi:.1f} 한계)"
    elif rsi > 70.0: rsi_stat = f"🟠 경고 (1D RSI {rsi:.1f} 과매수)"
    else: rsi_stat = f"🟢 안전 (1D RSI {rsi:.1f} 안정권)"

    ma_ratio = market['price_to_ma20_ratio'] * 100
    if ma_ratio > 20.0: ma_stat = f"🔴 위험 (1D MA20 대비 +{ma_ratio:.1f}% 폭등)"
    elif ma_ratio > 10.0: ma_stat = f"🟡 주의 (1D MA20 이격 상승)"
    else: ma_stat = f"🟢 안전 (이평선 안착)"

    peg_stat = "🔴 위험 (디페깅)" if market['stablecoin_peg'] < 0.985 else "🟢 안전"
    depth_stat = "🔴 위험 (호가 진공)" if market['bid_depth'] < 20 else "🟢 안전"
    atr_stat = "🔴 위험 (변동성 폭발)" if market['max_tr_15m'] > (btc_price * 0.05) else "🟢 안전"

    cond_a_block = f"""══════════════════════
<b>[조건 A: 온체인/파생/심리 복합 과열 현황]</b>
• 온체인 MVRV(20): {mvrv_stat}
• 공포 탐욕(20): {fgi_stat}
• 파생 과열(20): {fr_stat}
• 매크로 RSI(20): {rsi_stat}
• 이평선 이격(20): {ma_stat}"""

    cond_b_block = f"""══════════════════════
<b>[조건 B: 블랙스완 킬 스위치 현황]</b>
• 스테이블 뱅크런: {peg_stat}
• 오더북 뎁스 붕괴: {depth_stat}
• 청산맵/ATR 폭발: {atr_stat}"""

    if score >= 80:
        action_advice = "대중의 탐욕과 온체인 과열이 극에 달한 사이클 고점입니다. 즉시 모든 자산을 현금화하십시오."
        header_title = "🚨 [전량 매도] 비트코인 하이브리드 위험도 분석"
    elif score >= 50:
        action_advice = "시장의 쏠림과 구조적 과열이 강합니다. 알트코인 전량 매도 및 비트코인 50% 분할 익절을 권장합니다."
        header_title = "🔴 [강력 경고] 비트코인 하이브리드 위험도 분석"
    elif score >= 30:
        action_advice = "과열 징후가 포착되었습니다. 신규 진입을 중단하고 레버리지를 축소하십시오."
        header_title = "🟠 [비중 축소] 비트코인 하이브리드 위험도 분석"
    else:
        action_advice = "온체인 및 기술적 지표 모두 과열되지 않은 안전 구간입니다. 기존 포지션을 유지하십시오."
        header_title = "🟢 [안전 유지] 비트코인 하이브리드 위험도 분석"

    if scenario_type == 'A':
        return f"""<b>{header_title}</b>

📈 타겟 자산: BTC (${btc_price:,.2f})
⚠️ 시장 과열 스코어: {score}점 / 100점

{cond_a_block}

{cond_b_block}
➔ 판정: 🟢 안전 (조건 미달)

💡 <b>시스템 판독 및 행동 지침</b>: 
{action_advice}"""

    elif scenario_type == 'B':
        return f"""<b>🚨 [시스템 마비] 비트코인 블랙스완 킬 스위치 발동</b>

📉 타겟 자산: BTC (${btc_price:,.2f})
⚠️ 킬 스위치 발동 (조건 A 점수 무시 및 강제 오버라이드)

{cond_a_block}

{cond_b_block}
➔ 판정: 🔴 대피 (시스템 장악)

💡 <b>시스템 판독 및 행동 지침</b>:
시장 미시구조의 진공 상태 또는 연쇄 청산이 감지되었습니다. 스코어와 무관하게 즉시 모든 레버리지 및 현물을 전량 매도하고 대피하십시오."""

    elif scenario_type == 'C':
        return f"""<b>🟢 [초고속 재진입] 비트코인 숏 스퀴즈 구조대 발동</b>

🚀 타겟 자산: BTC (${btc_price:,.2f})
⏱️ 상태: 블랙스완 대피 이후 특이 현상(V자 랠리) 포착

{cond_a_block}

{cond_b_block}
➔ 판정: 🟢 조건 C 충족 (강제 재진입 승인)

💡 <b>시스템 판독 및 행동 지침</b>:
세력의 유동성 사냥(Liquidity Sweep)이 종료되었습니다. 블랙스완 매도 상태를 오버라이드하고 즉시 롱 포지션 및 현물을 재진입하여 V자 반등 수익을 확보하십시오."""
    
    return "전략 오류"

def send_telegram_message(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id: return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
    except: pass

def main():
    print(f"[{datetime.now()}] 비트코인 퀀트 전략 시스템 스캔 시작 (CoinMetrics Edition)...")
    market_data = fetch_market_data()
    btc_current_price = market_data.get('price', 0.0)
    
    if btc_current_price == 0.0:
        print("API 통신 지연으로 가격을 불러오지 못했습니다. 에러 알림을 전송합니다.")
        send_telegram_message("<b>🚨 [시스템 에러]</b> API 통신 장애 발생. 봇이 데이터를 불러오지 못했습니다. 거래소 API 상태를 확인하십시오.")
        return
        
    scenario, total_score = analyze_strategy(market_data)
    alert_message = get_strategy_message(scenario, btc_current_price, total_score, market_data)
    send_telegram_message(alert_message)
    print("시스템 스캔 및 프로세스 종료")

if __name__ == "__main__":
    main()
