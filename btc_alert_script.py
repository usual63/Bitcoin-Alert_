import os
import json
import requests
from datetime import datetime, timedelta

# =========================================================================
# [0] 상태(State) 저장 및 로드 모듈 (기억 상실 방지)
# =========================================================================
STATE_FILE = "alert_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except: pass
    return {
        "last_daily_date": None, 
        "last_score": None, 
        "last_scenario": None, 
        "last_c_level": None,
        "last_oi": None,
        "last_error_date": None
    }

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"상태 저장 실패: {e}")

# =========================================================================
# [1] 실시간 API 데이터 수집 모듈 (MEXC + CoinMetrics + Coinbase)
# =========================================================================

def fetch_coinmetrics_data(metric):
    try:
        now = datetime.utcnow()
        start_str = (now - timedelta(days=5)).strftime('%Y-%m-%d')
        url = f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics={metric}&start_time={start_str}&frequency=1d"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json().get('data', [])
            if data: return float(data[-1].get(metric, 1.0))
    except: pass
    return 1.0 

def fetch_fear_and_greed_index():
    try:
        res = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        if res.status_code == 200: return int(res.json()['data'][0]['value'])
    except: pass
    return 50

def fetch_coinbase_price():
    try:
        res = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=5)
        if res.status_code == 200: return float(res.json()['data']['amount'])
    except: pass
    return 0.0

def fetch_market_data():
    market_data = {
        'price': 0.0, 'funding_rate_annual': 0.0, 'oi_current': 0.0,
        'atr_15m_avg': 0.0, 'max_tr_15m': 0.0, 'vwap': 0.0,
        'is_sweep_candle': False, 'stablecoin_peg': 1.0, 
        'price_to_ma120_ratio': 0.0, 'mvrv_ratio': 1.0, 'sopr': 1.0, 'fear_greed_index': 50,
        'buy_vol_ratio': 0.5, 'coinbase_premium': 0.0, 'price_drop_24h': 0.0,
        'flash_crash_5m': False, 'basis_ratio': 0.0
    }
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
    
    try:
        market_data['fear_greed_index'] = fetch_fear_and_greed_index()
        market_data['mvrv_ratio'] = fetch_coinmetrics_data('CapMVRVCur')
        market_data['sopr'] = fetch_coinmetrics_data('SOPR')
        
        cb_price = fetch_coinbase_price()
        
        ticker_url = "https://contract.mexc.com/api/v1/contract/ticker?symbol=BTC_USDT"
        res_ticker = requests.get(ticker_url, headers=headers, timeout=10)
        if res_ticker.status_code == 200:
            ticker_data = res_ticker.json().get('data', {})
            market_data['price'] = float(ticker_data.get('lastPrice', 0))
            market_data['funding_rate_annual'] = float(ticker_data.get('fundingRate', 0)) * 3 * 365 * 100
            market_data['oi_current'] = float(ticker_data.get('openInterest', ticker_data.get('holdVol', 0)))
            
        if cb_price > 0 and market_data['price'] > 0:
            market_data['coinbase_premium'] = ((cb_price - market_data['price']) / market_data['price']) * 100
            market_data['basis_ratio'] = ((market_data['price'] - cb_price) / cb_price) * 100
        
        # 5분봉 쾌속 센서 (플래시 크래시 감지)
        k5_url = "https://contract.mexc.com/api/v1/contract/kline/BTC_USDT?interval=Min5&limit=2"
        k5_res = requests.get(k5_url, headers=headers, timeout=5)
        if k5_res.status_code == 200:
            k5_data = k5_res.json().get('data', {})
            if len(k5_data.get('close', [])) >= 2:
                open_5m = float(k5_data['open'][-1])
                close_5m = float(k5_data['close'][-1])
                if open_5m > 0 and ((close_5m - open_5m) / open_5m) <= -0.03:
                    market_data['flash_crash_5m'] = True

        # 15분봉 로직 (스윕 캔들, VWAP, 현물 CVD Proxy)
        klines_15m_url = "https://contract.mexc.com/api/v1/contract/kline/BTC_USDT?interval=Min15&limit=100"
        k15_res = requests.get(klines_15m_url, headers=headers, timeout=10)
        if k15_res.status_code == 200:
            k15_data = k15_res.json().get('data', {})
            closes = k15_data.get('close', [])
            if len(closes) > 2:
                times, opens, highs, lows, vols = k15_data['time'], k15_data['open'], k15_data['high'], k15_data['low'], k15_data['vol']
                typical_price_vol, total_vol = 0, 0
                now_utc_date = datetime.utcnow().date()
                
                for i in range(1, len(closes)):
                    ts = float(times[i])
                    if ts > 1e11: ts = ts / 1000
                    if datetime.utcfromtimestamp(ts).date() == now_utc_date:
                        high, low, close_curr, vol = float(highs[i]), float(lows[i]), float(closes[i]), float(vols[i])
                        typical_price_vol += ((high + low + close_curr) / 3) * vol
                        total_vol += vol
                        
                market_data['vwap'] = typical_price_vol / total_vol if total_vol > 0 else market_data['price']
                
                body = abs(float(closes[-2]) - float(opens[-2]))
                lower_wick = min(float(opens[-2]), float(closes[-2])) - float(lows[-2])
                if lower_wick > (body * 2) and lower_wick > (market_data['price'] * 0.002): 
                    market_data['is_sweep_candle'] = True

                recent_buy_vol = sum([float(vols[i]) for i in range(-5, 0) if float(closes[i]) > float(opens[i])])
                recent_total_vol = sum([float(vols[i]) for i in range(-5, 0)])
                market_data['buy_vol_ratio'] = (recent_buy_vol / recent_total_vol) if recent_total_vol > 0 else 0.5

        # 1일봉 로직 (MA120, 24시간 절대 낙폭)
        klines_1d_url = "https://contract.mexc.com/api/v1/contract/kline/BTC_USDT?interval=Day1&limit=150"
        k1d_res = requests.get(klines_1d_url, headers=headers, timeout=10)
        if k1d_res.status_code == 200:
            k1d_data = k1d_res.json().get('data', {})
            closes_1d = [float(c) for c in k1d_data.get('close', [])]
            if len(closes_1d) >= 120:
                ma120 = sum(closes_1d[-120:]) / 120
                market_data['price_to_ma120_ratio'] = (market_data['price'] - ma120) / ma120
            if len(closes_1d) >= 2:
                market_data['price_drop_24h'] = ((market_data['price'] - closes_1d[-2]) / closes_1d[-2]) * 100

        peg_url = "https://api.mexc.com/api/v3/ticker/price?symbol=USDCUSDT"
        peg_res = requests.get(peg_url, headers=headers, timeout=10)
        if peg_res.status_code == 200:
            market_data['stablecoin_peg'] = float(peg_res.json().get('price', 1.0))

    except Exception as e:
        print(f"시장 데이터 수집 에러: {e}")
        
    return market_data

