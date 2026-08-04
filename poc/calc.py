#!/usr/bin/env python3
"""PoC 계산기: 2026년 세제개편안(2026-08-03 발표) 반영 양도세/보유세 시나리오.

규칙 출처: poc/law-archive/press/mofe-2026-tax-reform_2026-08-03/ (상세본·문답자료)
           poc/law-archive/statutes/sodukse-sihaengryeong/... (소득세법 시행령 현행)
모든 금액은 원 단위 정수. confidence: drafted (원문 대조 검증 전).

실행: python3 poc/calc.py  → poc/scenario-results.json 생성
"""
import json
from pathlib import Path

MAN = 10_000
EOK = 100_000_000

# ── 기본세율 (소득세법 §55, 2026년 현행) ─────────────────────────
BRACKETS = [
    (14_000_000, 0.06, 0),
    (50_000_000, 0.15, 1_260_000),
    (88_000_000, 0.24, 5_760_000),
    (150_000_000, 0.35, 15_440_000),
    (300_000_000, 0.38, 19_940_000),
    (500_000_000, 0.40, 25_940_000),
    (1_000_000_000, 0.42, 35_940_000),
    (float("inf"), 0.45, 65_940_000),
]

HIGH_VALUE_THRESHOLD = 1_200_000_000  # 고가주택 기준 (양도가액 12억)


def basic_rate_tax(base):
    if base <= 0:
        return 0
    for cap, rate, deduct in BRACKETS:
        if base <= cap:
            return int(base * rate - deduct)
    return 0


def bracket_label(base):
    for cap, rate, _ in BRACKETS:
        if base <= cap:
            return f"{int(rate*100)}%"
    return "45%"


# ── 장기보유특별공제 / 장기거주소득공제 공제율 ────────────────────
# 개편안 (8)② : 1세대 1주택자 — 양도시점별 단계적 개편
#   ~'27.12.31.  거주 연4%(최대40%) + 보유 연4%(최대40%)
#   '28.1.1~12.31 거주 연6%(최대60%) + 보유 연2%(최대20%)
#   '29.1.1~      거주 연8%(최대80%), 보유공제 폐지
ONE_HOUSE_TABLE = {
    2026: {"residence": (0.04, 0.40), "holding": (0.04, 0.40)},
    2027: {"residence": (0.04, 0.40), "holding": (0.04, 0.40)},
    2028: {"residence": (0.06, 0.60), "holding": (0.02, 0.20)},
    2029: {"residence": (0.08, 0.80), "holding": (0.00, 0.00)},
    2030: {"residence": (0.08, 0.80), "holding": (0.00, 0.00)},
}
# 개편안 (8)③ : 다주택자 — '28년은 거주/보유 중 높은 쪽, '29년 이후 거주만
MULTI_HOUSE_TABLE = {
    2026: {"residence": (0.00, 0.00), "holding": (0.02, 0.30)},
    2027: {"residence": (0.00, 0.00), "holding": (0.02, 0.30)},
    2028: {"residence": (0.02, 0.30), "holding": (0.01, 0.15)},
    2029: {"residence": (0.02, 0.30), "holding": (0.00, 0.00)},
    2030: {"residence": (0.02, 0.30), "holding": (0.00, 0.00)},
}
# 현행(개편 전) 비교용
CURRENT_LAW = {
    "one_house": {"residence": (0.04, 0.40), "holding": (0.04, 0.40)},
    "multi": {"residence": (0.00, 0.00), "holding": (0.02, 0.30)},
}

# 개편안 (12) 다주택자 양도세 중과 한시완화 (2주택 기준 %p)
SURCHARGE_2HOUSE = {2026: 0, 2027: 5, 2028: 10, 2029: 20, 2030: 20}


