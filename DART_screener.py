"""
워파머 v13.6.8 — DART 악재 자동 스크리너 (v3.1)
새 기능:
- 한국 전체 ~2,800종 자동 분석
- corp_code_map.json 자동 활용
- 알림 임계값 강화 (노이즈 감소)
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
import time
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
# 알림 임계값 (강한 시그널만!)
# ============================================
ALERT_THRESHOLD_NEG = -25  # 점수 -25 이하: 강한 악재
ALERT_THRESHOLD_POS = 15   # 점수 +15 이상: 강한 호재

# ============================================
# corp_code 다운로드 + 매핑 JSON 생성
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
            code_map[stock_code] = {
                "corp_code": corp_code,
                "corp_name": corp_name
            }
            if corp_name:
                name_map[corp_name] = stock_code
    
    with open("corp_code_map.json", "w", encoding="utf-8") as f:
        json.dump(code_map, f, ensure_ascii=False, indent=2)
    
    with open("name_to_code.json", "w", encoding="utf-8") as f:
        json.dump(name_map, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 매핑 완료: {len(code_map)}종")
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
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get("status") == "000":
            return data.get("list", [])
        return []
    except Exception:
        return []

# ============================================
# 종목 스크리닝 (한국 전체)
# ============================================
def screen_all_stocks(code_map):
    results = []
    total = len(code_map)
    
    print(f"\n📊 {total}종 분석 시작...\n")
    
    for i, (stock_code, info) in enumerate(code_map.items(), 1):
        if i % 100 == 0:
            print(f"[{i}/{total}] 진행 중... ({i*100//total}%)")
        
        corp_code = info["corp_code"]
        corp_name = info["corp_name"]
        
        disclosures = fetch_disclosures(corp_code, days=30)
        
        # 공시 없는 종목은 스킵 (성능)
        if not disclosures:
            time.sleep(0.1)
            continue
        
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
        
        # 점수 0 또는 의미 없는 종목 스킵 (CSV 크기 절약)
        if score == 0 and not tier1_list and len(disclosures) < 3:
            time.sleep(0.1)
            continue
        
        results.append({
            "종목명": corp_name,
            "종목코드": stock_code,
            "DART점수": score,
            "Tier1발동": "🚨 YES" if tier1_list else "OK",
            "Tier1": " | ".join(tier1_list[:3])[:300],
            "호재내역": " | ".join(호재_list[:5])[:500],
            "악재내역": " | ".join(악재_list[:5])[:500],
            "최근공시": " | ".join(recent_5)[:500],
            "공시건수": len(disclosures),
        })
        time.sleep(0.1)
    
    return pd.DataFrame(results)

# ============================================
# 텔레그램 알림 (강한 시그널만!)
# ============================================
def send_telegram_alert(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": TG_CHAT_ID,
        "text": text[:4000],
        "parse_mode": "HTML"
    }, timeout=10)

# ============================================
# 메인 실행
# ============================================
if __name__ == "__main__":
    print("=" * 60)
    print(f"🎯 워파머 DART 스크리너 v3.1 (한국 전체)")
    print(f"📅 실행일: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    # 1. corp_code 매핑 JSON 생성
    download_corp_codes()
    code_map = generate_corp_code_map()
    
    # 2. 한국 전체 분석
    df = screen_all_stocks(code_map)
    df = df.sort_values("DART점수")
    
    today = datetime.now().strftime('%Y%m%d')
    output_csv = f"DART_점수_{today}.csv"
    df.to_csv(output_csv, index=False, encoding="utf-8-sig")
    print(f"\n✅ 저장: {output_csv} ({len(df)}종)")
    
    # 3. 텔레그램 알림 (강한 시그널만!)
    tier1_strong = df[
        (df["Tier1발동"].str.contains("YES")) & 
        (df["DART점수"] <= ALERT_THRESHOLD_NEG)
    ].head(10)
    
    호재_strong = df[df["DART점수"] >= ALERT_THRESHOLD_POS].head(10)
    
    alert = f"🎯 <b>DART 일일 스크리닝 ({today})</b>\n"
    alert += f"━━━━━━━━━━━━━━━━━━━━━━\n"
    alert += f"📊 한국 전체 {len(df)}종 분석 (공시 있음)\n\n"
    
    if len(tier1_strong) > 0:
        alert += f"🚨 <b>강한 악재 (-{abs(ALERT_THRESHOLD_NEG)} 이하): TOP {len(tier1_strong)}</b>\n"
        for _, row in tier1_strong.iterrows():
            alert += f"• <b>{row['종목명']}</b> ({row['종목코드']}): {row['DART점수']}점\n"
        alert += "\n"
    else:
        alert += f"✅ 강한 악재 (-{abs(ALERT_THRESHOLD_NEG)} 이하): 없음\n\n"
    
    if len(호재_strong) > 0:
        alert += f"💎 <b>강한 호재 (+{ALERT_THRESHOLD_POS} 이상): TOP {len(호재_strong)}</b>\n"
        for _, row in 호재_strong.iterrows():
            alert += f"• <b>{row['종목명']}</b> ({row['종목코드']}): +{row['DART점수']}점\n"
        alert += "\n"
    
    alert += f"━━━━━━━━━━━━━━━━━━━━━━\n"
    alert += f"📱 봇 검색: 한국 전체 실시간!\n"
    alert += f"🔥 CB 플레이북 자동 추적!"
    
    send_telegram_alert(alert)
    
    print(f"\n📊 결과 요약:")
    print(f"   전체 분석: {len(df)}종")
    print(f"   강한 악재: {len(tier1_strong)}종")
    print(f"   강한 호재: {len(호재_strong)}종")
    print(f"\n🎉 v3.1 작업 완료")
