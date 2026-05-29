---
name: commit
description: 변경사항을 분석해 conventional commit으로 커밋. 인자로 --no-push, --amend 등 지원.
argument-hint: "[--no-push] [--amend]"
---

# 커밋 워크플로

현재 작업 폴더(없으면 VEGA 루트)에서 git 변경사항을 커밋한다. `bash_exec`로 git 명령을 직접 실행해라.

## 1. 상태 확인
다음을 실행해 변경 내용을 파악한다:
```
git status
git diff --stat
git log -5 --oneline
git branch --show-current
```
변경사항이 없으면 "커밋할 변경사항이 없어"라고 답하고 종료.

## 2. 스테이징
- staged 파일이 없으면 변경 파일을 검토한 뒤 관련 있는 것만 `git add <파일>`로 추가한다.
- `git add -A`는 무관한 변경까지 섞일 수 있으니 신중히. 사용자가 전체 추가를 원하면 OK.

## 3. 커밋 메시지
변경 내용을 분석해 conventional commit 형식으로 작성:
```
<type>(<scope>): <subject>

<body — 무엇을/왜 바꿨는지 2~4줄>
```
- type: feat / fix / refactor / docs / chore / test / perf
- **Co-Authored-By 라인 절대 금지. AI/Claude 표기 삽입 금지.** 사용자 본인 이름으로만 커밋.
- 제목은 한국어 또는 영어, 간결하게.

## 4. 커밋 실행
`git commit -m "..."` 으로 커밋. 메시지가 길면 `-m` 여러 개로 본문 분리.

## 5. 푸시 (인자에 --no-push 없으면)
- 기본 브랜치(main/master)면 푸시 전에 사용자에게 확인.
- feature 브랜치면 `git push` (upstream 없으면 `git push -u origin <브랜치>`).
- `--no-push` 인자가 있으면 커밋만 하고 종료.
- `--amend` 인자가 있으면 마지막 커밋 수정 (push 안 된 경우만 안전).

## 마무리
무엇을 커밋했는지 1~2줄 요약하고, 커밋 해시를 보여줘.
