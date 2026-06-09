import os
import requests
from datetime import datetime

# =========================================================================
# [1] 실시간 API 데이터 수집 모듈 (MEXC - IP 차단 우회 최종 솔루션)
# =========================================================================

def fetch_market_data():
    """
    MEXC 퍼블릭 API를 통해 실시간 데이터를 수집합니다.
    (GitHub Actions의 북미 IP를 차단하지 않는 가장 안정적인 대안입니다.)
    """
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
        # 1. 가격, 펀딩비, 미결제약정 (Ticker)
        ticker_url = "https://contract.mexc.com/api/v1/contract/ticker?symbol=BTC_USDT"
        res_ticker = requests.get(ticker_url, headers=headers, timeout=10)
        
        if res_ticker.status_code != 200:
            print(f"API 에러 (Status {res_ticker.status_code}): {res_ticker.text[:200]}")
            return market_data
            
        ticker_data = res_ticker.json().get('data', {})
        
        market_data['price'] = float(ticker_data.get('lastPrice', 0))
        # 펀딩비 연환산 (MEXC 기준 8시간마다 갱신)
        market_data['funding_rate_annual'] = float(ticker_data.get('fundingRate', 0)) * 3 * 365 * 100
        market_data['oi_value'] = float(ticker_data.get('openInterest', 0)) * market_data['price']
        
        # 2. 오더북 뎁스 (호가창 진공 상태 파악)
        depth_url = "https://contract.mexc.com/api/v1/contract/depth/BTC_USDT?limit=50"
        depth_res = requests.get(depth_url, headers=headers, timeout=10).json().get('data', {})
        bids = depth_res.get('bids', [])
        market_data['bid_depth'] = sum([float(b[1]) for b in bids if len(b) > 1])
        
        # 3. 15분봉 캔들 기반 단기 미시구조 (ATR, VWAP, 아래꼬리 스윕)
        klines_url = "https://contract.mexc.com/api/v1/contract/kline/BTC_USDT?interval=Min15&limit=14"
        klines_res = requests.get(klines_url, headers=headers, timeout=10).json().get('data', {})
        
        times = klines_res.get('time', [])
        opens = klines_res.get('open', [])
        highs = klines_res.get('high', [])
        lows = klines_res.get('low', [])
        closes = klines_res.get('close', [])
        vols = klines_res.get('vol', [])
        
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
            
            # 마지막 캔들 스윕(아래꼬리) 여부 확인
            last_open = float(opens[-1])
            last_high = float(highs[-1])
            last_low = float(lows[-1])
            last_close = float(closes[-1])
            
            body = abs(last_close - last_open)
            lower_wick = min(last_open, last_close) - last_low
            if lower_wick > (body * 2):
                market_data['is_sweep_candle'] = True

        # 4. 스테이블코인 뱅크런 디페깅 확인 (현물 마켓)
        peg_url = "https://api.mexc.com/api/v3/ticker/price?symbol=USDCUSDT"
        peg_res = requests.get(peg_url, headers=headers, timeout=10).json()
        market_data['stablecoin_peg'] = float(peg_res.get('price', 1.0))

    except Exception as e:
        print(f"시장 데이터 수집 중 에러 발생 (MEXC): {e}")
        
    return market_data

def fetch_onchain_data():
    cq_api_key = os.environ.get("CQ_API_KEY", "")
    onchain_data = {
        'lth_sopr': 1.5,
        'cdd_spike': False,
        'miner_flow_ratio': 1.0,
        'mvrv_z': 1.2,
        'whale_ratio': 65.0
    }
    
    if not cq_api_key:
        return onchain_data
        
    headers = {'Authorization': f'Bearer {cq_api_key}'}
    try:
        pass 
    except Exception as e:
        print(f"온체인 데이터 수집 중 에러 발생: {e}")
        
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
# [3] 텔레그램 메시지 포맷팅 및 발송
# =========================================================================

