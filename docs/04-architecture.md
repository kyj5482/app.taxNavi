# 04. 시스템 아키텍처

## 1차: GitHub Pages (서버리스 정적 SPA)

```
┌─────────────────────────── GitHub Repository ───────────────────────────┐
│  packages/engine    ← 규칙 인터프리터 + 계산기 (순수 TS, 플랫폼 무관)      │
│  packages/rules     ← 세법 규칙 JSON + 테스트 + JSON Schema               │
│  packages/web       ← React SPA (GitHub Pages 배포 대상)                  │
│  .github/workflows  ← CI(규칙 테스트/스키마 검증) + Pages 배포             │
└──────────────────────────────────────────────────────────────────────────┘
                                     │ deploy (gh-pages)
                                     ▼
┌────────────────────────────── 사용자 브라우저 ────────────────────────────┐
│  React SPA (정적 호스팅)                                                  │
│   ├─ UI Layer          입력 폼 / 타임라인 / 대시보드 / 시나리오            │
│   ├─ Engine (동일 코드) replay → select → evaluate → trace                │
│   ├─ RuleSet Loader    rules/*.json fetch + 버전 확인 + IndexedDB 캐시     │
│   └─ Local Store       IndexedDB (이벤트/자산/계좌/시나리오)               │
│                         └─ 암호화 JSON 내보내기/가져오기 (백업·이관)        │
└──────────────────────────────────────────────────────────────────────────┘
```

### 왜 이 구조인가

- **개인정보 무저장**: 자산 데이터는 IndexedDB에만 존재. Pages는 코드와 규칙(공개 데이터)만 서빙. 개인정보보호 규제 부담 없음.
- **규칙 갱신 = git push**: 규칙 JSON 수정 → CI 테스트 통과 → Pages 재배포 → 앱이 새 RuleSet 버전 감지 → 사용자에게 "세법 개정 반영됨 + 내 세금 영향" 알림.
- **엔진 재사용**: `packages/engine`은 DOM 의존성이 없어 이후 React Native/Capacitor 앱, 또는 백엔드에서 그대로 사용.

### 기술 스택 (제안)

| 영역 | 선택 | 이유 |
|---|---|---|
| 언어 | TypeScript (strict) | 엔진·규칙 스키마 타입 공유 |
| 모노레포 | pnpm workspaces | engine/rules/web 분리 |
| UI | React + Vite | Pages 정적 빌드, 앱 전환 시 RN 지식 재사용 |
| 상태 | Zustand + 파생값은 엔진 호출 | 이벤트소싱 원장과 UI 상태 분리 |
| 로컬 DB | Dexie (IndexedDB) | 스키마 버저닝·마이그레이션 지원 |
| 검증 | Zod (이벤트 입력) + JSON Schema (규칙) | |
| 테스트 | Vitest — 엔진 단위 + 규칙 테이블 테스트 | 규칙 JSON의 tests 필드를 자동 실행 |
| 라우팅 | HashRouter | GitHub Pages는 SPA fallback이 없음 |
| 날짜 | date-fns + 원 단위 정수 금액 | 부동소수점 금지 |
| 배포 | GitHub Actions → gh-pages | |

### GitHub Pages 제약과 대응

| 제약 | 대응 |
|---|---|
| 서버 없음 → 외부 API 프록시 불가 | 공시가격·환율은 (a) 사용자 수동 입력 기본 (b) CORS 허용 공개 API만 선택적 사용 (c) 규칙 데이터에 기준환율 테이블 동봉 검토 |
| SPA 라우팅 fallback 없음 | HashRouter 또는 404.html 트릭 |
| 저장 공간 = 브라우저 | 브라우저 데이터 삭제 시 유실 위험 → 첫 사용 흐름에서 내보내기 습관화 유도, File System Access API로 자동 백업(지원 브라우저) |
| 알림 푸시 불가 | 앱 접속 시점 알림(D-day 배지) + .ics 캘린더 파일 내보내기로 대체 |

## 2차: 앱 전환

```
packages/engine, packages/rules  ← 변경 없이 재사용
packages/app (신규)              ← React Native(Expo) 또는 Capacitor 래핑
  ├─ SQLite (IndexedDB 대체, 동일 스키마 직렬화 포맷)
  ├─ 로컬 푸시 알림 (D-day: 처분기한, ISA 만기, 과세기준일)
  └─ 생체인증 잠금
(선택) 클라우드 동기화 백엔드     ← E2E 암호화 동기화. 자산 데이터 평문 미보관 원칙 유지
```

- 웹에서 내보낸 JSON을 앱에서 가져오기 → 이관 경로 확보 (schemaVersion 공유가 전제).
- Capacitor는 웹 코드 재사용율이 높고, RN(Expo)은 네이티브 UX가 좋다 — 앱 전환 시점에 사용자 피드백으로 결정. 지금 결정할 필요 없음. 지금 필요한 것은 **엔진의 플랫폼 독립성**뿐.

## 규칙 데이터 파이프라인 (운영)

```
세법 개정 발표/시행
  → [자동] 원문 수집·불변 보관 + 조문 diff + 규칙 초안 PR 생성
           (GitHub Actions cron — 상세는 07-law-ingestion)
  → [수동 게이트] 사람이 원문 대조 검증 → confidence: verified 승격
  → CI: JSON Schema 검증 + 원문 인용 일치 검증 + 규칙 내장 테스트 + 회귀 시나리오
  → 머지 → Pages 배포
  → 클라이언트: RuleSet 매니페스트(version.json) 폴링 → 새 버전 감지
  → 로컬 타임라인으로 diff 계산 → "개정 영향 리포트" 표시
```

수집·추출은 전부 저장소 측(GitHub Actions)에서 실행되고, 클라이언트는 결과물(규칙 JSON + 원문 발췌·링크)만 소비한다 — 정적 호스팅 제약과 충돌하지 않는다. 원문 보관소는 별도 저장소(`taxnavi-law-archive`, Git LFS)로 분리한다.

- `rules/manifest.json`: 최신 RuleSet 버전, 변경 요약(changelog), 최소 호환 엔진 버전.
- 엔진과 규칙의 호환성: 규칙 스키마에 `schemaVersion`, 엔진이 지원 범위 검사 — 오래된 클라이언트가 새 스키마 규칙을 잘못 해석하는 것 방지.

## 보안·프라이버시

- 자산 데이터는 어떤 네트워크 요청에도 포함하지 않는다 (분석 이벤트에도 금액·자산 정보 금지).
- 내보내기 파일 암호화: WebCrypto AES-GCM + PBKDF2(사용자 패스프레이즈).
- 선택적 앱 잠금(웹: 패스프레이즈, 앱: 생체인증).
- 의존성 최소화 — 정적 사이트의 공급망 공격 면적 축소, lockfile + CI 감사.
