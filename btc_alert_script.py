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
        "last_dca_stage": None,  # DCA 매매 구간 변동 추적용 추가
        "last_error_date": None
    }

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"상태 저장 실패: {e}")

# =========================================================================
# [1] 실시간 API 데이터 수집 모듈 (기존 + DCA 심층 지표 추가)
# =========================================================================

def fetch_mvrv_ratio():
    try:
        now = datetime.utcnow()
        start_str = (now - timedelta(days=5)).strftime('%Y-%m-%d')
        url = f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=CapMVRVCur&start_time={start_str}&frequency=1d"
        res = requests.get(url, timeout=10)
        
        if res.status_code == 200:
            data = res.json().get('data', [])
            if data:
                return float(data[-1].get('CapMVRVCur', 1.0))
    except Exception as e:
        print(f"MVRV Ratio 통신 에러: {e}")
    return 1.0 

def fetch_fear_and_greed_index():
    try:
        res = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        if res.status_code == 200:
            return int(res.json()['data'][0]['value'])
    except: pass
    return 50

def fetch_market_data():
    market_data = {
        'price': 0.0, 'funding_rate_annual': 0.0, 'bid_depth': 0.0,
        'atr_15m_avg': 0.0, 'max_tr_15m': 0.0, 'vwap': 0.0,
        'is_sweep_candle': False, 'stablecoin_peg': 1.0, 'rsi_1d': 50.0,
        'price_to_ma20_ratio': 0.0, 'mvrv_ratio': 1.0, 'fear_greed_index': 50,
        # DCA 전용 S급 지표 변수 추가
        'cb_premium': 0.0, 'cvd_status': 0, 'oi_trend': 0.0, 'vol_ratio': 1.0
    }
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36', 'Accept': 'application/json'}
    
    try:
        market_data['fear_greed_index'] = fetch_fear_and_greed_index()
        market_data['mvrv_ratio'] = fetch_mvrv_ratio()
        
        # 1. MEXC 비트코인 선물 현재가 및 펀딩비
        ticker_url = "https://contract.mexc.com/api/v1/contract/ticker?symbol=BTC_USDT"
        res_ticker = requests.get(ticker_url, headers=headers, timeout=10)
        if res_ticker.status_code == 200:
            ticker_data = res_ticker.json().get('data', {})
            market_data['price'] = float(ticker_data.get('lastPrice', 0))
            market_data['funding_rate_annual'] = float(ticker_data.get('fundingRate', 0)) * 3 * 365 * 100
        
        # 2. 코인베이스 현물 가격 가져오기 (프리미엄 계산용 추가)
        try:
            cb_res = requests.get('https://api.coinbase.com/v2/prices/BTC-USD/spot', timeout=5)
            cb_price = float(cb_res.json()['data']['amount'])
            market_data['cb_premium'] = cb_price - market_data['price']
        except:
            market_data['cb_premium'] = 0.0

        # 기존 뎁스 및 K-Line 연산 로직 (그대로 유지)
        depth_url = "https://contract.mexc.com/api/v1/contract/depth/BTC_USDT?limit=50"
        depth_res = requests.get(depth_url, headers=headers, timeout=10)
        if depth_res.status_code == 200:
            bids = depth_res.json().get('data', {}).get('bids', [])
            market_data['bid_depth'] = sum([float(b[1]) * 0.0001 for b in bids if len(b) > 1])
        
        klines_15m_url = "https://contract.mexc.com/api/v1/contract/kline/BTC_USDT?interval=Min15&limit=100"
        k15_res = requests.get(klines_15m_url, headers=headers, timeout=10)
        if k15_res.status_code == 200:
            k15_data = k15_res.json().get('data', {})
            closes = k15_data.get('close', [])
            if len(closes) > 2:
                times, opens, highs, lows, vols = k15_data['time'], k15_data['open'], k15_data['high'], k15_data['low'], k15_data['vol']
                tr_list, typical_price_vol, total_vol = [], 0, 0
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
                        
                market_data['max_tr_15m'] = max(tr_list) if tr_list else 0
                market_data['vwap'] = typical_price_vol / total_vol if total_vol > 0 else market_data['price']
                
                body = abs(float(closes[-2]) - float(opens[-2]))
                lower_wick = min(float(opens[-2]), float(closes[-2])) - float(lows[-2])
                if lower_wick > (body * 2) and lower_wick > (market_data['price'] * 0.002): 
                    market_data['is_sweep_candle'] = True

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

        peg_url = "https://api.mexc.com/api/v3/ticker/price?symbol=USDCUSDT"
        peg_res = requests.get(peg_url, headers=headers, timeout=10)
        if peg_res.status_code == 200:
            market_data['stablecoin_peg'] = float(peg_res.json().get('price', 1.0))

    except Exception as e:
        print(f"시장 데이터 수집 에러: {e}")
        
    return market_data