# =========================================================================
# [2] 하이브리드 전략 엔진 (V4 무결성 가중치 및 논리 회로)
# =========================================================================

def analyze_strategy(market, past_oi):
    score = 0
    
    # 1. 온체인 절대 가치 평가 (최대 40점)
    if market['mvrv_ratio'] >= 3.0: score += 40
    elif market['mvrv_ratio'] >= 2.4: score += 20
    
    # 2. 파생 거품 및 과열 (최대 30점)
    if market['funding_rate_annual'] > 50.0: score += 30
    elif market['funding_rate_annual'] > 20.0: score += 15
    
    # 3. 스마트머니 이탈 감지 (최대 20점)
    if market['sopr'] >= 1.05: score += 20
    elif market['sopr'] >= 1.02: score += 10
    
    # 4. 투심 및 이격도 (최대 10점)
    if market['fear_greed_index'] >= 80: score += 5
    if market['price_to_ma120_ratio'] > 0.30: score += 5 

    # [조건 B] 블랙스완 킬 스위치 (1Hit-Kill)
    is_blackswan = False
    oi_drop_ratio = 0.0
    if past_oi and past_oi > 0 and market['oi_current'] > 0:
        oi_drop_ratio = ((past_oi - market['oi_current']) / past_oi) * 100
        
    if market['stablecoin_peg'] < 0.985: is_blackswan = True
    if oi_drop_ratio >= 5.0: is_blackswan = True
    if market['flash_crash_5m']: is_blackswan = True
    if market['basis_ratio'] < -5.0: is_blackswan = True
    if market['price_drop_24h'] < -10.0: is_blackswan = True

    # [조건 C] 현물 딥바잉 스나이퍼 (메인/서브 다차원 교차 검증)
    main_keys = 0
    if market['coinbase_premium'] > 0.05: main_keys += 1
    if market['buy_vol_ratio'] > 0.60: main_keys += 1
        
    sub_keys = 0
    if market['funding_rate_annual'] < -50.0: sub_keys += 1
    if market['is_sweep_candle']: sub_keys += 1
    if market['price'] > market['vwap']: sub_keys += 1
    
    c_level = 0
    if main_keys == 0 and sub_keys >= 1: 
        c_level = 1
    elif main_keys >= 1 and sub_keys >= 1: 
        c_level = 2
    elif main_keys == 2 and sub_keys >= 1: 
        c_level = 3

    scenario = 'B' if is_blackswan else 'A'
    
    return scenario, score, c_level, oi_drop_ratio

