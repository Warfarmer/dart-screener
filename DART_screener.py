"""
============================================================
워파머 v13.6.8 — DART 악재 자동 스크리너
실행: GitHub Actions 매일 09:00 KST 자동 실행
출력: DART_점수_YYYYMMDD.csv + 텔레그램 알림
============================================================
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
# 1. 설정 (GitHub Secrets에서 자동 로드)
# ============================================
API_KEY = os.environ.get("DART_API_KEY", "")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# 안전 체크
if not API_KEY:
    raise ValueError("DART_API_KEY가 설정되지 않음. GitHub Secrets 확인")

# ============================================
# 2. 워파머 28종 + 신규 종목 매핑
# ============================================
STOCKS_INPUT = {
    # S+ 등급 (강력매수)
    "포스코퓨처엠": "003670",
    "롯데웰푸드": "280360",
    "저스템": "417840",
    "오로스테크놀로지": "322310",
    "제룡전기": "033100",
    "유진테크": "084370",
    
    # S 등급
    "진성티이씨": "036890",
    "오킨스전자": "100120",
    "삼성증권": "016360",
    "미래반도체": "254490",
    "LS마린솔루션": "060370",
    
    # A+ 등급
    "현대건설": "000720",
    "자비스": "254120",
    "코데즈컴바인": "047770",
    "케이엠더블유": "032500",
    "타이거일렉": "219130",
    
    # 모니터링
    "유니퀘스트": "077500",
    "일진홀딩스": "015860",
    "KBI메탈": "024840",
    "지아이에스": "306620",
    "에치에프알": "230240",
    "큐로셀": "372320",
    "PS일렉트로닉스": "332570",
    "아진엑스텍": "059120",
    "서울바이오시스": "092190",
    
    # D등급 (회피 - 모니터링)
    "코칩": "008930",
    "필에너지": "475580",
    "하이브": "352820",
    "현대로템": "064350",
    "빛샘전자": "072950",
    "선광": "003100",
    "남해화학": "025860",
}

# ============================================
# 3. DART 악재/호재 키워드 (워파머 v13.6.8 룰)
# ============================================
TIER1_NEG = {  # 즉시 회피
    "전환사채권발행결정": -15,
    "신주인수권부사채권발행결정": -15,
    "유상증자결정": -15,
    "감자결정": -20,
    "횡령": -25,
    "배임": -25,
    "감사범위제한": -25,
    "감사의견거절": -25,
    "관리종목지정": -30,
    "거래정지": -30,
}

TIER2_NEG = {  # 강위험
    "자기주식처분결정": -10,
    "외부감사인변경": -8,
    "임원변동": -5,
    "최대주주변경": -5,
}

TIER3_NEG = {  # 주의
    "단기매매차익": -3,
    "분기보고서.*제출지연": -3,
    "사외이사사임": -3,
    "소송제기": -3,
}

POSITIVE = {  # 호재
    "단일판매·공급계약체결": 5,
    "영업.*잠정.*실적": 3,
    "자기주식취득결정": 5,
    "무상증자결정": 5,
    "현금·현물배당": 3,
    "타법인주식및출자증권취득결정": 5,
    "특허": 3,
}

# ============================================
# 4. corp_code 자동 변환 (DART API 핵심)
# ============================================
CORP_CODE_FILE = "CORPCODE.xml"
STOCKS = {}  # {종목명: corp_code}

def download_corp_codes():
    """OpenDART 전체 종목 corp_code 매핑 다운로드"""
    if os.path.exists(CORP_CODE_FILE):
        print("✅ CORPCODE.xml 기존 파일 사용")
        return
    
    print("📥 종목코드 매핑 다운로드 중...")
    url = "https://opendart.fss.or.kr/api/corpCode.xml"
    params = {"crtfc_key": API_KEY}
    
    response = requests.get(url, params=params)
    zip_path = "corp_code.zip"
    
    with open(zip_path, "wb") as f:
        f.write(response.content)
    
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(".")
    
    os.remove(zip_path)
    print("✅ CORPCODE.xml 다운로드 완료")

def map_stock_codes():
    """6자리 종목코드 → 8자리 corp_code 변환"""
    global STOCKS
    
    tree = ET.parse(CORP_CODE_FILE)
    root = tree.getroot()
    
    code_map = {}
    for item in root.iter("list"):
        stock_code = item.findtext("stock_code", "").strip()
        corp_code = item.findtext("corp_code", "").strip()
        if stock_code:
            code_map[stock_code] = corp_code
    
    for name, stock_code in STOCKS_INPUT.items():
        corp_code = code_map.get(stock_code)
        if corp_code:
            STOCKS[name] = corp_code
        else:
            print(f"⚠️ 매핑 실패: {name} ({stock_code})")
    
    print(f"✅ 종목 매핑 완료: {len(STOCKS)}/{len(STOCKS_INPUT)}종")

# ============================================
# 5. 핵심 함수
# ============================================
def fetch_disclosures(corp_code, days=90):
    """종목별 최근 N일 공시 수집"""
    url = "https://opendart.fss.or.kr/api/list.json"
    end_de = datetime.now().strftime("%Y%m%d")
    bgn_de = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    
    params = {
        "crtfc_key": API_KEY,
        "corp_code": corp_code,
        "bgn_de": bgn_de,
        "end_de": end_de,
        "page_count": 100,
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if data.get("status") == "000":
            return data.get("list", [])
        return []
    except Exception as e:
        print(f"❌ 오류 ({corp_code}): {e}")
        return []

def calculate_score(disclosures):
    """공시 리스트에서 DART 점수 산출"""
    score = 0
    detected = []
    
    for disc in disclosures:
        title = disc.get("report_nm", "")
        rcept_dt = disc.get("rcept_dt", "")
        matched = False
        
        for kw, pts in TIER1_NEG.items():
            if re.search(kw, title):
                score += pts
                detected.append((rcept_dt, title, pts, "T1"))
                matched = True
                break
        if matched: continue
        
        for kw, pts in TIER2_NEG.items():
            if re.search(kw, title):
                score += pts
                detected.append((rcept_dt, title, pts, "T2"))
                matched = True
                break
        if matched: continue
        
        for kw, pts in TIER3_NEG.items():
            if re.search(kw, title):
                score += pts
                detected.append((rcept_dt, title, pts, "T3"))
                matched = True
                break
        if matched: continue
        
        for kw, pts in POSITIVE.items():
            if re.search(kw, title):
                score += pts
                detected.append((rcept_dt, title, pts, "POS"))
                break
    
    return score, detected

def screen_all_stocks():
    """전 종목 일괄 스크리닝"""
    results = []
    total = len(STOCKS)
    
    for i, (name, corp_code) in enumerate(STOCKS.items(), 1):
        print(f"[{i}/{total}] 조회 중: {name} ({corp_code})")
        
        disclosures = fetch_disclosures(corp_code)
        score, detected = calculate_score(disclosures)
        
        tier1 = [d for d in detected if d[3] == "T1"]
        tier2 = [d for d in detected if d[3] == "T2"]
        tier3 = [d for d in detected if d[3] == "T3"]
        positives = [d for d in detected if d[3] == "POS"]
        
        results.append({
            "종목명": name,
            "DART점수": score,
            "Tier1발동": "🚨 YES" if tier1 else "OK",
            "공시건수": len(disclosures),
            "Tier1": "; ".join([f"{d[1]}({d[0]})" for d in tier1[:3]]),
            "Tier2": "; ".join([f"{d[1]}({d[0]})" for d in tier2[:3]]),
            "Tier3": "; ".join([f"{d[1]}({d[0]})" for d in tier3[:3]]),
            "호재": "; ".join([f"{d[1]}({d[0]})" for d in positives[:3]]),
            "조회일자": datetime.now().strftime("%Y-%m-%d"),
        })
        
        time.sleep(0.3)
    
    return pd.DataFrame(results)

def send_telegram_alert(message):
    """텔레그램 알림 발송"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("\n" + "=" * 50)
        print(message)
        print("=" * 50)
        return
    
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    
    # 메시지 길이 제한 (텔레그램 최대 4096자)
    if len(message) > 4000:
        message = message[:3950] + "\n\n... (메시지 길이 제한)"
    
    params = {
        "chat_id": TG_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    
    try:
        response = requests.post(url, params=params, timeout=10)
        if response.status_code == 200:
            print("✅ 텔레그램 알림 발송 완료")
        else:
            print(f"⚠️ 텔레그램 응답: {response.status_code}")
    except Exception as e:
        print(f"❌ 텔레그램 오류: {e}")

# ============================================
# 6. 메인 실행
# ============================================
if __name__ == "__main__":
    print("=" * 60)
    print(f"🎯 워파머 DART 악재 자동 스크리너 v1.0")
    print(f"📅 실행일: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    # 1) corp_code 다운로드
    download_corp_codes()
    
    # 2) 종목코드 매핑
    map_stock_codes()
    
    if not STOCKS:
        print("❌ 매핑된 종목 없음. STOCKS_INPUT 확인 필요")
        exit()
    
    # 3) 전 종목 스크리닝
    print(f"\n📊 {len(STOCKS)}종 스크리닝 시작...\n")
    df = screen_all_stocks()
    
    # 4) 점수순 정렬
    df = df.sort_values("DART점수")
    
    # 5) CSV 저장
    today = datetime.now().strftime('%Y%m%d')
    output_csv = f"DART_점수_{today}.csv"
    df.to_csv(output_csv, index=False, encoding="utf-8-sig")
    
    print(f"\n✅ 결과 저장: {output_csv}")
    
    # 6) 텔레그램 알림 메시지 작성
    tier1_stocks = df[df["Tier1발동"].str.contains("YES")]
    호재_stocks = df[df["DART점수"] >= 10]
    
    alert = f"🎯 <b>DART 일일 스크리닝 ({today})</b>\n"
    alert += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if len(tier1_stocks) > 0:
        alert += "🚨 <b>Tier 1 악재 발동 (즉시 회피)</b>\n"
        alert += "━━━━━━━━━━━━━━━━━━━━━━\n"
        for _, row in tier1_stocks.iterrows():
            alert += f"\n• <b>{row['종목명']}</b>: {row['DART점수']}점\n"
            if row['Tier1']:
                alert += f"  └ {row['Tier1'][:80]}\n"
    else:
        alert += "✅ Tier 1 발동 종목 없음 (양호)\n\n"
    
    if len(호재_stocks) > 0:
        alert += "\n💎 <b>호재 강세 (DART +10 이상)</b>\n"
        alert += "━━━━━━━━━━━━━━━━━━━━━━\n"
        for _, row in 호재_stocks.iterrows():
            alert += f"• <b>{row['종목명']}</b>: +{row['DART점수']}점\n"
            if row['호재']:
                alert += f"  └ {row['호재'][:80]}\n"
    
    alert += f"\n📁 상세: github.com/Warfarmer/dart-screener\n"
    alert += f"📊 총 {len(df)}종 분석 완료"
    
    # 7) 알림 발송
    send_telegram_alert(alert)
    
    # 8) 콘솔 출력
    print("\n📈 DART 점수 분포 (점수 낮은순):")
    print(df[["종목명", "DART점수", "Tier1발동", "공시건수"]].to_string(index=False))
    
    print(f"\n🎉 작업 완료. {output_csv} 파일 확인하세요.")