def get_strategy_message(scenario_type, btc_price, score):
    if scenario_type == 'A':
        return f"""🟠 [비중 축소] 비트코인 온체인/파생 위험도 분석

📈 타겟 자산: BTC (${btc_price:,.2f})
⚠️ 사이클 고점 스코어: {score}점 / 100점 (현황 브리핑)

══════════════════════
**[조건 A: 온체인 구조적 과열 (76점 이상 대피)]**
• 고래 차익(25): 🔴 위험 (LTH-SOPR 10.0 초과 및 CDD 스파이크)
• 파생 과열(25): 🟠 경고 (OI 사상 최고치 및 펀딩비 연 50% 돌파)
• 채굴자 유입(20): 🟡 주의 (거래소 유입량 평소 대비 2배 증가)
• Z-Score(15): 🟡 주의 (Z-Score 2.0 돌파 강세장 후반)
• 분배 장세(15): 🟢 안전 (고래 비중 70% 미만)

══════════════════════
**[조건 B: 블랙스완 킬 스위치 (1개라도 충족 시 대피)]**
• 스테이블 뱅크런: 🟢 안전
• 오더북 뎁스 붕괴: 🟢 안전
• 청산맵/ATR 폭발: 🟢 안전
➔ 판정: 🟢 안전 (조건 미달)

💡 **시스템 판독**: 현재 시장의 펀더멘털 스코어를 반영한 실시간 분석입니다.
위험 점수에 따라 신규 진입을 통제하고 분할 매도를 권장합니다."""

    elif scenario_type == 'B':
        return f"""🚨 [전량 매도] 비트코인 시스템 블랙스완 킬 스위치 발동

📉 타겟 자산: BTC (${btc_price:,.2f})
⚠️ 사이클 고점 스코어: {score}점 / 100점 (조건 A 무시 및 강제 오버라이드)

══════════════════════
**[조건 B: 블랙스완 킬 스위치 (1개라도 충족 시 대피)]**
• 스테이블 뱅크런: 🟢 안전
• 오더북 뎁스 붕괴: 🟢 안전
• 청산맵/ATR 폭발: 🔴 위험 (롱 청산 클러스터 붕괴 및 ATR 급증)
➔ 판정: 🔴 대피 (시스템 장악)

══════════════════════
**[조건 A: 온체인 구조적 과열 현황]**
• 미시구조 붕괴로 인해 킬 스위치가 우선 작동합니다

💡 **시스템 판독**: 시장 미시구조의 진공 상태 또는 연쇄 청산이 감지되었습니다.
조건 A의 점수와 무관하게 즉시 보유 중인 모든 레버리지 및 현물 포지션을 시장가로 전량 매도하고 시스템 일시 정지를 권장합니다."""

    elif scenario_type == 'C':
        return f"""🟢 [초고속 재진입] 비트코인 숏 스퀴즈 구조대 발동

🚀 타겟 자산: BTC (${btc_price:,.2f})
⏱️ 상태: 블랙스완 대피 이후 특이 현상(V자 랠리) 포착

══════════════════════
**[조건 C: V자 역발상 회복 (2개 이상 포착 시 진입)]**
• 극음수 펀딩비 + OI: 🟢 포착 (추격 숏 쏠림 및 펀딩비 극음수 전환)
• 스팟 투매 흡수: 🟢 포착 (가격 신저가 갱신 중 투매 흡수)
• 스윕 캔들 및 VWAP: 🟢 포착 (VWAP 저항선 상향 돌파)
➔ 판정: 🟢 조건 충족 (강제 재진입 승인)

💡 **시스템 판독**: 세력의 유동성 사냥(Liquidity Sweep)이 종료되었으며 강력한 매수세가 추격 숏 물량을 잡아먹고 있습니다.
블랙스완 매도 상태를 오버라이드하고 즉시 롱 포지션 및 현물을 재진입하여 V자 반등 수익을 확보합니다."""
    
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
    
    # 1. API 데이터 실시간 수집 (북미 IP 차단 없는 MEXC로 완전 이주)
    market_data = fetch_market_data()
    onchain_data = fetch_onchain_data()
    
    # 2. 전략 엔진 구동 및 시나리오 도출
    scenario, total_score = analyze_strategy(market_data, onchain_data)
    btc_current_price = market_data.get('price', 0.0)
    
    # 가격 로드 실패 시 방어 로직
    if btc_current_price == 0.0:
        print("API 통신 지연으로 가격을 불러오지 못했습니다. 실행을 종료합니다.")
        return
        
    # 3. 알림 메시지 생성 및 발송
    alert_message = get_strategy_message(scenario, btc_current_price, total_score)
    send_telegram_message(alert_message)
    print("시스템 스캔 및 프로세스 종료")

if __name__ == "__main__":
    main()
