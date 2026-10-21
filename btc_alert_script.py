import os
import json
import requests
from datetime import datetime, timedelta

# =========================================================================
# [0] 텔레그램 메시지 발송 모듈 (전송 성공 여부 반환)
# =========================================================================
def send_telegram_message(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("텔레그램 환경변수 미설정")
        return False
    try:
        res = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
        return res.status_code == 200
    except Exception as e:
        print(f"텔레그램 발송 실패: {e}")
        return False


# =========================================================================
# [1] 상태(State) 저장 및 로드 모듈 (경로 무결성 및 히스토리 캐시 유지)
# =========================================================================
base_dir = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(base_dir, "alert_state.json")

def load_state():
    default_state = {
        "is_initialized": False,
        "last_daily_date": None,
        "last_score": 0,
        "last_scenario": "A",
        "last_c_level": 0,
        "oi_history": [],
        "last_error_date": None,
        "last_market_cache": {
            "mvrv_ratio": 1.5,
            "sopr_proxy": 1.0,
            "fear_greed_index": 50,
            "cb_price": 0.0
        }
    }
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                for k, v in default_state.items():
                    if k not in loaded:
                        loaded[k] = v
                return loaded
        except Exception as e:
            print(f"상태 로드 실패: {e}")
    return default_state

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"상태 저장 실패: {e}")
        send_telegram_message(f"🚨 <b>[시스템 에러]</b> alert_state.json 파일 저장 실패. 권한/경로를 확인하세요. ({e})")


# =========================================================================
# [2] 실시간 API 데이터 수집 모듈 (현물/선물 분리 및 폴백 캐싱)
# =========================================================================
def fetch_coinmetrics_mvrv(fallback_val):
    try:
        now = datetime.utcnow()
        start_str = (now - timedelta(days=5)).strftime('%Y-%m-%d')
        url = f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=CapMVRVCur&start_time={start_str}&frequency=1d"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json().get('data', [])
            if data and data[-1].get('CapMVRVCur') is not None:
                return float(data[-1]['CapMVRVCur'])
    except Exception as e:
        print(f"MVRV 수집 지연(캐시 사용): {e}")
    return fallback_val

def fetch_fear_and_greed_index(fallback_val):
    try:
        res = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        if res.status_code == 200:
            data = res.json().get('data', [])
            if data:
                return int(data[0]['value'])
    except Exception as e:
        print(f"FGI 수집 지연(캐시 사용): {e}")
    return fallback_val

def fetch_coinbase_price(fallback_val):
    try:
        res = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=5)
        if res.status_code == 200:
            return float(res.json()['data']['amount'])
    except Exception as e:
        print(f"코인베이스 시세 수집 지연(캐시 사용): {e}")
    return fallback_val

