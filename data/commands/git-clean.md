---
name: git-clean
description: 현재 작업폴더에서 git의 추적 안 되는 파일을 확인하고 사용자 확인 후 정리한다.
---

# /git-clean

현재 작업폴더에서 Git이 추적하지 않는(untracked) 파일/디렉터리를 확인하고, 사용자 확인 후 정리한다.

## 실행 절차

1. `bash_exec`로 현재 작업폴더의 Git 상태를 확인한다.
   - 실행 명령:
     ```bash
     git status --short
     ```
   - 출력에서 `??`로 표시되는 untracked 파일/디렉터리를 식별한다.

2. untracked 항목이 없으면 사용자에게 "정리할 untracked 파일이 없다"고 알리고 종료한다.

3. untracked 항목이 있으면 `bash_exec`로 삭제 예정 목록을 dry-run으로 보여준다.
   - 실행 명령:
     ```bash
     git clean -nd
     ```
   - 삭제 예정 파일/디렉터리를 요약해서 사용자에게 보여준다.

4. 위험 작업이므로 실제 삭제 전 반드시 사용자 확인을 받는다.
   - 확인 문구 예:
     "위 항목들을 `git clean -fd`로 삭제할까?"

5. 사용자가 승인하면 `bash_exec`로 실제 정리를 실행한다.
   - 실행 명령:
     ```bash
     git clean -fd
     ```

6. 정리 후 `bash_exec`로 상태를 다시 확인한다.
   - 실행 명령:
     ```bash
     git status --short
     ```
   - 결과를 1~3줄로 요약한다.

## 주의

- tracked 파일 변경사항은 건드리지 않는다.
- ignored 파일까지 삭제하지 않는다. 즉 `git clean -fdx`는 사용하지 않는다.
- 삭제 작업은 반드시 사용자 확인 후 실행한다.
