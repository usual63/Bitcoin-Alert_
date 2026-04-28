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
    df['Close'] = data['^VIX']
    df['VVIX'] = data['^VVIX']
    df['VIX3M'] = data['^VIX3M']
    
    # 4대 핵심 이동평균선 생성
    df['sma5'] = df['Close'].rolling(window=5).mean()
    df['sma20'] = df['Close'].rolling(window=20).mean()
    df['sma50'] = df['Close'].rolling(window=50).mean()
    df['sma200'] = df['Close'].rolling(window=200).mean()
    
    # 볼린저 밴드 %B 추가
    std = df['Close'].rolling(window=20).std()
    df['pct_b'] = (df['Close'] - (df['sma20'] - 2*std)) / (4*std)
    
    return df.dropna()

def check_condition(df, short_col, long_col, condition='above', days=3, buffer=0.0):
    recent = df.tail(days)
    curr = df.iloc[-1]
    
    if condition == 'above':
        is_consistent = all(r[short_col] > r[long_col] for _, r in recent.iterrows())
        gap = (curr[short_col] - curr[long_col]) / curr[long_col]
        return is_consistent and (gap >= buffer)
    else: 
        is_consistent = all(r[short_col] < r[long_col] for _, r in recent.iterrows())
        gap = (curr[long_col] - curr[short_col]) / curr[long_col]
        return is_consistent and (gap >= buffer)

def get_phase_info(df):
    curr = df.iloc[-1]
    phase, action, memo = "Phase X", "직전 비중 유지", "방향성 탐색 중"

    if check_condition(df, 'sma50', 'sma200', 'above', days=3, buffer=0.05):
        if check_condition(df, 'sma20', 'sma50', 'below', days=3, buffer=0.03):
            phase, action, memo = "Phase 6", "현금 0% (잔여 현금 전량 재투입)", "장기는 무너졌으나 중기 추세가 완벽히 회복되었습니다"
        elif check_condition(df, 'sma5', 'sma20', 'below', days=3, buffer=0.00):
            phase, action, memo = "Phase 5", "현금 67% (확보 현금의 33% 1차 매수)", "패닉장 속에서 초단기 공포가 진정되었습니다"
        else:
            phase, action, memo = "Phase 4", "현금 100% (관망 및 매수 대기)", "거시적/미시적 공포가 극에 달한 피바람 구간입니다"
            
    elif check_condition(df, 'sma50', 'sma200', 'below', days=3, buffer=0.00):
        if check_condition(df, 'sma20', 'sma50', 'above', days=3, buffer=0.03):
            phase, action, memo = "Phase 3", "현금 70% 확보 (전략적 도피)", "거시적 불장이지만 중기 펀더멘털이 붕괴되었습니다"
        elif check_condition(df, 'sma5', 'sma20', 'above', days=3, buffer=0.00):
            phase, action, memo = "Phase 2", "현금 0% (비중 유지 및 관망)", "상승장 속의 단기적인 흔들림입니다"
        elif check_condition(df, 'sma5', 'sma20', 'below', days=3, buffer=0.00) and \
             check_condition(df, 'sma20', 'sma50', 'below', days=3, buffer=0.00):
            phase, action, memo = "Phase 1", "현금 0% (수익 극대화)", "모든 이평선이 역배열인 최상의 강세장입니다"

    return phase, action, memo

def get_confidence_score(curr):
    score = 0
    if curr['Close'] < 20: score += 1                
    if curr['VVIX'] < 100: score += 1                 
    if curr['Close'] / curr['VIX3M'] < 1.0: score += 1 
    if curr['pct_b'] < 0.8: score += 1                
    if curr['Close'] < curr['sma5']: score += 1       
    return score