def fetch_market_data(cache):
    market_data = {
        'price': 0.0,
        'spot_price': 0.0,
        'index_price': 0.0,
        'cb_price': 0.0,
        'funding_rate_annual': 0.0,
        'oi_current': 0.0,
        'vwap_24h': 0.0,
        'was_below_vwap': False,
        'is_sweep_candle': False,
        'stablecoin_peg': 1.0,
        'price_to_ma120_ratio': 0.0,
        'mvrv_ratio': cache.get('mvrv_ratio', 1.5),
        'sopr': cache.get('sopr_proxy', 1.0),
        'fear_greed_index': cache.get('fear_greed_index', 50),
        'buy_vol_ratio': 0.5,
        'coinbase_premium': 0.0,
        'price_drop_24h': 0.0,
        'drawdown_24h_high': 0.0,
        'flash_crash_5m': False,
        'basis_ratio': 0.0
    }
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}

    try:
        # 1. 외부 매크로 지표 수집 (실패 시 직전 캐시 유지)
        market_data['fear_greed_index'] = fetch_fear_and_greed_index(market_data['fear_greed_index'])
        market_data['mvrv_ratio'] = fetch_coinmetrics_mvrv(market_data['mvrv_ratio'])
        cb_price = fetch_coinbase_price(cache.get('cb_price', 0.0))
        market_data['cb_price'] = cb_price

        # 2. MEXC 선물 티커 수집 (선물가, 인덱스가, 펀딩비, 미결제약정)
        ticker_url = "https://contract.mexc.com/api/v1/contract/ticker?symbol=BTC_USDT"
        res_ticker = requests.get(ticker_url, headers=headers, timeout=10)
        if res_ticker.status_code == 200:
            ticker_data = res_ticker.json().get('data', {})
            market_data['price'] = float(ticker_data.get('lastPrice', 0))
            market_data['index_price'] = float(ticker_data.get('indexPrice', market_data['price']))
            market_data['funding_rate_annual'] = float(ticker_data.get('fundingRate', 0)) * 3 * 365 * 100
            market_data['oi_current'] = float(ticker_data.get('openInterest', ticker_data.get('holdVol', 0)))

        # 3. MEXC 현물 가격 수집 (코인베이스 프리미엄 현물 대 현물 비교용)
        spot_url = "https://api.mexc.com/api/v3/ticker/price?symbol=BTCUSDT"
        spot_res = requests.get(spot_url, headers=headers, timeout=5)
        if spot_res.status_code == 200:
            market_data['spot_price'] = float(spot_res.json().get('price', market_data['price']))
        else:
            market_data['spot_price'] = market_data['index_price'] or market_data['price']

        # 코인베이스 프리미엄(현물 vs 현물) 및 선물 베이시스(선물 vs 인덱스) 분리 산출
        if cb_price > 0 and market_data['spot_price'] > 0:
            market_data['coinbase_premium'] = ((cb_price - market_data['spot_price']) / market_data['spot_price']) * 100
        if market_data['index_price'] > 0 and market_data['price'] > 0:
            market_data['basis_ratio'] = ((market_data['price'] - market_data['index_price']) / market_data['index_price']) * 100

        # 4. 5분봉 쾌속 센서 (직전 마감 캔들 -2, 현재 캔들 -1, 2개 합산 낙폭 모두 검증)
        k5_url = "https://contract.mexc.com/api/v1/contract/kline/BTC_USDT?interval=Min5&limit=3"
        k5_res = requests.get(k5_url, headers=headers, timeout=5)
        if k5_res.status_code == 200:
            k5_data = k5_res.json().get('data', {})
            opens_5m = [float(x) for x in k5_data.get('open', [])]
            closes_5m = [float(x) for x in k5_data.get('close', [])]
            lows_5m = [float(x) for x in k5_data.get('low', [])]
            if len(closes_5m) >= 2:
                drop_curr = (closes_5m[-1] - opens_5m[-1]) / opens_5m[-1] if opens_5m[-1] > 0 else 0
                drop_prev = (closes_5m[-2] - opens_5m[-2]) / opens_5m[-2] if opens_5m[-2] > 0 else 0
                drop_two = (lows_5m[-1] - opens_5m[-2]) / opens_5m[-2] if opens_5m[-2] > 0 else 0
                if min(drop_curr, drop_prev, drop_two) <= -0.03:
                    market_data['flash_crash_5m'] = True

        # 5. 선물 15분봉 (롤링 24시간 VWAP, 24시간 낙폭, 윗꼬리 필터 적용 스윕 캔들)
        klines_15m_url = "https://contract.mexc.com/api/v1/contract/kline/BTC_USDT?interval=Min15&limit=100"
        k15_res = requests.get(klines_15m_url, headers=headers, timeout=10)
        if k15_res.status_code == 200:
            k15_data = k15_res.json().get('data', {})
            closes = [float(x) for x in k15_data.get('close', [])]
            opens = [float(x) for x in k15_data.get('open', [])]
            highs = [float(x) for x in k15_data.get('high', [])]
            lows = [float(x) for x in k15_data.get('low', [])]
            vols = [float(x) for x in k15_data.get('vol', [])]

            if len(closes) >= 10:
                # 최근 96개 마감 캔들(24시간 롤링 윈도우) 기준 VWAP 및 24시간 낙폭 계산
                closed_end = len(closes) - 1
                start_24h = max(0, closed_end - 96)
                typical_price_vol = 0.0
                total_vol = 0.0
                for i in range(start_24h, closed_end):
                    tp = (highs[i] + lows[i] + closes[i]) / 3.0
                    typical_price_vol += tp * vols[i]
                    total_vol += vols[i]

                market_data['vwap_24h'] = typical_price_vol / total_vol if total_vol > 0 else market_data['price']

                # 최근 4개 캔들 중 저점이 VWAP 하단에 있었는지 확인 (하락 후 VWAP 상향 돌파 판정용)
                recent_lows = lows[-5:-1]
                if recent_lows and min(recent_lows) < market_data['vwap_24h']:
                    market_data['was_below_vwap'] = True

                # 24시간 전 시가 대비 낙폭 및 24시간 최고점 대비 낙폭 산출
                price_24h_ago = opens[start_24h]
                high_24h = max(highs[start_24h:])
                drop_from_24h_open = ((market_data['price'] - price_24h_ago) / price_24h_ago) * 100 if price_24h_ago > 0 else 0.0
                drop_from_24h_high = ((market_data['price'] - high_24h) / high_24h) * 100 if high_24h > 0 else 0.0
                market_data['price_drop_24h'] = min(drop_from_24h_open, drop_from_24h_high)
                market_data['drawdown_24h_high'] = drop_from_24h_high

                # 직전 마감 캔들(-2) 스윕 판정 (아랫꼬리가 몸통 및 윗꼬리의 2배 초과)
                c_open, c_close, c_high, c_low = opens[-2], closes[-2], highs[-2], lows[-2]
                body = abs(c_close - c_open)
                lower_wick = min(c_open, c_close) - c_low
                upper_wick = c_high - max(c_open, c_close)
                if (lower_wick > body * 2.0) and (lower_wick > upper_wick * 2.0) and (lower_wick > market_data['price'] * 0.002):
                    market_data['is_sweep_candle'] = True

        # 6. 현물(Spot) CVD 산출 (현물 체결 및 마감 캔들 기반 매수 우위 비율)
        spot_cvd_calculated = False
        spot_trades_url = "https://api.mexc.com/api/v3/aggTrades?symbol=BTCUSDT&limit=1000"
        st_res = requests.get(spot_trades_url, headers=headers, timeout=5)
        if st_res.status_code == 200:
            trades = st_res.json()
            if isinstance(trades, list) and len(trades) > 0:
                taker_buy_vol = sum(float(t['q']) for t in trades if not t.get('m', True))
                total_spot_vol = sum(float(t['q']) for t in trades)
                if total_spot_vol > 0:
                    market_data['buy_vol_ratio'] = taker_buy_vol / total_spot_vol
                    spot_cvd_calculated = True

        if not spot_cvd_calculated:
            spot_k15_url = "https://api.mexc.com/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=7"
            sk_res = requests.get(spot_k15_url, headers=headers, timeout=5)
            if sk_res.status_code == 200:
                sk_data = sk_res.json()
                if isinstance(sk_data, list) and len(sk_data) >= 6:
                    closed_spot = sk_data[-6:-1]  # 미완성 현재 캔들(-1) 제외
                    buy_v = sum(float(k[5]) for k in closed_spot if float(k[4]) > float(k[1]))
                    tot_v = sum(float(k[5]) for k in closed_spot)
                    market_data['buy_vol_ratio'] = (buy_v / tot_v) if tot_v > 0 else 0.5

        # 7. 1일봉 로직 (MA120 이격도 및 단기 홀더 실현 단가 STH-RP 기반 SOPR 프록시 산출)
        klines_1d_url = "https://contract.mexc.com/api/v1/contract/kline/BTC_USDT?interval=Day1&limit=150"
        k1d_res = requests.get(klines_1d_url, headers=headers, timeout=10)
        if k1d_res.status_code == 200:
            k1d_data = k1d_res.json().get('data', {})
            closes_1d = [float(c) for c in k1d_data.get('close', [])]
            vols_1d = [float(v) for v in k1d_data.get('vol', [])]
            if len(closes_1d) >= 120:
                ma120 = sum(closes_1d[-120:]) / 120.0
                market_data['price_to_ma120_ratio'] = (market_data['price'] - ma120) / ma120

            # 최근 30일 거래량 가중 평균 매입단가(STH Realized Price Proxy) 대비 수익 실현 비율 산출
            if len(closes_1d) >= 31 and len(vols_1d) >= 31:
                sth_vwap = sum(closes_1d[-31:-1][i] * vols_1d[-31:-1][i] for i in range(30)) / max(1.0, sum(vols_1d[-31:-1]))
                if sth_vwap > 0:
                    raw_ratio = market_data['price'] / sth_vwap
                    # 단기 홀더 평균 수익률 +15% 이상을 SOPR 1.05, +6% 이상을 1.02 스케일로 정규화
                    market_data['sopr'] = 1.0 + ((raw_ratio - 1.0) / 3.0)

        # 8. 스테이블코인 페깅 수집 (USDC/USDT 양방향 감시용)
        peg_url = "https://api.mexc.com/api/v3/ticker/price?symbol=USDCUSDT"
        peg_res = requests.get(peg_url, headers=headers, timeout=10)
        if peg_res.status_code == 200:
            market_data['stablecoin_peg'] = float(peg_res.json().get('price', 1.0))

    except Exception as e:
        print(f"시장 데이터 수집 에러: {e}")

    return market_data