def deduction_rate(years_hold, years_reside, year, is_one_house, law="reform"):
    """공제율 산정. 기본요건: 3년 이상 보유(1주택은 +2년 이상 거주)."""
    if years_hold < 3:
        return 0.0, "보유 3년 미만 — 공제 없음"
    if law == "current":
        t = CURRENT_LAW["one_house"] if is_one_house else CURRENT_LAW["multi"]
    else:
        t = (ONE_HOUSE_TABLE if is_one_house else MULTI_HOUSE_TABLE).get(year, ONE_HOUSE_TABLE[2030])
    if is_one_house and years_reside < 2:
        t = CURRENT_LAW["multi"] if law == "current" else MULTI_HOUSE_TABLE.get(year, MULTI_HOUSE_TABLE[2030])
        is_one_house = False

    r_rate, r_cap = t["residence"]
    h_rate, h_cap = t["holding"]
    r = min(int(years_reside) * r_rate, r_cap)
    h = min(int(years_hold) * h_rate, h_cap)

    if not is_one_house and year >= 2028 and law == "reform":
        # 개편안 (8)③: '28년은 거주공제율과 보유공제율 중 높은 것 (합산 아님)
        rate = max(r, h)
        detail = f"거주 {r:.0%} vs 보유 {h:.0%} 중 높은 쪽"
    else:
        rate = min(r + h, 0.80)
        detail = f"거주 {int(years_reside)}년×{r_rate:.0%}={r:.0%} + 보유 {int(years_hold)}년×{h_rate:.0%}={h:.0%}"
    return rate, detail


def capital_gains(sale_price, acq_price, expenses, years_hold, years_reside,
                  year, *, exempt_1house, is_one_house, resident=True,
                  law="reform", long_resident_years=0, surcharge_pp=0,
                  no_long_deduction=False, deduction_cap=None):
    """양도세 계산. exempt_1house=True면 1세대1주택 비과세(고가주택 초과분만 과세).

    no_long_deduction: 중과 한시완화 기간 적용 등으로 장특공제 배제되는 경우
    deduction_cap: 개편안 (8)④ 공제한도 ('28년 20억, '29년~ 10억)
    """
    gross = sale_price - acq_price - expenses
    steps = [
        ("양도가액", sale_price), ("− 취득가액", -acq_price),
        ("− 필요경비(취득세 등)", -expenses), ("= 양도차익", gross),
    ]
    if exempt_1house and resident:
        if sale_price <= HIGH_VALUE_THRESHOLD:
            return {"tax": 0, "local": 0, "total": 0,
                    "steps": steps + [("1세대 1주택 비과세 (양도가액 12억 이하)", 0)],
                    "rate_label": "비과세", "deduction_rate": 0, "taxable": 0, "base": 0}
        ratio = (sale_price - HIGH_VALUE_THRESHOLD) / sale_price
        taxable = int(gross * ratio)
        steps.append((f"× 고가주택 과세비율 ({(sale_price-HIGH_VALUE_THRESHOLD)/EOK:.2f}억/{sale_price/EOK:.2f}억 = {ratio:.1%})", taxable))
    else:
        taxable = gross
        if exempt_1house and not resident:
            steps.append(("비거주자 → 1세대 1주택 비과세 배제 (전액 과세)", taxable))

    if no_long_deduction:
        rate, detail, ded = 0.0, "중과 한시완화 적용 → 장기보유특별공제 배제", 0
        steps.append(("− 장기보유특별공제 (배제)", 0))
    else:
        rate, detail = deduction_rate(years_hold, years_reside + long_resident_years,
                                      year, is_one_house and resident, law)
        ded = int(taxable * rate)
        if deduction_cap is not None and ded > deduction_cap:
            ded = deduction_cap
            detail += f" · 공제한도 {deduction_cap/EOK:.0f}억 적용"
        steps.append((f"− 장기{'거주소득' if year >= 2028 and law=='reform' else '보유특별'}공제 {rate:.0%} ({detail})", -ded))
    income = taxable - ded

    # 개편안 (9): 10년 이상 거주 + 양도가액 30억 이하 1세대1주택 → 기본공제 2,500만원
    basic = 2_500_000
    if (law == "reform" and year >= 2027 and resident and is_one_house
            and (years_reside + long_resident_years) >= 10 and sale_price <= 3_000_000_000):
        basic = 25_000_000
        steps.append(("− 기본공제 (10년 이상 거주 1주택 · 개편안 §103)", -basic))
    else:
        steps.append(("− 기본공제", -basic))
    base = max(income - basic, 0)
    steps.append(("= 과세표준", base))

    tax = basic_rate_tax(base)
    label = bracket_label(base)
    if surcharge_pp:
        tax += int(base * surcharge_pp / 100)
        label += f" + {surcharge_pp}%p 중과"
        steps.append((f"× 기본세율 {bracket_label(base)} + 중과 {surcharge_pp}%p", tax))
    else:
        steps.append((f"× 기본세율 {label} (누진공제 적용)", tax))
    local = int(tax * 0.1)
    steps.append(("+ 지방소득세 10%", local))
    steps.append(("= 총 예상세액", tax + local))
    return {"tax": tax, "local": local, "total": tax + local, "steps": steps,
            "rate_label": label, "deduction_rate": rate, "taxable": taxable, "base": base}


