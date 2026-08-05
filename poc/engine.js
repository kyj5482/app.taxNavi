/* taxNavi PoC 계산 엔진 — poc/calc.py 의 JS 포팅 (브라우저 실시간 재계산용)
 *
 * 규칙 출처: poc/law-archive/press/mofe-2026-tax-reform_2026-08-03/ (2026년 세제개편안)
 *            poc/law-archive/statutes/sodukse-sihaengryeong/ (소득세법 시행령 제36343호)
 * confidence: drafted — 원문 대조 검증 전. 지방소득세는 10% 단순 가산.
 */
export const EOK = 100_000_000;

/* ── 소득세 기본세율 (소득세법 §55) ───────────────────────────── */
const BRACKETS = [
  [14_000_000, 0.06, 0],
  [50_000_000, 0.15, 1_260_000],
  [88_000_000, 0.24, 5_760_000],
  [150_000_000, 0.35, 15_440_000],
  [300_000_000, 0.38, 19_940_000],
  [500_000_000, 0.40, 25_940_000],
  [1_000_000_000, 0.42, 35_940_000],
  [Infinity, 0.45, 65_940_000],
];
const HIGH_VALUE = 1_200_000_000; // 고가주택 기준 (양도가액 12억)

function basicRateTax(base) {
  if (base <= 0) return 0;
  for (const [cap, rate, ded] of BRACKETS) if (base <= cap) return Math.floor(base * rate - ded);
  return 0;
}
function bracketLabel(base) {
  for (const [cap, rate] of BRACKETS) if (base <= cap) return `${Math.round(rate * 100)}%`;
  return '45%';
}

/* ── 공제율 테이블 ─────────────────────────────────────────────
 * 개편안 (8)② 1세대1주택: ~'27 거주4%/보유4%(각 40%), '28 거주6%(60%)/보유2%(20%),
 *                          '29~ 거주8%(80%) · 보유공제 폐지
 * 개편안 (8)③ 다주택:     '28 거주2%(30%) vs 보유1%(15%) 중 높은 쪽, '29~ 거주만
 */
const ONE_HOUSE = {
  2026: { r: [0.04, 0.40], h: [0.04, 0.40] }, 2027: { r: [0.04, 0.40], h: [0.04, 0.40] },
  2028: { r: [0.06, 0.60], h: [0.02, 0.20] }, 2029: { r: [0.08, 0.80], h: [0, 0] },
  2030: { r: [0.08, 0.80], h: [0, 0] },
};
const MULTI = {
  2026: { r: [0, 0], h: [0.02, 0.30] }, 2027: { r: [0, 0], h: [0.02, 0.30] },
  2028: { r: [0.02, 0.30], h: [0.01, 0.15] }, 2029: { r: [0.02, 0.30], h: [0, 0] },
  2030: { r: [0.02, 0.30], h: [0, 0] },
};
const CURRENT = { one: { r: [0.04, 0.40], h: [0.04, 0.40] }, multi: { r: [0, 0], h: [0.02, 0.30] } };

/* 개편안 (12) 2주택 중과 한시완화 (%p) */
export const SURCHARGE_2H = { 2026: 0, 2027: 5, 2028: 10, 2029: 20, 2030: 20 };
/* 개편안 (8)④ 공제한도 */
const DEDUCTION_CAP = { 2028: 2_000_000_000, 2029: 1_000_000_000, 2030: 1_000_000_000 };

function deductionRate(yHold, yReside, year, isOne, law = 'reform') {
  if (yHold < 3) return [0, '보유 3년 미만 — 공제 없음'];
  let t = law === 'current'
    ? (isOne ? CURRENT.one : CURRENT.multi)
    : (isOne ? ONE_HOUSE : MULTI)[year] || ONE_HOUSE[2030];
  if (isOne && yReside < 2) {                       // 1주택 우대공제 기본요건 미충족
    t = law === 'current' ? CURRENT.multi : (MULTI[year] || MULTI[2030]);
    isOne = false;
  }
  const r = Math.min(Math.floor(yReside) * t.r[0], t.r[1]);
  const h = Math.min(Math.floor(yHold) * t.h[0], t.h[1]);
  if (!isOne && year >= 2028 && law === 'reform') {  // 개편안 (8)③ 높은 쪽 (합산 아님)
    return [Math.max(r, h), `거주 ${pct(r)} vs 보유 ${pct(h)} 중 높은 쪽`];
  }
  return [Math.min(r + h, 0.80),
    `거주 ${Math.floor(yReside)}년×${pct(t.r[0])}=${pct(r)} + 보유 ${Math.floor(yHold)}년×${pct(t.h[0])}=${pct(h)}`];
}
const pct = (v) => `${Math.round(v * 100)}%`;