# =========================================================================
# [2] 하이브리드 전략 및 DCA 스코어 엔진
# =========================================================================

def analyze_strategy(market):
    # 기존 로직 (건드리지 않음)
    score = 0
    if market['mvrv_ratio'] >= 3.0: score += 20
    elif market['mvrv_ratio'] >= 2.4: score += 10
    
    if market['fear_greed_index'] >= 85: score += 20
    elif market['fear_greed_index'] >= 75: score += 10
    
    if market['funding_rate_annual'] > 50.0: score += 20
    elif market['funding_rate_annual'] > 20.0: score += 10
    
    if market['rsi_1d'] > 80.0: score += 20
    elif market['rsi_1d'] > 70.0: score += 10
    
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

    scenario = 'A'
    if is_blackswan:
        scenario = 'C' if is_rescue else 'B'
        
    return scenario, score

def analyze_dca_score(market):
    """
    S급 5가지 지표를 활용한 -100점 ~ +100점 기반 통합 마켓 스코어 산출
    (온체인 데이터가 없는 부분은 0점 중립 처리 또는 연동 가능하게 구조화)
    """
    dca_score = 0
    details = {}
    
    # 1. 코인베이스 프리미엄 (30점 만점)
    cb_val = market['cb_premium']
    if cb_val >= 30: s_cb = 30; msg_cb = "+$30 이상 (기관 공격적 매수)"
    elif cb_val >= 10: s_cb = 15; msg_cb = "+$10~$29 (기관 매수 우위)"
    elif cb_val <= -30: s_cb = -30; msg_cb = "-$30 이하 (기관 공격적 매도)"
    elif cb_val <= -10: s_cb = -15; msg_cb = "-$10~-$29 (기관 매도 우위)"
    else: s_cb = 0; msg_cb = "중립 횡보"
    dca_score += s_cb
    details['cb'] = {'score': s_cb, 'msg': msg_cb, 'val': cb_val}
    
    # 2. 펀딩비 (20점 만점) - 기존 연환산 펀딩비 데이터 역이용
    fr_val = market['funding_rate_annual'] / (3 * 365 * 100) # 연환산을 다시 % 단위로 원복
    if fr_val <= -0.01: s_fr = 20; msg_fr = "극단적 공포 숏"
    elif fr_val <= 0.0: s_fr = 10; msg_fr = "음수(마이너스) 유지"
    elif fr_val >= 0.04: s_fr = -20; msg_fr = "극단적 롱 과열 (청산 임박)"
    elif fr_val >= 0.02: s_fr = -10; msg_fr = "롱 과열 심화"
    else: s_fr = 0; msg_fr = "베이스라인 정상"
    dca_score += s_fr
    details['fr'] = {'score': s_fr, 'msg': msg_fr, 'val': market['funding_rate_annual']}
    
    # 3, 4, 5. CVD, OI, 거래량 비율은 DB 연결이 필요하므로 현재 중립(0점)으로 구조만 확립 
    # (향후 데이터 연동 시 s_cvd, s_oi, s_vol 값만 분기해주면 자동 연산됨)
    s_cvd, s_oi, s_vol = 0, 0, 0
    details['cvd'] = {'score': s_cvd, 'msg': "데이터 대기중 (중립)"}
    details['oi'] = {'score': s_oi, 'msg': "데이터 대기중 (중립)"}
    details['vol'] = {'score': s_vol, 'msg': "데이터 대기중 (중립)"}
    dca_score += (s_cvd + s_oi + s_vol)
    
    # 구간 판별 로직
    if dca_score >= 70: stage = "Extreme Buy"; stage_ko = "🟢 강력 매수 (Extreme Buy)"
    elif dca_score >= 30: stage = "Gradual Buy"; stage_ko = "🟢 점진 매수 (Gradual Buy)"
    elif dca_score >= 10: stage = "Hold & Wait"; stage_ko = "🟡 관망/유지 (Hold & Wait)"
    elif dca_score > -10: stage = "Neutral"; stage_ko = "⚪ 중립 구간 (Neutral)"
    elif dca_score > -30: stage = "Caution"; stage_ko = "🟠 위험 경고 (Caution)"
    elif dca_score > -70: stage = "Gradual Sell"; stage_ko = "🔴 점진 매도 (Gradual Sell)"
    else: stage = "Extreme Sell"; stage_ko = "🚨 강력 매도 (Extreme Sell)"
    
    return dca_score, stage, stage_ko, details

# =========================================================================
# [3] 동적 텔레그램 메시지 발송
# =========================================================================

