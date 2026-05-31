# Created: 2026-05-31
# Purpose: 2단 라우터 모델 속도 회귀 벤치 — local(gemma-4-e4b) vs cloud(deepseek-v4-flash).
#          도메인 질의(짧은 응답)에서 로컬 SLM 의 TTFT 우위를 수치로 확인. 모델/포트 교체 시 재실행.
# Dependencies: 로컬 LM Studio(1234)에 gemma-4-e4b-it-mlx 로드, OPENROUTER_API 키.
# 실행: python tests/bench_tier_speed.py   (로컬 SLM 다운이면 local 구간 skip)
"""tier 속도 벤치마크.

2026-05-31 측정 (도메인 질의 3종 × 3회):
  gemma-4-e4b (로컬)   : TTFT 0.20s / TOTAL 0.76s / 42.8 tps
  gpt-5-nano (클라우드): TTFT 3.38s / tok=0 (reasoning 타입, 짧은 질의 부적합)
  → local=gemma-4-e4b, cloud=deepseek-v4-flash 확정.
"""
import json, time, urllib.request, statistics, socket, os, sys

PROMPTS = [
    "오늘 가장 급한 일이 뭐야? 한 문장으로 답해.",
    "이번 주 마감인 작업을 간단히 정리해줘.",
    "지금 여유로운 사람이 누구인지 한 줄로 알려줘.",
]


def _key():
    for line in open(os.path.expanduser("/Users/unohee/dev/kyte-portal/.env")):
        if line.startswith("OPENROUTER_API="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.getenv("OPENROUTER_API", "")


def _alive(host, port):
    s = socket.socket(); s.settimeout(2)
    try:
        s.connect((host, port)); return True
    except Exception:
        return False
    finally:
        s.close()


def call(url, model, key, prompt, max_tokens=120):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "stream": True, "max_tokens": max_tokens, "temperature": 0.2}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    t0 = time.monotonic(); ttft = None; ntok = 0
    with urllib.request.urlopen(req, timeout=60) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            d = line[5:].strip()
            if d == "[DONE]":
                break
            try:
                delta = (json.loads(d).get("choices") or [{}])[0].get("delta", {}).get("content")
                if delta:
                    if ttft is None:
                        ttft = time.monotonic() - t0
                    ntok += 1
            except Exception:
                pass
    total = time.monotonic() - t0
    return ttft or total, total, ntok


def bench(label, url, model, key, reps=3):
    print(f"\n=== {label} ({model}) ===")
    ttfts, totals = [], []
    for p in PROMPTS:
        for _ in range(reps):
            try:
                ttft, total, ntok = call(url, model, key, p)
                ttfts.append(ttft); totals.append(total)
            except Exception as e:
                print(f"  ERROR {type(e).__name__}: {str(e)[:80]}")
    if ttfts:
        print(f"  >> TTFT={statistics.median(ttfts):.2f}s TOTAL={statistics.median(totals):.2f}s (n={len(ttfts)})")
    return ttfts, totals


if __name__ == "__main__":
    if _alive("127.0.0.1", 1234):
        bench("로컬 gemma-4-e4b", "http://127.0.0.1:1234/v1/chat/completions", "gemma-4-e4b-it-mlx", None)
    else:
        print("SKIP local: LM Studio(1234) 미기동")
    key = _key()
    if key:
        bench("클라우드 deepseek-v4-flash", "https://openrouter.ai/api/v1/chat/completions", "deepseek/deepseek-v4-flash", key)
    else:
        print("SKIP cloud: OPENROUTER_API 미설정")
