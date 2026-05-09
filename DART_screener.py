"""
워파머 v13.6.8 — DART 악재 자동 스크리너 (v2.1 업그레이드)
새 기능: 최근 공시 5건 + 호재/악재 자동 분류
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
import time
import re
import os
import zipfile
import xml.etree.ElementTree as ET

# ============================================
# 1. 설정
# ============================================
API_KEY = os.environ.get("DART_API_KEY", "")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

if not API_KEY:
    raise ValueError("DART_API_KEY가 설정되지 않음")

# ============================================
# 2. 워파머 매트릭스 32종
# ============================================
STOCKS_INPUT = {
    "포스코퓨처엠": "003670",
    "롯데웰푸드": "280360",
    "저스템": "417840",
    "오로스테크놀로지": "322310",
    "제룡전기": "033100",
    "유진테크": "084370",
    "진성티이씨": "036890",
    "오킨스전자": "100120",
    "삼성증권": "016360",
    "미래반도체": "254490",
    "LS마린솔루션": "060370",
    "현대건설": "000720",
    "자비스": "254120",
    "코데즈컴바인": "047770",
    "케이엠더블유": "032500",
    "타이거일렉": "219130",
    "유니퀘스트": "077500",
    "일진홀딩스": "015860",
    "KBI메탈": "024840",
    "지아이에스": "306620",
    "에치에프알": "230240",
    "큐로셀": "372320",
    "PS일렉트로닉스": "332570",
    "아진엑스텍": "059120",
    "서울바이오시스": "092190",
    "코칩": "008930",
    "필에너지": "475580",
    "하이브": "352820",
    "현대로템": "064350",
    "빛샘전자": "072950",
    "선광": "003100",
    "남해화학": "025860",
}

# ============================================
# 3. 키워드 점수 (v13.6.8)
# ============================================
TIER1_NEG = {
    "전환사채권발행결정": -15, "신주인수권부사채권발행결정": -15,
    "유상증자결정": -15, "감자결정": -20, "횡령": -25, "배임": -25,
    "감사범위제한": -25, "감사의견거절": -25,
    "관리종목지정": -30, "거래정지": -30,
}
TIER2_NEG = {
    "자기주식처분결정": -10, "외부감사인변경": -8,
    "임원변동": -5, "최대주주변경": -5,
}
TIER3_NEG = {
    "단기매매차익": -3, "사외이사사임": -3, "소송제기": -3,
}
POSITIVE = {
    "단일판매·공급계약체결": 5, "단일판매ㆍ공급계약체결": 5,
    "영업(잠정)실적(공정공시)": 3, "유형자산취득결정": 3,
    "자기주식취득결정": 5, "주식분할결정": 3,
    "주식소각결정": 5, "회사합병결정": 3,
}

ALL_NEG = {**TIER1_NEG, **TIER2_NEG, **TIER3_NEG}

# ============================================
# 4. corp_code 다운로드 + 매핑
# ============================================
STOCKS = {}

def download_corp_codes():
    print("📥 corp_code 다운로드 중...")
    url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={API_KEY}"
    r = requests.get(url, timeout=30)
    
    with open("corpCode.zip", "wb") as f:
        f.write(r.content)
    
    with zipfile.ZipFile("corpCode.zip", "r") as z:
        z.extractall(".")
    
    print("✅ CORPCODE.xml 다운로드 완료")

def map_stock_codes():
    global STOCKS
    tree = ET.parse("CORPCODE.xml")
    root = tree.getroot()
    
    code_to_corp = {}
    for child in root:
        stock_code = child.findtext("stock_code", "").strip()
        corp_code = child.findtext("corp_code", "").strip()
        if stock_code and corp_code:
            code_to_corp[stock_code] = corp_code
    
    matched = 0
    for name, code in STOCKS_INPUT.items():
        corp = code_to_corp.get(code)
        if corp:
            STOCKS[name] = corp
            matched += 1
    
    print(f"✅ 종목 매핑 완료: {matched}/{len(STOCKS_INPUT)}종")

# ============================================
# 5. DART API 호출
# ============================================
def fetch_disclosures(corp_code):
    today = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
    
    url = "https://opendart.fss.or.kr/api/list.json"
    params = {
        "crtfc_key": API_KEY,
        "corp_code": corp_code,
        "bgn_de": start,
        "end_de": today,
        "page_count": 100,
    }
    
    try:
        r = requests.get(url, params=params, timeout=30)
        data = r.json()
        if data.get("status") == "000":
            return data.get("list", [])
        return []
    except Exception as e:
        print(f"  ⚠️ 오류: {e}")
        return []

# ============================================
# 6. 종목 스크리닝 (v2.1: 최근 공시 + 호재/악재 분류)
# ============================================
def screen_stock(name, corp_code):
    disclosures = fetch_disclosures(corp_code)
    
    score = 0
    tier1_list = []
    tier2_list = []
    tier3_list = []
    호재_list = []
    악재_list = []
    recent_5 = []
    
    # 최근 5건 추출
    for d in disclosures[:5]:
        date = d.get("rcept_dt", "")
        report = d.get("report_nm", "")
        if date and report:
            recent_5.append(f"{date[:4]}-{date[4:6]}-{date[6:]}: {report}")
    
    # 키워드 매칭
    for d in disclosures:
        report = d.get("report_nm", "")
        date = d.get("rcept_dt", "")
        
        # Tier 1
        for kw, pts in TIER1_NEG.items():
            if kw in report:
                score += pts
                tier1_list.append(f"{report}")
                악재_list.append(f"{report}({pts})")
        
        # Tier 2
        for kw, pts in TIER2_NEG.items():
            if kw in report:
                score += pts
                tier2_list.append(f"{report}")
                악재_list.append(f"{report}({pts})")
        
        # Tier 3
        for kw, pts in TIER3_NEG.items():
            if kw in report:
                score += pts
                tier3_list.append(f"{report}")
        
        # 호재
        for kw, pts in POSITIVE.items():
            if kw in report:
                score += pts
                호재_list.append(f"{report}(+{pts})")
    
    return {
        "종목명": name,
        "종목코드": next((c for n, c in STOCKS_INPUT.items() if n == name), ""),
        "DART점수": score,
        "Tier1발동": "🚨 YES" if tier1_list else "OK",
        "Tier1": " | ".join(tier1_list[:3])[:300],
        "Tier2": " | ".join(tier2_list[:3])[:300],
        "Tier3": " | ".join(tier3_list[:3])[:200],
        "호재": " | ".join(호재_list[:5])[:500],
        "호재내역": " | ".join(호재_list[:5])[:500],
        "악재내역": " | ".join(악재_list[:5])[:500],
        "최근공시": " | ".join(recent_5)[:500],
        "공시건수": len(disclosures),
    }

def screen_all_stocks():
    results = []
    for i, (name, corp_code) in enumerate(STOCKS.items(), 1):
        print(f"[{i}/{len(STOCKS)}] 조회 중: {name} ({corp_code})")
        result = screen_stock(name, corp_code)
        results.append(result)
        time.sleep(0.3)
    return pd.DataFrame(results)

# ============================================
# 7. 텔레그램 알림
# ============================================
def send_telegram_alert(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ 텔레그램 미설정")
        return
    
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    
    try:
        requests.post(url, data=data, timeout=10)
        print("✅ 텔레그램 알림 발송")
    except Exception as e:
        print(f"⚠️ 텔레그램 오류: {e}")

# ============================================
# 8. 메인 실행
# ============================================
if __name__ == "__main__":
    print("=" * 60)
    print(f"🎯 워파머 DART 스크리너 v2.1")
    print(f"📅 실행일: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    download_corp_codes()
    map_stock_codes()
    
    if not STOCKS:
        print("❌ 매핑된 종목 없음")
        exit()
    
    print(f"\n📊 {len(STOCKS)}종 스크리닝 시작...\n")
    df = screen_all_stocks()
    df = df.sort_values("DART점수")
    
    today = datetime.now().strftime('%Y%m%d')
    output_csv = f"DART_점수_{today}.csv"
    df.to_csv(output_csv, index=False, encoding="utf-8-sig")
    print(f"\n✅ 저장: {output_csv}")
    
    # 텔레그램 알림
    tier1_stocks = df[df["Tier1발동"].str.contains("YES")]
    호재_stocks = df[df["DART점수"] >= 10]
    
    alert = f"🎯 <b>DART 일일 스크리닝 ({today})</b>\n"
    alert += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if len(tier1_stocks) > 0:
        alert += "🚨 <b>Tier 1 악재 발동 (즉시 회피)</b>\n"
        for _, row in tier1_stocks.iterrows():
            alert += f"• <b>{row['종목명']}</b>: {row['DART점수']}점\n"
            if row['Tier1']:
                alert += f"  └ {row['Tier1'][:80]}\n"
    else:
        alert += "✅ Tier 1 발동 종목 없음\n"
    
    if len(호재_stocks) > 0:
        alert += "\n💎 <b>호재 강세 (DART +10 이상)</b>\n"
        for _, row in 호재_stocks.iterrows():
            alert += f"• <b>{row['종목명']}</b>: +{row['DART점수']}점\n"
    
    alert += f"\n📊 총 {len(df)}종 분석 완료"
    send_telegram_alert(alert)
    
    print("\n📈 점수 분포:")
    print(df[["종목명", "DART점수", "Tier1발동", "공시건수"]].to_string(index=False))
    print(f"\n🎉 작업 완료")
