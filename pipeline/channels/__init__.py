# Created: 2026-05-31
# Purpose: VEGA 채널 어댑터 패키지. 텔레그램/슬랙 등 외부 메신저를 vega-agent 에이전트 루프
#          (streaming.stream_gpt)에 연결하는 thin 어댑터들을 담는다.
# Dependencies: pipeline.streaming, pipeline.session_store
"""VEGA 채널 어댑터.

각 채널(텔레그램/슬랙)은 자기 SDK로 메시지를 수신해 공통 헬퍼 run_agent_turn() 으로
에이전트를 호출하고, 스트리밍 응답을 자기 채널 방식으로 점진 전송한다.
세션 매핑(채널 대화 ID ↔ vega 세션 ID)은 channel_session.py 가 담당.
"""