def get_strategy_message(scenario_type, btc_price, score, market, alert_mode="DAILY"):
    
    # --- 기존 메시지 블록 포매팅 ---
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
<b>[조건 B: 블랙스완 킬 스위 현황]</b>
• 스테이블 뱅크런: {peg_stat}
• 오더북 뎁스 붕괴: {depth_stat}
• 청산맵/ATR 폭발: {atr_stat}"""

    # --- 신규 DCA 블록 포매팅 ---
    dca_score, stage, stage_ko, dca_dtls = analyze_dca_score(market)
    
    if stage == "Extreme Buy": action_dca = "기관 매집 및 숏 스퀴즈 구간입니다. 시드의 40~50%를 공격적으로 매수하십시오."
    elif stage == "Gradual Buy": action_dca = "건강한 상승 초입입니다. 보유 시드의 10~20%씩 점진적 분할 매수를 진행하십시오."
    elif stage == "Hold & Wait": action_dca = "추세 전환 모색 구간입니다. 신규 진입을 보류하고 관망하십시오."
    elif stage == "Neutral": action_dca = "명확한 주도세가 없는 중립 횡보 구간입니다."
    elif stage == "Caution": action_dca = "추세가 꺾이기 시작했습니다. 신규 매수를 중지하고 리스크를 관리하십시오."
    elif stage == "Gradual Sell": action_dca = "상승이 한계에 달했습니다. 알림 시마다 보유 물량의 10~20%씩 분할 매도하십시오."
    else: action_dca = "레버리지 거품 붕괴 직전입니다. 즉시 물량의 70~100%를 시장가 매도하여 현금화하십시오."

    cond_c_block = f"""══════════════════════
<b>[조건 C: 퀀트 스코어 기반 DCA 매매 가이드]</b>
📊 통합 마켓 스코어: {dca_score}점 / 100점
🎯 현재 구간: {stage_ko}

• 코인베이스 프리미엄: {dca_dtls['cb']['score']}점 ({dca_dtls['cb']['msg']})
• CVD 다이버전스: {dca_dtls['cvd']['score']}점 ({dca_dtls['cvd']['msg']})
• 펀딩비: {dca_dtls['fr']['score']}점 ({dca_dtls['fr']['msg']})
• 미결제약정(OI): {dca_dtls['oi']['score']}점 ({dca_dtls['oi']['msg']})
• 현/선물 거래량: {dca_dtls['vol']['score']}점 ({dca_dtls['vol']['msg']})

=> 🤖 DCA 액션 가이드: 
{action_dca}"""

    # 기존 조건 A/B 기반의 행동 지침
    if score >= 80: action_advice = "대중의 탐욕과 온체인 과열이 극에 달한 사이클 고점입니다. 즉시 모든 자산을 현금화하십시오."
    elif score >= 50: action_advice = "시장의 쏠림과 구조적 과열이 강합니다. 알트코인 전량 매도 및 비트코인 50% 분할 익절을 권장합니다."
    elif score >= 30: action_advice = "과열 징후가 포착되었습니다. 신규 진입을 중단하고 레버리지를 축소하십시오."
    else: action_advice = "온체인 및 기술적 지표 모두 과열되지 않은 안전 구간입니다. 기존 포지션을 유지하십시오."

    # 알림 모드에 따른 헤더 분기
    if alert_mode == "DAILY":
        prefix = "🌅 <b>[오전 07:30 정규 브리핑]</b>\n"
    elif alert_mode == "DCA_CHANGE":
        prefix = "⚡ <b>[DCA 매매 구간 변동 긴급 알림]</b>\n"
    else:
        prefix = "⚡ <b>[위험도 지표 변동 긴급 알림]</b>\n"

    # 시나리오에 따른 메시지 조립
    if scenario_type == 'A':
        if score >= 80: header_title = prefix + "🚨 [전량 매도] 비트코인 하이브리드 위험도 분석"
        elif score >= 50: header_title = prefix + "🔴 [강력 경고] 비트코인 하이브리드 위험도 분석"
        elif score >= 30: header_title = prefix + "🟠 [비중 축소] 비트코인 하이브리드 위험도 분석"
        else: header_title = prefix + "🟢 [안전 유지] 비트코인 하이브리드 위험도 분석"
        
        return f"""{header_title}

📈 타겟 자산: BTC (${btc_price:,.2f})
⚠️ 시장 과열 스코어: {score}점 / 100점

{cond_a_block}

{cond_b_block}
➔ 판정: 🟢 안전 (블랙스완 미달)

💡 <b>기존 시스템 판독 지침</b>: 
{action_advice}

{cond_c_block}"""

    elif scenario_type == 'B':
        return f"""{prefix}<b>🚨 [시스템 마비] 비트코인 블랙스완 킬 스위치 발동</b>