def get_detailed_action(prev_num, curr_num):
    """국면 변화에 따른 상세 행동강령 매핑"""
    transition = f"{prev_num}->{curr_num}"
    
    actions = {
        "1->2": "상승 추세 중 일시적인 흔들림입니다. 포지션을 유지하며 관망하시고 추가 매수는 자제하세요",
        "2->3": "중기 추세가 무너졌으므로 즉시 자산의 70%를 현금화하세요. '조금 더 오르면 팔자'는 생각이 가장 위험합니다",
        "3->4": "시장이 통제 불능 상태에 빠졌습니다. 남은 자산도 모두 현금화하여 100% 현금 비중을 유지하며 대기하세요",
        "4->5": "초단기 공포가 정점을 찍고 내려오기 시작했습니다. 전체 투자 예정 금액의 33%를 1차로 분할 매수하세요",
        "5->6": "중기 추세까지 회복되었습니다. 현금 0%를 목표로 잔여 자금을 모두 투입하여 시장 진입을 완료하세요",
        "6->1": "모든 이평선이 정배열로 돌아섰습니다. 복리 수익을 극대화하며 기존 물량을 홀딩하세요",
        "6->5": "V자 반등인 줄 알았으나 다시 공포가 커지는 재발작 단계입니다. 투입 자금을 회수해 현금 비중을 67%까지 높이세요",
        "3->2": "하락장으로 가려다 상승장으로 복귀했습니다. 확보했던 현금을 다시 투입하여 기존 비중을 회복하세요"
    }
    
    return actions.get(transition, "국면 변화에 따른 비중 조절 및 포지션 재평가를 검토하세요")

def get_score_modifier(score):
    """신뢰도 점수에 따른 최종 실행 지침 (사용자 요구사항 완벽 반영)"""
    if score >= 4:
        return f"🔥 [최우선 집행 / 신뢰도 {score}점] 보조 지표가 매우 강력합니다. 위의 상세 행동강령을 100% 즉시 이행하세요"
    elif score == 3:
        return f"⚠️ [보수적 집행 / 신뢰도 {score}점] 신호는 유효하나 노이즈가 있습니다. 위 행동강령 지침의 50%만 먼저 실행하세요"
    else:
        return f"🛑 [집행 보류 / 신뢰도 {score}점] 핵심 신호가 발생했으나 보조 지표 신뢰도가 3점 미만입니다. 위 지침의 절반 미만만 실행하거나 하루 더 관망하는 전략을 취하세요"

def analyze_regime():
    df = get_vix_data()
    
    curr_phase, curr_action, curr_memo = get_phase_info(df)
    prev_phase, _, _ = get_phase_info(df.iloc[:-1]) 
    
    curr_data = df.iloc[-1]
    vix = curr_data['Close']
    s5, s20, s50, s200 = curr_data['sma5'], curr_data['sma20'], curr_data['sma50'], curr_data['sma200']
    
    score = get_confidence_score(curr_data)

    # 평상시(변화 없음) 리포트 구성
    report = (
        f"📊 *VIX 탑다운 매크로 매트릭스*\n\n"
        f"🔹 VIX 현재가: `{round(vix, 2)}`\n"
        f"🔹 SMA(5/20/50/200):\n`{round(s5,1)} / {round(s20,1)} / {round(s50,1)} / {round(s200,1)}`\n\n"
        f"📍 현재 상태: **{curr_phase}**"
    )

    # 상태 변화 발생 시에만 동작하는 상세 알림 로직
    if curr_phase != prev_phase:
        match_curr = re.search(r'\d+', curr_phase)
        match_prev = re.search(r'\d+', prev_phase)
        curr_num = int(match_curr.group()) if match_curr else 0
        prev_num = int(match_prev.group()) if match_prev else 0
        
        risk_map = {1: 1, 2: 2, 6: 3, 5: 4, 3: 5, 4: 6}
        curr_risk = risk_map.get(curr_num, 99)
        prev_risk = risk_map.get(prev_num, 99)
        
        if curr_risk < prev_risk:
            direction_msg = "📈 *시장 환경 개선 감지*"
        else:
            direction_msg = "📉 *위험 신호 포착*"

        # 핵심 추가 사항: 상세 행동강령 및 점수 보정 메시지 생성
        detailed_action = get_detailed_action(prev_num, curr_num)
        score_modifier = get_score_modifier(score)

        change_alert = (
            f"\n\n{direction_msg}\n"
            f"🔔 **국면 변화**: {prev_phase} ➔ {curr_phase}\n\n"
            f"🎯 **기본 대응**: {curr_action}\n"
            f"📝 **변화 이유**: {curr_memo}\n\n"
            f"💡 **상세 행동강령**: {detailed_action}\n"
            f"⚖️ **최종 실행 판단**: {score_modifier}"
        )
        report += change_alert

    send_telegram(report)

if __name__ == "__main__":
    analyze_regime()