# =========================================================================
# [3] 미결제약정(OI) 타임스탬프 히스토리 관리 모듈
# =========================================================================
def analyze_oi_history(oi_history, current_oi, now_ts):
    # 최근 24시간(86400초) 이내 기록만 유지
    valid_history = [item for item in oi_history if (now_ts - item.get("ts", 0)) <= 86400 and item.get("oi", 0) > 0]

    oi_drop_15m = 0.0
    oi_surge_24h = 0.0

    if current_oi > 0 and valid_history:
        # 최근 15분~20분(1200초) 이내의 최고 OI 대비 감소율 계산 (연쇄 청산 감지)
        recent_15m_ois = [item["oi"] for item in valid_history if (now_ts - item["ts"]) <= 1200]
        if not recent_15m_ois:
            recent_15m_ois = [valid_history[-1]["oi"]]
        ref_oi_15m = max(recent_15m_ois)
        if ref_oi_15m > 0:
            oi_drop_15m = ((ref_oi_15m - current_oi) / ref_oi_15m) * 100

        # 최근 24시간 평균 OI 대비 증가율 계산 (조건 A 파생 레버리지 과열 감지)
        avg_oi_24h = sum(item["oi"] for item in valid_history) / len(valid_history)
        if avg_oi_24h > 0:
            oi_surge_24h = ((current_oi - avg_oi_24h) / avg_oi_24h) * 100

    if current_oi > 0:
        valid_history.append({"ts": now_ts, "oi": current_oi})
        # 파일 용량 최적화를 위해 최근 300개 포인트만 유지
        valid_history = valid_history[-300:]

    return valid_history, oi_drop_15m, oi_surge_24h


