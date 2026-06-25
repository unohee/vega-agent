#!/usr/bin/env python3
# Created: 2026-06-25
# Purpose: Ingest external benchmark routing subsets → data/bench_external/{suite}/tasks.jsonl
"""Download or synthesize VEGA-normalized external bench tasks."""

from __future__ import annotations

import argparse
import gzip
import json
import re
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "data" / "bench_external" / "manifest.json"
OUT_ROOT = REPO / "data" / "bench_external"

HUMANEVAL_URL = (
    "https://github.com/openai/human-eval/raw/master/data/HumanEval.jsonl.gz"
)
MBPP_URL = (
    "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/sanitized-mbpp.json"
)


def _write_jsonl(path: Path, tasks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for t in tasks:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")


def _fetch_jsonl(url: str) -> list[dict]:
    with urllib.request.urlopen(url, timeout=120) as r:
        raw = r.read()
    if url.endswith(".gz"):
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _fetch_json(url: str):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _base_task(
    suite: str,
    idx: int,
    *,
    category: str,
    harness: str,
    verify: str,
    prompt: str,
    rubric: list[str],
    **extra,
) -> dict:
    tid = f"ext_{suite}_{idx:03d}"
    return {
        "id": tid,
        "source": suite,
        "source_id": extra.pop("source_id", tid),
        "suite": suite,
        "category": category,
        "harness": harness,
        "verify": verify,
        "routing_default": True,
        "prompt": prompt,
        "rubric": rubric,
        **extra,
    }


def ingest_humaneval(limit: int) -> list[dict]:
    rows = _fetch_jsonl(HUMANEVAL_URL)
    out: list[dict] = []
    for i, row in enumerate(rows[:limit]):
        prompt = row["prompt"] + "\n\nComplete the function. Output Python code only in a fenced block."
        out.append(_base_task(
            "humaneval", i, category="swe", harness="smoke", verify="swe",
            source_id=row.get("task_id", f"HumanEval/{i}"),
            prompt=prompt,
            rubric=["코드가 실행 가능하고 테스트를 통과하는가"],
            entry_point=row["entry_point"],
            test=row["test"],
        ))
    return out


def _mbpp_assert_line(raw: str) -> str:
    """sanitized-mbpp test_list entries already include 'assert'."""
    s = (raw or "").strip()
    if s.startswith("assert "):
        return s
    return f"assert {s}"


def ingest_mbpp(limit: int) -> list[dict]:
    data = _fetch_json(MBPP_URL)
    out: list[dict] = []
    for i, row in enumerate(data[:limit]):
        text = row.get("text") or row.get("prompt") or ""
        test_list = row.get("test_list") or []
        test_code = "\n".join(_mbpp_assert_line(t) for t in test_list[:5])
        # 테스트 assertion 을 프롬프트에 포함 — 모델이 기대 함수명·시그니처를 알 수 있게.
        # MBPP 공식 평가 관례. 누락 시 모델이 다른 함수명으로 작성 → NameError false-negative (INT-1920).
        prompt = (
            f"{text}\n\nYour code should pass these tests:\n```python\n{test_code}\n```\n\n"
            "Output the function definition in a ```python block."
        )
        out.append(_base_task(
            "mbpp", i, category="swe", harness="smoke", verify="swe",
            source_id=str(row.get("task_id", i)),
            prompt=prompt,
            rubric=["MBPP test_list assertions pass"],
            test=test_code,
            entry_point=None,
        ))
    return out


def ingest_swebench_lite(limit: int) -> list[dict]:
    """Micro single-file patch tasks (no Docker)."""
    micro = [
        {
            "prompt": "Fix avg() so empty list returns 0.0. Current buggy code:\n```python\ndef avg(nums):\n    return sum(nums) / len(nums)\n```\nUse python_exec then save fixed code to fixed_avg.py via file write in sandbox.",
            "agent_prompt": "Buggy avg() returns error on []. Fix with python_exec and write corrected function to fixed_avg.py in working folder.",
            "expected_snippet": "if not nums",
            "required_tools": ["python_exec"],
        },
        {
            "prompt": "Implement top_word(s) returning (word, count) for most frequent word (case-insensitive).",
            "agent_prompt": "Implement top_word in Python using python_exec. Test with 'Hello, hello! World.' → ('hello', 2).",
            "expected_snippet": "def top_word",
            "required_tools": ["python_exec"],
        },
        {
            "prompt": "Parse '2026-06-25' to datetime and return ISO week number.",
            "agent_prompt": "Use python_exec to compute ISO week for date string 2026-06-25. Print result.",
            "expected_snippet": "isocalendar",
            "required_tools": ["python_exec"],
        },
        {
            "prompt": "Read CSV string 'a,b\\n1,2\\n3,4' and return sum of column b.",
            "agent_prompt": "Use python_exec: parse CSV 'a,b\\n1,2\\n3,4' and return sum of b column (expect 6).",
            "expected_snippet": "6",
            "required_tools": ["python_exec"],
        },
        {
            "prompt": "Normalize whitespace in multiline text to single spaces.",
            "agent_prompt": "python_exec: normalize 'hello\\n  world' → 'hello world'",
            "expected_snippet": "hello world",
            "required_tools": ["python_exec"],
        },
        {
            "prompt": "Extract all email addresses from text using regex.",
            "agent_prompt": "python_exec: find emails in 'contact a@b.com and c@d.org'",
            "expected_snippet": "@",
            "required_tools": ["python_exec"],
        },
        {
            "prompt": "Merge two sorted lists [1,3,5] and [2,4,6] in Python.",
            "agent_prompt": "python_exec merge sorted lists, verify result [1,2,3,4,5,6]",
            "expected_snippet": "[1, 2, 3, 4, 5, 6]",
            "required_tools": ["python_exec"],
        },
        {
            "prompt": "Validate JSON string and return pretty-printed form.",
            "agent_prompt": "python_exec: validate and pretty-print '{\"a\":1}'",
            "expected_snippet": "a",
            "required_tools": ["python_exec"],
        },
        {
            "prompt": "Compute SHA256 hex of string 'VEGA'.",
            "agent_prompt": "python_exec: sha256 hex of 'VEGA'",
            "expected_snippet": "hashlib",
            "required_tools": ["python_exec"],
        },
        {
            "prompt": "Convert list of dicts to CSV rows.",
            "agent_prompt": "python_exec: [{'x':1},{'x':2}] to CSV with header x",
            "expected_snippet": "x",
            "required_tools": ["python_exec"],
        },
    ]
    out: list[dict] = []
    for i, m in enumerate(micro[:limit]):
        out.append(_base_task(
            "swebench_lite", i, category="swe", harness="agent", verify="swe",
            source_id=f"micro/{i}",
            prompt=m["prompt"],
            agent_prompt=m["agent_prompt"],
            rubric=["패치/구현이 expected_snippet을 포함하거나 테스트 통과"],
            expected_snippet=m["expected_snippet"],
            required_tools=m["required_tools"],
            min_tool_rounds=1,
        ))
    return out


def ingest_presentbench(limit: int) -> list[dict]:
    topics = [
        ("VEGA AI workspace", "로컬-first AI 데스크톱", ["VEGA", "로컬", "프라이버시", "5슬라이드"]),
        ("Q2 sales review", "월별 매출 120,95,140,110,130 만원", ["매출", "Q2", "5슬라이드"]),
        ("Product launch ILL", "K-pop 싱글 ILL 6/24 발매", ["ILL", "발매", "5슬라이드"]),
        ("ISO27001 audit", "정보보안 인증 준비", ["ISO27001", "보안", "5슬라이드"]),
        ("Team OKR 2026 H2", "매출 20% 성장 목표", ["OKR", "H2", "5슬라이드"]),
        ("Onboarding deck", "신입 1주 온보딩", ["온보딩", "1주", "5슬라이드"]),
        ("Investor update", "ARR 12억, churn 2%", ["ARR", "churn", "5슬라이드"]),
        ("Marketing plan", "SNS + 유튜브 캠페인", ["마케팅", "SNS", "5슬라이드"]),
        ("Engineering roadmap", "벤치·라우팅·메모리", ["로드맵", "벤치", "5슬라이드"]),
        ("Customer case study", "B2B 도입 3개월", ["케이스", "B2B", "5슬라이드"]),
        ("Training workshop", "Excel 피벗 실습", ["Excel", "피벗", "5슬라이드"]),
        ("Annual report summary", "FY2025 highlights", ["FY2025", "하이라이트", "5슬라이드"]),
        ("Partnership proposal", "API 연동 제휴", ["제휴", "API", "5슬라이드"]),
        ("Risk review", "공급망·규제 리스크", ["리스크", "공급망", "5슬라이드"]),
        ("All-hands meeting", "CEO 메시지 + Q&A", ["All-hands", "CEO", "5슬라이드"]),
    ]
    out: list[dict] = []
    fixture_dir = OUT_ROOT / "presentbench" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    for i, (title, bg, checks) in enumerate(topics[:limit]):
        fix = fixture_dir / f"bg_{i:03d}.md"
        fix.write_text(f"# {title}\n\n{bg}\n", encoding="utf-8")
        checklist = [{"item": c, "type": "keyword"} for c in checks]
        ap = (
            f"배경 자료({fix.name})를 읽고 '{title}' 주제로 pptx_create로 5장 이상 슬라이드 deck.pptx 생성. "
            f"필수 키워드: {', '.join(checks[:-1])}."
        )
        out.append(_base_task(
            "presentbench", i, category="office", harness="agent", verify="office",
            source_id=f"present/{title}",
            prompt=f"Create 5+ slide deck for: {title}. Background: {bg}",
            agent_prompt=ap,
            rubric=[f"슬라이드 5장+, 키워드 {checks}"],
            fixture=f"presentbench/fixtures/bg_{i:03d}.md",
            checklist=checklist,
            required_tools=["pptx_create"],
            min_tool_rounds=1,
            min_slides=5,
        ))
    return out


def ingest_slidesgen(limit: int) -> list[dict]:
    prompts = [
        "AI workspace product pitch — 6 slides, problem/solution/features/pricing/team/CTA",
        "Quarterly business review — revenue, costs, outlook (5 slides)",
        "Technical architecture overview — 5 layers diagram description",
        "User research findings — 5 slides with quotes",
        "Competitive landscape — 4 competitors matrix",
        "Project kickoff — scope, timeline, roles (5 slides)",
        "Security compliance overview — ISO27001 controls (6 slides)",
        "Mobile app launch — features, screenshots placeholders (5 slides)",
        "Data pipeline explain — ingest/transform/load (5 slides)",
        "Customer success stories — 3 cases (5 slides)",
    ]
    out: list[dict] = []
    for i, p in enumerate(prompts[:limit]):
        out.append(_base_task(
            "slidesgen", i, category="office", harness="agent", verify="office",
            source_id=f"slidesgen/{i}",
            prompt=p,
            agent_prompt=f"{p} — pptx_create로 slidesgen_{i}.pptx 저장, 5장 이상.",
            rubric=["5+ slides", "content coverage"],
            required_tools=["pptx_create"],
            min_tool_rounds=1,
            min_slides=5,
        ))
    return out


def ingest_deckbench(limit: int) -> list[dict]:
    papers = [
        ("Attention Is All You Need", "Transformer architecture intro for 5-slide academic deck"),
        ("LoRA", "Low-rank adaptation for LLMs — 5 slides"),
        ("RAG survey", "Retrieval augmented generation overview"),
        ("Prompt caching", "KV cache reuse for cost reduction"),
        ("Agent benchmarks", "SWE-bench and office agent eval trends"),
    ]
    out: list[dict] = []
    for i, (title, desc) in enumerate(papers[:limit]):
        out.append(_base_task(
            "deckbench", i, category="office", harness="agent", verify="none",
            source_id=f"deck/{title}",
            prompt=f"Paper: {title}. {desc}",
            agent_prompt=f"Academic slide deck from paper '{title}': {desc}. pptx_create → deck_{i}.pptx, 5+ slides.",
            rubric=["학술 구조", "5+ slides", "핵심 개념 포함"],
            required_tools=["pptx_create"],
            min_tool_rounds=1,
        ))
    return out


def ingest_odysseybench(limit: int) -> list[dict]:
    """실데이터 이식 (INT-1923 재구축): microsoft/OdysseyBench(스펙·골든, MIT) +
    zlwang-cs/OfficeBench(입력 testbed, Apache-2.0)의 Excel subtask 를 VEGA agent 태스크로.

    입력/골든 xlsx 는 data/bench_external/odysseybench/{files,golden}/ 에 커밋됨.
    각 태스크는 결정적 verify(odyssey_eval: exact_match/contain) — judge 불필요.
    spec.json 은 download 스크립트가 생성(원본 task 지시문 + eval 함수 보존)."""
    spec_path = OUT_ROOT / "odysseybench" / "spec.json"
    if not spec_path.is_file():
        return []
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    out: list[dict] = []
    for i, s in enumerate(spec[:limit]):
        inp = s["input"]
        result_file = s["result_file"]
        instruction = s["instruction"]
        ap = (
            f"You have an Excel file named '{inp}' in your working directory. "
            f"Task: {instruction}. "
            f"Read it with the xlsx_read tool, perform the operation, and save the result as "
            f"'{result_file}' in the working directory (use xlsx_create or python_exec)."
        )
        odyssey_eval = {
            "function": s["eval"],
            "result_file": result_file,
            "golden": s["golden_rel"],
        }
        if s["eval"] == "contain":
            odyssey_eval["keywords"] = s.get("keywords") or []
        out.append(_base_task(
            "odysseybench", i, category="office", harness="agent",
            verify="office",
            source_id=f"odyssey/{s['dir']}/{s['sub']}",
            prompt=instruction,
            agent_prompt=ap,
            rubric=[instruction, "결정적 verify (골든 대조)"],
            fixture=s["input_rel"],
            required_tools=["xlsx_read"],
            min_tool_rounds=1,
            odyssey_eval=odyssey_eval,
        ))
    return out


def ingest_officeeval(limit: int) -> list[dict]:
    tasks = [
        ("sum_range", "Create xlsx with values 10,20,30,40,50 and SUM=150", {"values": [10, 20, 30, 40, 50], "sum": 150}),
        ("avg_range", "Monthly 100,120,80 avg=100", {"values": [100, 120, 80], "avg": 100}),
        ("pct_change", "MoM 100→120 = 20%", {"values": [100, 120], "mom": 20}),
        ("header_row", "Sheet with headers Name,Score", {"headers": ["Name", "Score"]}),
        ("multi_sheet", "Two sheets Sales,Costs", {"sheets": 2}),
        ("sales_q", "Q sales 120,95,140,110,130 sum 595", {"values": [120, 95, 140, 110, 130], "sum": 595}),
        ("dedupe", "Unique count of a,a,b,c", {"unique": 3}),
        ("sort", "Sort 3,1,2 ascending", {"sorted": [1, 2, 3]}),
        ("filter", "Filter scores > 80 from 70,85,90", {"filtered_min": 85}),
        ("pivot_like", "Category A:10 B:20 total 30", {"sum": 30}),
    ]
    out: list[dict] = []
    for i, (sid, desc, spec) in enumerate(tasks[:limit]):
        out.append(_base_task(
            "officeeval", i, category="office", harness="agent", verify="office",
            source_id=f"officeeval/{sid}",
            prompt=f"OfficeEval Excel adapt: {desc}",
            agent_prompt=f"{desc} — xlsx_create로 officeeval_{i}.xlsx 생성.",
            rubric=[desc, "xlsx artifact correct"],
            required_tools=["xlsx_create"],
            min_tool_rounds=1,
            officeeval_spec=spec,
        ))
    return out


def ingest_creativity(limit: int) -> list[dict]:
    brands = [
        ("VEGA", "Insights", "로컬-first AI workspace의 차별점 3가지"),
        ("VEGA", "Ideas", "비개발자 파워유저 타겟 캠페인 아이디어 3개"),
        ("VEGA", "Wild Ideas", "터미널 없이 코덱스급 AI — 과감한 카피 1개"),
        ("1300", "Insights", "K-pop 복귀 싱글 ILL 타겟 팬 insight"),
        ("1300", "Ideas", "SNS 숏폼 캠페인 3개"),
        ("1300", "Wild Ideas", "바이럴 챌린지 컨cept"),
        ("Komca", "Insights", "음저협 정산 pain point"),
        ("Komca", "Ideas", "크리에이터 온보딩 메시지"),
        ("Komca", "Wild Ideas", "정산 투명성 슬로건"),
        ("Local Cafe", "Insights", "단골 retention insight"),
    ]
    out: list[dict] = []
    for i, (brand, ptype, brief) in enumerate(brands[:limit]):
        out.append(_base_task(
            "creativity", i, category="creative", harness="smoke", verify="none",
            source_id=f"creativity/{brand}/{ptype}",
            prompt=f"Brand: {brand}. Type: {ptype}. {brief}. Korean output.",
            rubric=[
                "브랜드 톤 일치",
                "창의성·구체성",
                "요청 형식(Insights/Ideas/Wild) 준수",
                "한국어 자연스러움",
            ],
            brand=brand,
            prompt_type=ptype,
        ))
    return out


def ingest_adbench(limit: int) -> list[dict]:
    fixture_dir = OUT_ROOT / "adbench" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    csv = "campaign,impressions,clicks,spend\nA,10000,120,500\nB,8000,200,600\nC,12000,90,450\n"
    (fixture_dir / "campaigns.csv").write_text(csv, encoding="utf-8")
    questions = [
        "Which campaign has highest CTR? Return name and CTR%.",
        "Total spend across all campaigns?",
        "Rank campaigns by clicks descending.",
        "Campaign with lowest CPC (spend/clicks)?",
        "If budget cap is 1500 total, which to pause?",
    ]
    gt = [
        {"answer_contains": "B", "ctr": True},
        {"answer_contains": "1550", "sum_spend": 1550},
        {"answer_contains": "B"},
        {"answer_contains": "C"},
        {"answer_contains": "pause"},
    ]
    out: list[dict] = []
    for i, (q, g) in enumerate(list(zip(questions, gt, strict=True))[:limit]):
        out.append(_base_task(
            "adbench", i, category="office", harness="agent", verify="office",
            source_id=f"adbench/{i}",
            prompt=q,
            agent_prompt=f"Read campaigns.csv in fixtures. {q} Use python_exec on CSV data.",
            rubric=[q, "analytics correct"],
            fixture="adbench/fixtures/campaigns.csv",
            required_tools=["python_exec"],
            min_tool_rounds=1,
            adbench_gt=g,
            trajectory_gt=["python_exec"],
        ))
    return out


def ingest_bizgeneval(limit: int) -> list[dict]:
    prompts = [
        "Slide layout JSON: title + 3 bullet product features (VEGA AI workspace). max 80 chars title.",
        "Poster copy JSON: headline + subhead for local-first AI event.",
        "Webpage hero JSON: h1 + cta button text for BYOK model hub.",
    ]
    out: list[dict] = []
    for i, p in enumerate(prompts[:limit]):
        out.append(_base_task(
            "bizgeneval", i, category="creative", harness="smoke", verify="office",
            source_id=f"bizgeneval/{i}",
            prompt=f"{p} Output JSON only with keys: title, bullets or headline, subhead.",
            rubric=["JSON valid", "layout fields present", "length constraints"],
            bizgeneval_keys=["title"] if i == 0 else ["headline"],
        ))
    return out


INGESTERS = {
    "humaneval": ingest_humaneval,
    "mbpp": ingest_mbpp,
    "swebench_lite": ingest_swebench_lite,
    "presentbench": ingest_presentbench,
    "slidesgen": ingest_slidesgen,
    "deckbench": ingest_deckbench,
    "odysseybench": ingest_odysseybench,
    "officeeval": ingest_officeeval,
    "creativity": ingest_creativity,
    "adbench": ingest_adbench,
    "bizgeneval": ingest_bizgeneval,
}


def ingest_suite(name: str, *, routing: bool, limit: int | None) -> list[dict]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    meta = manifest["suites"][name]
    cap = limit if limit is not None else (meta["routing_limit"] if routing else 0)
    if cap <= 0 and routing:
        cap = meta["routing_limit"]
    if cap <= 0:
        cap = 9999
    fn = INGESTERS[name]
    tasks = fn(cap)
    _write_jsonl(OUT_ROOT / name / "tasks.jsonl", tasks)
    return tasks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="all", help="suite name or all")
    ap.add_argument("--routing", action="store_true", help="use routing_limit from manifest")
    ap.add_argument("--limit", type=int, default=None, help="override task count")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    names = manifest["routing_suites"] if args.suite == "all" else [args.suite]
    total = 0
    for name in names:
        if name not in INGESTERS:
            print(f"[ingest] skip unknown suite: {name}")
            continue
        try:
            tasks = ingest_suite(name, routing=args.routing or args.limit is None, limit=args.limit)
            print(f"[ingest] {name}: {len(tasks)} tasks → {OUT_ROOT / name / 'tasks.jsonl'}")
            total += len(tasks)
        except Exception as e:
            print(f"[ingest] ERROR {name}: {e}")
            return 1
    print(f"[ingest] total {total} tasks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
