---
name: widget
description: Agent View(홈)에 커스텀 위젯을 대화형으로 추가. 인자로 만들 위젯을 바로 설명해도 됨.
argument-hint: "[만들 위젯 설명]"
---

# 위젯 생성 마법사

Agent View(홈 화면) 하단에 커스텀 위젯을 만든다. 완성되면 `widget_save` 도구로 저장하면 다음 새로고침부터 표시된다.

## 위젯 타입
- **stat** — 큰 숫자 + 라벨 (예: 미읽음 메일 수)
- **list** — 항목 목록 (제목 + 부제)
- **text** — 텍스트/마크다운 (브리핑 등)
- **action** — 폼(입력 박스) + 실행 버튼 + 결과 영역. 사용자가 사무직 친화 GUI로 슬래시 커맨드를 호출할 수 있게 함.

## action 위젯 만들기 (반복 작업 자동화 UI)

사용자가 "유튜브 동영상 메타 추출" 같은 반복 작업을 GUI로 쓰고 싶다고 하면:

1. **먼저 slash 커맨드를 만든다** (`skill_save` 도구) — 본문에서 입력값은 `${name}` 또는 `$name` 으로 참조.
   예) `/youtube-meta` 본문에 `yt-dlp --print "%(title)s ..." ${url}` 같은 식.
2. **그 슬래시를 부르는 action 위젯을 만든다** (`widget_save` 도구, type='action').
   - `skill`: 위에서 만든 슬래시 이름 (e.g. 'youtube-meta')
   - `inputs`: 입력 폼 필드 배열. 각 필드는 `{name, label, type:'text'|'url'|'number'|'textarea', placeholder?, required?}`.
   - `span`: 폼 + 결과가 들어가니 보통 2 또는 3.
3. 사용자는 홈에서 그 카드만 보고 입력 → Run → 결과를 바로 본다 (채팅 안 거침).

## 사용 가능한 데이터소스 (이 중에서만 선택 — 보안 화이트리스트)
| source | 내용 | 적합 타입 |
|--------|------|-----------|
| `clock` | 현재 시각 | stat |
| `session_count` | 대화 세션 수 | stat |
| `skill_count` | 커스텀 skill 수/목록 | stat/list |
| `git_status` | 작업폴더 git 변경 파일 수/목록 | stat/list |
| `mail_count` | 중요 메일 수/목록 | stat/list |
| `project_count` | 추적 프로젝트 수/목록 | stat/list |
| `today_brief` | 오늘 브리핑 본문 | text |

새 데이터가 필요한데 위 목록에 없으면, 사용자에게 "그 데이터소스는 아직 화이트리스트에 없어 — 서버에 핸들러 추가가 필요해"라고 알리고, 가장 가까운 기존 source로 대안을 제시한다. 임의 URL/명령은 위젯에 넣을 수 없다.

## 진행 방식
인자에 위젯 설명이 있으면 출발점으로, 없으면 "어떤 위젯을 만들까?"로 시작. 다음을 합의 (명확한 건 다시 묻지 말 것):
1. **id** — 소문자/숫자/하이픈 (예: `mail-today`)
2. **제목 + 아이콘(이모지)**
3. **타입** (stat/list/text/action)
4. **데이터소스** (stat/list/text의 경우 위 표에서) 또는 정적 text
5. **span** — 폭 1~3칸 (text/list는 보통 2, action도 2~3 권장)
6. **action 타입이면 추가로**: skill(슬래시 이름) + inputs(폼 필드 배열)

## 마무리
1. 합의 내용을 미리보기로 보여주고 확인받는다.
2. action 타입이면 `skill_save`를 먼저, 그 다음 `widget_save` 호출 — 둘이 한 쌍.
3. 확인되면 `widget_save(widget_id, title, type, ...)` 호출.
4. 저장되면 "Agent View 새로고침하면 보여"라고 알린다.

수정은 `widget_save(..., overwrite=true)`, 삭제는 `widget_delete(widget_id)`.