# =========================================================================
# [4] 하이브리드 전략 엔진 (V4 무결성 가중치 및 논리 회로)
# =========================================================================
def analyze_strategy(market, oi_drop_ratio, oi_surge_24h):
    score = 0

    # [조건 A] 1. 온체인 절대 가치 평가 (최대 40점)
    if market['mvrv_ratio'] >= 3.0:
        score += 40
    elif market['mvrv_ratio'] >= 2.4:
        score += 20

    # [조건 A] 2. 파생 레버리지 과열 - 펀딩비(20점) + OI 급증(10점) 결합 (최대 30점)
    deriv_score = 0
    if market['funding_rate_annual'] > 50.0:
        deriv_score += 20
    elif market['funding_rate_annual'] > 20.0:
        deriv_score += 10

    if oi_surge_24h >= 10.0:
        deriv_score += 10
    elif oi_surge_24h >= 5.0:
        deriv_score += 5
    score += deriv_score

    # [조건 A] 3. 고래 엑시트 / 단기 홀더 과열 (최대 20점)
    if market['sopr'] >= 1.05:
        score += 20
    elif market['sopr'] >= 1.02:
        score += 10

    # [조건 A] 4. 투심(5점) 및 MA120 이격도(5점) (최대 10점)
    if market['fear_greed_index'] >= 80:
        score += 5
    elif market['fear_greed_index'] >= 75:
        score += 3

    if market['price_to_ma120_ratio'] > 0.30:
        score += 5
    elif market['price_to_ma120_ratio'] > 0.20:
        score += 2

    # [조건 B] 블랙스완 킬 스위치 (1Hit-Kill, 양방향 디페깅 포함)
    is_blackswan = False
    if market['stablecoin_peg'] < 0.985 or market['stablecoin_peg'] > 1.015:
        is_blackswan = True
    if oi_drop_ratio >= 5.0:
        is_blackswan = True
    if market['flash_crash_5m']:
        is_blackswan = True
    if market['basis_ratio'] < -5.0:
        is_blackswan = True
    if market['price_drop_24h'] <= -10.0:
        is_blackswan = True

    # [조건 C] 숏 스퀴즈 현물 구조대 (조정/하락 전제 조건 및 메인/서브 키 교차 검증)
    # 평상시 단순 강세장 오작동 방지를 위해 24시간 고점 대비 조정이거나 파생 숏/스윕 징후가 있을 때만 스나이퍼 활성화
    is_dip_context = (
        market['drawdown_24h_high'] <= -1.5
        or market['funding_rate_annual'] < 0.0
        or market['is_sweep_candle']
        or market['was_below_vwap']
    )

    main_keys = 0
    if market['coinbase_premium'] > 0.05:
        main_keys += 1
    if market['buy_vol_ratio'] > 0.60:
        main_keys += 1

    sub_keys = 0
    if market['funding_rate_annual'] < -50.0:
        sub_keys += 1
    if market['is_sweep_candle']:
        sub_keys += 1
    if (market['price'] > market['vwap_24h']) and market['was_below_vwap']:
        sub_keys += 1

    # if-elif 순서 교정 완료 (레벨 3 최우선 평가)
    c_level = 0
    if is_dip_context:
        if main_keys == 2 and sub_keys >= 1:
            c_level = 3
        elif main_keys >= 1 and sub_keys >= 1:
            c_level = 2
        elif main_keys == 0 and sub_keys >= 1:
            c_level = 1

    scenario = 'B' if is_blackswan else 'A'

    return scenario, score, c_level, deriv_score


