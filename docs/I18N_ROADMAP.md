# VEGA 다국어(i18n) 지원 로드맵

## 현재 상태 (Phase 0 — 완료)

**2026-06-02** 초기 구현 완료.

- `chat.html`, `dashboard.html`에 `VEGA_STRINGS` 객체 기반 i18n 시스템 삽입
- 헤더에 언어 토글 버튼 추가 (`KO` ↔ `EN`)
- `localStorage['vega_lang']`으로 선택 언어 지속화
- `data-i18n`, `data-i18n-title`, `data-i18n-placeholder` 속성 패턴으로 정적 UI 텍스트 마킹
- 지원 언어: **한국어(ko)**, **영어(en)**

---

## Phase 1 — 문자열 완전 번역 (우선순위: 높음)

**목표**: 모든 하드코딩 한국어 텍스트를 `data-i18n` 마킹으로 교체.

### 남은 작업
| 파일 | 대상 영역 | 번역 키 수 (추정) |
|------|-----------|------------------|
| `chat.html` | MCP 관리 모달 | ~20개 |
| `chat.html` | LLM 프로바이더 모달 | ~15개 |
| `chat.html` | 컨텍스트 메뉴 (동적 생성) | ~10개 |
| `chat.html` | 토스트 / 에러 메시지 | ~30개 |
| `chat.html` | 파일 탐색기 패널 | ~8개 |
| `dashboard.html` | 동적 생성 텍스트 (renderMails 등) | ~15개 |
| `install_wizard.html` | 설치 마법사 전체 | ~40개 |

**접근 방식**: 동적으로 생성되는 innerHTML 문자열은 `t(key)` 헬퍼 함수로 교체.

```js
// 권장 패턴 — 동적 문자열용
function t(key) {
  const lang = localStorage.getItem('vega_lang') || 'ko';
  return (VEGA_STRINGS[lang] || VEGA_STRINGS.ko)[key] || key;
}

// 사용 예
el.innerHTML = `<button>${t('mcp_reload')}</button>`;
```

---

## Phase 2 — 번역 파일 외부화 (우선순위: 중간)

**목표**: 번역 문자열을 HTML에서 분리해 유지보수성 향상.

### 방안 A: JSON 파일 (`data/i18n/`)
```
data/
  i18n/
    ko.json
    en.json
    ja.json   (추후 추가)
```

서버 엔드포인트 `/api/i18n/{lang}` 추가, 페이지 로드 시 fetch.

**장점**: 번역자가 HTML 수정 없이 JSON만 편집 가능.  
**단점**: 추가 HTTP 요청, 초기 렌더링 텍스트 깜빡임(FOUC) 가능성.

### 방안 B: 인라인 유지 + 빌드 시 생성
`scripts/generate_i18n.py`가 `data/i18n/*.json`을 읽어 HTML에 자동 삽입.

**권장**: Phase 2에서는 방안 A, 번들링 도입 시 방안 B로 전환.

---

## Phase 3 — 추가 언어 (우선순위: 낮음)

| 언어 | 코드 | 비고 |
|------|------|------|
| 일본어 | `ja` | 한자 공유로 번역 비용 낮음 |
| 중국어 (간체) | `zh-CN` | 대형 사용자 풀 |
| 스페인어 | `es` | 글로벌 2위 언어 |

**버튼 UI 전환**: 토글 버튼 → 드롭다운(`<select>`)으로 교체 필요.

```html
<select id="lang-select">
  <option value="ko">한국어</option>
  <option value="en">English</option>
  <option value="ja">日本語</option>
  <option value="zh-CN">中文</option>
</select>
```

---

## Phase 4 — 에이전트 응답 언어 연동 (우선순위: 낮음)

**목표**: UI 언어 설정이 에이전트 시스템 프롬프트에도 반영되어, LLM이 선택 언어로 응답.

### 구현 방향
1. `POST /api/lang` 엔드포인트 — 사용자 언어 설정을 서버에 저장
2. `pipeline/session_store.py` — 세션 메타에 `preferred_lang` 필드 추가
3. `llm_gateway.py` — 시스템 프롬프트에 언어 지시 삽입:
   ```
   Respond in English. The user has set their preferred language to English.
   ```

### 고려사항
- 언어 설정은 세션별이 아닌 사용자 전역 설정
- `data/user_profile.json`의 `preferred_lang` 필드로 관리
- `web/routers/onboarding.py`의 프로필 저장 로직에 통합

---

## 기술 부채 및 제약

| 항목 | 현재 상태 | 해결책 |
|------|-----------|--------|
| 동적 생성 텍스트 | 하드코딩 한국어 | `t()` 헬퍼 함수 점진적 도입 |
| RTL 언어 지원 없음 | CSS `direction` 미적용 | 아랍어·히브리어 추가 시 필요 |
| 복수형 처리 없음 | "3개 세션" 등 단순 처리 | `Intl.PluralRules` 도입 필요 시 |
| 날짜/숫자 로케일 | `Intl.DateTimeFormat` 미사용 | Phase 2에서 통합 권장 |

---

## 참고

- 현재 구현 위치: `web/static/chat.html:5400~5533`, `web/static/dashboard.html:1030~1083`
- 번역 키 네이밍 규칙: `{컴포넌트}_{요소}` (예: `ob_title`, `card_events`)
- `localStorage` 키: `vega_lang` (값: `"ko"` | `"en"`)