# ════════════════════════════════════════════════════════════════
# 사용자 자산 — 취득금액은 실제값, 양도가액·공시가는 입력 대기
# ════════════════════════════════════════════════════════════════
GWACHEON = {
    "name": "과천자이 615동 903호 (25평 A형)",
    "acq_date": "2022-01-20",            # 분양 잔금·입주 (날짜는 확인 필요)
    "acq_price": 910_000_000,            # ✅ 실제 분양가 9.1억
    "acquisition_tax_rate": 0.03,        # ✅ 실제 취득세율 3%
    "acquisition_tax": 27_300_000,        # 9.1억 × 3%
    "other_expenses": 5_000_000,         # 취득 부대비용 가정
    "sale_price": 1_600_000_000,         # ⚠ 입력 대기 (UI에서 입력)
    "reside_from": "2022-01-20", "reside_to": "2024-07-05",
    "reside_years": 2.46,
    "adjusted_at_acq": True,             # 과천: 2016-11 지정 → 2023-01-05 해제
    "official_price": 1_100_000_000,     # ⚠ 입력 대기 (공시가격 알리미 조회 필요)
}
SIBEOM = {
    "name": "분당 시범한양 332동 405호 (14평형)",
    "contract_date": "2022-05-20",
    "acq_date": "2022-09-15",            # 잔금
    "acq_price": 680_000_000,            # ✅ 실제 매수가 6.8억
    "acquisition_tax_rate": 0.08,        # ✅ 실제 취득세율 8% (2주택 중과)
    "acquisition_tax": 54_400_000,        # 6.8억 × 8%
    "other_expenses": 5_000_000,
    "sale_price": 860_000_000,           # ⚠ 입력 대기
    "reside_years": 0.0,
    "adjusted_at_acq": True,             # 성남 분당: 2023-01-05 해제
    "official_price": 600_000_000,       # ⚠ 입력 대기
    "reconstruction": True,              # 재건축 추진 단지
}
for a in (GWACHEON, SIBEOM):
    a["expenses"] = a["acquisition_tax"] + a["other_expenses"]


def hold_years(acq_date, year, month=7):
    y, m, _ = map(int, acq_date.split("-"))
    return (year - y) + (month - m) / 12


results = {
    "generatedFor": "2026-08-04",
    "assets": {
        "gwacheon": {k: GWACHEON[k] for k in
                     ("name", "acq_date", "acq_price", "acquisition_tax_rate",
                      "acquisition_tax", "other_expenses", "expenses", "sale_price",
                      "reside_years", "official_price")},
        "sibeom": {k: SIBEOM[k] for k in
                   ("name", "acq_date", "acq_price", "acquisition_tax_rate",
                    "acquisition_tax", "other_expenses", "expenses", "sale_price",
                    "reside_years", "official_price")},
    },
    "confirmed": {
        "gwacheon_acq_price": "실제 분양가 9.1억 (사용자 확인)",
        "gwacheon_acq_tax": "취득세율 3% → 2,730만원 (사용자 확인)",
        "sibeom_acq_price": "실제 매수가 6.8억 (사용자 확인)",
        "sibeom_acq_tax": "취득세율 8% → 5,440만원 (사용자 확인, 2주택 중과세율)",
    },
    "pendingInputs": [
        {"field": "gwacheon.sale_price", "label": "과천자이 예상 매도가", "assumed": 1_600_000_000},
        {"field": "sibeom.sale_price", "label": "시범한양 예상 매도가", "assumed": 860_000_000},
        {"field": "gwacheon.official_price", "label": "과천자이 615동 903호 공시가격",
         "assumed": 1_100_000_000,
         "howTo": "부동산공시가격 알리미(realtyprice.kr) → 공동주택 공시가격 → 경기 과천시 주소 + 615동 903호"},
        {"field": "sibeom.official_price", "label": "시범한양 332동 405호 공시가격",
         "assumed": 600_000_000,
         "howTo": "부동산공시가격 알리미(realtyprice.kr) → 공동주택 공시가격 → 경기 성남시 분당구 주소 + 332동 405호"},
    ],
}

