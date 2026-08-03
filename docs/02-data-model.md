# 02. 데이터 모델 — 시계열 이벤트 기반

## 설계 원칙

1. **이벤트 소싱**: 사용자 데이터의 원본(source of truth)은 불변 이벤트의 append-only 로그다. "현재 보유 주택 수", "보유 기간" 같은 상태는 저장하지 않고 타임라인 재생으로 파생한다.
2. **사실과 판정의 분리**: 이벤트에는 법적 판정(비과세 여부 등)을 기록하지 않는다. 판정은 규칙 엔진의 출력이다.
3. **날짜가 여러 개인 사건을 정직하게 모델링**: 부동산 취득은 계약일·중도금일·잔금일·등기일이 모두 세법상 의미를 가질 수 있으므로 별도 필드로 보존한다.
4. **가상 이벤트(시나리오)와 실제 이벤트의 동형성**: 시나리오는 "실제 타임라인 + 가상 이벤트"로 구성한다. 같은 스키마를 쓰므로 계산 엔진은 구분할 필요가 없다.

## 엔티티 개요

```
UserProfile ─┬─ HouseholdMember*        (세대 구성)
             ├─ Asset* ── Event*        (자산과 그 이력)
             ├─ Account*                (증권/절세 계좌)
             └─ Scenario* ── VirtualEvent*
```

### UserProfile / HouseholdMember

```typescript
interface UserProfile {
  id: string;
  birthDate: ISODate;          // 종부세 연령공제, 연금계좌 수령 요건
  members: HouseholdMember[];  // 1세대 판정용
}

interface HouseholdMember {
  id: string;
  relation: 'spouse' | 'child' | 'parent' | 'other';
  birthDate: ISODate;
  separateHouseholdFrom?: ISODate;  // 세대 분리 시점 (분리도 이벤트지만 단순화)
}
```

### Asset — 자산 원장

```typescript
type Asset = RealEstateAsset | SecurityAsset;

interface RealEstateAsset {
  id: string;
  kind: 'house' | 'officetel' | 'presale_right' | 'membership' | 'land' | 'commercial';
  // presale_right(분양권), membership(입주권)은 주택 수 포함 규칙이 취득 시점에 따라 달라 별도 kind
  name: string;                 // 사용자 표시명
  regionCode: string;           // 법정동 코드 — 조정대상지역 판정의 조인 키
  area: { exclusive: number };  // 전용면적 (㎡) — 취득세 감면, 소형주택 특례
  ownershipShare: number;       // 지분율 0~1 (공동명의)
  ownerMemberId: string;        // 명의자 (본인 or 세대원)
}

interface SecurityAsset {
  id: string;
  ticker: string;
  market: 'KRX' | 'US' | 'JP' | 'HK' | 'ETC';
  securityType: 'stock' | 'etf_domestic' | 'etf_overseas_listed' | 'etf_domestic_overseas'
              | 'fund' | 'bond';
  // 국내상장 해외지수 ETF(etf_domestic_overseas)는 매매차익이 배당소득 과세 — 유형 구분이 곧 세제 구분
  currency: 'KRW' | 'USD' | 'JPY' | 'HKD' | 'ETC';
  accountId: string;            // 어느 계좌에 있는가 (ISA 여부가 과세를 좌우)
}

interface Account {
  id: string;
  type: 'general' | 'isa' | 'pension_savings' | 'irp' | 'dc';
  isaSubtype?: 'general' | 'seomin' | 'farmer';  // 비과세 한도 차이
  broker: string;
  openedAt: ISODate;            // ISA 의무가입기간 기산일
  maturityAt?: ISODate;         // ISA 만기
  costBasisMethod: 'moving_average' | 'fifo';    // 증권사별 취득원가 방식
}
```

### Event — 시계열 이벤트 (핵심)

```typescript
interface BaseEvent {
  id: string;
  assetId?: string;      // 자산 귀속 이벤트
  accountId?: string;    // 계좌 귀속 이벤트 (납입 등)
  occurredAt: ISODate;   // 대표 발생일 (정렬 키)
  recordedAt: ISODateTime;
  note?: string;
  attachments?: string[];  // 계약서 사진 등 (로컬 저장, 후순위)
}
```

#### 부동산 이벤트