/* ── 양도세 ─────────────────────────────────────────────────────
 * share: 지분비율. 부부공동명의 50%면 0.5.
 *   - 고가주택 12억 기준은 주택 전체 양도가액으로 판정 (지분이 아님)
 *   - 과세표준·누진세율·기본공제는 인별로 적용 → 지분 분할의 최대 효과
 *   - 공제한도(개편안 8④)는 인별 한도와 양도물건별 한도×지분 중 작은 쪽
 */
export function capitalGains(o) {
  const {
    salePrice, acqPrice, expenses, yearsHold, yearsReside, year,
    exempt1House = false, isOneHouse = false, resident = true, law = 'reform',
    overseasCredit = 0, surchargePp = 0, noLongDeduction = false,
    isUnitRight = false,       // 조합원입주권 양도 여부
    share = 1,                 // 이 납세자의 지분
  } = o;
  const housePrice = salePrice;              // 주택 전체 양도가액 (12억 판정 기준)
  const mySale = salePrice * share;
  const gross = Math.floor((salePrice - acqPrice - expenses) * share);
  const steps = share < 1
    ? [[`양도가액 (전체 ${(housePrice / EOK).toFixed(2)}억 × 지분 ${Math.round(share * 100)}%)`, mySale],
       ['− 취득가액 (지분)', -acqPrice * share],
       ['− 필요경비 (지분)', -expenses * share], ['= 양도차익 (지분)', gross]]
    : [['양도가액', salePrice], ['− 취득가액', -acqPrice],
       ['− 필요경비(취득세 등)', -expenses], ['= 양도차익', gross]];
  let taxable;
  if (exempt1House && resident) {
    if (housePrice <= HIGH_VALUE) {
      return { tax: 0, local: 0, total: 0, taxable: 0, base: 0, rate: 0, share,
        rateLabel: '비과세', steps: [...steps, ['1세대 1주택 비과세 (주택 전체 양도가액 12억 이하)', 0]] };
    }
    // 12억 판정은 주택 전체 기준 — 공동명의라도 지분가액으로 나눠 판정하지 않는다
    const ratio = (housePrice - HIGH_VALUE) / housePrice;
    taxable = Math.floor(gross * ratio);
    steps.push([`× 고가주택 과세비율 (${((housePrice - HIGH_VALUE) / EOK).toFixed(2)}억/${(housePrice / EOK).toFixed(2)}억 = ${(ratio * 100).toFixed(1)}%)`, taxable]);
  } else {
    taxable = gross;
    if (exempt1House && !resident) steps.push(['비거주자 → 1세대 1주택 비과세 배제 (전액 과세)', taxable]);
  }

  let rate = 0, detail = '', ded = 0;
  if (noLongDeduction) {
    detail = '중과 한시완화 적용 → 장기보유특별공제 배제';
    steps.push(['− 장기보유특별공제 (배제)', 0]);
  } else if (isUnitRight) {
    detail = '조합원입주권 — 관리처분인가 후 기간은 장특공제 대상 아님 (시행령 §166①)';
    [rate] = deductionRate(yearsHold, 0, year, false, law);
    ded = Math.floor(taxable * rate);
    steps.push([`− 장기보유특별공제 ${pct(rate)} (${detail})`, -ded]);
  } else {
    [rate, detail] = deductionRate(yearsHold, yearsReside + overseasCredit, year, isOneHouse && resident, law);
    ded = Math.floor(taxable * rate);
    // 개편안 (8)④: 인별 한도 + 양도물건별 한도(공동소유는 지분비율로 안분)
    const cap = law === 'reform' && DEDUCTION_CAP[year]
      ? Math.min(DEDUCTION_CAP[year], DEDUCTION_CAP[year] * share) : undefined;
    if (cap && ded > cap) {
      ded = cap;
      detail += ` · 공제한도 ${(cap / EOK).toFixed(0)}억 적용${share < 1 ? ` (양도물건별 ${DEDUCTION_CAP[year] / EOK}억 × 지분 ${Math.round(share * 100)}%)` : ''}`;
    }
    steps.push([`− 장기${year >= 2028 && law === 'reform' ? '거주소득' : '보유특별'}공제 ${pct(rate)} (${detail})`, -ded]);
  }
  const income = taxable - ded;

  // 개편안 (9) 10년 이상 거주 + 양도가액 30억 이하 1주택 → 기본공제 2,500만
  // 문답자료 p.49: "부부공동명의 주택의 경우에는 각각 2,500만원 공제" — 안분하지 않는다
  let basic = 2_500_000;
  if (law === 'reform' && year >= 2027 && resident && isOneHouse
      && (yearsReside + overseasCredit) >= 10 && housePrice <= 3_000_000_000) {
    basic = 25_000_000;
    steps.push([`− 기본공제 (10년 이상 거주 1주택 · 개편안)${share < 1 ? ' — 부부공동명의는 각자 전액' : ''}`, -basic]);
  } else steps.push([`− 기본공제 (인별 연 250만)`, -basic]);

  const base = Math.max(income - basic, 0);
  steps.push(['= 과세표준 (인별)', base]);
  let tax = basicRateTax(base);
  let label = bracketLabel(base);
  if (surchargePp) {
    tax += Math.floor(base * surchargePp / 100);
    label += ` + ${surchargePp}%p 중과`;
    steps.push([`× 기본세율 ${bracketLabel(base)} + 중과 ${surchargePp}%p`, tax]);
  } else steps.push([`× 기본세율 ${label} (누진공제 적용)`, tax]);
  const local = Math.floor(tax * 0.1);
  steps.push(['+ 지방소득세 10%', local]);
  steps.push([share < 1 ? '= 1인분 세액' : '= 총 예상세액', tax + local]);
  return { tax, local, total: tax + local, taxable, base, rate, rateLabel: label, share, steps };
}

