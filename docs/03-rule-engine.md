# 03. 세법 규칙 엔진 — 규칙의 데이터화와 버저닝

## 왜 규칙을 데이터로 만드는가

세율·공제·요건을 코드에 하드코딩하면 세법 개정 때마다 코드 수정·배포가 필요하고, 경과규정 처리를 위해 코드에 분기가 누적되어 유지 불가능해진다. 대신:

- 규칙은 **선언적 JSON 문서**로 저장소의 `rules/` 디렉토리에서 버전 관리한다.
- 엔진은 규칙을 해석하는 **순수 함수 인터프리터**다. 세법이 바뀌면 규칙 파일만 추가/갱신한다.
- GitHub Pages 배포와 궁합이 좋다: 규칙 파일은 정적 자산으로 서빙되고, 규칙 갱신 = git commit + Pages 재배포.

## 규칙의 3층 구조

```
RuleSet (배포 단위)
 └─ RuleGroup (세목: acquisition_tax, capital_gains_house, ...)
     └─ Rule (개별 규칙: 세율표, 요건, 특례, 한시 조치)
```

### Rule 스키마

```jsonc
{
  "id": "cgt.house.exemption.1house",
  "group": "capital_gains_house",
  "title": "1세대 1주택 비과세",
  "legalBasis": ["소득세법 제89조 제1항 제3호", "소득세법 시행령 제154조"],
  "source": {                                  // 상세 스키마는 07-law-ingestion 참조
    "docId": "statutes/soduksebeop/2026-01-01_시행_제XXXXX호",  // law-archive 보관본
    "sha256": "ab3f...",                       // 수집 시점 원문 해시 (무결성 체인)
    "articlePath": "제89조/제1항/제3호",
    "quotedText": "…원문 발췌…",
    "url": "https://law.go.kr/...",
    "checkedAt": "2026-07-15"
  },
  "confidence": "verified",          // verified | drafted | uncertain — UI에 표기

  // ── 시간 유효성: 이 규칙 버전이 유효한 기간 ──
  "effective": { "from": "2022-05-10", "to": null },
  "status": "in_force",              // in_force | scheduled | repealed | suspended
  "supersedes": "cgt.house.exemption.1house@2021-01-01",

  // ── 적용 조건: 어떤 사건/자산에 적용되는가 ──
  // 조건식은 파생값(주택수, 보유기간 등)을 참조하는 선언적 표현
  "applicability": {
    "all": [
      { "fact": "household.houseCount", "at": "disposal.date", "op": "==", "value": 1 },
      { "fact": "asset.holdingYears", "at": "disposal.date", "op": ">=", "value": 2 },
      { "if": { "fact": "asset.wasAdjustedAreaAtAcquisition", "op": "==", "value": true },
        "then": { "fact": "asset.residenceYears", "op": ">=", "value": 2 } }
    ]
  },

  // ── 효과: 세액 계산에 미치는 영향 ──
  "effect": {
    "type": "exemption_with_cap",
    "capFact": "params.highValueThreshold",   // 고가주택 기준 — 별도 파라미터 규칙 참조
    "excessTaxable": true
  },

  // ── 규칙 자체 테스트 케이스 (CI에서 실행) ──
  "tests": [
    { "name": "보유2년+거주2년 조정지역 1주택 → 비과세",
      "given": { "household.houseCount": 1, "asset.holdingYears": 3,
                 "asset.wasAdjustedAreaAtAcquisition": true, "asset.residenceYears": 2.5 },
      "expect": { "applies": true } }
  ]
}
```

### 규칙 유형 분류

| 유형 | 예 | effect 형태 |
|---|---|---|
| 세율표 (rate table) | 양도세 기본세율 누진표, 취득세율 | 과표 구간별 세율 배열 |
| 판정 요건 (predicate) | 1세대 1주택, 대주주, 일시적 2주택 | boolean + 파생 판정 결과 |
| 공제/감면 (deduction) | 장특공제 테이블, 250만 기본공제, 종부세 기본공제 | 과표 또는 세액 차감 |
| 가산/중과 (surcharge) | 다주택 중과 가산율, 단기양도세율 | 세율 대체 또는 가산 |
| 한시 조치 (temporary) | 다주택 중과 한시 배제 | 다른 규칙의 suspension (기간 한정) |
| 파라미터 (parameter) | 고가주택 기준금액, 공정시장가액비율, ISA 비과세 한도 | 단일 값 + 유효기간 |
| 참조 데이터 (reference) | **조정대상지역 지정/해제 이력** (지역코드 × 기간) | 조회 테이블 |

핵심: **한시 배제·유예를 독립 규칙(suspension)으로 모델링**한다. "다주택 중과"는 규칙으로 존재하되, "중과 한시 배제(2022-05-10 ~ 20XX-XX-XX)"가 이를 기간 한정으로 정지시킨다. 유예가 연장되면 배제 규칙의 `to` 날짜만 바꾸면 된다 — 실제 세법 개정이 작동하는 방식과 동형이다.