| type | 주요 필드 | 세법상 의미 |
|---|---|---|
| `re.purchase_contract` | contractDate, price, downPayment | **계약일 경과규정** 판정 기준 |
| `re.interim_payment` | date, amount | 분양 중도금 (자금 흐름) |
| `re.balance_payment` | date, amount | **잔금일 = 원칙적 취득일**. 취득세·보유기간 기산 |
| `re.registration` | date | 등기일 (잔금 전 등기 시 취득일이 됨) |
| `re.acquisition_cost` | date, amount, category | 취득 부대비용·자본적 지출 (양도세 필요경비) |
| `re.move_in` / `re.move_out` | date | 거주기간 산정 (비과세·장특공제 거주요건) |
| `re.lease_contract` | startDate, endDate, deposit, monthlyRent, isRenewal | 임대소득, 간주임대료, 상생임대인 요건 |
| `re.lease_terminate` | date | |
| `re.sale_contract` | contractDate, price | 양도 계약 |
| `re.sale_balance` | date, price | **양도일**. 양도세 계산 트리거 |
| `re.inheritance` / `re.gift_received` | date, appraisedValue, from | 상속·증여 취득 (특례 판정) |
| `re.conversion` | date, from: 'presale_right', to: 'house' | 분양권 → 주택 전환 (사용승인/입주) |
| `re.appraisal` | date, officialPrice, marketPriceEstimate | 연도별 공시가격·시세 기록 (보유세 입력) |

#### 주식·계좌 이벤트

| type | 주요 필드 | 세법상 의미 |
|---|---|---|
| `sec.buy` | tradeDate, settleDate, qty, price, currency, fxRate, fee | 취득원가. 해외주식은 **결제일 환율** |
| `sec.sell` | tradeDate, settleDate, qty, price, currency, fxRate, fee | 양도차익 실현. 대주주 판정 |
| `sec.dividend` | payDate, amount, currency, withheldTax, foreignTax | 배당소득, 금융소득종합과세 누계, 외국납부세액 |
| `sec.split` / `sec.merge` | date, ratio | 수량 조정 (원가 재계산) |
| `sec.transfer` | date, fromAccountId, toAccountId | 계좌 간 이관 (ISA 만기 이전 등) |
| `acct.open` / `acct.close` | date | |
| `acct.deposit` | date, amount | ISA·연금 납입 한도 추적 |
| `acct.maturity_action` | date, action: 'extend' \| 'close' \| 'rollover_to_pension' | ISA 만기 처리 |

#### 인적 이벤트

| type | 의미 |
|---|---|
| `hh.marriage` | 혼인 합가 특례 기산 |
| `hh.member_join` / `hh.member_leave` | 세대 합가/분가 (동거봉양 특례, 주택 수 합산 변경) |

### Scenario — 가상 타임라인

```typescript
interface Scenario {
  id: string;
  name: string;                    // "B아파트 2027년 3월 매도"
  baseline: 'actual';              // 실제 타임라인 위에 얹음
  virtualEvents: Event[];          // 실제와 같은 스키마, isVirtual 플래그만 추가
  assumptions: Assumption[];       // "양도가 9억 가정", "공시가 연 3% 상승 가정"
}
```

## 파생 계산 (저장하지 않고 재생으로 산출)

| 파생값 | 계산 방법 |
|---|---|
| 시점 t의 세대 주택 수 | t 이전 이벤트 재생: 취득(+1), 양도(-1), 분양권/입주권은 취득 시점별 포함 규칙 적용, 특례 제외 반영 |
| 자산별 보유기간 | 취득일(잔금/등기 중 빠른 날) ~ t |
| 자산별 거주기간 | move_in ~ move_out 구간 합산 |
| 계좌·티커별 보유 수량과 평균단가 | buy/sell/split 재생, costBasisMethod에 따라 |
| 연도별 실현손익 (해외주식) | sell 이벤트별 (양도가액×양도환율 − 취득가액×취득환율 − 비용) 합산 |
| 연도별 금융소득 누계 | dividend 합산 |
| ISA 납입 누계·한도 잔여 | deposit 재생 |

## 저장소 (1차: 브라우저 로컬)

- **IndexedDB** (Dexie.js 등): events, assets, accounts, scenarios, rulesets 테이블.
- **내보내기/가져오기**: 전체 데이터를 단일 JSON으로 직렬화, 선택적 암호화(WebCrypto, 사용자 패스프레이즈) 후 파일 다운로드. 기기 이전·백업 수단.
- **스키마 버전 필드** 필수 (`schemaVersion`). 마이그레이션 함수를 처음부터 준비 — 앱 전환 시에도 동일 포맷 사용.
- 규칙 데이터(rulesets)는 저장소에서 fetch 후 캐싱; 사용자 데이터와 물리적으로 분리된 테이블.

## 데이터 검증 규칙 (입력 시점)

- 잔금일 < 계약일 → 오류.
- 매도 수량 > 보유 수량 → 오류 (이관·분할 반영 후).
- 동일 자산에 balance_payment 중복 → 경고.
- 이벤트 삭제는 소프트 삭제(revoked 플래그) — 원장 불변성 유지, 실수 복구 가능.
