import os
import requests

def get_strategy_message(scenario_type, btc_price, score):
    """
    각 시나리오(A, B, C)에 따라 텔레그램 발송용 메시지 원문을 동적으로 반환합니다
    """
    if scenario_type == 'A':
        return f"""🟠 [비중 축소] 비트코인 온체인/파생 위험도 분석

📈 타겟 자산: BTC (${btc_price:,.2f})
⚠️ 사이클 고점 스코어: {score}점 / 100점 (경고 2단계)

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

💡 **시스템 판독**: 파생상품 시장의 과열과 장기 홀더의 대규모 물량 출회가 중첩되고 있습니다
신규 진입을 중단하고 알트코인 전량 매도 및 비트코인 현물 50% 분할 매도를 권장합니다."""

    elif scenario_type == 'B':
        return f"""🚨 [전량 매도] 비트코인 시스템 블랙스완 킬 스위치 발동

📉 타겟 자산: BTC (${btc_price:,.2f})
⚠️ 사이클 고점 스코어: {score}점 / 100점 (조건 A 무시 및 강제 오버라이드)

══════════════════════
**[조건 B: 블랙스완 킬 스위치 (1개라도 충족 시 대피)]**
• 스테이블 뱅크런: 🟢 안전
• 오더북 뎁스 붕괴: 🟢 안전
• 청산맵/ATR 폭발: 🔴 위험 (롱 청산 클러스터 붕괴 및 ATR 2배 폭등)
➔ 판정: 🔴 대피 (시스템 장악)

══════════════════════
**[조건 A: 온체인 구조적 과열 현황]**
• 전체 스코어는 안전 구간이나, 미시구조 붕괴로 인해 킬 스위치가 우선 작동합니다

💡 **시스템 판독**: 대규모 롱 포지션 연쇄 청산이 발생하여 오더북 진공 상태에 빠졌습니다
조건 A의 점수와 무관하게 즉시 보유 중인 모든 레버리지 및 현물 포지션을 시장가로 전량 매도하고 시스템을 일시 정지합니다."""

    elif scenario_type == 'C':
        return f"""🟢 [초고속 재진입] 비트코인 숏 스퀴즈 구조대 발동

🚀 타겟 자산: BTC (${btc_price:,.2f})
⏱️ 상태: 블랙스완 대피 이후 특이 현상(V자 랠리) 포착

══════════════════════
**[조건 C: V자 역발상 회복 (2개 이상 포착 시 진입)]**
• 극음수 펀딩비 + OI: 🟢 포착 (추격 숏 쏠림 및 펀딩비 극음수 전환)
• 스팟 투매 흡수: 🟢 포착 (가격 신저가 갱신 중 현물 CVD 폭발적 상승)
• 스윕 캔들 및 VWAP: 🔴 미달 (아직 VWAP 저항선 돌파 대기 중)
➔ 판정: 🟢 조건 충족 (2/3 충족으로 강제 재진입 승인)

💡 **시스템 판독**: 세력의 유동성 사냥(Liquidity Sweep)이 종료되었으며, 스팟 거래소 주도의 강력한 매수세가 추격 숏 물량을 잡아먹고 있습니다
블랙스완 매도 상태를 오버라이드하고 즉시 롱 포지션 및 현물을 재진입하여 V자 반등 수익을 확보합니다."""
    
    return "전략 오류: 알 수 없는 시나리오입니다."

def send_telegram_message(text):
    """
    GitHub Secrets에 등록된 환경 변수를 불러와 텔레그램 API로 메시지를 발송합니다
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("에러: 텔레그램 토큰 또는 채팅방 ID가 설정되지 않았습니다.")
        return
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        print("텔레그램 메시지 발송 완료")
    except Exception as e:
        print(f"메시지 발송 실패: {e}")

def main():
    # [백테스트 및 실시간 데이터 수집 연동 구역]
    # 향후 실제 API 연동 시 아래 변수들을 동적으로 업데이트하는 로직이 추가되어야 합니다
    
    # 임시 테스트 변수 세팅
    current_scenario = 'A' # 'A', 'B', 'C' 중 현재 시장 상황에 맞는 시나리오로 변경하여 테스트 가능
    current_btc_price = 82450.00
    current_score = 68
    
    # 1. 시나리오 판단 및 텍스트 생성
    alert_message = get_strategy_message(current_scenario, current_btc_price, current_score)
    
    # 2. 텔레그램 발송
    send_telegram_message(alert_message)

if __name__ == "__main__":
    main()
