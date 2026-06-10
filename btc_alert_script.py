import os
import requests
from datetime import datetime

# =========================================================================
# [1] 실시간 API 데이터 수집 모듈 (MEXC + Fear&Greed 완전 독립형 엔진)
# =========================================================================

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
        'rsi_4h': 50.0,
        'price_to_ma20_ratio': 0.0,
        'volume_exhaustion': False, 
        'fear_greed_index': 50,
        'mayer_multiple': 1.0  # MVRV를 대체하는 100% 무료 자체 연산 매크로 지표
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    }
    
    try:
        market_data['fear_greed_index'] = fetch_fear_and_greed_index()
        
        # 1. 가격 및 펀딩비 (MEXC)
        ticker_url = "https://contract.mexc.com/api/v1/contract/ticker?symbol=BTC_USDT"
        res_ticker = requests.get(ticker_url, headers=headers, timeout=10)
        if res_ticker.status_code == 200:
            ticker_data = res_ticker.json().get('data', {})
            market_data['price'] = float(ticker_data.get('lastPrice', 0))
            market_data['funding_rate_annual'] = float(ticker_data.get('fundingRate', 0)) * 3 * 365 * 100
        
        # 2. 오더북 뎁스
        depth_url = "https://contract.mexc.com/api/v1/contract/depth/BTC_USDT?limit=50"
        depth_res = requests.get(depth_url, headers=headers, timeout=10)
        if depth_res.status_code == 200:
            bids = depth_res.json().get('data', {}).get('bids', [])
            market_data['bid_depth'] = sum([float(b[1]) * 0.0001 for b in bids if len(b) > 1])
        
        # 3. 15분봉 미시구조
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

        # 4. 4시간봉 매크로 지표 (RSI & Volume)
        klines_4h_url = "https://contract.mexc.com/api/v1/contract/kline/BTC_USDT?interval=Hour4&limit=100"
        k4h_res = requests.get(klines_4h_url, headers=headers, timeout=10)
        if k4h_res.status_code == 200:
            k4h_data = k4h_res.json().get('data', {})
            closes_4h = [float(c) for c in k4h_data.get('close', [])]
            vols_4h = [float(v) for v in k4h_data.get('vol', [])]
            
            if len(closes_4h) >= 21:
                ma20 = sum(closes_4h[-20:]) / 20
                market_data['price_to_ma20_ratio'] = (market_data['price'] - ma20) / ma20
                
                vol_ma20 = sum(vols_4h[-22:-2]) / 20 if len(vols_4h) > 21 else 1
                if vols_4h[-2] < (vol_ma20 * 0.5):
                    market_data['volume_exhaustion'] = True
                
                if len(closes_4h) >= 15:
                    diffs = [closes_4h[i] - closes_4h[i-1] for i in range(1, len(closes_4h))]
                    gains = [d if d > 0 else 0 for d in diffs]
                    losses = [abs(d) if d < 0 else 0 for d in diffs]
                    
                    avg_gain = sum(gains[:14]) / 14
                    avg_loss = sum(losses[:14]) / 14
                    
                    for i in range(14, len(diffs)):
                        avg_gain = (avg_gain * 13 + gains[i]) / 14
                        avg_loss = (avg_loss * 13 + losses[i]) / 14
                        
                    if avg_loss == 0: market_data['rsi_4h'] = 100.0
                    else: market_data['rsi_4h'] = 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))

        # 5. 일봉(1Day) 메이어 배수 연산 (MVRV 완벽 대체 100% 무료 엔진)
        klines_1d_url = "https://contract.mexc.com/api/v1/contract/kline/BTC_USDT?interval=Day1&limit=200"
        k1d_res = requests.get(klines_1d_url, headers=headers, timeout=10)
        if k1d_res.status_code == 200:
            k1d_data = k1d_res.json().get('data', {})
            closes_1d = [float(c) for c in k1d_data.get('close', [])]
            if len(closes_1d) >= 200:
                ma200 = sum(closes_1d[-200:]) / 200
                market_data['mayer_multiple'] = market_data['price'] / ma200

        # 6. 스테이블코인 페깅
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
    
    # MVRV를 메이어 배수(Mayer Multiple)로 완벽 대체
    if market['mayer_multiple'] >= 2.4: score += 20
    elif market['mayer_multiple'] >= 2.0: score += 10
    
    if market['fear_greed_index'] >= 85: score += 20
    elif market['fear_greed_index'] >= 75: score += 10
    
    if market['funding_rate_annual'] > 50.0: score += 20
    elif market['funding_rate_annual'] > 20.0: score += 10
    
    if market['rsi_4h'] > 80.0: score += 20
    elif market['rsi_4h'] > 70.0: score += 10
    
    if market['price_to_ma20_ratio'] > 0.10: score += 20
    elif market['price_to_ma20_ratio'] > 0.05: score += 10

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
    
    # 동적 상태 문자열 매핑 (MVRV -> 메이어 배수 교체)
    mm = market['mayer_multiple']
    if mm >= 2.4: mm_stat = f"🔴 위험 (메이어 배수 {mm:.2f} 역사적 과열)"
    elif mm >= 2.0: mm_stat = f"🟠 경고 (메이어 배수 {mm:.2f} 강세장 과열)"
    else: mm_stat = f"🟢 안전 (메이어 배수 {mm:.2f} 정상 궤도)"
    
    fgi = market['fear_greed_index']
    if fgi >= 85: fgi_stat = f"🔴 위험 (극단적 탐욕 {fgi})"
    elif fgi >= 75: fgi_stat = f"🟠 경고 (탐욕 진입 {fgi})"
    else: fgi_stat = f"🟢 안전 (중립 또는 공포 {fgi})"

    fr = market['funding_rate_annual']
    if fr > 50.0: fr_stat = f"🔴 위험 (연환산 {fr:.1f}% 과열)"
    elif fr > 20.0: fr_stat = f"🟠 경고 (연환산 {fr:.1f}% 누적)"
    else: fr_stat = f"🟢 안전 (정상 펀딩비)"

    rsi = market['rsi_4h']
    if rsi > 80.0: rsi_stat = f"🔴 위험 (RSI {rsi:.1f} 한계)"
    elif rsi > 70.0: rsi_stat = f"🟠 경고 (RSI {rsi:.1f} 과매수)"
    else: rsi_stat = f"🟢 안전 (RSI {rsi:.1f} 안정권)"

    ma_ratio = market['price_to_ma20_ratio'] * 100
    if ma_ratio > 10.0: ma_stat = f"🔴 위험 (MA20 대비 +{ma_ratio:.1f}% 폭등)"
    elif ma_ratio > 5.0: ma_stat = f"🟡 주의 (MA20 이격 상승)"
    else: ma_stat = f"🟢 안전 (이평선 안착)"

    peg_stat = "🔴 위험 (디페깅)" if market['stablecoin_peg'] < 0.985 else "🟢 안전"
    depth_stat = "🔴 위험 (호가 진공)" if market['bid_depth'] < 20 else "🟢 안전"
    atr_stat = "🔴 위험 (변동성 폭발)" if market['max_tr_15m'] > (btc_price * 0.05) else "🟢 안전"

    cond_a_block = f"""══════════════════════
<b>[조건 A: 온체인/파생/심리 복합 과열 현황]</b>
• 매크로 메이어(20): {mm_stat}
• 공포 탐욕(20): {fgi_stat}
• 파생 과열(20): {fr_stat}
• 매