# ── 시나리오 1: 과천자이 — 1주택 + 거주자 상태, 연도별 ────────────
scen = []
for year in (2027, 2028, 2029):
    # 개편안 (18): 해외 근무상 형편 출국 → 비거주기간을 거주기간으로 인정 (최장 3년),
    #              공제율 산정에만 적용. '28.1.1. 이후 양도분부터.
    bonus = 3.0 if year >= 2028 else 0.0
    cap = None if year < 2028 else (2_000_000_000 if year == 2028 else 1_000_000_000)
    r = capital_gains(GWACHEON["sale_price"], GWACHEON["acq_price"], GWACHEON["expenses"],
                      hold_years(GWACHEON["acq_date"], year), GWACHEON["reside_years"],
                      year, exempt_1house=True, is_one_house=True, resident=True,
                      law="reform", long_resident_years=bonus, deduction_cap=cap)
    cur = capital_gains(GWACHEON["sale_price"], GWACHEON["acq_price"], GWACHEON["expenses"],
                        hold_years(GWACHEON["acq_date"], year), GWACHEON["reside_years"],
                        year, exempt_1house=True, is_one_house=True, resident=True, law="current")
    scen.append({"year": year, "reform": r, "current": cur, "overseasCredited": bonus})
results["gwacheon_1house_resident"] = scen

# ── 시나리오 2: 과천자이 — 비거주자(주재원 상태) 양도 ─────────────
results["gwacheon_nonresident"] = {}
for year in (2027, 2028, 2029):
    results["gwacheon_nonresident"][year] = capital_gains(
        GWACHEON["sale_price"], GWACHEON["acq_price"], GWACHEON["expenses"],
        hold_years(GWACHEON["acq_date"], year), GWACHEON["reside_years"], year,
        exempt_1house=True, is_one_house=True, resident=False)

# ── 시나리오 3: 과천자이 — 2주택 상태(현재) 양도, 중과 한시완화 반영 ──
results["gwacheon_two_house"] = {}
for year in (2027, 2028, 2029):
    pp = SURCHARGE_2HOUSE[year]
    # 개편안 (12): 완화기간('27~'28) 중 양도는 장특공제 미적용
    results["gwacheon_two_house"][year] = capital_gains(
        GWACHEON["sale_price"], GWACHEON["acq_price"], GWACHEON["expenses"],
        hold_years(GWACHEON["acq_date"], year), GWACHEON["reside_years"], year,
        exempt_1house=False, is_one_house=False, resident=True,
        surcharge_pp=pp, no_long_deduction=(year in (2027, 2028)))

# ── 시나리오 4: 시범한양 — 2주택 상태 과세 양도 (연도별) ──────────
sib = []
for year in (2027, 2028, 2029):
    pp = SURCHARGE_2HOUSE[year]
    r = capital_gains(SIBEOM["sale_price"], SIBEOM["acq_price"], SIBEOM["expenses"],
                      hold_years(SIBEOM["acq_date"], year), 0, year,
                      exempt_1house=False, is_one_house=False, resident=True,
                      surcharge_pp=pp, no_long_deduction=(year in (2027, 2028)))
    sib.append({"year": year, "reform": r, "surchargePp": pp})
results["sibeom_taxed"] = sib

# ── 시나리오 5: 시범한양 — 1주택 + 상생임대 거주요건 면제 비과세 ──
sib_ex = capital_gains(SIBEOM["sale_price"], SIBEOM["acq_price"], SIBEOM["expenses"],
                       hold_years(SIBEOM["acq_date"], 2029), 0, 2029,
                       exempt_1house=True, is_one_house=True, resident=True)
results["sibeom_sangsaeng_exempt"] = {"deadline": "2029-07-31", **sib_ex}