# =========================================================================
# [5] 동적 텔레그램 메시지 생성 (중간 경고 표시 및 시나리오 간섭 해결)
# =========================================================================
def get_strategy_message(scenario_type, btc_price, score, c_level, market, oi_drop_ratio, oi_surge_24h, deriv_score, alert_mode="DAILY", c_triggered=False):
    # 조건 A 세부 상태 텍스트 (중간 경고 🟠 단계 및 MA120 이격도 완벽 표기)
    mvrv = market['mvrv_ratio']
    if mvrv >= 3.0:
        mvrv_stat = f"🔴 위험 (MVRV {mvrv:.2f})"
    elif mvrv >= 2.4:
        mvrv_stat = f"🟠 경고 (MVRV {mvrv:.2f})"
    else:
        mvrv_stat = f"🟢 안전 (MVRV {mvrv:.2f})"

    fr = market['funding_rate_annual']
    if deriv_score >= 20:
        fr_stat = f"🔴 과열 (펀딩 {fr:.1f}% / OI {oi_surge_24h:+.1f}%)"
    elif deriv_score >= 10:
        fr_stat = f"🟠 경고 (펀딩 {fr:.1f}% / OI {oi_surge_24h:+.1f}%)"
    else:
        fr_stat = f"🟢 정상 (펀딩 {fr:.1f}% / OI {oi_surge_24h:+.1f}%)"

    sopr = market['sopr']
    if sopr >= 1.05:
        sopr_stat = f"🔴 이탈 (지표 {sopr:.2f})"
    elif sopr >= 1.02:
        sopr_stat = f"🟠 주의 (지표 {sopr:.2f})"
    else:
        sopr_stat = f"🟢 안전 (지표 {sopr:.2f})"

    fgi = market['fear_greed_index']
    ma_pct = market['price_to_ma120_ratio'] * 100
    if fgi >= 80 or ma_pct > 30.0:
        fgi_stat = f"🔴 과열 (FGI {fgi} / MA120 {ma_pct:+.1f}%)"
    elif fgi >= 75 or ma_pct > 20.0:
        fgi_stat = f"🟠 주의 (FGI {fgi} / MA120 {ma_pct:+.1f}%)"
    else:
        fgi_stat = f"🟢 보통 (FGI {fgi} / MA120 {ma_pct:+.1f}%)"

    # 조건 B 세부 상태 텍스트
    peg = market['stablecoin_peg']
    peg_stat = f"🔴 위험 (디페깅 {peg:.4f})" if (peg < 0.985 or peg > 1.015) else f"🟢 안전 ({peg:.4f})"
    oi_drop_stat = f"🔴 위험 (-{oi_drop_ratio:.1f}% 증발)" if oi_drop_ratio >= 5.0 else f"🟢 방어 ({oi_drop_ratio:+.1f}%)"
    flash_stat = "🔴 위험 (투매 발생)" if market['flash_crash_5m'] else "🟢 안전"
    basis_stat = f"🔴 위험 ({market['basis_ratio']:.2f}%)" if market['basis_ratio'] < -5.0 else f"🟢 안전 ({market['basis_ratio']:+.2f}%)"
    drop_stat = f"🔴 위험 ({market['price_drop_24h']:.1f}%)" if market['price_drop_24h'] <= -10.0 else f"🟢 안전 ({market['price_drop_24h']:+.1f}%)"

    # 조건 C 세부 상태 텍스트
    cbp = market['coinbase_premium']
    buy_ratio = market['buy_vol_ratio'] * 100
    vwap_ cross = (market['price'] > market['vwap_24h']) and market['was_below_vwap']

    cbp_stat = f"🟢 기관 유입 ({cbp:+.3f}%)" if cbp > 0.05 else f"⚪ 대기 ({cbp:+.3f}%)"
    cvd_stat = f"🟢 순매수 리드 ({buy_ratio:.1f}%)" if market['buy_vol_ratio'] > 0.60 else f"⚪ 대기 ({buy_ratio:.1f}%)"
    fr_c_stat = f"🟢 숏 과밀 ({fr:.1f}%)" if fr < -50.0 else f"⚪ 대기 ({fr:.1f}%)"
    sweep_stat = "🟢 꼬리 방어" if market['is_sweep_candle'] else "⚪ 대기"
    vwap_stat = "🟢 하단 돌파 회복" if vwap_cross else "⚪ 대기"

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
- 온체인 MVRV(40): {mvrv_stat}
- 파생 레버리지(30): {fr_stat}
- 고래 엑시트(20): {sopr_stat}
- 심리 및 이격도(10): {fgi_stat}"""

    cond_b_block = f"""══════════════════════
