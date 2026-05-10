"""
워파머 v13.6.8 — DART 악재 자동 스크리너 (v3.0)
새 기능: 한국 전체 상장사 corp_code 매핑 (~2,800종)
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
import time
import re
import os
import json
import zipfile
import xml.etree.ElementTree as ET

API_KEY = os.environ.get("DART_API_KEY", "")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

if not API_KEY:
    raise ValueError("DART_API_KEY가 설정되지 않음")

# ============================================
# 워파머 매트릭스 32종 (자동 알림용)
# ============================================
STOCKS_INPUT = {
    "포스코퓨처엠": "003670", "롯데웰푸드": "280360", "저스템": "417840",
    "오로스테크놀로지": "322310", "제룡전기": "033100", "유진테크": "084370",
    "진성티이씨": "036890", "오킨스전자": "100120", "삼성증권": "016360",
    "미래반도체": "254490", "LS마린솔루션": "060370", "현대건설": "000720",
    "자비스": "254120", "코데즈컴바인": "047770", "케이엠더블유": "032500",
    "타이거일렉": "219130", "유니퀘스트": "077500", "일진홀딩스": "015860",
    "KBI메탈": "024840", "지아이에스": "306620", "에치에프알": "230240",
    "큐로셀": "372320", "PS일렉트로닉스": "332570", "아진엑스텍": "059120",
    "서울바이오시스": "092190", "코칩": "008930", "필에너지": "475580",
    "하이브": "352820", "현대로템": "064350", "빛샘전자": "072950",
    "선광": "003100", "남해화학": "025860",
}

# ============================================
# 키워드 점수 (v13.6.8)
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

# ============================================
# corp_code 다운로드 + 매핑 JSON 생성 (NEW v3.0!)
# ============================================
def download_corp_codes():
    print("📥 corp_code 다운로드 중...")
    url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={API_KEY}"
    r = requests.get(url, timeout=30)
    
    with open("corpCode.zip", "wb") as f:
        f.write(r.content)
    
    with zipfile.ZipFile("corpCode.zip", "r") as z:
        z.extractall(".")
    
    print("✅ CORPCODE.xml 다운로드 완료")

def generate_corp_code_map():
    """전체 한국 상장사의 stock_code → corp_code 매핑 JSON 생성"""
    print("🔧 corp_code 매핑 JSON 생성 중...")
    tree = ET.parse("CORPCODE.xml")
    root = tree.getroot()
    
    code_map = {}
    name_map = {}
    
    for child in root:
        stock_code = child.findtext("stock_code", "").strip()
        corp_code = child.findtext("corp_code", "").strip()
        corp_name = child.findtext("corp_name", "").strip()
        
        if stock_code and corp_code:
            # stock_code → corp_code, corp_name
            code_map[stock_code] = {
                "corp_code": corp_code,
                "corp_name": corp_name
            }
            # corp_name → stock_code (이름으로 검색)
            if corp_name:
                name_map[corp_name] = stock_code
    
    # JSON 파일로 저장 (봇이 GitHub에서 fetch)
    with open("corp_code_map.json", "w", encoding="utf-8") as f:
        json.dump(code_map, f, ensure_ascii=False, indent=2)
    
    with open("name_to_code.json", "w", encoding="utf-8") as f:
        json.dump(name_map, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 매핑 완료: {len(code_map)}종 (한국 전체 상장사!)")
    return code_map

# ============================================
# DART API 호출
# ============================================
def fetch_disclosures(corp_code, days=30):
    today = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    
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
# 매트릭스 32종 스크리닝 (자동 알림용)
# ============================================
def screen_matrix_stocks(code_map):
    results = []
    for i, (name, stock_code) in enumerate(STOCKS_INPUT.items(), 1):
        info = code_map.get(stock_code)
        if not info:
            print(f"[{i}/{len(STOCKS_INPUT)}] ⚠️ {name}: corp_code 없음")
            continue
        
        corp_code = info["corp_code"]
        print(f"[{i}/{len(STOCKS_INPUT)}] 조회 중: {name} ({corp_code})")
        
        disclosures = fetch_disclosures(corp_code, days=30)
        
        score = 0
        tier1_list = []
        호재_list = []
        악재_list = []
        recent_5 = []
        
        for d in disclosures[:5]:
            date = d.get("rcept_dt", "")
            report = d.get("report_nm", "")
            if date and report:
                recent_5.append(f"{date[:4]}-{date[4:6]}-{date[6:]}: {report}")
        
        for d in disclosures:
            report = d.get("report_nm", "")
            for kw, pts in TIER1_NEG.items():
                if kw in report:
                    score += pts
                    tier1_list.append(report)
                    악재_list.append(f"{report}({pts})")
            for kw, pts in TIER2_NEG.items():
                if kw in report:
                    score += pts
                    악재_list.append(f"{report}({pts})")
            for kw, pts in TIER3_NEG.items():
                if kw in report:
                    score += pts
            for kw, pts in POSITIVE.items():
                if kw in report:
                    score += pts
                    호재_list.append(f"{report}(+{pts})")
        
        results.append({
            "종목명": name,
            "종목코드": stock_code,
            "DART점수": score,
            "Tier1발동": "🚨 YES" if tier1_list else "OK",
            "Tier1": " | ".join(tier1_list[:3])[:300],
            "호재내역": " | ".join(호재_list[:5])[:500],
            "악재내역": " | ".join(악재_list[:5])[:500],
            "최근공시": " | ".join(recent_5)[:500],
            "공시건수": len(disclosures),
        })
        time.sleep(0.3)
    
    return pd.DataFrame(results)

# ============================================
# 텔레그램 알림
# ============================================
def send_telegram_alert(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }, timeout=10)

# ============================================
# 메인 실행
# ============================================
if __name__ == "__main__":
    print("=" * 60)
    print(f"🎯 워파머 DART 스크리너 v3.0")
    print(f"📅 실행일: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    # 1. corp_code 매핑 JSON 생성 (한국 전체 상장사!)
    download_corp_codes()
    code_map = generate_corp_code_map()
    
    # 2. 매트릭스 32종 자동 분석 (텔레그램 알림용)
    print(f"\n📊 매트릭스 32종 분석 시작...\n")
    df = screen_matrix_stocks(code_map)
    df = df.sort_values("DART점수")
    
    today = datetime.now().strftime('%Y%m%d')
    output_csv = f"DART_점수_{today}.csv"
    df.to_csv(output_csv, index=False, encoding="utf-8-sig")
    print(f"\n✅ 저장: {output_csv}")
    print(f"✅ 매핑: corp_code_map.json")
    print(f"✅ 매핑: name_to_code.json")
    
    # 3. 텔레그램 알림
    tier1_stocks = df[df["Tier1발동"].str.contains("YES")]
    호재_stocks = df[df["DART점수"] >= 10]
    
    alert = f"🎯 <b>DART 일일 스크리닝 ({today})</b>\n"
    alert += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if len(tier1_stocks) > 0:
        alert += "🚨 <b>Tier 1 악재 발동 (즉시 회피)</b>\n"
        for _, row in tier1_stocks.iterrows():
            alert += f"• <b>{row['종목명']}</b>: {row['DART점수']}점\n"
    else:
        alert += "✅ Tier 1 발동 종목 없음\n"
    
    if len(호재_stocks) > 0:
        alert += "\n💎 <b>호재 강세 (DART +10 이상)</b>\n"
        for _, row in 호재_stocks.iterrows():
            alert += f"• <b>{row['종목명']}</b>: +{row['DART점수']}점\n"
    
    alert += f"\n📊 매트릭스 {len(df)}종 분석 완료"
    alert += f"\n🚀 봇 검색: 한국 전체 상장사 6개월 실시간!"
    send_telegram_alert(alert)
    
    print("\n🎉 v3.0 작업 완료")