/** 부부공동명의 양도 — 각 배우자를 인별로 계산해 합산.
 *  누진세율·기본공제가 인별로 적용되므로 단독명의보다 유리하다. */
export function capitalGainsJoint(o, shares = [0.5, 0.5]) {
  const parts = shares.map((s) => capitalGains({ ...o, share: s }));
  const total = parts.reduce((a, p) => a + p.total, 0);
  return { total, parts, rate: parts[0].rate, rateLabel: parts[0].rateLabel,
    solo: capitalGains({ ...o, share: 1 }) };
}

/* ── 종합부동산세 ───────────────────────────────────────────────
 * 개편안 (3) 기본공제 (종부법 §8①) — '27.1.1. 이후 납세의무 성립분
 *   1세대1주택자: 거주 14억 / 비거주 9억
 *   그 외:        4억 + (5억 × 거주주택 공시가격 ÷ 주택 공시가격 합계액)
 *     → 5억분은 "거주주택 가액 비중"만큼만 공제된다. 비거주면 4억이 상한.
 * 개편안 (4) 공정시장가액비율 (종부법 §8①·§13, 종부령 §2의4)
 *   3주택 이상 또는 조정대상지역 주택 보유자(1세대1주택자 제외):
 *     '26년 60% → '27년 70% → '28년 이후 80%
 *   그 외(1세대1주택자·지방 1·2주택·조정지역 1세대1주택): 70%
 * 개편안 (5) 세율 (종부법 §9①·②) — '27년은 중간세율, '28년 이후 주택수 구분 폐지
 * 개편안 (6) 1세대1주택 세액공제 (종부법 §9⑤⑧⑨)
 *   보유공제 → 거주공제 전환. '27년은 보유공제(거주공제의 1/2)와 거주공제 중 큰 쪽,
 *   '28년 이후는 거주공제만. 연령공제와 합산 최대 80%.
 *   금액한도 신설: '27년 800만원 → '28년 이후 600만원
 * 개편안 (7) 세부담 상한 150% → 200% (종부법 §10·§15)
 */
const JB_RATES_CUR   = [[300_000_000, .005], [600_000_000, .007], [1_200_000_000, .010],
  [2_500_000_000, .013], [5_000_000_000, .015], [9_400_000_000, .020], [Infinity, .027]];
const JB_RATES_CUR3  = [[300_000_000, .005], [600_000_000, .007], [1_200_000_000, .010],
  [2_500_000_000, .020], [5_000_000_000, .030], [9_400_000_000, .040], [Infinity, .050]];
/* '27년 중간세율 (2주택 이하) */
const JB_RATES_27    = [[300_000_000, .005], [600_000_000, .007], [1_200_000_000, .013],
  [2_500_000_000, .015], [5_000_000_000, .020], [9_400_000_000, .027], [Infinity, .035]];
/* '27년 3주택 이상 */
const JB_RATES_27_3  = [[300_000_000, .005], [600_000_000, .007], [1_200_000_000, .013],
  [2_500_000_000, .020], [5_000_000_000, .030], [9_400_000_000, .040], [Infinity, .050]];