# =========================================================================
# [3] 동적 텔레그램 메시지 발송
# =========================================================================

def get_strategy_message(scenario_type, btc_price, score, c_level, market, oi_drop_ratio, alert_mode="DAILY"):
    
    mvrv_stat = f"🔴 위험 (MVRV {market['mvrv_ratio']:.2f})" if market['mvrv_ratio'] >= 3.0 else f"🟢 안전 (MVRV {market['mvrv_ratio']:.2f})"
    sopr_stat = f"🔴 이탈 (SOPR {market['sopr']:.2f})" if market['sopr'] >= 1.05 else f"🟢 안전 (SOPR {market['sopr']:.2f})"
    fr_stat = f"🔴 과열 ({market['funding_rate_annual']:.1f}%)" if market['funding_rate_annual'] > 50.0 else f"🟢 정상"
    fgi_stat = f"🔴 탐욕 ({market['fear_greed_index']})" if market['fear_greed_index'] >= 80 else f"🟢 보통 ({market['fear_greed_index']})"
    
    peg_stat = "🔴 위험 (디페깅)" if market['stablecoin_peg'] < 0.985 else "🟢 안전"
    oi_drop_stat = f"🔴 위험 ({oi_drop_ratio:.1f}% 증발)" if oi_drop_ratio >= 5.0 else f"🟢 방어"
    flash_stat = "🔴 위험 (투매 발생)" if market['flash_crash_5m'] else "🟢 안전"
    basis_stat = f"🔴 위험 ({market['basis_ratio']:.1f}%)" if market['basis_ratio'] < -5.0 else "🟢 안전"
    drop_stat = f"🔴 위험 ({market['price_drop_24h']:.1f}%)" if market['price_drop_24h'] < -10.0 else "🟢 안전"

    cbp = market['coinbase_premium']
    buy_ratio = market['buy_vol_ratio'] * 100
    fr = market['funding_rate_annual']
    
    cbp_stat = f"🟢 기관 유입 ({cbp:.3f}%)" if cbp > 0.05 else f"⚪ 대기 ({cbp:.3f}%)"
    cvd_stat = f"🟢 순매수 리드 ({buy_ratio:.1f}%)" if market['buy_vol_ratio'] > 0.60 else f"⚪ 대기 ({buy_ratio:.1f}%)"
    fr_c_stat = f"🟢 숏 과밀 ({fr:.1f}%)" if fr < -50.0 else f"⚪ 대기 ({fr:.1f}%)"
    sweep_stat = "🟢 꼬리 방어" if market['is_sweep_candle'] else "⚪ 대기"
    vwap_stat = "🟢 상향 돌파" if market['price'] > market['vwap'] else "⚪ 대기"

    if c_level == 0:
        c_decision = "➔ 판정: ⚪ 대기 (특이사항 없음)"
    elif c_level == 1:
        c_decision = "➔ 판정: ⚪ 휩쏘 경계 (현물 주체 미확인)"
    elif c_level == 2:
        c_decision = "➔ 판정: 🟡 1차 분할 진입 (세력 개입 및 찐반등 초입)"
    else:
        c_decision = "➔ 판정: 🟢 강력 매수 스나이핑 (기관 유입 및 100% 스퀴즈 확정)"

    cond_a_block = f"""══════════════════════
<b>[조건 A: 온체인/파생/심리 복합 과열 현황]</b>
<i>(💡 시장이 서서히 과열되며 파생 레버리지 거품이 끼고 있는지를 진단하는 중장기 관점의 사이클 고점 경보기)</i>
• 온체인 MVRV(40): {mvrv_stat}
• 파생 레버리지(30): {fr_stat}
• 고래 엑시트(20): {sopr_stat}
• 심리 및 이격도(10): {fgi_stat}"""

    cond_b_block = f"""══════════════════════
<b>[조건 B: 블랙스완 킬 스위치 현황]</b>
<i>(💡 건강한 조정이 아닌 시스템 붕괴(연쇄 청산 등)로 인해 즉각 대피해야 할 폭락장 시작 신호)</i>
• 뱅크런 방어: {peg_stat}
• 15분 청산맵: {oi_drop_stat}
• 5분 투매/베이시스: {flash_stat} / {basis_stat}
• 24시간 계단식 폭락: {drop_stat}
➔ 판정: {"🔴 대피 (시스템 장악)" if scenario_type == 'B' else "🟢 안전 (조건 미달)"}"""

    cond_c_block = f"""══════════════════════
<b>[조건 C: 숏 스퀴즈 현물 구조대 현황]</b>
<i>(💡 모두가 던지는 공포의 하락장 속에서 현물 세력이 바닥을 주워 담는 단기 V자 반등 딥바잉 스나이퍼)</i>
• 코베 프리미엄 (Main): {cbp_stat}
• 현물 CVD (Main): {cvd_stat}
• 펀딩비 극음수 (Sub): {fr_c_stat}
• 스윕 캔들 (Sub): {sweep_stat}
• VWAP 상향 (Sub): {vwap_stat}
{c_decision}"""

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

    prefix = "🌅 <b>[오전 07:30 정규 브리핑]</b>\n" if alert_mode == "DAILY" else "⚡ <b>[지표 변동 긴급 알림]</b>\n"
    header_title = prefix + header_title

    if scenario_type == 'A':
        return f"""{header_title}

📈 타겟 자산: BTC (${btc_price:,.2f})
⚠️ 시장 과열 스코어: {score}점 / 100점

{cond_a_block}

{cond_b_block}

{cond_c_block}

💡 <b>시스템 판독 및 행동 지침</b>: 
{action_advice}"""

    elif scenario_type == 'B':
        return f"""{prefix}<b>🚨 [시스템 마비] 비트코인 블랙스완 킬 스위치 발동</b>

📉 타겟 자산: BTC (${btc_price:,.2f})
⚠️ 킬 스위치 발동 (조건 A 점수 무시 및 강제 오버라이드)

{cond_a_block}

{cond_b_block}

{cond_c_block}

💡 <b>비상 행동 지침</b>:
시장 미시구조의 붕괴 및 연쇄 청산이 감지되었습니다. 스코어와 무관하게 즉시 모든 레버리지 및 현물을 전량 매도하고 대피하십시오."""
    
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
    print(f"[{datetime.utcnow()}] 비트코인 퀀트 전략 시스템 스캔 시작 (V4 Master Architecture)...")
    
    current_kst = datetime.utcnow() + timedelta(hours=9)
    kst_date_str = current_kst.strftime('%Y-%m-%d')
    
    state = load_state()
    past_oi = state.get("last_oi", 0.0)
    
    market_data = fetch_market_data()
    btc_current_price = market_data.get('price', 0.0)
    
    if btc_current_price == 0.0:
        if state.get("last_error_date") != kst_date_str:
            send_telegram_message("<b>🚨 [시스템 에러]</b> API 통신 장애 발생. 봇이 데이터를 불러오지 못했습니다.")
            state["last_error_date"] = kst_date_str
            save_state(state)
        return
        
    scenario, total_score, current_c_level, oi_drop_ratio = analyze_strategy(market_data, past_oi)
    
    current_minutes = current_kst.hour * 60 + current_kst.minute
    target_minutes = 7 * 60 + 30
    
    is_daily_needed = (current_minutes >= target_minutes) and (state.get("last_daily_date") != kst_date_str)
    
    is_color_changed = (state.get("last_c_level") != current_c_level) and (state.get("last_c_level") is not None)
    is_danger_score = total_score >= 50 and state.get("last_score", 0) < 50
    is_scenario_changed = (state.get("last_scenario") != scenario) and (state.get("last_scenario") is not None)
    
    state["last_score"] = total_score
    state["last_scenario"] = scenario
    state["last_c_level"] = current_c_level
    if market_data['oi_current'] > 0:
        state["last_oi"] = market_data['oi_current'] 
    
    if is_daily_needed:
        alert_message = get_strategy_message(scenario, btc_current_price, total_score, current_c_level, market_data, oi_drop_ratio, alert_mode="DAILY")
        send_telegram_message(alert_message)
        state["last_daily_date"] = kst_date_str
        save_state(state)
        print("정규 브리핑 발송 완료")
        
    elif (is_color_changed or is_danger_score or is_scenario_changed):
        alert_message = get_strategy_message(scenario, btc_current_price, total_score, current_c_level, market_data, oi_drop_ratio, alert_mode="CHANGE")
        send_telegram_message(alert_message)
        save_state(state)
        print(f"색깔 변동 또는 위험 감지 긴급 알림 발송 완료 (C등급: {current_c_level})")
        
    elif state.get("last_c_level") is None:
        save_state(state)
        print("시스템 최초 실행: 상태 저장 완료")
    else:
        save_state(state)
        print(f"지표 변동 없음 (스코어: {total_score}, C등급: {current_c_level}). 침묵 유지.")

if __name__ == "__main__":
    main()
