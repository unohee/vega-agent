---
name: documents
description: 현재 작업 폴더의 핵심 문서(README, CHANGELOG, ARCHITECTURE)를 코드 변경에 맞게 점검·갱신.
argument-hint: "[대상 폴더 또는 빈칸=현재 작업폴더]"
---

# 문서 유지 워크플로

현재 작업 폴더(없으면 인자로 받은 경로)의 핵심 문서를 점검하고 갱신한다. `file_read`/`bash_exec`로 직접 작업해라.

## 점검할 문서
| 문서 | 용도 | 조건 |
|------|------|------|
| README.md | 설치·사용법·구조 개요 | 항상 |
| CHANGELOG.md | 변경 이력 (Keep a Changelog 포맷) | 코드 변경 있으면 |
| ARCHITECTURE.md | 다른 LLM에게 모듈 책임·데이터 흐름 전달 | 항상 |

## 절차
1. `bash_exec`로 폴더 구조 파악: `ls -la` + 주요 소스 파일 목록 + `git log -10 --oneline`(있으면).
2. 각 문서가 없으면 **생성**, 있으면 최근 변경에 맞게 **갱신**.
3. CHANGELOG는 최신 변경을 `## [Unreleased]` 아래 added/changed/fixed로 정리.
4. README는 실제 코드와 어긋난 부분(설치 명령, 사용 예)을 바로잡는다.
5. ARCHITECTURE는 디렉터리별 책임 + 핵심 데이터 흐름을 간결히.

## 원칙
- 추측으로 쓰지 말고 실제 코드를 읽고 반영한다.
- 4종 다 점검하기 전엔 "완료" 선언 금지.
- 마무리: 어떤 문서를 생성/갱신했는지 1~3줄 요약.