/* '28년 이후 — 주택 수 구분 폐지, 단일 체계 */
const JB_RATES_28    = [[300_000_000, .005], [600_000_000, .007], [1_200_000_000, .013],
  [2_500_000_000, .020], [5_000_000_000, .030], [9_400_000_000, .040], [Infinity, .050]];

/** 1세대1주택 세액공제 한도 (개편안 6) */
const JB_CREDIT_CAP = { 2027: 8_000_000, 2028: 6_000_000, 2029: 6_000_000, 2030: 6_000_000 };
/** 거주/보유 기간별 공제율 (개편안 6) */
function jbPeriodCredit(years, kind) {
  //  reside      : 개편안 거주공제 20/40/50 (= 현행 보유공제와 동일한 표)
  //  holdCurrent : 현행 보유공제 20/40/50 (종부법 §9⑧)
  //  hold        : 개편안 '27년 보유공제 = 거주공제의 1/2 → 10/20/25
  const t = kind === 'hold' ? [[5, 0.10], [10, 0.20], [15, 0.25]]
    : [[5, 0.20], [10, 0.40], [15, 0.50]];
  let r = 0;
  for (const [y, v] of t) if (years >= y) r = v;
  return r;
}
/** 연령별 공제율 (현행 유지) */
function jbAgeCredit(age) {
  if (age >= 70) return 0.40;
  if (age >= 65) return 0.30;
  if (age >= 60) return 0.20;
  return 0;
}

/** 종부세 (주택분).
 *  prices: 이 납세자에게 귀속되는 주택별 공시가격(지분 반영 후)
 *  year: 연도별로 세율·공정비율이 다르므로 필수에 가깝다 (미지정 시 개편 최종안)
 *  residentHousePrice: 그 중 "거주하는 주택"의 공시가격 (지분 반영 후)
 *  houseCount: 주택 수 판정용. 공동명의는 지분이 아니라 물건 수로 센다(§154의2 취지)
 *  isOneHouseSpecial: 공동명의 1주택자가 1세대1주택자 특례를 신청한 경우
 *  adjustedArea: 조정대상지역 주택 보유 여부 (공정비율 80% 트랙 판정)
 *  age/holdYears/resideYears: 1세대1주택 세액공제 산정용
 */