## 시간 축이 두 개다 (bi-temporal)

1. **valid time**: 규칙이 법적으로 유효한 기간 (`effective.from/to`).
2. **판정 기준일 (as-of)**: 같은 규칙이라도 "계약일 기준", "취득일 기준", "양도일 기준" 중 무엇으로 조건을 평가하는지가 다르다. `applicability`의 `at` 필드가 이를 지정한다.

경과규정 예: "2020-07-10 이전 계약분은 종전 취득세율" → 신 세율 규칙의 applicability에 `{ "fact": "acquisition.contractDate", "op": "<", "value": "2020-07-11", "then": "not_applicable" }` 형태의 예외를 명시하고, 종전 규칙의 effective.to 이후에도 이 조건에서는 종전 규칙이 선택되도록 **규칙 선택기(selector)** 가 처리한다.

## 계산 파이프라인

```
[사용자 이벤트 로그] ──replay──> [파생 사실(facts) @ 판정기준일]
                                        │
[RuleSet] ──selector(세목, 기준일)──> [적용 규칙 집합]
                                        │
                              evaluate(facts, rules)
                                        │
                     [세액 + 적용규칙 추적(explanation trace)]
```

- **replay**: 이벤트 로그를 기준일까지 재생해 facts를 만든다 (주택 수, 보유·거주기간, 평균단가, 실현손익 누계 등 — [[02-data-model]]의 파생 계산).
- **selector**: 세목과 판정 기준일(들)로 유효 규칙 버전을 고른다. suspension 규칙이 있으면 대상 규칙을 제외한다.
- **evaluate**: 조건 평가 → 효과 적용 → 세액 산출. 모든 단계는 순수 함수. 적용/미적용된 규칙과 이유를 trace로 남긴다.
- **explanation trace**가 UI의 "왜 이 금액인가" 화면과 규칙 테스트의 기반이 된다.

## Diff 엔진 (세법 개정 영향 분석)

```
diff(ruleSetV1, ruleSetV2, userTimeline) =
  for each 관심 세목 × 시점:
    taxV1 = calculate(userTimeline, ruleSetV1)
    taxV2 = calculate(userTimeline, ruleSetV2)
    → { 변경 규칙 목록, 세액 변화량, 영향받는 자산 }
```

- RuleSet은 semver + 발표일로 태깅 (`2026.1-rev2`).
- 새 RuleSet이 배포되면 앱이 자동으로 직전 버전과 diff를 계산해 "개정 영향 리포트"를 생성.
- "예정(scheduled)" 상태의 규칙(국회 통과 전 개정안)도 별도 RuleSet 브랜치로 배포해 "개정안이 통과되면?" 시나리오 제공.

## 규칙 데이터 품질 관리

1. **테스트 필수**: 모든 Rule은 최소 1개의 test 케이스를 가진다. 국세청 사례집·계산 예시를 테스트로 옮긴다. CI에서 전체 규칙 테스트 실행.
2. **스키마 검증**: Rule JSON Schema로 구조 검증 (CI).
3. **신뢰도 표기**: `confidence` 필드를 UI에 노출 — "이 계산에는 검증되지 않은 규칙이 포함되어 있습니다".
4. **원문 근거 필수**: 모든 Rule의 source는 law-archive에 보관된 정부 원문(해시 포함)을 가리켜야 한다. 원문 수집·보관·추출 파이프라인은 [[07-law-ingestion]] 참조. CI가 docId 존재 여부와 quotedText의 원문 일치를 검증.
5. **출처와 확인일**: 규칙마다 source.checkedAt. 오래된 확인일은 대시보드에서 갱신 알림.
6. **커버리지 리포트**: 사용자 계산에서 "규칙 없음(no rule found)" 상황은 오류가 아니라 명시적 "계산 불가 — 규칙 미수록" 응답으로 처리.

## 엔진 구현 원칙

- TypeScript 순수 함수 모듈 (`@taxnavi/engine`) — DOM/스토리지 의존성 없음. 웹·앱·Node(CI 테스트) 어디서나 동일 실행.
- 금액 계산은 부동소수점 대신 정수(원 단위) 연산.
- 조건식 인터프리터는 화이트리스트 연산자만 지원 (`==, !=, <, <=, >, >=, in, between`) — eval 없음.
- 규칙으로 표현 불가능한 극단적 로직(예: 복잡한 안분 계산)은 엔진에 **명명된 내장 계산기(builtin)** 로 두고 규칙이 이름으로 참조: `"effect": { "type": "builtin", "name": "longTermHoldingDeductionTable" }`. builtin 추가는 코드 배포가 필요하므로 최소화.
