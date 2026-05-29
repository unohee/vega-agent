---
name: youtube-meta
description: YouTube 동영상 URL로부터 제목·채널·썸네일·길이 등 메타데이터 추출 (oEmbed + web_fetch)
argument-hint: "<url>"
---

# YouTube 메타데이터 추출

## 🚫 절대 사용 금지 도구
**bash_exec, python_exec, host_exec, sandbox_exec — 이 도구들은 절대 호출하지 마라.**
샌드박스는 인터넷 차단이고 host_exec는 승인 UI가 위젯에 없어서 영원히 멈춘다.
오직 **`web_fetch`** 만 사용해라.

## 입력
- `${url}` — YouTube 동영상 URL (action 위젯 호출 시)
- `$ARGUMENTS` — 채팅에서 직접 호출 시

## 절차 (web_fetch만, 최대 2회 호출)

**중요: web_fetch는 정확히 2번만 호출한다. 같은 URL로 중복 호출 금지.**

1. URL 정규화: `youtu.be/<id>` 또는 `youtube.com/shorts/<id>` 형식이면 `https://www.youtube.com/watch?v=<id>` 로.
   URL이 비어 있으면 `$ARGUMENTS`로 폴백. 둘 다 비면 짧은 에러로 응답.

2. **web_fetch 호출 #1**: `https://www.youtube.com/oembed?url=<URL>&format=json`
   응답 JSON에서 추출: `title`, `author_name`, `author_url`, `thumbnail_url`

3. **web_fetch 호출 #2**: `<원본URL>` (YouTube 페이지)
   응답 본문에서 정규식으로 한 번에 추출(매칭 실패 시 "—"):
   - 길이: `"lengthSeconds":"(\d+)"` → 초 → m:ss 포맷
   - 조회수: `"viewCount":"(\d+)"` → 1,234,567 포맷
   - 업로드일: `"uploadDate":"(20\d\d-\d\d-\d\d)"`
   - 설명: `"shortDescription":"((?:[^"\\]|\\.)*)"` — 첫 500자 (이스케이프 \\n, \\" 풀어줄 것)

4. **2번의 fetch로 모은 데이터를 즉시 마크다운으로 작성해 응답.** 추가 fetch 호출 금지. "도구 호출했어요" 같은 메시지도 금지.

## ⛔ 절대 금지 (응답 마무리)
- 마크다운 출력 후 어떤 추가 텍스트도 붙이지 마라.
- "오늘 브리핑", "프로젝트 진행도", "한 번 더 확인할까?" 같은 후속 제안 금지.
- "참고:", "여담:" 등으로 메모 덧붙이기 금지.
- 위 출력 포맷 한 덩어리로 끝. 마지막 줄은 `🔗 <URL>`이어야 한다. 그 다음엔 아무것도 없다.

## 출력 포맷

```
## 🎬 <title>

| 항목 | 값 |
|------|------|
| 채널 | [<author_name>](<author_url>) |
| 업로드 | <upload_date> |
| 조회수 | <viewCount, 천단위 쉼표> |
| 길이 | <m:ss> |

![](<thumbnail_url>)

**설명**

> <설명 500자, 길면 '…'>

🔗 <원본URL>
```

## 에러
- oEmbed 응답이 JSON이 아니거나 'Unauthorized'/'Not Found' 포함: "비공개·삭제됨·연령제한"
- web_fetch가 "fetch 실패"로 시작: "YouTube 접근 실패 — URL 확인"
- URL 형식 부적합 (youtube.com/youtu.be 도메인 아님): "올바른 YouTube URL이 아님"