export function jongbuse(prices, {
  law = 'reform', year = 2029, residentHousePrice = 0, houseCount = null,
  isOneHouseSpecial = false, notOneHouse = false, adjustedArea = false,
  age = 0, holdYearsVal = 0, resideYearsVal = 0, propertyTaxCredit = null,
  priorHoldingTax = 0, propertyTaxThisYear = 0,
} = {}) {
  const list = prices.filter((p) => p > 0);
  const total = list.reduce((a, b) => a + b, 0);
  const n = houseCount === null ? list.length : houseCount;
  // 공동명의 1주택을 각자 개별 납부하는 경우, 각 공유자는 1세대1주택자가 아니라
  // "그 외" 납세자로 취급된다 (문답자료 p.41: 거주 각 9억 / 비거주 각 4억).
  const isOne = isOneHouseSpecial || (n === 1 && !notOneHouse);
  const resides = residentHousePrice > 0;
  let basic, fair, rates, basicLabel;

  if (law === 'current') {
    basic = isOne ? 1_200_000_000 : 900_000_000;
    basicLabel = isOne ? '1세대1주택 12억' : '9억';
    fair = 0.60;
    rates = n >= 3 ? JB_RATES_CUR3 : JB_RATES_CUR;
  } else {
    if (isOne) {
      basic = resides ? 1_400_000_000 : 900_000_000;
      basicLabel = resides ? '1세대1주택 거주 14억' : '1세대1주택 비거주 9억';
    } else {
      // 4억은 무조건, 5억은 거주주택 가액 비중만큼만
      const ratio = total ? Math.min(residentHousePrice / total, 1) : 0;
      basic = 400_000_000 + Math.floor(500_000_000 * ratio);
      basicLabel = `4억 + 5억×${(ratio * 100).toFixed(1)}%(거주주택 비중)`;
    }
    /* 개편안 (4) 공정시장가액비율 (종부법 §13①②, 종부령 §2의4)
       원문(상세본 p.62 / 문답 p.41~42): "3주택 이상 또는 조정대상지역 주택 보유자
       (단, 1세대1주택자 제외)는 60% → 80%까지, 그 외는 60% → 70%까지 상향.
       (‘26)60% → (’27)70% → (‘28)80%"
       문답 p.42 각주: "2주택자가 조정지역 주택 1채, 비조정지역 주택 1채 소유한 경우 포함"
       → 조정대상지역 주택을 1채라도 가지고 있으면 80% 트랙이다.
       ※ 비거주자 여부는 80% 트랙 요건이 아니다 (원문에 없음). 거주 여부는 기본공제(§8①)의
         5억분 비중에만 영향을 준다. */
    const heavy = !isOne && (n >= 3 || adjustedArea);
    fair = year <= 2026 ? 0.60 : (heavy ? (year >= 2028 ? 0.80 : 0.70) : 0.70);
    rates = year <= 2027 ? (n >= 3 ? JB_RATES_27_3 : JB_RATES_27) : JB_RATES_28;
  }

  /* 개편안 (2) 과세대상 조정 (종부법 §7①) — 기본공제와 별개의 진입 문턱.
     1세대1주택자는 공시가 합계 14억 초과, 그 외는 9억 초과일 때만 납세의무가 성립한다.
     → 다주택 기본공제가 4억으로 줄어도 공시가 합계가 9억 이하면 아예 과세대상이 아니다. */
  const threshold = law === 'current' ? 0 : (isOne ? 1_400_000_000 : 900_000_000);
  const taxable = total > threshold;
  const base = taxable ? Math.max(Math.floor((total - basic) * fair), 0) : 0;
  let gross = 0, prev = 0;
  for (const [cap, rate] of rates) {
    if (base <= prev) break;
    gross += Math.floor((Math.min(base, cap) - prev) * rate);
    prev = cap;
  }
  /* 공제할 재산세액 (종부법 §9③, 종부령 §4의2) — 종부세 과세표준에 대응하는 재산세 상당액을
     산출세액에서 차감한다. 시행령 산식이 복잡해 개산치를 쓰되, 기재부 문답자료 p.44~45의
     사례 ①②③(‘26·’27·‘28년, 공시가 30억·50억) 4개 데이터포인트에 모두 정확히 들어맞는
     "과세표준 × 0.18%"로 캘리브레이션했다. 숫자를 직접 넘기면 그 값이 우선한다. */
  const ptc = propertyTaxCredit === null ? Math.floor(base * 0.0018) : propertyTaxCredit;
  const net = Math.max(gross - ptc, 0);

  // 1세대1주택 세액공제 (개편안 6) — 거주/보유 + 연령, 합계 80% 한도, 금액한도 신설
  let credit = 0, creditLabel = '', creditCapped = 0;
  if (isOne && (age >= 60 || holdYearsVal >= 5 || resideYearsVal >= 5)) {
    // 현행: 보유공제(20/40/50) + 연령공제, 금액한도 없음
    // 개편안: 거주공제로 전환. '27년은 보유공제(거주공제의 1/2)와 거주공제 중 큰 쪽,
    //         '28년 이후는 거주공제만. 금액한도 '27년 800만 → '28년 이후 600만 신설.
    const rPeriod = law === 'current'
      ? jbPeriodCredit(holdYearsVal, 'holdCurrent')
      : Math.max(jbPeriodCredit(resideYearsVal, 'reside'),
                 year <= 2027 ? jbPeriodCredit(holdYearsVal, 'hold') : 0);
    const rate = Math.min(rPeriod + jbAgeCredit(age), 0.80);
    credit = Math.floor(net * rate);
    const cap = law === 'current' ? Infinity : (JB_CREDIT_CAP[year] || 6_000_000);
    creditCapped = Math.min(credit, cap);
    creditLabel = `${Math.round(rate * 100)}%${credit > cap ? ` (금액한도 ${cap / 10_000}만원 적용, 한도 전 ${Math.round(credit / 10_000)}만원)` : ''}`;
  }
  const beforeCap = Math.max(net - creditCapped, 0);

  /* 개편안 (7) 세부담 상한 (종부법 §10·§15) — 직전연도 총 보유세상당액(재산세+종부세)의
     150%(’27년 이후 200%)를 넘는 부분은 부과하지 않는다. priorHoldingTax(직전연도 보유세)를
     주지 않으면 상한을 적용하지 않는다(= 상한 판정 불가로 두고 그대로 부과). */
  const capRate = law === 'current' ? 1.50 : (year >= 2027 ? 2.00 : 1.50);
  let burdenCap = null, capped = false;
  let tax = beforeCap;
  if (priorHoldingTax > 0) {
    burdenCap = Math.max(Math.floor(priorHoldingTax * capRate) - propertyTaxThisYear, 0);
    if (beforeCap > burdenCap) { tax = burdenCap; capped = true; }
  }
  const nongteug = Math.floor(tax * 0.20);        // 농어촌특별세 20% (농특법 §5①)
  return { basic, basicLabel, fair, base, gross, propertyTaxCredit: ptc, net, threshold, taxable,
    credit, creditCapped, creditLabel, beforeCap, capRate, burdenCap, capped,
    tax, nongteug, withSurtax: tax + nongteug,
    total, count: n, isOne, resides, year, law };
}

