import os
import requests

def send_telegram_message(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    response = requests.post(url, json=payload)
    return response.json()

def main():
    # [전략 1, 2, 3 로직 수행 및 데이터 수집 코드 파트]
    # 예시 스코어 및 상태 산출
    btc_price = 82450.00
    score_a = 68
    
    # 앞서 LOCKED 처리된 시나리오 A 템플릿 적용
    message = f"""
🟠 *[비중 축소] 비트코인 온체인/파생 위험도 분석*

📈 타겟 자산: BTC (${btc_price:,.2f})
⚠️ 사이클 고점 스코어: {score_a}점 / 100점 (경고 2단계)

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

💡 **시스템 판독**: 파생상품 시장의 과열과 장기 홀더의 대규모 물량 출회가 중첩되고 있습니다. 신규 진입을 중단하고 알트코인 전량 매도 및 비트코인 현물 50% 분할 매도를 권장합니다.
"""
    
    send_telegram_message(message)

if __name__ == "__main__":
    main()