<b>[조건 B: 블랙스완 킬 스위치 현황]</b>
<i>(💡 건강한 조정이 아닌 시스템 붕괴(연쇄 청산 등)로 인해 즉각 대피해야 할 폭락장 시작 신호)</i>
- 뱅크런 방어: {peg_stat}
- 15분 청산맵: {oi_drop_stat}
- 5분 투매/베이시스: {flash_stat} / {basis_stat}
- 24시간 롤링 낙폭: {drop_stat}
➔ 판정: {"🔴 대피 (시스템 장악)" if scenario_type == 'B' else "🟢 안전 (조건 미달)"}"""

    cond_c_block = f"""══════════════════════
<b>[조건 C: 숏 스퀴즈 현물 구조대 현황]</b>
<i>(💡 모두가 던지는 공포의 하락장 속에서 현물 세력이 바닥을 주워 담는 단기 V자 반등 딥바잉 스나이퍼)</i>
- 코베 프리미엄 (Main): {cbp_stat}
- 현물 CVD (Main): {cvd_stat}
- 펀딩비 극음수 (Sub): {fr_c_stat}
- 스윕 캔들 (Sub): {sweep_stat}
- 24h VWAP 회복 (Sub): {vwap_stat}
{c_decision}"""

    prefix = "🌅 <b>[오전 07:30 정규 브리핑]</b>\n" if alert_mode == "DAILY" else "⚡ <b>[지표 변동 긴급 알림]</b>\n"

    # 조건 C 매수 타점 상향으로 발동된 긴급 알림일 경우 (폭락장 바닥 스나이핑 시나리오 충돌 방지)
    if alert_mode == "CHANGE" and c_triggered:
        c_title = "🟢 [강력 매수 타점]" if c_level == 3 else "🟡 [1차 분할 진입]"
        b_warn = "\n⚠️ <i>주의: 현재 조건 B(급락 경보)가 함께 활성화되어 있으므로 반드시 무레버리지 현물 분할로만 접근하십시오.</i>" if scenario_type == 'B' else ""
        return f"""{prefix}<b>{c_title} 현물 구조대 스나이핑 포착</b>