/** 부부공동명의 종부세.
 *  종부세는 인별 과세(종부세법 §7①)이고 공동소유주택은 각자가 그 주택을 소유한 것으로 보므로
 *  (시행령 §154의2) 각자 자기 지분에 대해 기본공제를 받는다.
 *
 *  ★ 개편안의 결정적 디테일: 다주택 기본공제 9억이 "4억 + 5억×거주주택 비중"으로 쪼개지면서
 *    - 4억은 각자 무조건 → 부부 합 8억
 *    - 5억분은 거주주택 가액 비중만큼만 → 비거주면 0
 *    즉 비거주 부부공동명의 2주택은 합 8억이 상한이고, 예전의 "각 9억 = 합 18억"이 아니다.
 *
 *  공동명의 1주택은 문답자료 p.41에 따라 두 방식 중 유리한 쪽을 선택할 수 있다:
 *    - 부부 개별 납부:      거주 각 9억 / 비거주 각 4억   ← 1세대1주택자가 아니라 "그 외"로 취급
 *    - 1세대1주택자 특례신청: 거주 14억 / 비거주 9억 (지분 합산해 1인이 납부)
 */
export function jongbuseJoint(prices, {
  law = 'reform', year = 2029, residentHousePrice = 0, shares = [0.5, 0.5],
  adjustedArea = false, age = 0, holdYearsVal = 0, resideYearsVal = 0,
  propertyTaxCredit = null, oneHouseSpecial = 'auto',
  priorHoldingTax = 0, propertyTaxThisYear = 0,
} = {}) {
  const houseCount = prices.filter((p) => p > 0).length;   // 물건 수 (지분과 무관)
  const mk = (s) => jongbuse(prices.map((p) => p * s), {
    law, year, residentHousePrice: residentHousePrice * s, houseCount,
    // 공유자 개별 납부는 1세대1주택자 판정을 받지 못한다 (문답 p.41)
    notOneHouse: shares.length > 1,
    adjustedArea, age, holdYearsVal, resideYearsVal,
    // null이면 각 공유자의 과세표준에서 자동 개산 (곱하면 null이 0이 되므로 분기 필요)
    propertyTaxCredit: propertyTaxCredit === null ? null : propertyTaxCredit * s,
    // 세부담 상한도 인별로 판정한다 (종부세가 인별 과세이므로)
    priorHoldingTax: priorHoldingTax * s, propertyTaxThisYear: propertyTaxThisYear * s,
  });
  const parts = shares.map(mk);
  const separate = {
    parts, tax: parts.reduce((a, p) => a + p.tax, 0),
    withSurtax: parts.reduce((a, p) => a + p.withSurtax, 0),
    basic: parts.reduce((a, p) => a + p.basic, 0),
    base: parts.reduce((a, p) => a + p.base, 0),
    gross: parts.reduce((a, p) => a + p.gross, 0),
    fair: parts[0].fair, count: houseCount, mode: 'separate',
    capped: parts.some((p) => p.capped), capRate: parts[0].capRate,
  };
  // 공동명의 1주택 → 1세대1주택자 특례 신청 가능 (지분을 합쳐 1인이 납부)
  let special = null;
  if (houseCount === 1 && shares.length > 1) {
    const whole = jongbuse(prices, {
      law, year, residentHousePrice, houseCount: 1, isOneHouseSpecial: true,
      adjustedArea, age, holdYearsVal, resideYearsVal, propertyTaxCredit,
      priorHoldingTax, propertyTaxThisYear,
    });
    special = { ...whole, parts: [whole], mode: 'special' };
  }
  let chosen = separate;
  if (special && (oneHouseSpecial === true
      || (oneHouseSpecial === 'auto' && special.tax < separate.tax))) chosen = special;
  const solo = jongbuse(prices, {
    law, year, residentHousePrice, houseCount, adjustedArea,
    age, holdYearsVal, resideYearsVal, propertyTaxCredit,
    priorHoldingTax, propertyTaxThisYear,
  });
  return { ...chosen, separate, special, solo,
    total: prices.filter((p) => p > 0).reduce((a, b) => a + b, 0) };
}