# ── 시나리오 6: 시범한양 재건축 단계별 세제 변화 ───────────────────
# 근거: 소득세법 시행령 §156의2(주택과 조합원입주권 특례), §166(양도차익 산정),
#       지방세법 §107(재산세 납세의무자), 종부법 §8(주택 범위)
RECONSTRUCTION_STAGES = [
    {
        "stage": "조합설립인가",
        "housingCount": "주택",
        "propertyTax": "주택분 재산세 (현행 유지)",
        "jongbuse": "주택으로 합산",
        "capitalGains": "주택 양도 — 일반 규정",
        "note": "세제상 변화 없음. 안전진단·정비구역 지정 단계도 동일.",
        "keyPoint": "이 단계까지는 상생임대·1주택 비과세 판정이 일반 주택과 같다.",
    },
    {
        "stage": "사업시행인가",
        "housingCount": "주택",
        "propertyTax": "주택분 재산세",
        "jongbuse": "주택으로 합산",
        "capitalGains": "주택 양도 — 일반 규정",
        "note": "아직 주택이다. 다만 이 시점 이후 취득한 '대체주택'은 시행령 §156의2⑤에 따라 1세대1주택으로 보아 비과세 가능(1년 이상 거주 등 요건).",
        "keyPoint": "재건축 기간 거주용 대체주택 특례의 기산점. 사업시행인가일 이전 취득 주택은 이 특례 대상이 아니다.",
    },
    {
        "stage": "관리처분계획인가",
        "housingCount": "조합원입주권으로 전환",
        "propertyTax": "멸실 전까지 주택분, 멸실 후 토지분",
        "jongbuse": "멸실 후 주택 아님 → 종부세 주택 합산에서 제외 (토지 종부세 별도 판단)",
        "capitalGains": "조합원입주권 양도 — 시행령 §166①에 따라 양도차익 산정. 관리처분계획 인가일 전후로 기간을 나눠 계산",
        "note": "가장 중요한 분기점. 주택이 조합원입주권(부동산을 취득할 권리)으로 바뀐다. 1세대1주택 비과세는 §156의2 특례에 해당해야만 가능.",
        "keyPoint": "관리처분인가 이후에는 '주택 수'에서 빠지지만 조합원입주권은 다른 주택의 비과세 판정에서 주택 수에 포함된다(§154, §156의2). 상생임대는 임대 자체가 종료되므로 거주요건 면제 경로가 끊긴다.",
    },
    {
        "stage": "이주·멸실",
        "housingCount": "조합원입주권 (건물 멸실)",
        "propertyTax": "토지분 재산세로 전환 (주택분 아님)",
        "jongbuse": "주택 종부세 대상 아님 → 보유세 부담 크게 감소",
        "capitalGains": "조합원입주권 양도. 실거주 불가로 거주기간 추가 적립 불가",
        "note": "임차인 퇴거 → 임대소득 중단, 임대차 계약 종료. 상생임대 특례는 이 시점 이전에 요건을 완성해야 한다.",
        "keyPoint": "보유세는 줄지만 상생임대·거주요건 경로가 완전히 닫힌다. 멸실 전에 양도 여부를 결정해야 한다.",
    },
    {
        "stage": "준공·입주 (신축주택 취득)",
        "housingCount": "신축주택",
        "propertyTax": "주택분 재산세 재개시 (신축 공시가 기준 — 통상 크게 상승)",
        "jongbuse": "주택으로 합산, 공시가 상승분 반영",
        "capitalGains": "신축주택 양도 — 시행령 §166②에 따라 기존건물분/청산금분 나눠 산정. 보유기간은 기존주택 취득일부터 통산",
        "note": "추가분담금은 필요경비에 가산. 신축 후 실거주 시 거주기간 재적립 가능.",
        "keyPoint": "공시가 급등으로 보유세가 크게 오른다. 개편안 종부세(공정비율 70%, 기본공제 거주 차등)와 맞물리면 부담이 배가된다.",
    },
]
results["reconstruction_stages"] = RECONSTRUCTION_STAGES


# ── 종합부동산세 (개편안 반영) ────────────────────────────────────
def jongbuse(official_prices, year, *, one_house=False, resident_house_price=0, law="reform"):
    total = sum(official_prices)
    n = len(official_prices)
    if law == "current":
        basic = 1_200_000_000 if (one_house and n == 1) else 900_000_000
        fair = 0.60
        rates = [(300_000_000, .005), (600_000_000, .007), (1_200_000_000, .010),
                 (2_500_000_000, .013), (5_000_000_000, .015), (9_400_000_000, .020), (float("inf"), .027)]
    else:
        if one_house and n == 1:
            basic = 1_400_000_000 if resident_house_price > 0 else 900_000_000
        else:
            # 개편안 (3): 4억 + (5억 × 거주주택공시가/주택공시가 합계)
            basic = 400_000_000 + int(500_000_000 * (resident_house_price / total if total else 0))
        fair = 0.70
        rates = [(300_000_000, .005), (600_000_000, .007), (1_200_000_000, .013),
                 (2_500_000_000, .015), (5_000_000_000, .020), (9_400_000_000, .027), (float("inf"), .035)]
    base = max(int((total - basic) * fair), 0)
    tax, prev = 0, 0
    for cap, rate in rates:
        if base > prev:
            tax += int((min(base, cap) - prev) * rate)
            prev = cap
        else:
            break
    return {"basicDeduction": basic, "fairRatio": fair, "taxBase": base, "tax": tax,
            "totalOfficial": total, "houseCount": n}


