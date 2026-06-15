; VEGA NSIS installer hooks
; 설치 전 실행 중인 vega_backend.exe / VEGA.exe 프로세스를 강제 종료한다.
; 프로세스가 살아있으면 파일 잠금으로 복사 단계에서 오류가 발생한다.

!macro NSIS_HOOK_PREINSTALL
  DetailPrint "실행 중인 VEGA 프로세스 종료 중..."
  nsExec::ExecToLog 'taskkill /F /IM "vega_backend.exe" /T'
  nsExec::ExecToLog 'taskkill /F /IM "VEGA.exe" /T'
  ; 프로세스가 완전히 종료될 때까지 잠시 대기
  Sleep 1500
  DetailPrint "프로세스 정리 완료"
!macroend