/* ── 재산세 (주택분, 개산) ─────────────────────────────────────── */
const PT_TIERS = [[60_000_000, .001, 0], [150_000_000, .0015, 30_000],
  [300_000_000, .0025, 180_000], [Infinity, .004, 630_000]];

/** 재산세 (주택분·토지분 개산).
 *  priorOfficialPrice: 직전연도 공시가격. 주면 지방세법 §110③ 주택 과세표준상한제를 적용한다.
 *
 *  ★ 공시가가 급등하는 시나리오에서는 이 상한이 세액을 지배한다 (지방세법 §110③, 시행령 §109의2):
 *      과세표준상한액 = 직전연도 공시가 × 공정비율 + (당해 공시가 × 공정비율 × 5%)
 *    즉 주택 재산세 과세표준은 전년 대비 "당해 과세표준의 5%"까지만 오른다. 공시가가 30%
 *    올라도 과세표준은 그만큼 오르지 않으므로, 상한을 넣지 않으면 재산세를 크게 과대계상한다.
 *    (참고: 지방세법 §122 세부담상한 150%는 단서에서 주택을 제외하므로 주택엔 §110③만 적용된다)
 */
export function propertyTax(officialPrice, { asLand = false, priorOfficialPrice = null } = {}) {
  if (officialPrice <= 0) {
    return { total: 0, base: 0, main: 0, edu: 0, city: 0, kind: '없음', baseCapped: false, baseCap: null };
  }
  if (asLand) {
    // 멸실 후 토지분 — 별도합산 0.2~0.4% 개산 (공정비율 70%). 토지는 과세표준상한제 대상이 아니다
    const base = Math.floor(officialPrice * 0.70);
    const main = Math.floor(base * 0.002);
    const edu = Math.floor(main * 0.2);
    const city = Math.floor(base * 0.0014);
    return { total: main + edu + city, base, main, edu, city, kind: '토지분',
      baseCapped: false, baseCap: null };
  }
  const FAIR = 0.60;
  const raw = Math.floor(officialPrice * FAIR);
  // 지방세법 §110③ 과세표준상한액 (시행령 §109의2 — 상한율 5%)
  let baseCap = null, baseCapped = false, base = raw;
  if (priorOfficialPrice !== null && priorOfficialPrice > 0) {
    baseCap = Math.floor(priorOfficialPrice * FAIR) + Math.floor(officialPrice * FAIR * 0.05);
    if (raw > baseCap) { base = baseCap; baseCapped = true; }
  }
  let main = 0;
  for (const [cap, rate, ded] of PT_TIERS) if (base <= cap) { main = Math.floor(base * rate - ded); break; }
  const edu = Math.floor(main * 0.2);
  const city = Math.floor(base * 0.0014);
  return { total: main + edu + city, base, rawBase: raw, main, edu, city, kind: '주택분',
    baseCapped, baseCap, fair: FAIR };
}

/** 공시가격 상승률을 적용한 연도별 공시가.
 *  공시가격은 매년 1월 1일 기준으로 새로 결정되므로 base 연도 대비 복리로 오른다. */
export function officialAt(basePrice, baseYear, year, growth) {
  if (!growth || year <= baseYear) return basePrice;
  return Math.round(basePrice * (1 + growth) ** (year - baseYear));
}

/* ── 재건축 단계 정의 ──────────────────────────────────────────── */
export const STAGES = [
  { id: 'none', label: '해당 없음 / 조합설립 전', isHouse: true, isUnitRight: false,
    landTax: false, jongbuseIncluded: true, sangsaengAlive: true,
    desc: '세제상 변화 없음. 안전진단·정비구역 지정·조합설립인가까지 일반 주택과 동일하게 판정된다.' },
  { id: 'approval', label: '사업시행인가', isHouse: true, isUnitRight: false,
    landTax: false, jongbuseIncluded: true, sangsaengAlive: true,
    desc: '아직 주택이다. 다만 이 날 이후 취득한 대체주택은 시행령 §156의2⑤에 따라 1세대1주택으로 보아 비과세 가능(1년 이상 거주 등 요건).' },
  { id: 'management', label: '관리처분계획인가', isHouse: false, isUnitRight: true,
    landTax: false, jongbuseIncluded: true, sangsaengAlive: false,
    desc: '주택이 조합원입주권으로 전환된다. 양도차익은 시행령 §166①에 따라 인가일 전후로 나눠 산정하고, 인가일 이후 기간은 장기보유특별공제 대상이 아니다. 멸실 전까지는 주택분 재산세가 유지된다.' },
  { id: 'demolished', label: '이주·멸실', isHouse: false, isUnitRight: true,
    landTax: true, jongbuseIncluded: false, sangsaengAlive: false,
    desc: '건물이 없어져 주택분 재산세가 토지분으로 바뀌고 주택 종부세 합산에서 빠진다. 임차인 퇴거로 임대가 종료되므로 상생임대 경로는 완전히 닫힌다.' },
  { id: 'completed', label: '준공·입주 (신축주택)', isHouse: true, isUnitRight: false,
    landTax: false, jongbuseIncluded: true, sangsaengAlive: false, priceMultiplier: 1.8,
    desc: '신축주택 취득. 양도차익은 시행령 §166②에 따라 기존건물분과 청산금분을 나눠 산정하고 보유기간은 기존주택 취득일부터 통산한다. 추가분담금은 필요경비에 가산되며 공시가 급등으로 보유세가 크게 오른다.' },
];
export const stageById = (id) => STAGES.find((s) => s.id === id) || STAGES[0];