def property_tax(official_price, *, one_house_under_900m=False):
    """재산세(주택분) 개산. 지방세법 §111, 공정시장가액비율 60%(1주택 특례 43~45% 별도).
    도시지역분·지방교육세 포함 개산치."""
    fair = 0.60
    base = int(official_price * fair)
    tiers = [(60_000_000, .001, 0), (150_000_000, .0015, 30_000),
             (300_000_000, .0025, 180_000), (float("inf"), .004, 630_000)]
    for cap, rate, ded in tiers:
        if base <= cap:
            tax = int(base * rate - ded)
            break
    edu = int(tax * 0.2)            # 지방교육세 20%
    city = int(base * 0.0014)       # 도시지역분 0.14%
    return {"taxBase": base, "propertyTax": tax, "eduTax": edu, "cityTax": city,
            "total": tax + edu + city}


OFFICIAL = [GWACHEON["official_price"], SIBEOM["official_price"]]
results["jongbuse"] = {
    "official": OFFICIAL,
    "current_2026": jongbuse(OFFICIAL, 2026, resident_house_price=0, law="current"),
    "reform_2027_no_residence": jongbuse(OFFICIAL, 2027, resident_house_price=0),
    "reform_2027_if_living_in_gwacheon": jongbuse(OFFICIAL, 2027, resident_house_price=OFFICIAL[0]),
    "reform_2027_one_house_resident": jongbuse([OFFICIAL[0]], 2027, one_house=True, resident_house_price=OFFICIAL[0]),
    "reform_2027_gwacheon_only_nonresident": jongbuse([OFFICIAL[0]], 2027, one_house=True, resident_house_price=0),
    # 시범한양 멸실 후 (주택 아님 → 과천자이만 주택)
    "reform_after_demolition_nonresident": jongbuse([OFFICIAL[0]], 2028, one_house=True, resident_house_price=0),
}

# ── 보유세 연도별 타임라인 (재산세 + 종부세) ──────────────────────
holding_timeline = []
for year in (2026, 2027, 2028, 2029):
    law = "current" if year == 2026 else "reform"
    two = jongbuse(OFFICIAL, year, resident_house_price=0, law=law)
    two_res = jongbuse(OFFICIAL, year, resident_house_price=OFFICIAL[0], law=law)
    one = jongbuse([OFFICIAL[0]], year, one_house=True, resident_house_price=OFFICIAL[0], law=law)
    one_nr = jongbuse([OFFICIAL[0]], year, one_house=True, resident_house_price=0, law=law)
    pt_g = property_tax(GWACHEON["official_price"])
    pt_s = property_tax(SIBEOM["official_price"])
    holding_timeline.append({
        "year": year,
        "law": "현행" if law == "current" else "개편안",
        "propertyTax": {"gwacheon": pt_g["total"], "sibeom": pt_s["total"],
                        "bothTotal": pt_g["total"] + pt_s["total"]},
        "jongbuse": {
            "twoHouse_nonresident": two["tax"],
            "twoHouse_livingInGwacheon": two_res["tax"],
            "oneHouse_resident": one["tax"],
            "oneHouse_nonresident": one_nr["tax"],
            "basicDeduction_twoHouse_nonresident": two["basicDeduction"],
            "fairRatio": two["fairRatio"],
        },
        "total_twoHouse_nonresident": pt_g["total"] + pt_s["total"] + two["tax"],
        "total_oneHouse_resident": pt_g["total"] + one["tax"],
    })
results["holding_tax_timeline"] = holding_timeline


