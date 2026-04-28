import yfinance as yf
import pandas as pd
import requests
import os

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
    # 200일선 계산을 위해 넉넉히 500일치 데이터 호출
    vix = yf.Ticker("^VIX")
    df = vix.history(period="500d")
    
    # 4대 핵심 이동평균선 생성
    df['sma5'] = df['Close'].rolling(window=5).mean()
    df['sma20'] = df['Close'].rolling(window=20).mean()
    df['sma50'] = df['Close'].rolling(window=50).mean()
    df['sma200'] = df['Close'].rolling(window=200).mean()
    
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

def analyze_regime():
    df = get_vix_data()
    
    # 1. 오늘과 어제의 데이터 분리하여 각각의 Phase 산출
    curr_phase, curr_action, curr_memo = get_phase_info(df)
    prev_phase, _, _ = get_phase_info(df.iloc[:-1]) # 마지막 행 제외(어제 데이터)
    
    curr_data = df.iloc[-1]
    vix = curr_data['Close']
    s5, s20, s50, s200 = curr_data['sma5'], curr_data['sma20'], curr_data['sma50'], curr_data['sma200']

    # 2. 기본 리포트 구성 (변화가 없을 때 전송될 양식)
    report = (
        f"📊 *VIX 탑다운 매크로 매트릭스*\n\n"
        f"🔹 VIX 현재가: `{round(vix, 2)}`\n"
        f"🔹 SMA(5/20/50/200):\n`{round(s5,1)} / {round(s20,1)} / {round(s50,1)} / {round(s200,1)}`\n\n"
        f"📍 현재 상태: **{curr_phase}**"
    )

    # 3. 상태 변화 발생 시 대응 가이드 추가
    if curr_phase != prev_phase:
        change_alert = (
            f"\n\n🔔 *국면 변화 감지: {prev_phase} ➔ {curr_phase}*\n"
            f"🎯 **향후 대응**: {curr_action}\n"
            f"📝 **변화 이유**: {curr_memo}"
        )
        report += change_alert

    send_telegram(report)

if __name__ == "__main__":
    analyze_regime()