📉 타겟 자산: BTC (${btc_price:,.2f})
⚠️ 킬 스위치 발동 (조건 A 및 C 무시, 강제 오버라이드)

{cond_a_block}

{cond_b_block}
➔ 판정: 🔴 대피 (시스템 장악)

💡 <b>시스템 판독 및 행동 지침</b>:
시장 미시구조의 진공 상태 또는 연쇄 청산이 감지되었습니다. 스코어와 무관하게 즉시 모든 레버리지 및 현물을 전량 매도하고 대피하십시오.

{cond_c_block}"""

    elif scenario_type == 'C':
        return f"""{prefix}<b>🟢 [초고속 재진입] 비트코인 숏 스퀴즈 구조대 발동</b>

🚀 타겟 자산: BTC (${btc_price:,.2f})
⏱️ 상태: 블랙스완 대피 이후 특이 현상(V자 랠리) 포착

{cond_a_block}

{cond_b_block}
➔ 판정: 🟢 조건 C 충족 (강제 재진입 승인)

💡 <b>시스템 판독 및 행동 지침</b>:
세력의 유동성 사냥(Liquidity Sweep)이 종료되었습니다. 블랙스완 매도 상태를 오버라이드하고 즉시 롱 포지션 및 현물을 재진입하여 V자 반등 수익을 확보하십시오.

{cond_c_block}"""
    
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
    print(f"[{datetime.utcnow()}] 비트코인 퀀트 전략 시스템 스캔 시작 (DCA Hybrid Edition)...")
    
    current_kst = datetime.utcnow() + timedelta(hours=9)
    kst_date_str = current_kst.strftime('%Y-%m-%d')
    
    market_data = fetch_market_data()
    btc_current_price = market_data.get('price', 0.0)
    
    state = load_state()
    
    if btc_current_price == 0.0:
        if state.get("last_error_date") != kst_date_str:
            send_telegram_message("<b>🚨 [시스템 에러]</b> API 통신 장애 발생. 봇이 데이터를 불러오지 못했습니다.")
            state["last_error_date"] = kst_date_str
            save_state(state)
        return
        
    scenario, total_score = analyze_strategy(market_data)
    dca_score, dca_stage, _, _ = analyze_dca_score(market_data)
    
    # 정규 브리핑 시간 조건
    current_minutes = current_kst.hour * 60 + current_kst.minute
    target_minutes = 7 * 60 + 30
    
    is_daily_needed = (current_minutes >= target_minutes) and (state.get("last_daily_date") != kst_date_str)
    
    # 2가지 종류의 상태 변동 감지 (기존 A조건 변동 vs 신규 C조건 변동)
    is_state_changed = (state.get("last_score") != total_score) or (state.get("last_scenario") != scenario)
    is_dca_changed = (state.get("last_dca_stage") != dca_stage)
    
    # 최초 실행(기억 없음)인지 여부 확인
    is_first_run = (state.get("last_score") is None or state.get("last_dca_stage") is None)

    if is_daily_needed:
        # [정규 브리핑] 발송
        alert_message = get_strategy_message(scenario, btc_current_price, total_score, market_data, alert_mode="DAILY")
        send_telegram_message(alert_message)
        
        state["last_daily_date"] = kst_date_str
        state["last_score"] = total_score
        state["last_scenario"] = scenario
        state["last_dca_stage"] = dca_stage
        save_state(state)
        print("정규 브리핑 발송 완료")
        
    elif not is_first_run and is_dca_changed:
        # [DCA 매매 구간 변동 알림] 발송 (최우선 긴급도)
        alert_message = get_strategy_message(scenario, btc_current_price, total_score, market_data, alert_mode="DCA_CHANGE")
        send_telegram_message(alert_message)
        
        state["last_dca_stage"] = dca_stage
        state["last_score"] = total_score
        state["last_scenario"] = scenario
        save_state(state)
        print("DCA 매매 구간 변동 긴급 알림 발송 완료")
        
    elif not is_first_run and is_state_changed:
        # [기존 지표 변동 알림] 발송
        alert_message = get_strategy_message(scenario, btc_current_price, total_score, market_data, alert_mode="CHANGE")
        send_telegram_message(alert_message)
        
        state["last_score"] = total_score
        state["last_scenario"] = scenario
        save_state(state)
        print("위험도 지표 변동 긴급 알림 발송 완료")
        
    elif is_first_run:
        # 시스템 최초 실행 시 데이터만 저장하고 침묵
        state["last_score"] = total_score
        state["last_scenario"] = scenario
        state["last_dca_stage"] = dca_stage
        save_state(state)
        print("시스템 최초 실행: 상태 저장 완료")
    else:
        print("지표 및 DCA 구간 변동 없음. 침묵을 유지합니다.")

if __name__ == "__main__":
    main()
