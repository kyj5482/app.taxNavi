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

/* ── 양도세 ───────────────────────────────────────────────────── */
export function capitalGains(o) {
  const {
    salePrice, acqPrice, expenses, yearsHold, yearsReside, year,
    exempt1House = false, isOneHouse = false, resident = true, law = 'reform',
    overseasCredit = 0, surchargePp = 0, noLongDeduction = false,
    isUnitRight = false,       // 조합원입주권 양도 여부
  } = o;
  const gross = salePrice - acqPrice - expenses;
  const steps = [
    ['양도가액', salePrice], ['− 취득가액', -acqPrice],
    ['− 필요경비(취득세 등)', -expenses], ['= 양도차익', gross],
  ];
  let taxable;
  if (exempt1House && resident) {
    if (salePrice <= HIGH_VALUE) {
      return { tax: 0, local: 0, total: 0, taxable: 0, base: 0, rate: 0,
        rateLabel: '비과세', steps: [...steps, ['1세대 1주택 비과세 (양도가액 12억 이하)', 0]] };
    }
    const ratio = (salePrice - HIGH_VALUE) / salePrice;
    taxable = Math.floor(gross * ratio);
    steps.push([`× 고가주택 과세비율 (${((salePrice - HIGH_VALUE) / EOK).toFixed(2)}억/${(salePrice / EOK).toFixed(2)}억 = ${(ratio * 100).toFixed(1)}%)`, taxable]);
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
    const cap = law === 'reform' ? DEDUCTION_CAP[year] : undefined;
    if (cap && ded > cap) { ded = cap; detail += ` · 공제한도 ${cap / EOK}억 적용`; }
    steps.push([`− 장기${year >= 2028 && law === 'reform' ? '거주소득' : '보유특별'}공제 ${pct(rate)} (${detail})`, -ded]);
  }
  const income = taxable - ded;

  // 개편안 (9) 10년 이상 거주 + 양도가액 30억 이하 1주택 → 기본공제 2,500만
  let basic = 2_500_000;
  if (law === 'reform' && year >= 2027 && resident && isOneHouse
      && (yearsReside + overseasCredit) >= 10 && salePrice <= 3_000_000_000) {
    basic = 25_000_000;
    steps.push(['− 기본공제 (10년 이상 거주 1주택 · 개편안)', -basic]);
  } else steps.push(['− 기본공제', -basic]);

  const base = Math.max(income - basic, 0);
  steps.push(['= 과세표준', base]);
  let tax = basicRateTax(base);
  let label = bracketLabel(base);
  if (surchargePp) {
    tax += Math.floor(base * surchargePp / 100);
    label += ` + ${surchargePp}%p 중과`;
    steps.push([`× 기본세율 ${bracketLabel(base)} + 중과 ${surchargePp}%p`, tax]);
  } else steps.push([`× 기본세율 ${label} (누진공제 적용)`, tax]);
  const local = Math.floor(tax * 0.1);
  steps.push(['+ 지방소득세 10%', local]);
  steps.push(['= 총 예상세액', tax + local]);
  return { tax, local, total: tax + local, taxable, base, rate, rateLabel: label, steps };
}

/* ── 종합부동산세 ─────────────────────────────────────────────── */
const JB_RATES_CUR = [[300_000_000, .005], [600_000_000, .007], [1_200_000_000, .010],
  [2_500_000_000, .013], [5_000_000_000, .015], [9_400_000_000, .020], [Infinity, .027]];
const JB_RATES_REF = [[300_000_000, .005], [600_000_000, .007], [1_200_000_000, .013],
  [2_500_000_000, .015], [5_000_000_000, .020], [9_400_000_000, .027], [Infinity, .035]];

export function jongbuse(prices, { law = 'reform', residentHousePrice = 0 } = {}) {
  const list = prices.filter((p) => p > 0);
  const total = list.reduce((a, b) => a + b, 0);
  const n = list.length;
  let basic, fair, rates;
  if (law === 'current') {
    basic = n === 1 ? 1_200_000_000 : 900_000_000;
    fair = 0.60; rates = JB_RATES_CUR;
  } else {
    // 개편안 (3): 1주택 거주 14억 / 비거주 9억, 다주택 4억 + 5억×(거주주택/합계)
    basic = n === 1
      ? (residentHousePrice > 0 ? 1_400_000_000 : 900_000_000)
      : 400_000_000 + Math.floor(500_000_000 * (total ? residentHousePrice / total : 0));
    fair = 0.70; rates = JB_RATES_REF;
  }
  const base = Math.max(Math.floor((total - basic) * fair), 0);
  let tax = 0, prev = 0;
  for (const [cap, rate] of rates) {
    if (base <= prev) break;
    tax += Math.floor((Math.min(base, cap) - prev) * rate);
    prev = cap;
  }
  return { basic, fair, base, tax, total, count: n };
}

/* ── 재산세 (주택분, 개산) ─────────────────────────────────────── */
const PT_TIERS = [[60_000_000, .001, 0], [150_000_000, .0015, 30_000],
  [300_000_000, .0025, 180_000], [Infinity, .004, 630_000]];

export function propertyTax(officialPrice, { asLand = false } = {}) {
  if (officialPrice <= 0) return { total: 0, base: 0, main: 0, edu: 0, city: 0, kind: '없음' };
  if (asLand) {
    // 멸실 후 토지분 — 별도합산 0.2~0.4% 개산 (공정비율 70%)
    const base = Math.floor(officialPrice * 0.70);
    const main = Math.floor(base * 0.002);
    const edu = Math.floor(main * 0.2);
    const city = Math.floor(base * 0.0014);
    return { total: main + edu + city, base, main, edu, city, kind: '토지분' };
  }
  const base = Math.floor(officialPrice * 0.60);
  let main = 0;
  for (const [cap, rate, ded] of PT_TIERS) if (base <= cap) { main = Math.floor(base * rate - ded); break; }
  const edu = Math.floor(main * 0.2);
  const city = Math.floor(base * 0.0014);
  return { total: main + edu + city, base, main, edu, city, kind: '주택분' };
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

export const won = (v) => `${Math.round(v).toLocaleString('ko-KR')}`;
export const eok = (v) => `${(v / EOK).toFixed(2)}억`;
