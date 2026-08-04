#!/usr/bin/env python3
"""PoC 계산기: 2026년 세제개편안(2026-08-03 발표) 반영 양도세/보유세 시나리오.

규칙 출처: poc/law-archive/press/mofe-2026-tax-reform_2026-08-03/ (상세본·문답자료)
           poc/law-archive/statutes/sodukse-sihaengryeong/... (소득세법 시행령 현행)
모든 금액은 원 단위 정수. confidence: drafted (원문 대조 검증 전).
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
# 현행(개편 전) 비교용: 1주택 보유·거주 각 연4% 최대40%, 다주택 보유 연2% 최대30%
CURRENT_LAW = {
    "one_house": {"residence": (0.04, 0.40), "holding": (0.04, 0.40)},
    "multi": {"residence": (0.00, 0.00), "holding": (0.02, 0.30)},
}


def deduction_rate(years_hold, years_reside, year, is_one_house, law="reform"):
    """공제율 산정. 기본요건: 3년 이상 보유(1주택은 +2년 이상 거주)."""
    if years_hold < 3:
        return 0.0, "보유 3년 미만 — 공제 없음"
    if law == "current":
        t = CURRENT_LAW["one_house"] if is_one_house else CURRENT_LAW["multi"]
    else:
        t = (ONE_HOUSE_TABLE if is_one_house else MULTI_HOUSE_TABLE).get(year, ONE_HOUSE_TABLE[2030])
    if is_one_house and years_reside < 2:
        # 1세대 1주택 우대공제 기본요건 미충족 → 일반(다주택) 공제표 적용
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
                  law="reform", long_resident_years=0, surcharge_pp=0):
    """양도세 계산. exempt_1house=True면 1세대1주택 비과세(고가주택 초과분만 과세)."""
    gross = sale_price - acq_price - expenses
    steps = [
        ("양도가액", sale_price), ("− 취득가액", -acq_price),
        ("− 필요경비", -expenses), ("= 양도차익", gross),
    ]
    if exempt_1house and resident:
        if sale_price <= HIGH_VALUE_THRESHOLD:
            return {"tax": 0, "total": 0, "steps": steps + [("1세대 1주택 비과세 (12억 이하)", 0)],
                    "rate_label": "비과세", "deduction_rate": 0}
        ratio = (sale_price - HIGH_VALUE_THRESHOLD) / sale_price
        taxable = int(gross * ratio)
        steps.append((f"× 고가주택 과세비율 ({(sale_price-HIGH_VALUE_THRESHOLD)//EOK}억/{sale_price//EOK}억 = {ratio:.1%})", taxable))
    else:
        taxable = gross
        if exempt_1house and not resident:
            steps.append(("비거주자 → 1세대 1주택 비과세 배제 (전액 과세)", taxable))

    rate, detail = deduction_rate(years_hold, years_reside + long_resident_years,
                                  year, is_one_house and resident, law)
    ded = int(taxable * rate)
    steps.append((f"− 장기{'거주소득' if year >= 2028 and law=='reform' else '보유특별'}공제 {rate:.0%} ({detail})", -ded))
    income = taxable - ded

    # 개편안 (9): 10년 이상 거주 + 양도가액 30억 이하 1세대1주택 → 기본공제 2,500만원
    #             ('27.1.1. 이후 양도분, 비거주자 적용 제외)
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
        tax = int(base * (0 if base <= 0 else surcharge_pp / 100)) + tax
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
# 사용자 자산 (가정값 — 실제 금액으로 교체 필요)
# ════════════════════════════════════════════════════════════════
GWACHEON = {
    "name": "과천자이 25평 A형",
    "acq_date": "2022-01-20",       # 분양 잔금·입주 (가정)
    "acq_price": 700_000_000,
    "expenses": 20_000_000,
    "sale_price": 1_600_000_000,
    "reside_from": "2022-01-20", "reside_to": "2024-07-05",
    "reside_years": 2.46,           # 입주 ~ 해외 출국
    "adjusted_at_acq": True,        # 과천: 2016-11 조정대상지역 지정 → 2023-01-05 해제
}
SIBEOM = {
    "name": "분당 시범한양 14평형",
    "contract_date": "2022-05-20",
    "acq_date": "2022-09-15",       # 잔금
    "acq_price": 750_000_000,
    "expenses": 15_000_000,
    "sale_price": 860_000_000,
    "reside_years": 0.0,
    "adjusted_at_acq": True,        # 성남 분당: 조정대상지역 (2023-01-05 해제)
}


def hold_years(acq_date, year, month=7):
    y, m, _ = map(int, acq_date.split("-"))
    return (year - y) + (month - m) / 12


results = {"generatedFor": "2026-08-04", "assumptions": {
    "gwacheon_sale_price": GWACHEON["sale_price"], "gwacheon_acq_price": GWACHEON["acq_price"],
    "sibeom_sale_price": SIBEOM["sale_price"], "sibeom_acq_price": SIBEOM["acq_price"],
    "note": "양도가액·취득가액은 예시 가정값. 실제 계약금액으로 교체 시 세액이 달라짐. 동일 양도가액을 유지해 '세법 변화 효과'만 분리 비교."}}

# ── 시나리오 1: 과천자이 — 1주택 + 거주자 상태, 연도별 ────────────
scen = []
for year in (2027, 2028, 2029):
    # 개편안 (18): 해외 근무상 형편 출국 → 비거주기간을 거주기간으로 인정 (최장 3년),
    #              공제율 산정에만 적용. '28.1.1. 이후 양도분부터.
    bonus = 3.0 if year >= 2028 else 0.0
    r = capital_gains(GWACHEON["sale_price"], GWACHEON["acq_price"], GWACHEON["expenses"],
                      hold_years(GWACHEON["acq_date"], year), GWACHEON["reside_years"],
                      year, exempt_1house=True, is_one_house=True, resident=True,
                      law="reform", long_resident_years=bonus)
    cur = capital_gains(GWACHEON["sale_price"], GWACHEON["acq_price"], GWACHEON["expenses"],
                        hold_years(GWACHEON["acq_date"], year), GWACHEON["reside_years"],
                        year, exempt_1house=True, is_one_house=True, resident=True, law="current")
    scen.append({"year": year, "reform": r, "current": cur, "overseasCredited": bonus})
results["gwacheon_1house_resident"] = scen

# ── 시나리오 2: 과천자이 — 비거주자(주재원 상태) 양도 ─────────────
nr = capital_gains(GWACHEON["sale_price"], GWACHEON["acq_price"], GWACHEON["expenses"],
                   hold_years(GWACHEON["acq_date"], 2027), GWACHEON["reside_years"], 2027,
                   exempt_1house=True, is_one_house=True, resident=False)
results["gwacheon_nonresident_2027"] = nr

# ── 시나리오 3: 과천자이 — 2주택 상태(현재) 양도 ──────────────────
two = capital_gains(GWACHEON["sale_price"], GWACHEON["acq_price"], GWACHEON["expenses"],
                    hold_years(GWACHEON["acq_date"], 2027), GWACHEON["reside_years"], 2027,
                    exempt_1house=False, is_one_house=False, resident=False)
results["gwacheon_two_house_2027"] = two

# ── 시나리오 4: 시범한양 — 2주택 상태 과세 양도 (연도별) ──────────
sib = []
for year in (2027, 2028, 2029):
    r = capital_gains(SIBEOM["sale_price"], SIBEOM["acq_price"], SIBEOM["expenses"],
                      hold_years(SIBEOM["acq_date"], year), 0, year,
                      exempt_1house=False, is_one_house=False, resident=False)
    sib.append({"year": year, "reform": r})
results["sibeom_taxed"] = sib

# ── 시나리오 5: 시범한양 — 1주택 + 상생임대 거주요건 면제 비과세 ──
# 상생임대차계약 2026-07 체결·개시, 2년 → 2028-07 종료
# 개편안 (16): '27.1.1. 이후 종료 → 계약종료 후 1년이 되는 날과 '29.12.31. 중 빠른 날
sib_ex = capital_gains(SIBEOM["sale_price"], SIBEOM["acq_price"], SIBEOM["expenses"],
                       hold_years(SIBEOM["acq_date"], 2029), 0, 2029,
                       exempt_1house=True, is_one_house=True, resident=True)
results["sibeom_sangsaeng_exempt"] = {"deadline": "2029-07-31", **sib_ex}

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
            "totalOfficial": total}


OFFICIAL = [1_100_000_000, 600_000_000]  # 과천자이, 시범한양 공시가격 가정
results["jongbuse"] = {
    "official": OFFICIAL,
    "current_2026": jongbuse(OFFICIAL, 2026, resident_house_price=0, law="current"),
    "reform_2027_no_residence": jongbuse(OFFICIAL, 2027, resident_house_price=0),
    "reform_2027_if_living_in_gwacheon": jongbuse(OFFICIAL, 2027, resident_house_price=OFFICIAL[0]),
    "reform_2027_one_house_resident": jongbuse([OFFICIAL[0]], 2027, one_house=True, resident_house_price=OFFICIAL[0]),
}

# ── 증여 (배우자) 비교 ────────────────────────────────────────────
def gift_spouse(value, deduction=600_000_000):
    base = max(value - deduction, 0)
    tiers = [(100_000_000, .10, 0), (500_000_000, .20, 10_000_000),
             (1_000_000_000, .30, 60_000_000), (3_000_000_000, .40, 160_000_000),
             (float("inf"), .50, 460_000_000)]
    for cap, rate, ded in tiers:
        if base <= cap:
            tax = int(base * rate - ded)
            break
    credit = int(tax * 0.03)  # 신고세액공제 3%
    return {"value": value, "deduction": deduction, "base": base,
            "tax": tax, "afterCredit": tax - credit,
            "acquisitionTax": int(value * 0.035)}


results["gift_spouse_sibeom"] = gift_spouse(SIBEOM["sale_price"])
results["gift_spouse_gwacheon"] = gift_spouse(GWACHEON["sale_price"])

# ── 상속 (참고) ───────────────────────────────────────────────────
estate_total = GWACHEON["sale_price"] + SIBEOM["sale_price"]
results["inheritance_reference"] = {
    "estateTotal": estate_total,
    "deductionAssumed": 1_000_000_000,  # 일괄공제 5억 + 배우자공제 최소 5억
    "taxBase": estate_total - 1_000_000_000,
    "note": "상속은 선택 가능한 절세 수단이 아님 — 자산 규모가 상속세 과세구간에 있음을 확인하는 용도",
}

out = Path(__file__).parent / "scenario-results.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), "utf-8")


def won(v):
    return f"{v:,}원"


print("═" * 72)
print("과천자이 — 1주택 + 거주자(귀국) 상태 양도, 동일 양도가액 16억 가정")
print("═" * 72)
for s in scen:
    print(f"  {s['year']}년: 개편안 {won(s['reform']['total']):>16}  (공제율 {s['reform']['deduction_rate']:.0%}"
          f"{', 해외 비거주 3년 거주인정' if s['overseasCredited'] else ''})"
          f"   현행유지 시 {won(s['current']['total'])}")
print(f"\n  ⚠ 비거주자 상태로 2027년 양도: {won(nr['total'])}  (비과세 배제)")
print(f"  ⚠ 2주택 상태로 2027년 양도  : {won(two['total'])}")
print(f"\n{'═'*72}\n시범한양 — 2주택 상태 과세 양도")
for s in sib:
    print(f"  {s['year']}년: {won(s['reform']['total']):>16} (공제율 {s['reform']['deduction_rate']:.0%})")
print(f"\n  1주택 + 상생임대 거주요건 면제 비과세 (기한 2029-07): {won(sib_ex['total'])}")
print(f"\n{'═'*72}\n종합부동산세 (공시가 합계 {sum(OFFICIAL)//EOK}억)")
j = results["jongbuse"]
print(f"  현행 2026년           : {won(j['current_2026']['tax']):>14}  (기본공제 {j['current_2026']['basicDeduction']//EOK}억, 공정비율 60%)")
print(f"  개편안 2027년 (비거주): {won(j['reform_2027_no_residence']['tax']):>14}  (기본공제 {j['reform_2027_no_residence']['basicDeduction']//EOK}억, 공정비율 70%)")
print(f"  개편안 2027년 (과천 거주): {won(j['reform_2027_if_living_in_gwacheon']['tax']):>14}  (기본공제 {j['reform_2027_if_living_in_gwacheon']['basicDeduction']/EOK:.1f}억)")
print(f"  개편안 2027년 (1주택·거주): {won(j['reform_2027_one_house_resident']['tax']):>14}  (기본공제 {j['reform_2027_one_house_resident']['basicDeduction']//EOK}억)")
print(f"\n증여(배우자, 시범한양 8.6억): 증여세 {won(results['gift_spouse_sibeom']['afterCredit'])} + 취득세 {won(results['gift_spouse_sibeom']['acquisitionTax'])}")
print(f"\n→ {out.name} 저장 완료")
