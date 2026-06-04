// Created: 2026-06-03
// Purpose: chat.html 인터리빙(텍스트↔도구 시간순 배치) DOM 로직 회귀 테스트.
//          chat.html에서 실제 함수를 추출해 jsdom-lite stub 위에서 SSE 시퀀스를 시뮬레이션.
// Usage: node tests/js/interleave_runner.js  (exit 0 = pass)
// 호출: tests/test_chat_interleave.py 가 pytest에서 이 파일을 실행.

const fs = require('fs');
const path = require('path');

// ── chat.html에서 인터리빙 관련 함수 추출 ──
const html = fs.readFileSync(path.join(__dirname, '../../web/static/chat.html'), 'utf-8');
const scriptMatch = html.match(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/);
if (!scriptMatch) { console.error('script 블록 없음'); process.exit(2); }
const script = scriptMatch[1];

function extract(name) {
  const re = new RegExp('function ' + name + '\\([\\s\\S]*?\\n\\}');
  const m = script.match(re);
  if (!m) { console.error('함수 추출 실패: ' + name); process.exit(2); }
  return m[0];
}
const SRC = ['esc', 'getStreamContent', 'closeStreamSegment', 'getOrCreateBadgeContainer', 'renderMarkdown', 'makeTyper', 'renderEventsInto']
  .map(extract).join('\n');

// ── jsdom-lite: innerHTML 설정 시 textContent 반영 (브라우저 동작 모방) ──
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this._cls = new Set(); this._text = ''; this._html = ''; this.style = {}; }
  classList = { add: (c) => this._cls.add(c), contains: (c) => this._cls.has(c), remove: (c) => this._cls.delete(c) };
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get className() { return [...this._cls].join(' '); }
  set innerHTML(v) { this._html = v; this._text = String(v).replace(/<[^>]*>/g, ''); }
  get innerHTML() { return this._html; }
  appendChild(c) { this.children.push(c); c.parent = this; return c; }
  insertBefore(c, ref) { const i = ref ? this.children.indexOf(ref) : 0; this.children.splice(i < 0 ? this.children.length : i, 0, c); c.parent = this; return c; }
  remove() { if (this.parent) { const i = this.parent.children.indexOf(this); if (i >= 0) this.parent.children.splice(i, 1); } }
  querySelector() { return null; }
  get lastElementChild() { return this.children[this.children.length - 1] || null; }
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this._text || this.children.map(c => c.textContent).join(''); }
  set textContent(v) { this._text = v; }
  addEventListener() {}
}
const document = { createElement: (t) => new El(t) };
function scrollBottom() {}
const marked = { parse: (t) => '<p>' + t + '</p>' };
const TYPING_SPEED = 0, CHUNK_SIZE = 9999;

// renderEventsInto가 호출하는 도구 배지 함수들의 stub — 버블 끝에 컨테이너만 만들어
// 순서 검증이 가능하게 (실제 배지 내부 DOM은 라이브 인터리빙 테스트가 이미 커버).
function addToolBadge(bub, name, label) {
  const c = getOrCreateBadgeContainer(bub);
  const x = new El('div'); x.className = 'tool-item'; x._text = label || name;
  c.appendChild(x);
}
function updateToolBadge(bub, name, summary) {
  const c = bub.lastElementChild;
  if (c && c.classList.contains('tool-badges')) {
    const last = c.children[c.children.length - 1];
    if (last) last._text = summary;
  }
}
function appendTerminal() {}
function appendChart() {}

eval(SRC);  // 추출한 chat.html 함수들을 현재 스코프에 정의

// ── 시나리오 ──
const norm = s => String(s).replace(/<[^>]*>/g, '');
const results = [];
function check(label, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  results.push(ok);
  console.log((ok ? '✓' : '✗') + ' ' + label + ': ' + JSON.stringify(actual) + (ok ? '' : ' (expected ' + JSON.stringify(expected) + ')'));
}
const sig = (bub) => bub.children.map(c =>
  c.classList.contains('stream-content') ? 'TEXT[' + norm(c.innerHTML) + ']'
  : c.classList.contains('tool-badges') ? 'TOOLS(' + c.children.length + ')'
  : c.className);

async function run() {
  // 1. 인터리빙: 텍스트A → 도구 → 텍스트B → 도구 → 텍스트C
  {
    const bub = new El('div'), typer = makeTyper(bub);
    const tool = () => { typer.breakSegment(); const c = getOrCreateBadgeContainer(bub); const x = new El('div'); x.className = 'tool-item'; c.appendChild(x); };
    typer.push('텍스트A'); tool(); typer.push('텍스트B'); tool(); typer.push('텍스트C'); typer.flush();
    await new Promise(r => setTimeout(r, 30));
    check('인터리빙 시간순', sig(bub), ['TEXT[텍스트A]', 'TOOLS(1)', 'TEXT[텍스트B]', 'TOOLS(1)', 'TEXT[텍스트C]']);
  }
  // 2. 연속 도구: 텍스트 후 도구 3개 연속 → 한 컨테이너, 빈 세그먼트 없음
  {
    const bub = new El('div'), typer = makeTyper(bub);
    const tool = () => { typer.breakSegment(); const c = getOrCreateBadgeContainer(bub); const x = new El('div'); x.className = 'tool-item'; c.appendChild(x); };
    typer.push('시작'); tool(); tool(); tool(); typer.flush();
    await new Promise(r => setTimeout(r, 30));
    check('연속 도구 묶임', sig(bub).map(s => s.startsWith('TEXT') ? 'TEXT' : s), ['TEXT', 'TOOLS(3)']);
  }
  // 3. 도구 먼저(설명 없이): 빈 텍스트 세그먼트 안 생겨야
  {
    const bub = new El('div'), typer = makeTyper(bub);
    const tool = () => { typer.breakSegment(); const c = getOrCreateBadgeContainer(bub); const x = new El('div'); x.className = 'tool-item'; c.appendChild(x); };
    tool(); typer.push('결과 요약'); typer.flush();
    await new Promise(r => setTimeout(r, 30));
    check('도구 먼저', sig(bub), ['TOOLS(1)', 'TEXT[결과 요약]']);
  }
  // 4. 텍스트만 (도구 없음): 단일 세그먼트
  {
    const bub = new El('div'), typer = makeTyper(bub);
    typer.push('안녕'); typer.push(' 반가워'); typer.flush();
    await new Promise(r => setTimeout(r, 30));
    check('텍스트만 단일세그먼트', sig(bub), ['TEXT[안녕 반가워]']);
  }
  // 5. 재방문 복원: renderEventsInto가 저장된 events를 시간순 DOM으로 (라이브와 동일).
  {
    const bub = new El('div');
    renderEventsInto(bub, [
      { type: 'text', data: '실행할게.' },
      { type: 'tool', name: 'bash_exec', label: '실행 중', call_id: 'c1', status: 'done', summary: '✓ echo hello' },
      { type: 'text', data: 'hello' },
    ]);
    check('재방문 events 복원', sig(bub), ['TEXT[실행할게.]', 'TOOLS(1)', 'TEXT[hello]']);
  }
  // 6. 재방문 중단: aborted 마커가 ⏹로 복원.
  {
    const bub = new El('div');
    renderEventsInto(bub, [
      { type: 'tool', name: 'x', label: 't', call_id: 'c', status: 'done', summary: '✓ x' },
      { type: 'aborted' },
    ]);
    const hasStopped = bub.children.some(c => c.classList.contains('stopped-mark'));
    check('재방문 중단마커', hasStopped, true);
  }

  process.exit(results.every(Boolean) ? 0 : 1);
}
run();
