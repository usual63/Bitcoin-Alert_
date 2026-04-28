import yfinance as yf
import pandas as pd
import requests
import os
import re

# 환경 변수 설정 (GitHub Secrets 등에 등록 필요)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"텔레그램 전송 실패: {e}")

def get_vix_data():
    # 200일선 계산을 위해 넉넉히 500일치 데이터 호출 및 5대 지표 티커 추가
    tickers = ["^VIX", "^VVIX", "^VIX3M"]
    data = yf.download(tickers, period="500d")['Close']
    
    df = pd.DataFrame(index=data.index)
    # 원본 로직 호환성을 위해 VIX 지수를 'Close' 컬럼으로 유지
    df['Close'] = data['^VIX']
    df['VVIX'] = data['^VVIX']
    df['VIX3M'] = data['^VIX3M']
    
    # 4대 핵심 이동평균선 생성 (원본 100% 유지)
    df['sma5'] = df['Close'].rolling(window=5).mean()
    df['sma20'] = df['Close'].rolling(window=20).mean()
    df['sma50'] = df['Close'].rolling(window=50).mean()
    df['sma200'] = df['Close'].rolling(window=200).mean()
    
    # 볼린저 밴드 %B 추가 (5대 지표)
    std = df['Close'].rolling(window=20).std()
    df['pct_b'] = (df['Close'] - (df['sma20'] - 2*std)) / (4*std)
    
    return df.dropna()

def check_condition(df, short_col, long_col, condition='above', days=3, buffer=0.0):
    """
    상태 판별 핵심 엔진: N일 연속 유지 및 이격도(버퍼) 동시 충족 여부 검사
    """
    recent = df.tail(days)
    curr = df.iloc[-1]
    
    if condition == 'above':
        is_consistent = all(r[short_col] > r[long_col] for _, r in recent.iterrows())
        gap = (curr[short_col] - curr[long_col]) / curr[long_col]
        return is_consistent and (gap >= buffer)
    else: # below
        is_consistent = all(r[short_col] < r[long_col] for _, r in recent.iterrows())
        gap = (curr[long_col] - curr[short_col]) / curr[long_col]
        return is_consistent and (gap >= buffer)

def get_phase_info(df):
    """특정 시점의 데이터프레임을 기준으로 Phase와 대응 방향을 반환"""
    curr = df.iloc[-1]
    
    # 기본값 설정
    phase, action, memo = "Phase X", "직전 비중 유지", "방향성 탐색 중"

    # ==============================================================
    # [탑다운 1단계] 거시적 하락장 확인 (50일선 > 200일선, 5% 패닉 버퍼)
    # ==============================================================
    if check_condition(df, 'sma50', 'sma200', 'above', days=3, buffer=0.05):
        
        # 미시적 회복(하행선)부터 순차 검사
        if check_condition(df, 'sma20', 'sma50', 'below', days=3, buffer=0.03):
            phase, action, memo = "Phase 6", "현금 0% (잔여 현금 전량 재투입)", "장기는 무너졌으나 중기 추세가 완벽히 회복되었습니다. 구조대를 전량 투입하세요"
            
        elif check_condition(df, 'sma5', 'sma20', 'below', days=3, buffer=0.00):
            phase, action, memo = "Phase 5", "현금 67% (확보 현금의 33% 1차 매수)", "패닉장 속에서 초단기 공포가 진정되었습니다. 1차 분할 매수를 시작하세요"
            
        else:
            phase, action, memo = "Phase 4", "현금 100% (관망 및 매수 대기)", "거시적/미시적 공포가 극에 달한 피바람 구간입니다. 떨어지는 칼날을 쥐지 말고 대기하세요"

    # ==============================================================
    # [탑다운 2단계] 거시적 상승장 확인 (50일선 < 200일선)
    # ==============================================================
    elif check_condition(df, 'sma50', 'sma200', 'below', days=3, buffer=0.00):
        
        # 미시적 위기(상행선)부터 순차 검사
        if check_condition(df, 'sma20', 'sma50', 'above', days=3, buffer=0.03):
            phase, action, memo = "Phase 3", "현금 70% 확보 (전략적 도피)", "거시적 불장이지만 중기 펀더멘털이 붕괴되었습니다. 즉시 자산을 현금화하여 대피하세요"
            
        elif check_condition(df, 'sma5', 'sma20', 'above', days=3, buffer=0.00):
            phase, action, memo = "Phase 2", "현금 0% (비중 유지 및 관망)", "상승장 속의 단기적인 흔들림(눌림목)입니다. 팔지 말고 굳건히 관망하세요"
            
        elif check_condition(df, 'sma5', 'sma20', 'below', days=3, buffer=0.00) and \
             check_condition(df, 'sma20', 'sma50', 'below', days=3, buffer=0.00):
            phase, action, memo = "Phase 1", "현금 0% (수익 극대화)", "모든 이평선이 역배열인 최상의 강세장입니다. 복리의 마법을 온전히 즐기세요"

    return phase, action, memo