/** 연도별 재건축 단계 스케줄(예: {2027:'management', 2028:'demolished'})에서
 *  해당 연도에 유효한 단계를 구한다 (가장 최근에 도달한 단계). */
export function stageAt(schedule, year) {
  let cur = STAGES[0];
  for (const y of Object.keys(schedule).map(Number).sort((a, b) => a - b)) {
    if (y <= year && schedule[y]) cur = stageById(schedule[y]);
  }
  return cur;
}

export function holdYears(acqDate, year, month = 7) {
  const [y, m] = acqDate.split('-').map(Number);
  return (year - y) + (month - m) / 12;
}

/* ── 증여세 (상증법 §53, §56, §57, §69) ──────────────────────────
 * 증여재산공제 (10년 합산): 배우자 6억 / 성년 직계비속 5,000만 / 미성년 2,000만
 * 세대생략 할증 30% (§57) — 조부모→손자녀. 부모→자녀는 해당 없음.
 */
export const GIFT_TIERS = [
  [100_000_000, 0.10, 0], [500_000_000, 0.20, 10_000_000],
  [1_000_000_000, 0.30, 60_000_000], [3_000_000_000, 0.40, 160_000_000],
  [Infinity, 0.50, 460_000_000],
];
export const GIFT_DEDUCTION = { spouse: 600_000_000, adultChild: 50_000_000, minorChild: 20_000_000 };

/** 만 나이 (증여일 기준). 미성년 = 만 19세 미만 */
export function ageAt(birthYear, onDate, birthMonth = 6) {
  const [y, m] = onDate.split('-').map(Number);
  return (y - birthYear) - (m < birthMonth ? 1 : 0);
}

/** 증여세 계산.
 *  value: 증여재산가액(지분가액), deduction: 증여재산공제,
 *  generationSkip: 세대생략 할증 30% 여부, acqTaxRate: 무상취득 취득세율 */
export function giftTax(value, { deduction = 0, generationSkip = false, acqTaxRate = 0.035 } = {}) {
  const base = Math.max(value - deduction, 0);
  let gross = 0;
  for (const [cap, rate, ded] of GIFT_TIERS) if (base <= cap) { gross = Math.max(Math.floor(base * rate - ded), 0); break; }
  const surcharge = generationSkip ? Math.floor(gross * 0.30) : 0;
  const beforeCredit = gross + surcharge;
  const credit = Math.floor(beforeCredit * 0.03);           // 신고세액공제 3% (§69)
  const giftDue = beforeCredit - credit;
  const acqTax = Math.floor(value * acqTaxRate);            // 무상취득 취득세 3.5% (지방세법 §11①2)
  return { value, deduction, base, gross, surcharge, credit, giftDue, acqTax,
    total: giftDue + acqTax, effectiveRate: value ? (giftDue + acqTax) / value : 0 };
}

/** 지분을 여러 수증자에게 쪼개 증여할 때의 합계.
 *  recipients: [{label, value, deduction, generationSkip}] */
export function giftSplit(recipients) {
  const parts = recipients.map((r) => ({ ...r, calc: giftTax(r.value, r) }));
  return { parts, total: parts.reduce((a, p) => a + p.calc.total, 0),
    giftDue: parts.reduce((a, p) => a + p.calc.giftDue, 0),
    acqTax: parts.reduce((a, p) => a + p.calc.acqTax, 0),
    value: parts.reduce((a, p) => a + p.value, 0) };
}

export const won = (v) => `${Math.round(v).toLocaleString('ko-KR')}`;
export const eok = (v) => `${(v / EOK).toFixed(2)}억`;
