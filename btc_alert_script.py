import os
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from fredapi import Fred

# ---------------------------------------------------------
# [기본 통신 및 헬퍼 함수]
# ---------------------------------------------------------
def send_telegram_message(text):
    bot_token = os.environ.get('TELEGRAM_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not bot_token or not chat_id:
        print("🚨 오류: 텔레그램 토큰이나 챗봇 ID가 누락되었습니다")
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    requests.post(url, data={'chat_id': chat_id, 'text': text})

def calculate_atr(df, period=22):
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    return np.max(ranges, axis=1).rolling(period).mean()

# ---------------------------------------------------------
# [메인 알고리즘 실행부]
# ---------------------------------------------------------
def main():
    try:
        # 1. API 키 로드
        fred_key = os.environ.get('FRED_API_KEY')
        if not fred_key:
            print("🚨 오류: FRED API 키가 존재하지 않습니다")
            return
        fred = Fred(api_key=fred_key)

        # 2. 시장 및 매크로 데이터 수집
        qqq = yf.Ticker("QQQ").history(period="1y")
        vix = yf.Ticker("^VIX").history(period="1y")
        vix3m = yf.Ticker("^VIX3M").history(period="1y")
        
        walcl = fred.get_series('WALCL') 
        wtregen = fred.get_series('WTREGEN') 
        rrp = fred.get_series('RRPONTSYD') 
        t10y2y = fred.get_series('T10Y2Y') 
        
        latest_close = qqq['Close'].iloc[-1]

        # 3. 공통 기술적 지표 사전 산출
        atr = calculate_atr(qqq, 22)
        short_emas = [qqq['Close'].ewm(span=p, adjust=False).mean() for p in [3, 5, 8, 10, 12, 15]]
        long_emas = [qqq['Close'].ewm(span=p, adjust=False).mean() for p in [30, 35, 40, 45, 50, 60]]
        
        # 4. 🌟 [조건 A] 100점 만점 3단계(3-Tier) 세분화 스코어링 🌟
        risk_score = 0
        details_A = {}

        df_macro = pd.concat([walcl, wtregen, rrp], axis=1).ffill().dropna()
        df_macro.columns = ['WALCL', 'WTREGEN', 'RRP']
        df_macro['Net_Liq'] = df_macro['WALCL'] - df_macro['WTREGEN'] - (df_macro['RRP'] * 1000)
        liq_20ma = df_macro['Net_Liq'].rolling(20).mean().iloc[-1]
        liq_50ma = df_macro['Net_Liq'].rolling(50).mean().iloc[-1]
        liq_spread = (liq_20ma - liq_50ma) / liq_50ma
        
        if liq_spread < 0: 
            risk_score += 15
            details_A['유동성'] = "🔴 위험 (데드크로스 본격화)"
        elif 0 <= liq_spread <= 0.01:
            risk_score += 7
            details_A['유동성'] = "🟡 경고 (자금 유입 모멘텀 둔화)"
        else:
            details_A['유동성'] = "🟢 안전 (시중 유동성 정배열 확장)"

        current_spread = t10y2y.dropna().iloc[-1]
        if 0.0 < current_spread <= 0.5: 
            risk_score += 15
            details_A['금리차'] = "🔴 위험 (0선 상향突破 침체 진입)"
        elif -0.2 <= current_spread <= 0.0:
            risk_score += 7
            details_A['금리차'] = "🟡 경고 (역전해소 임박 급격한 축소)"
        else:
            details_A['금리차'] = "🟢 안전 (안정적 역전 또는 해소 상태)"

        sp500_50d_pct = yf.Ticker("^SP500-50").history(period="1y")
        mcclellan_div = False
        co_drop = False
        if not sp500_50d_pct.empty:
            qqq_growth = qqq['Close'].iloc[-1] > qqq['Close'].shift(20).iloc[-1]
            sp_growth = sp500_50d_pct['Close'].iloc[-1] > sp500_50d_pct['Close'].shift(20).iloc[-1]
            if qqq_growth and not sp_growth:
                mcclellan_div = True
            elif not qqq_growth and not sp_growth:
                co_drop = True
                
        if mcclellan_div: 
            risk_score += 10
            details_A['시장폭'] = "🔴 위험 (소수 독점 하락 다이버전스)"
        elif co_drop:
            risk_score += 5
            details_A['시장폭'] = "🟡 경고 (시장 전체 동반 조정 국면)"
        else:
            details_A['시장폭'] = "🟢 안전 (시장 전반 건강한 동반 상승)"

        short_min = pd.concat(short_emas, axis=1).min(axis=1).iloc[-1]
        short_max = pd.concat(short_emas, axis=1).max(axis=1).iloc[-1]
        long_min = pd.concat(long_emas, axis=1).min(axis=1).iloc[-1]
        long_max = pd.concat(long_emas, axis=1).max(axis=1).iloc[-1]
        
        if short_max < long_min: 
            risk_score += 15
            details_A['GMMA'] = "🔴 위험 (그물망 하향 돌파 역배열)"
        elif short_min <= long_max:
            risk_score += 7
            details_A['GMMA'] = "🟡 경고 (이평선 응축 그물망 꼬임)"
        else:
            details_A['GMMA'] = "🟢 안전 (단장기 그물망 간격 정배열)"

        rolling_max = qqq['High'].rolling(22).max().iloc[-1]
        chandelier_val = rolling_max - (atr.iloc[-1] * 3.0)
        
        if latest_close < chandelier_val: 
            risk_score += 15
            details_A['샹들리에'] = "🔴 위험 (중기 핵심 지지선 하향 이탈)"
        elif chandelier_val <= latest_close <= (chandelier_val + atr.iloc[-1]):
            risk_score += 7
            details_A['샹들리에'] = "🟡 경고 (지지선 턱밑 리스크 근접)"
        else:
            details_A['샹들리에'] = "🟢 안전 (하단 지지선과 넉넉한 이격)"

        vix_val = vix['Close'].iloc[-1]
        vix3m_val = vix3m['Close'].iloc[-1]
        vix_ratio = vix_val / vix3m_val
        
        if vix_ratio > 1.0: 
            risk_score += 30
            details_A['VIX'] = "🔴 위험 (단기 불안 백워데이션 역전)"
        elif 0.9 <= vix_ratio <= 1.0:
            risk_score += 15
            details_A['VIX'] = "🟡 경고 (공포 확산 콘탱고 폭 둔화)"
        else:
            details_A['VIX'] = "🟢 안전 (안정적인 정상 콘탱고 구조)"

        # 5. 🌪️ [조건 B] 블랙스완 패닉 3중 필터 🌪️
        vix_inversion = vix_val > vix3m_val
        chandelier_drop = latest_close < chandelier_val
        atr_explosion = atr.iloc[-1] > (1.5 * atr.rolling(20).mean().iloc[-1])
        
        trigger_B = vix_inversion and chandelier_drop and atr_explosion

        # 6. 🚀 [우회 로직] V자 반등 (가짜 블랙스완) 감지 🚀
        vix_max_3d = vix['Close'].shift(1).rolling(3).max().iloc[-1]
        vix_crush = vix_val < (vix_max_3d * 0.80) if not pd.isna(vix_max_3d) else False

        daily_range = qqq['High'].iloc[-1] - qqq['Low'].iloc[-1]
        lower_tail = min(qqq['Open'].iloc[-1], latest_close) - qqq['Low'].iloc[-1]
        tail_ratio = lower_tail / daily_range if daily_range > 0 else 0
        volume_tail_absorb = (tail_ratio > 0.40) and (qqq['Volume'].iloc[-1] > (qqq['Volume'].rolling(20).mean().iloc[-1] * 1.2))

        short_spread = pd.concat(short_emas, axis=1).max(axis=1) - pd.concat(short_emas, axis=1).min(axis=1)
        gmma_compression = (short_spread.iloc[-1] < short_spread.iloc[-2]) and (latest_close > qqq['Close'].rolling(5).mean().iloc[-1])

        v_shape_recovery = (int(vix_crush) + int(volume_tail_absorb) + int(gmma_compression)) >= 2

        # 7. ⚖️ 마스터 시스템 상태 판별 및 우선순위 제어 ⚖️
        if v_shape_recovery:
            status_icon = "🚀 [고속 재진입]"
            action_msg = "V자 반등(가짜 블랙스완) 특이 현상이 확정되었습니다. 손절 국면을 완전히 마감하고 QQQ 및 레버리지 포지션의 즉각적인 전량 재매수를 강력 권장합니다."
            trigger_B = False  
        elif trigger_B or risk_score >= 80:
            status_icon = "🚨 [강력 매도]"
            action_msg = "조건 A(구조적 붕괴) 또는 조건 B(블랙스완)가 충족되었습니다. 전량 현금화 및 포트폴리오 대피를 즉시 권장합니다."
        elif risk_score >= 60:
            status_icon = "🟠 [비중 축소]"
            action_msg = "거시 환경이 악화 중입니다. 신규 매수를 전면 중단하고 위험 자산 비중의 단계적 축소를 권장합니다."
        elif risk_score >= 35:
            status_icon = "🟡 [사전 경고]"
            action_msg = "일부 지표에서 리스크가 감지되었습니다. 시장 변동성 모니터링 및 리스크 관리를 강화하세요."
        else:
            status_icon = "🟢 [Buy & Hold]"
            action_msg = "안전 장세입니다. 레버리지 및 코어 자산 포지션을 우상향 복리 스노우볼 방향으로 그대로 유지합니다."

        # 8. 📝 직관적 가독성 최적화 기반 메시지 조립
        alert_msg = (
            f"{status_icon} 데일리 퀀트 위험도 분석\n\n"
            f"📈 타겟 자산: QQQ (${latest_close:.2f})\n"
            f"⚠️ 시장 붕괴 스코어: {risk_score}점 / 100점\n\n"
            f"══════════════════════\n"
            f"**[조건 A: 구조적 붕괴 (80점 이상 매도)]**\n"
            f"• 유동성(15): {details_A['유동성']}\n"
            f"• 금리차(15): {details_A['금리차']}\n"
            f"• 시장폭(10): {details_A['시장폭']}\n"
            f"• GMMA(15): {details_A['GMMA']}\n"
            f"• 샹들리에(15): {details_A['샹들리에']}\n"
            f"• VIX역전(30): {details_A['VIX']}\n\n"
            f"══════════════════════\n"
            f"**[조건 B: 블랙스완 (3개 동시 충족 시 대피)]**\n"
            f"• 패닉 투심(VIX): {'🔴 위험 (단기 역전)' if vix_inversion else '🟢 안전'}\n"
            f"• 추세 붕괴(Price): {'🔴 위험 (지지선 이탈)' if chandelier_drop else '🟢 안전'}\n"
            f"• 변동성 폭발(ATR): {'🔴 위험 (당일 ' + str(round(atr_val,1)) + ')' if atr_explosion else '🟢 안전'}\n"
            f"➔ 판정: {'🔴 발동 (즉각 대피)' if (vix_inversion and chandelier_drop and atr_explosion) else '🟢 안전 (조건 미달)'}\n\n"
        )

        if (vix_inversion and chandelier_drop and atr_explosion) or v_shape_recovery:
            alert_msg += (
                f"══════════════════════\n"
                f"**[특이 현상: V자 반등 (가짜 블랙스완) 추적]**\n"
                f"• VIX 크러시: {'🟢 포착 (공포 급감)' if vix_crush else '⚪ 대기 (공포 지속)'}\n"
                f"• 투매 흡수: {'🟢 포착 (아래꼬리)' if volume_tail_absorb else '⚪ 대기 (매수세 부족)'}\n"
                f"• 단기망 압축: {'🟢 포착 (그물망 꺾임)' if gmma_compression else '⚪ 대기 (추세 하락)'}\n"
                f"➔ 판정: {'🚀 반등 확정 (즉시 복구)' if v_shape_recovery else '⏳ 징후 추적 중 (관망 유지)'}\n\n"
            )

        alert_msg += f"💡 **시스템 판독**: {action_msg}"

        send_telegram_message(alert_msg)

    except Exception as e:
        send_telegram_message(f"🚨 퀀트 시스템 런타임 에러 발생: {e}")

if __name__ == "__main__":
    main()