📈 타겟 자산: BTC (${btc_price:,.2f})
🎯 스나이퍼 등급: 레벨 {c_level} 도달 (시장 과열 스코어: {score}점)

{cond_a_block}

{cond_b_block}

{cond_c_block}

💡 <b>시스템 판독 및 행동 지침</b>:
하락 구간에서 현물 매수세 유입과 반등 시그널이 동시 포착되었습니다. 설정된 비중에 따라 분할 진입을 검토하십시오.{b_warn}"""

    # 조건 B 블랙스완 킬 스위치 우선 시나리오
    if scenario_type == 'B':
        return f"""{prefix}<b>🚨 [시스템 마비] 비트코인 블랙스완 킬 스위치 발동</b>

📉 타겟 자산: BTC (${btc_price:,.2f})
⚠️ 킬 스위치 발동 (조건 A 점수 무시 및 강제 오버라이드)

{cond_a_block}

{cond_b_block}

{cond_c_block}

💡 <b>비상 행동 지침</b>:
시장 미시구조의 붕괴 및 연쇄 청산이 감지되었습니다. 스코어와 무관하게 즉시 모든 레버리지 및 현물을 전량 매도하고 대피하십시오."""

    # 조건 A 일반 시나리오
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

    return f"""{prefix}<b>{header_title}</b>

📈 타겟 자산: BTC (${btc_price:,.2f})
⚠️ 시장 과열 스코어: {score}점 / 100점

{cond_a_block}

{cond_b_block}

{cond_c_block}