# ── 매도 순서 시나리오 (핵심) ─────────────────────────────────────
def order_scenario(first, second, first_year, second_year, *, resident,
                   sangsaeng_ok=False):
    """두 채를 순서대로 매도할 때 총 양도세.
    first = 2주택 상태 양도 (과세), second = 1주택 상태 양도.
    """
    a, b = (GWACHEON, SIBEOM) if first == "gwacheon" else (SIBEOM, GWACHEON)
    pp = SURCHARGE_2HOUSE[first_year]
    t1 = capital_gains(a["sale_price"], a["acq_price"], a["expenses"],
                       hold_years(a["acq_date"], first_year), a["reside_years"], first_year,
                       exempt_1house=False, is_one_house=False, resident=resident,
                       surcharge_pp=pp, no_long_deduction=(first_year in (2027, 2028)))
    bonus = 3.0 if (second == "gwacheon" and second_year >= 2028) else 0.0
    cap = None if second_year < 2028 else (2_000_000_000 if second_year == 2028 else 1_000_000_000)
    # 두 번째는 1주택 상태 → 비과세 가능 (거주요건 충족 또는 상생임대 면제)
    exempt = resident and (b["reside_years"] >= 2 or sangsaeng_ok)
    t2 = capital_gains(b["sale_price"], b["acq_price"], b["expenses"],
                       hold_years(b["acq_date"], second_year), b["reside_years"], second_year,
                       exempt_1house=exempt, is_one_house=True, resident=resident,
                       law="reform", long_resident_years=bonus, deduction_cap=cap)
    return {"first": {"asset": first, "year": first_year, "name": a["name"], **t1},
            "second": {"asset": second, "year": second_year, "name": b["name"],
                       "exemptApplied": exempt, **t2},
            "total": t1["total"] + t2["total"]}


orders = {
    # 권장: 시범한양 먼저 (상생임대 비과세) → 과천자이 2028 (1주택 비과세)
    "sibeom_first_sangsaeng_2027_then_gwacheon_2028": {
        "label": "시범한양 상생임대 비과세 먼저(1주택 취급 불가 → 2주택 상태 과세) → 과천자이 2028",
        **order_scenario("sibeom", "gwacheon", 2027, 2028, resident=True),
    },
    "gwacheon_first_2027_then_sibeom_2029": {
        "label": "과천자이 먼저 2027 (2주택 과세) → 시범한양 2029 (1주택 비과세)",
        **order_scenario("gwacheon", "sibeom", 2027, 2029, resident=True, sangsaeng_ok=True),
    },
    "sibeom_first_2027_then_gwacheon_2028_nonresident": {
        "label": "비거주자 상태 유지 · 시범한양 2027 → 과천자이 2028",
        **order_scenario("sibeom", "gwacheon", 2027, 2028, resident=False),
    },
}
results["sale_order_scenarios"] = orders

# ── 최선안: 시범한양을 상생임대 비과세로 (1주택 상태에서) ─────────
# 실제 최선은 "1주택 상태에서 각각 비과세"인데 2주택이므로 한 채는 과세 불가피.
# 어느 쪽을 과세로 떠안을지가 핵심 → 양도차익이 작은 쪽을 먼저 과세 처분.
best = {}
for year_first in (2027, 2028, 2029):
    for year_second in (2027, 2028, 2029):
        if year_second < year_first:
            continue
        for first in ("gwacheon", "sibeom"):
            second = "sibeom" if first == "gwacheon" else "gwacheon"
            key = f"{first}{year_first}_then_{second}{year_second}"
            s = order_scenario(first, second, year_first, year_second,
                               resident=True, sangsaeng_ok=(second == "sibeom" and year_second <= 2029))
            best[key] = {"total": s["total"], "first": first, "firstYear": year_first,
                         "second": second, "secondYear": year_second,
                         "firstTax": s["first"]["total"], "secondTax": s["second"]["total"],
                         "secondExempt": s["second"]["exemptApplied"]}
results["all_order_combinations"] = best
ranked = sorted(best.items(), key=lambda kv: kv[1]["total"])
results["best_order"] = {"key": ranked[0][0], **ranked[0][1]}
results["worst_order"] = {"key": ranked[-1][0], **ranked[-1][1]}


# ── 증여 (배우자) 비교 ────────────────────────────────────────────
def gift_spouse(value, deduction=600_000_000, acq_tax_rate=0.035):
    base = max(value - deduction, 0)
    tiers = [(100_000_000, .10, 0), (500_000_000, .20, 10_000_000),
             (1_000_000_000, .30, 60_000_000), (3_000_000_000, .40, 160_000_000),
             (float("inf"), .50, 460_000_000)]
    for cap, rate, ded in tiers:
        if base <= cap:
            tax = int(base * rate - ded)
            break
    credit = int(tax * 0.03)  # 신고세액공제 3%
    acq = int(value * acq_tax_rate)
    return {"value": value, "deduction": deduction, "base": base,
            "tax": tax, "afterCredit": tax - credit,
            "acquisitionTax": acq, "totalCost": tax - credit + acq}


