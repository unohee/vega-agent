# RULES — User-Defined Behavior Rules

> This file is the **mutable rules layer** evolved through conversation by the user
> and the agent itself. Separated from `_default.md` (immutable constitution),
> persistent behavior changes like "from now on do X this way" land here via the
> `rule_save` tool. Direct editing is also allowed.
>
> Format: `- [rule_id] rule_text` — rule_id is lowercase-hyphens, meaningfully named.
> Empty sections are not auto-removed; manage with `/rules` slash command or delete sections you don't need.

## Response Style

## Tool Usage

## Domain Rules

### 보도자료 생성 (1300 케이스 6대 패턴 체계화 — INT-1887)
- [press-release-triple-source-verification] 보도자료는 영문 원문·기존 국문 자료·현재 초안 3소스를 교차 대조한다. 영문 원문을 ground truth로 단정하지 말고, 기존 국문 자료의 정보를 기준으로 우선한다.
- [press-release-practical-details-checklist] 본문 작성 후 푸터 단계를 분리해 스트리밍·뮤직비디오·SNS 링크 자리표, 사진 설명, 공연 일정, 문의처를 반드시 채운다.
- [press-release-info-filtration] 이번 싱글/앨범과 직접 관련된 정보로만 제한한다(아티스트≤5, 플랫폼≤4, 대표곡≤5). 무관한 브랜드·플랫폼 협업을 전기처럼 나열하지 않는다.
- [press-release-quote-dedup] 동일 인용문은 1회만 사용한다. 곡 설명·뮤비 설명에 같은 표현을 중복하지 않는다.
- [press-release-title-optimization] 제목에 앨범/곡명·발매일·[공식]을 포함하고 70자 이내로, 뉴스가치를 선두에 둔다.
- [press-release-workflow] 위 규칙을 ① 본문 작성 → ② 푸터/링크/일정/사진 삽입 → ③ 3소스 크로스체크 → ④ 인용문 중복·정보 과잉 점검 순서로 적용한다.

## Communication

## Security & Sensitive Info
