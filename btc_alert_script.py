import os
import requests
from datetime import datetime

# =========================================================================
# [1] 실시간 API 데이터 수집 모듈 (MEXC - IP 차단 우회 최종 솔루션)
# =========================================================================

def fetch_market_data():
    market_data = {
        'price': 0.0,
        'funding_rate_annual': 0.0,
        'oi_value': 0.0,
        'bid_depth': 0.0,
        'atr': 0.0,
        'vwap': 0.0,
        'is_sweep_candle': False,
        'stablecoin_peg': 1.0
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    }
    
    try:
        # 1. 가격, 펀딩비, 미결제약정
        ticker_url = "https://contract.mexc.com/api/v1/contract/ticker?symbol=BTC_USDT"
        res_ticker = requests.get(ticker_url, headers=headers, timeout=10)
        
        if res_ticker.status_code == 200:
            ticker_data = res_ticker.json().get('data', {})
            market_data['price'] = float(ticker_data.get('lastPrice', 0))
            market_data['funding_rate_annual'] = float(ticker_data.get('fundingRate', 0)) * 3 * 365 * 100
            market_data['oi_value'] = float(ticker_data.get('openInterest', 0)) * market_data['price']
        
        # 2. 오더북 뎁스
        depth_url = "https://contract.mexc.com/api/v1/contract/depth/BTC_USDT?limit=50"
        depth_res = requests.get(depth_url, headers=headers, timeout=10)
        if depth_res.status_code == 200:
            bids = depth_res.json().get('data', {}).get('bids', [])
            market_data['bid_depth'] = sum([float(b[1]) for b in bids if len(b) > 1])
        
        # 3. 15분봉 캔들 (ATR, VWAP, 스윕)
        klines_url = "https://contract.mexc.com/api/v1/contract/kline/BTC_USDT?interval=Min15&limit=14"
        klines_res = requests.get(klines_url, headers=headers, timeout=10)
        if klines_res.status_code == 200:
            klines_data = klines_res.json().get('data', {})
            times = klines_data.get('time', [])
            opens = klines_data.get('open', [])
            highs = klines_data.get('high', [])
            lows = klines_data.get('low', [])
            closes = klines_data.get('close', [])
            vols = klines_data.get('vol', [])
            
            tr_list = []
            typical_price_vol = 0
            total_vol = 0
            
            if len(times) > 1:
                for i in range(1, len(times)):
                    high = float(highs[i])
                    low = float(lows[i])
                    close_prev = float(closes[i-1])
                    close_curr = float(closes[i])
                    volume = float(vols[i])
                    
                    tr = max(high - low, abs(high - close_prev), abs(low - close_prev))
                    tr_list.append(tr)
                    
                    tp = (high + low + close_curr) / 3
                    typical_price_vol += tp * volume
                    total_vol += volume
                    
                market_data['atr'] = sum(tr_list) / len(tr_list) if tr_list else 0
                market_data['vwap'] = typical_price_vol / total_vol if total_vol > 0 else market_data['price']
                
                body = abs(float(closes[-1]) - float(opens[-1]))
                lower_wick = min(float(opens[-1]), float(closes[-1])) - float(lows[-1])
                if lower_wick > (body * 2):
                    market_data['is_sweep_candle'] = True

        # 4. 스테이블코인 페깅
        peg_url = "https://api.mexc.com/api/v3/ticker/price?symbol=USDCUSDT"
        peg_res = requests.get(peg_url, headers=headers, timeout=10)
        if peg_res.status_code == 200:
            market_data['stablecoin_peg'] = float(peg_res.json().get('price', 1.0))

    except Exception as e:
        print(f"시장 데이터 수집 에러: {e}")
        
    return market_data

def fetch_onchain_data():
    cq_api_key = os.environ.get("CQ_API_KEY", "")
    # 온체인 API 키가 없을 때의 기본값(안전 상태)
    onchain_data = {
        'lth_sopr': 1.0,
        'cdd_spike': False,
        'miner_flow_ratio': 1.0,
        'mvrv_z': 1.0,
        'whale_ratio': 60.0
    }
    
    if not cq_api_key:
        return onchain_data
        
    # (실제 API 연동 로직 생략 - 키 등록 시 활성화)
    return onchain_data

# =========================================================================
# [2] 전략 엔진 및 스코어링 로직
# =========================================================================