💡 <b>시스템 판독 및 행동 지침</b>:
{action_advice}"""


# =========================================================================
# [6] 메인 실행 함수 (스마트 알림 및 상태 동기화 무결성 보장)
# =========================================================================
def main():
    now_utc = datetime.utcnow()
    now_ts = now_utc.timestamp()
    print(f"[{now_utc}] 비트코인 퀀트 전략 시스템 스캔 시작 (V4 Master Integrity Edition)...")

    current_kst = now_utc + timedelta(hours=9)
    kst_date_str = current_kst.strftime('%Y-%m-%d')

    state = load_state()
    is_first_run = not state.get("is_initialized", False)

    market_data = fetch_market_data(state.get("last_market_cache", {}))
    btc_current_price = market_data.get('price', 0.0)

    if btc_current_price == 0.0:
        if state.get("last_error_date") != kst_date_str:
            if send_telegram_message("<b>🚨 [시스템 에러]</b> API 통신 장애 발생. 봇이 데이터를 불러오지 못했습니다."):
                state["last_error_date"] = kst_date_str
                save_state(state)
        return

    # 정상 수집된 매크로 데이터를 캐시에 백업
    state["last_market_cache"] = {
        "mvrv_ratio": market_data['mvrv_ratio'],
        "sopr_proxy": market_data['sopr'],
        "fear_greed_index": market_data['fear_greed_index'],
        "cb_price": market_data['cb_price']
    }

    # 타임스탬프 기반 OI 히스토리 분석
    updated_oi_history, oi_drop_ratio, oi_surge_24h = analyze_oi_history(
        state.get("oi_history", []),
        market_data['oi_current'],
        now_ts
    )
    state["oi_history"] = updated_oi_history

    scenario, total_score, current_c_level, deriv_score = analyze_strategy(market_data, oi_drop_ratio, oi_surge_24h)

    # 최초 실행 시 알림을 발송하지 않고 기준 상태만 기록
    if is_first_run:
        state["is_initialized"] = True
        state["last_score"] = total_score
        state["last_scenario"] = scenario
        state["last_c_level"] = current_c_level
        save_state(state)
        print("시스템 최초 실행: 초기 상태 기준점 저장 완료")
        return

    # ---------------------------------------------------------
    # 스마트 알림 판정 로직 (상태 덮어쓰기 전에 비교 수행)
    # ---------------------------------------------------------
    last_c = state.get("last_c_level", 0)
    last_score = state.get("last_score", 0)
    last_scenario = state.get("last_scenario", "A")

    def get_score_tier(s):
        if s >= 80: return 3
        elif s >= 50: return 2
        elif s >= 30: return 1
        return 0

    current_tier = get_score_tier(total_score)
    last_tier = get_score_tier(last_score)

    is_score_upgraded = current_tier > last_tier
    is_blackswan_triggered = (last_scenario == 'A' and scenario == 'B')
    is_c_level_upgraded = (current_c_level > last_c) and (current_c_level >= 2)

    is_smart_alert = (is_score_upgraded or is_blackswan_triggered or is_c_level_upgraded)

    current_minutes = current_kst.hour * 60 + current_kst.minute
    target_minutes = 7 * 60 + 30
    is_daily_needed = (current_minutes >= target_minutes) and (state.get("last_daily_date") != kst_date_str)

    # 1. 오전 07:30 정규 브리핑 독립 실행 (전송 성공 시에만 일자 갱신)
    if is_daily_needed:
        daily_msg = get_strategy_message(
            scenario, btc_current_price, total_score, current_c_level,
            market_data, oi_drop_ratio, oi_surge_24h, deriv_score,
            alert_mode="DAILY", c_triggered=False
        )
        if send_telegram_message(daily_msg):
            state["last_daily_date"] = kst_date_str
            print("정규 브리핑 발송 완료")

    # 2. 스마트 긴급 알림 독립 실행 (정규 브리핑과 동시 발생해도 누락되지 않음)
    if is_smart_alert:
        change_msg = get_strategy_message(
            scenario, btc_current_price, total_score, current_c_level,
            market_data, oi_drop_ratio, oi_surge_24h, deriv_score,
            alert_mode="CHANGE", c_triggered=(is_c_level_upgraded and not is_blackswan_triggered)
        )
        if send_telegram_message(change_msg):
            state["last_score"] = total_score
            state["last_scenario"] = scenario
            state["last_c_level"] = current_c_level
            save_state(state)
            print(f"스마트 알림 조건 충족 긴급 발송 완료 (스코어: {total_score}, 시나리오: {scenario}, C등급: {current_c_level})")
        else:
            print("긴급 알림 전송 실패: 다음 스캔 시 재시도를 위해 이전 상태 유지")
            return
    else:
        # 상향 돌파가 아닐 때는 현재 상태(하향 안정화 포함)를 정상 기록하고 침묵 유지
        state["last_score"] = total_score
        state["last_scenario"] = scenario
        state["last_c_level"] = current_c_level
        save_state(state)
        print(f"특이 변동 없음 (스코어: {total_score}, 시나리오: {scenario}, C등급: {current_c_level}). 침묵 유지")

if __name__ == "__main__":
    main()