results["gift_spouse_sibeom"] = gift_spouse(SIBEOM["sale_price"])
results["gift_spouse_gwacheon"] = gift_spouse(GWACHEON["sale_price"])

# ── 상속 (참고) ───────────────────────────────────────────────────
estate_total = GWACHEON["sale_price"] + SIBEOM["sale_price"]
results["inheritance_reference"] = {
    "estateTotal": estate_total,
    "deductionAssumed": 1_000_000_000,
    "taxBase": estate_total - 1_000_000_000,
    "note": "상속은 선택 가능한 절세 수단이 아님 — 자산 규모가 상속세 과세구간에 있음을 확인하는 용도",
}

out = Path(__file__).parent / "scenario-results.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), "utf-8")


def won(v):
    return f"{v:,}원"


print("═" * 78)
print(f"취득 확정값  과천자이 분양가 {GWACHEON['acq_price']/EOK:.2f}억 + 취득세 3% {won(GWACHEON['acquisition_tax'])}")
print(f"             시범한양 매수가 {SIBEOM['acq_price']/EOK:.2f}억 + 취득세 8% {won(SIBEOM['acquisition_tax'])}")
print("═" * 78)
print("과천자이 — 1주택 + 거주자(귀국) 상태 양도")
for s in scen:
    print(f"  {s['year']}년: 개편안 {won(s['reform']['total']):>16}  (공제율 {s['reform']['deduction_rate']:.0%}"
          f"{', 해외 3년 거주인정' if s['overseasCredited'] else ''})   현행유지 {won(s['current']['total'])}")
print("\n과천자이 — 2주택 상태 양도 (중과 한시완화 + 장특공제 배제)")
for y, r in results["gwacheon_two_house"].items():
    print(f"  {y}년: {won(r['total']):>16}  ({r['rate_label']})")
print("\n과천자이 — 비거주자 상태 양도 (비과세 배제)")
for y, r in results["gwacheon_nonresident"].items():
    print(f"  {y}년: {won(r['total']):>16}")
print(f"\n{'═'*78}\n시범한양 — 2주택 상태 과세 양도")
for s in sib:
    print(f"  {s['year']}년: {won(s['reform']['total']):>16} (공제율 {s['reform']['deduction_rate']:.0%}, 중과 +{s['surchargePp']}%p)")
print(f"  1주택 + 상생임대 거주요건 면제 비과세 (기한 2029-07-31): {won(sib_ex['total'])}")

print(f"\n{'═'*78}\n보유세 타임라인 (재산세 + 종부세)")
for h in holding_timeline:
    print(f"  {h['year']}년 [{h['law']}] 재산세 {won(h['propertyTax']['bothTotal']):>12} | "
          f"종부세 2주택·비거주 {won(h['jongbuse']['twoHouse_nonresident']):>12} "
          f"(기본공제 {h['jongbuse']['basicDeduction_twoHouse_nonresident']/EOK:.1f}억, 공정 {h['jongbuse']['fairRatio']:.0%}) "
          f"→ 합계 {won(h['total_twoHouse_nonresident'])}")

print(f"\n{'═'*78}\n매도 순서 조합 — 총 양도세 상위 5개 (거주자 회복 전제)")
for k, v in ranked[:5]:
    print(f"  {won(v['total']):>16}  {v['first']}{v['firstYear']} ({won(v['firstTax'])}) → "
          f"{v['second']}{v['secondYear']} ({won(v['secondTax'])}{'·비과세' if v['secondExempt'] else ''})")
print("  ... 최악:")
k, v = ranked[-1]
print(f"  {won(v['total']):>16}  {v['first']}{v['firstYear']} → {v['second']}{v['secondYear']}")

print(f"\n증여(배우자) 시범한양: 증여세 {won(results['gift_spouse_sibeom']['afterCredit'])} + "
      f"취득세 {won(results['gift_spouse_sibeom']['acquisitionTax'])} = {won(results['gift_spouse_sibeom']['totalCost'])}")
print(f"\n→ {out.name} 저장 완료")