def get_confidence_score(curr):
    """정예 5대 필터 점수화 (Max 5점)"""
    score = 0
    if curr['Close'] < 20: score += 1                # 1. 절대 수치 안정
    if curr['VVIX'] < 100: score += 1                 # 2. 공포의 변동성 안정
    if curr['Close'] / curr['VIX3M'] < 1.0: score += 1 # 3. 기간 구조 정상화
    if curr['pct_b'] < 0.8: score += 1                # 4. 변동성 오버슈팅 해제
    if curr['Close'] < curr['sma5']: score += 1       # 5. 하향 기울기 발생
    return score

def get_action_guide(score):
    """점수에 따른 실전 투입 메시지 (사용자 요청 사항 반영)"""
    if score >= 4:
        return f"🔥 [최우선 집행 / {score}점] 보조 지표가 매우 안정적입니다. 페이즈 전략에 맞춰 즉시 비중을 조절하세요."
    elif score == 3:
        return f"⚠️ [분할 대응 / {score}점] 신호는 유효하나 변동성 노이즈가 존재합니다. 목표 비중의 50% 수준으로 보수적 접근을 권장합니다."
    else:
        return f"🛑 [집행 보류 / {score}점] 핵심 이평선은 교차했으나 내부 지표가 불안정합니다. 점수가 회복될 때까지 하루 이틀 대기하세요."

def analyze_regime():
    df = get_vix_data()
    
    curr_phase, curr_action, curr_memo = get_phase_info(df)
    prev_phase, _, _ = get_phase_info(df.iloc[:-1]) 
    
    curr_data = df.iloc[-1]
    vix = curr_data['Close']
    s5, s20, s50, s200 = curr_data['sma5'], curr_data['sma20'], curr_data['sma50'], curr_data['sma200']
    
    score = get_confidence_score(curr_data)
    guide = get_action_guide(score)

    report = (
        f"📊 *VIX 탑다운 매크로 매트릭스*\n\n"
        f"🔹 VIX 현재가: `{round(vix, 2)}`\n"
        f"🔹 SMA(5/20/50/200):\n`{round(s5,1)} / {round(s20,1)} / {round(s50,1)} / {round(s200,1)}`\n\n"
        f"📍 현재 상태: **{curr_phase}**\n"
        f"📢 **실전 가이드**: {guide}"
    )

    if curr_phase != prev_phase:
        # 안전한 페이즈 숫자 추출 로직 추가 (오류 방지)
        match_curr = re.search(r'\d+', curr_phase)
        match_prev = re.search(r'\d+', prev_phase)
        curr_num = int(match_curr.group()) if match_curr else 0
        prev_num = int(match_prev.group()) if match_prev else 0
        
        # 실제 시장 위험도에 기반한 리스크 레벨 맵핑 (낮을수록 좋음)
        # 1(불장) > 2(단기노이즈) > 6(V자반등) > 5(1차반등) > 3(대세하락) > 4(패닉)
        risk_map = {1: 1, 2: 2, 6: 3, 5: 4, 3: 5, 4: 6}
        curr_risk = risk_map.get(curr_num, 99)
        prev_risk = risk_map.get(prev_num, 99)
        
        # 리스크가 이전보다 낮아졌다면 '개선'으로 판별
        if curr_risk < prev_risk:
            direction_msg = "📈 *시장 환경 개선 감지*"
            detail = "공포가 잦아들며 안전 마진이 확보되고 있습니다. 공격적인 비중 확대를 검토하세요."
        else:
            direction_msg = "📉 *위험 신호 포착*"
            detail = "변동성이 확대되거나 추세가 붕괴되고 있습니다. 자산 보호를 위해 방어적 태세를 취하세요."

        change_alert = (
            f"\n\n{direction_msg}\n"
            f"🔔 **국면 변화**: {prev_phase} ➔ {curr_phase}\n"
            f"🎯 **향후 대응**: {curr_action}\n"
            f"📝 **변화 이유**: {curr_memo}\n"
            f"💡 **방향 가이드**: {detail}"
        )
        report += change_alert

    send_telegram(report)

if __name__ == "__main__":
    analyze_regime()