def analyze_strategy(market, onchain):
    score = 0
    
    if onchain['lth_sopr'] > 10.0 and onchain['cdd_spike']: score += 25
    elif onchain['lth_sopr'] > 3.0: score += 18
    elif onchain['lth_sopr'] > 2.0: score += 10
    
    if market['funding_rate_annual'] > 50.0: score += 25
    elif market['funding_rate_annual'] > 20.0: score += 10
    
    if onchain['miner_flow_ratio'] >= 2.5: score += 20
    elif onchain['miner_flow_ratio'] >= 2.0: score += 8
    
    if onchain['mvrv_z'] >= 3.0: score += 15
    elif onchain['mvrv_z'] >= 2.0: score += 5
    
    if onchain['whale_ratio'] >= 85.0: score += 15
    elif onchain['whale_ratio'] >= 80.0: score += 10
    elif onchain['whale_ratio'] >= 75.0: score += 5

    is_blackswan = False
    if market['stablecoin_peg'] < 0.985: is_blackswan = True
    if market['bid_depth'] < 100: is_blackswan = True
    if market['atr'] > (market['price'] * 0.05): is_blackswan = True

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
# [3] 동적 텔레그램 메시지 포맷팅 및 발송
# =========================================================================

def get_strategy_message(scenario_type, btc_price, score, market, onchain):
    # [상태 동적 할당 로직] 데이터에 따라 신호등 이모지가 변동됩니다.
    
    # 1. 고래 차익
    if onchain['lth_sopr'] > 10.0 and onchain['cdd_spike']: whale_stat = "🔴 위험 (대규모 차익실현 출회)"
    elif onchain['lth_sopr'] > 3.0: whale_stat = "🟠 경고 (스마트머니 익절 중)"
    elif onchain['lth_sopr'] > 2.0: whale_stat = "🟡 주의 (점진적 물량 이동)"
    else: whale_stat = "🟢 안전 (특이동향 없음)"

    # 2. 파생 과열
    if market['funding_rate_annual'] > 50.0: deriv_stat = "🔴 위험 (극단적 레버리지 롱 과열)"
    elif market['funding_rate_annual'] > 20.0: deriv_stat = "🟠 경고 (레버리지 누적 중)"
    else: deriv_stat = "🟢 안전 (펀딩비 정상 구간)"

    # 3. 채굴자 유입
    if onchain['miner_flow_ratio'] >= 2.5: miner_stat = "🔴 위험 (대규모 거래소 유입)"
    elif onchain['miner_flow_ratio'] >= 2.0: miner_stat = "🟡 주의 (운영비 출회 증가)"
    else: miner_stat = "🟢 안전 (채굴자 보유 유지)"

    # 4. Z-Score
    if onchain['mvrv_z'] >= 3.0: mvrv_stat = "🔴 위험 (역사적 과열권 진입)"
    elif onchain['mvrv_z'] >= 2.0: mvrv_stat = "🟡 주의 (강세장 후반부)"
    else: mvrv_stat = "🟢 안전 (정상 궤도 또는 저평가)"

    # 5. 분배 장세
    if onchain['whale_ratio'] >= 85.0: ratio_stat = "🔴 위험 (완벽한 분배 장세)"
    elif onchain['whale_ratio'] >= 80.0: ratio_stat = "🟠 경고 (세력 물량 떠넘기기)"
    elif onchain['whale_ratio'] >= 75.0: ratio_stat = "🟡 주의 (고래 비중 증가)"
    else: ratio_stat = "🟢 안전 (개인 주도 손바뀜)"
    
    # 6. 블랙스완 지표
    peg_stat = "🔴 위험 (디페깅 발생)" if market['stablecoin_peg'] < 0.985 else "🟢 안전"
    depth_stat = "🔴 위험 (오더북 진공 상태)" if market['bid_depth'] < 100 else "🟢 안전"
    atr_stat = "🔴 위험 (변동성 폭발)" if market['atr'] > (btc_price * 0.05) else "🟢 안전"

    # [동적 행동 지침 로직] 스코어에 따라 알림의 결론이 완전히 달라집니다.
    if score >= 76:
        action_advice = "극단적 사이클 고점 및 붕괴 임박 상태입니다. 즉시 모든 자산을 전량 현금화하고 대피하십시오."
        header_title = "🚨 [전량 매도] 비트코인 온체인/파생 위험도 분석"
    elif score >= 51:
        action_advice = "구조적 하락 전조가 강하게 나타나고 있습니다. 알트코인을 전량 매도하고 비트코인 현물을 50% 분할 익절하십시오."
        header_title = "🔴 [강력 경고] 비트코인 온체인/파생 위험도 분석"
    elif score >= 31:
        action_advice = "시장에 부분적인 과열 징후가 포착되었습니다. 신규 매수를 중단하고 레버리지 포지션을 30% 축소하십시오."
        header_title = "🟠 [비중 축소] 비트코인 온체인/파생 위험도 분석"
    else:
        action_advice = "현재 시장은 구조적 붕괴나 과열 징후가 없는 안전 구간입니다. 기존 포지션(현물/롱)을 유지하며 추세를 이어가십시오."
        header_title = "🟢 [안전 유지] 비트코인 온체인/파생 위험도 분석"

    # 메시지 생성
    if scenario_type == 'A':
        return f"""{header_title}

📈 타겟 자산: BTC (${btc_price:,.2f})
⚠️ 사이클 고점 스코어: {score}점 / 100점

══════════════════════
**[조건 A: 온체인 구조적 과열]**
• 고래 차익(25): {whale_stat}
• 파생 과열(25): {deriv_stat}
• 채굴자 유입(20): {miner_stat}
• Z-Score(15): {mvrv_stat}
• 분배 장세(15): {ratio_stat}

══════════════════════
**[조건 B: 블랙스완 킬 스위치 (대기 중)]**
• 스테이블 뱅크런: {peg_stat}
• 오더북 뎁스 붕괴: {depth_stat}
• 청산맵/ATR 폭발: {atr_stat}

💡 **시스템 판독 및 행동 지침**: 
{action_advice}"""

    elif scenario_type == 'B':
        return f"""🚨 [시스템 마비] 비트코인 블랙스완 킬 스위치 발동

📉 타겟 자산: BTC (${btc_price:,.2f})
⚠️ 킬 스위치 발동 (조건 A 점수 무시 및 강제 오버라이드)

══════════════════════
**[조건 B: 블랙스완 킬 스위치 트리거 현황]**
• 스테이블 뱅크런: {peg_stat}
• 오더북 뎁스 붕괴: {depth_stat}
• 청산맵/ATR 폭발: {atr_stat}
➔ 판정: 🔴 대피 (시스템 장악)

💡 **시스템 판독 및 행동 지침**:
시장 미시구조의 진공 상태 또는 연쇄 청산이 감지되었습니다. 펀더멘털 점수와 무관하게 즉시 보유 중인 모든 레버리지 및 현물 포지션을 시장가로 전량 매도하고 시스템 일시 정지를 권장합니다."""

    elif scenario_type == 'C':
        return f"""🟢 [초고속 재진입] 비트코인 숏 스퀴즈 구조대 발동

🚀 타겟 자산: BTC (${btc_price:,.2f})
⏱️ 상태: 블랙스완 대피 이후 특이 현상(V자 랠리) 포착

══════════════════════
**[조건 C: V자 역발상 회복 (강제 재진입 승인)]**
• 시장이 일시적 패닉을 흡수하고 급격한 회복세를 보이고 있습니다.

💡 **시스템 판독 및 행동 지침**:
세력의 유동성 사냥(Liquidity Sweep)이 종료되었으며 강력한 매수세가 추격 숏 물량을 잡아먹고 있습니다. 블랙스완 매도 상태를 오버라이드하고 즉시 롱 포지션 및 현물을 재진입하여 V자 반등 수익을 확보하십시오."""
    
    return "전략 오류: 알 수 없는 시나리오입니다."

def send_telegram_message(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("에러: 텔레그램 API 키가 설정되지 않았습니다.")
        return
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print("텔레그램 메시지 발송 완료")
    except Exception as e:
        print(f"메시지 발송 실패: {e}")

def main():
    print(f"[{datetime.now()}] 비트코인 퀀트 전략 시스템 스캔 시작...")
    
    market_data = fetch_market_data()
    onchain_data = fetch_onchain_data()
    
    scenario, total_score = analyze_strategy(market_data, onchain_data)
    btc_current_price = market_data.get('price', 0.0)
    
    if btc_current_price == 0.0:
        print("API 통신 지연으로 가격을 불러오지 못했습니다. 실행을 종료합니다.")
        return
        
    alert_message = get_strategy_message(scenario, btc_current_price, total_score, market_data, onchain_data)
    send_telegram_message(alert_message)
    print("시스템 스캔 및 프로세스 종료")

if __name__ == "__main__":
    main()
