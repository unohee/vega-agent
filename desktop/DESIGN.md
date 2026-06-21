# VEGA Desktop Design System

Conventions for VEGA's desktop UI. Read this before adding a component, overlay,
style, or window. It borrows the philosophy of hermes-agent's
`apps/desktop/DESIGN.md` but is **rewritten for our stack** — hermes is
Electron + React + Tailwind + nanostores, while VEGA is fundamentally different:

| Concern | hermes | VEGA |
| --- | --- | --- |
| Shell | Electron | **Tauri v2 (Rust)** — `desktop/src/lib.rs` |
| Renderer | React + TSX + Tailwind | **vanilla HTML/CSS/JS** (`web/static/chat.html`) |
| State | nanostores | plain JS + DOM + SSE |
| Styling | Tailwind tokens + `.tsx` primitives | CSS `:root` vars + hand-rolled classes |
| Chat backend | `hermes dashboard` gateway | **FastAPI `localhost:8100`** + SSE |
| i18n | `useI18n()` hook, 4 locales | `data-i18n` attrs + `applyLang()`, **en/ko** |
| Theme | light/dark tokens | **dark only** (hardcoded) |

So hermes's concrete primitives ("Button.tsx / control.ts / nanostores") do not
exist for us. Instead we map hermes's **invariant principles** onto our reality:

> One source per concern, tokens over literals, flat over boxed — plus VEGA's
> first-class principle: **safety, reversibility, visibility.**

This document governs **every VEGA frontend surface** (not one React app, but
several HTML surfaces):

| Surface | File | Kind |
| --- | --- | --- |
| Chat (home / Agent View) | `web/static/chat.html` (~7.2k lines) | FastAPI-served, loaded in WebView |
| Boot splash | `desktop/dist/index.html` | Tauri-native (shell's first screen) |
| Settings | `desktop/dist/settings.html` (~1.8k lines) | Tauri-native window |
| Client settings | `desktop/dist/client-settings.html` | Tauri-native window |
| Dashboard / install wizard | `web/static/dashboard.html`, `install_wizard.html` | FastAPI-served |

---

## 0. Product-rooted principles (VEGA-specific — the layer hermes lacks)

Every UI decision passes through `CLAUDE.md`'s product direction first.
**Power-user AI, without the terminal.** The audience is non-developer power
users who don't want to live in a terminal. Their barrier isn't *capability*,
it's *fear* (will I break the system, what did I just click, will I get stuck in
settings). Therefore:

1. **Safety, reversibility, and visibility are first-class design goals.**
   Destructive or outward-facing actions get a visible approval boundary. Always
   show "what just happened." This is the sales point, not an afterthought.
2. **Honest feedback — no fake theater.** The boot splash (`index.html`) shows a
   real **backend log tail** (`window.vegaLog`), not a fake staged animation.
   Progress is interpolated so it never *looks* stuck, but it follows the real
   target Rust reports. Every progress/completion indicator must reflect real
   state.
3. **No setup tax.** Sane defaults remove friction to the first message. The
   onboarding modal (`chat.html` `#ob-*`) is skippable and states that anything
   can be changed later in settings.
4. **Local-first.** The default screen must work fully without a cloud account.
   Cloud features *add to* the existing screens; they don't make the base screen
   depend on the cloud.
5. **Don't become a toy desktop app.** Ease comes from making power safe and
   comprehensible, not from removing the power-user surface. Don't hide tool
   execution, file access, or remote actions — expose them with visible
   permission/approval flows.
6. **User copy in non-developer language.** Lead with "AI workspace / no terminal
   required / connect your models, files, apps." Keep "LLM orchestration /
   MCP-first / daemon architecture" as internal vocabulary — it does not appear
   on a screen the user sees first.

---

## 1. UI principles (borrowed from hermes + our reality)

1. **Flat, not boxed.** No card-in-card, no divider-boxes inside a panel. Group
   with whitespace and a single hairline (`--border`); never nest rounded boxes.
2. **Borderless + shadow for elevation.** Overlays/modals (`.modal-window`)
   float, with a shadow + faint border instead of a hard border. The current
   `.modal` pattern is the source — new overlays reuse it.
3. **One primitive per concern.** Buttons, inputs, search, loaders, error states
   should converge onto a single class family. Don't fork; migrate onto them.
   *(Current gap: see §4 — buttons are forked into several families. New code
   must not grow another family; join the consolidation direction.)*
4. **Tokens, not literals.** Colors and repeated dimensions reference `:root` CSS
   vars (`--bg`, `--accent`, …). Don't bake raw hex / ad-hoc rgba into
   components. *(Current gap: chat.html has 136 hex literals, 105 rgba(). New/
   touched code uses tokens and migrates nearby literals as you go.)*
5. **Style lives in the primitive.** Variants/sizes own padding, radius, color,
   chrome. Call sites pick a variant class (`btn-primary`, …); they don't
   override padding/color inline on top of it.

> This doc separates "how it is now" from "how it should be." Items marked
> *Current gap* are known debt — new code follows the target, touched code is
> cleaned up incrementally (no big-bang).

---

## 2. Color & tokens

### Single token source (target) — today each surface forks its own `:root`

Dark-only theme, GitHub-dark-family palette. **Tokens are currently defined
per-surface, which has caused drift** — this is the #1 debt to fix first:

| Token | `chat.html` | `settings.html` | Note |
| --- | --- | --- | --- |
| `--bg` | `#0d1117` | `#0d1117` | ✅ match |
| `--surface` | `#161b22` | `#161b22` | ✅ match |
| `--surface2` | `#1c2128` | `#21262d` | ⚠️ **drift** |
| `--border` | `#30363d` | `#30363d` | ✅ match |
| `--text` | `#c9d1d9` | `#e6edf3` | ⚠️ **drift** |
| `--muted` | `#8b949e` | `#8b949e` | ✅ match |
| `--accent` | `#58a6ff` | `#58a6ff` | ✅ match |
| `--user-bg` | `#1f6feb` | — | absent in settings |
| `--ok` | *(referenced, undefined)* | `#3fb950` | ⚠️ `var(--ok)` is invalid in chat |
| `--error` | `#f85149` | `#f85149` | ✅ match |
| `--sidebar-w` | `260px` | — | layout token |

**Rules:**
- New colors must be tokens. Don't bake raw `#hex` / ad-hoc `rgba()` into
  components. If an accent needs transparency, converge on a token instead of
  one-offs like `rgba(88,166,255,.12)`.
- **The same semantic token must hold the same value across surfaces.** Drifted
  values like `--text`/`--surface2` are bugs. When you touch a token, align
  chat/settings to the same value.
- Don't reference a token without defining it (`var(--ok)`). The `:root` of the
  surface that uses it must define it.
- Dark-only is intentional. Don't slip in a light theme via
  `prefers-color-scheme` (a light theme is a separate product decision). Design
  so that one place owns the tokens, making a future theme switch possible.

### Fonts

- UI: `-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`
- Logs/console/code/key values: `ui-monospace, SFMono-Regular, Menlo, monospace`
- Don't pull in a new font outside these two families.

---

## 3. Surfaces & windows (Tauri shell integration)

VEGA has two kinds of window with different constraints, so *where* you put
something matters.

### (a) Remote page loaded in a WebView — `chat.html` (main window)

Loads `localhost:8100`. **Here, Tauri custom `invoke` is blocked by the ACL.**
It's a remote origin, so calling a custom command directly is blocked (a trap
we've hit twice). When you need a shell capability:

- Rust → JS: send a signal via the window's `eval` / event emit (e.g. the
  splash's `window.vegaProgress`).
- JS → Rust: instead of a custom `invoke`, use the **event emit + Rust
  `listen_any`** pattern.
- **Handle external links at the navigation level.** In the main window's
  `on_navigation`, send external http(s) to the OS browser (`open_url`) and only
  allow internal paths in the WebView. Because JS `invoke` is blocked, the
  navigation hook is the root fix (regression fixed in 0.1.41).

### (b) Tauri-native windows — `settings.html`, `client-settings.html`, splash `index.html`

Local assets bundled via `frontendDist: "dist"`. Here `withGlobalTauri: true`
gives you the Tauri JS API. Note the settings UI lives in **`desktop/dist/`**,
not `web/static` — don't confuse them.

### Boot splash (`index.html`) — the exemplar of honest feedback

- Rust (`wait_and_navigate`) calls `window.vegaProgress(pct, label)` per boot
  stage.
- `window.vegaLog(line)` tails the **real shell/backend log** (last 12 lines). It
  is not a staged animation. Use this honesty as the bar when building new
  progress UI.
- Progress self-interpolates so it never looks stuck, but follows the higher real
  target from Rust. It caps at 85% when signals stop, so it never fakes a "100%."

### Modals / overlays

- `chat.html`'s `.modal` / `.modal-backdrop` / `.modal-window` / `.modal-header`
  / `.modal-close` are the source. New overlays reuse this shell; don't rebuild a
  titlebar/backdrop.
- Close is an x icon (`.modal-close`), not the word "Close."

---

## 4. Buttons — current fork + convergence direction

hermes has a single `button.tsx`, but **VEGA's buttons are currently forked into
several families** (honest current state):

| Family | Location | Use |
| --- | --- | --- |
| `.panel-btn` | chat.html L77 | sidebar/terminal/explorer toggle icon buttons |
| `.btn-primary` / `.btn-secondary` / `.btn-ghost` | chat.html L714 | general actions |
| `.ob-btn` / `.ob-btn-primary` / `.ob-btn-ghost` | chat.html L626 | onboarding modal only (duplicate) |
| `.modal-close` | chat.html L606 | modal close |
| `.send` | chat.html | send |
| (settings's own buttons) | settings.html | defined separately in settings |

**Rules (direction):**
- New buttons should converge onto `.btn-primary` / `.btn-secondary` /
  `.btn-ghost` where possible. Don't create a screen-specific button family like
  `.ob-btn*` — absorb it into a variant.
- primary = `--accent` fill, ghost = transparent + `--muted`, secondary =
  `--surface2` on hover. Preserve these meanings.
- Don't override `h-*`/`px-*`/color inline at the call site. If you need a new
  variant, add it to the class.
- Icon buttons use inline SVG. The button class sets icon size/color.

---

## 5. Form controls

- Text inputs / textareas / selects share one shape (border `--border`,
  background `--surface`/`--bg`, `--accent` on focus). New inputs follow it.
- **Prevent iOS auto-zoom:** input `font-size` is at least 16px (below 16px,
  Safari auto-zooms on mobile). Already caught once as a mobile trap.
- Toggles/switches and segmented choices use a single control instead of a radio
  pile. Don't wrap new ones in a bordered text wrapper.

---

## 6. Layout

- **Sidebar width is a token (`--sidebar-w`).** Don't hardcode it.
- **Three panel toggles:** session list (⌘B) / terminal (⌘J) / file explorer
  (⌘⌥B), toggled via `.panel-btn`.
- **Mobile:**
  - The sidebar is a direct child of `body` to escape the stacking context
    (nested, z-index breaks).
  - The drawer's close must be called *before* the `await` (timing trap).
  - The input row must not collapse at narrow widths: fold the attach/mic buttons
    into a `+` menu (INT-1589). Always check that controls don't overflow under a
    narrow-width assumption.
- A divider between rows only when genuinely needed, as a single `--border`
  hairline. Default to whitespace.

---

## 7. Feedback / empty / error / loading

- **Loading:** never look stuck, but never fabricate progress (see §3 splash).
- **Streaming render is a fragile area.** Known traps (must be guarded by
  regression tests):
  - Wrong usage-meta flush order re-renders the body (duplicate).
  - Infinite reconnect silently stops streaming.
  - A `loadSession` race breaks the optimistic switch.
  - The typer (progressive render) is a block-delta parse + catch-up structure —
    when changing `makeTyper`, also update the `interleave_runner.js` stub /
    harness regexes.
- **Errors:** one look. Error color is `--error`. A user-facing error says what
  happened, why, and what to do next (fear relief = §0 principle).
- **Log display:** monospace, dark background, tight padding (the splash
  `.console` and settings `#dsLog` are the exemplars). Reuse this look anywhere
  raw logs are surfaced.
- **Empty states:** reuse a shared pattern instead of hand-rolling a centered
  empty each time.

---

## 8. Iconography & brand

- Icons are **inline SVG** (no mixing icon libraries). Color follows
  `currentColor`/tokens.
- The brand glyph is the "VEGA" wordmark (splash `.logo`, letter-spacing -1px,
  `#e6edf3`). Don't scatter decorative sparkle/star icons — use the wordmark for
  brand moments.

---

## 9. Motion

- Quick, functional transitions (~100ms on controls). The splash progress bar is
  `cubic-bezier(0.4,0,0.2,1)` 0.45s.
- **Current gap:** no `prefers-reduced-motion` handling. When adding animation
  beyond a fade, respect `prefers-reduced-motion`.
- Don't let a global fade swallow the inner detail animation (delay the outer
  container's fade).

---

## 10. i18n — attribute-based + en/ko parity

Unlike hermes's `useI18n()` hook, VEGA is **attribute-based + `applyLang()`**:

- Tag markup with keys: `data-i18n="key"` (text), `data-i18n-title="key"`
  (title/aria), `data-i18n-placeholder="key"` (placeholder).
- The dictionary is `const I18N = { en: {...}, ko: {...} }` (settings.html L457,
  etc.). `applyLang(lang)` walks the DOM and substitutes (chat.html L7198).
- **Always update both locales together.** Adding a key to only `en` or `ko` is a
  regression (the other leaks hardcoded Korean or misses the key). Keep
  punctuation and tone aligned across both.
- **New UI strings must:** ① be exposed via `data-i18n*` attributes, ② add keys
  to both `en` and `ko`. The fallback is the default Korean text in markup, not a
  JSX literal.
- Verification: with Playwright, assert zero residual Korean after
  `applyLang('en')` (guards settings i18n regressions).

---

## 11. State & JS

- No nanostores, no React. Plain JS + DOM + SSE streams. Global mutable state
  (streaming flags, queue, session id) is owned in one clear place and resets
  idempotently on reentry/reconnect.
- **Python import trap:** shared module-level state reassigned after
  `from x import y` doesn't change the original — write `_state_mod.VAR = val`
  (backend side).
- Streaming/session logic can't be vetted by reading code alone (TDZ, races, grid
  placement) → verify with Playwright E2E before committing (§13).

---

## 12. Affordances & accessibility

- Clickable elements get `cursor: pointer` — at the component class level, not
  per call site.
- Every interactive element gets an `aria-label` (icon buttons especially).
  Toggles expose state via aria.
- `Esc` closes overlays/modals (except ones that must not close, like onboarding
  / install wizard).
- Keyboard shortcuts (⌘B/⌘J/⌘⌥B, etc.) are noted in title/aria.

---

## 13. Editing & verification rules (VEGA operational reality)

- **Where to edit:** chat UI in `web/static/chat.html`, native windows in
  `desktop/dist/*.html`, shell behavior in `desktop/src/lib.rs`. When you fix one
  surface, check that the same-meaning bits on other surfaces (tokens, copy)
  don't drift.
- **UI is verified E2E-first.** Not just CSS/layout but JS state logic (streaming
  flags, queue, reconnect) is verified with Playwright before committing. Reading
  code alone misses TDZ clashes, grid placement, and races.
- **LLM-dependent flows:** instead of fixed `wait`, use `wait_for_function` +
  `page.route` to reproduce without an LLM (avoids non-deterministic failures).
- **Verify regression tests by mutation.** After writing a regression test,
  reinject the bug to confirm it goes red, then restore (a green-only run gives
  false safety).
- **Frozen-build self-sufficiency:** distinguish dev assumptions (mlx_env, system
  Python, Docker) from the standalone frozen build. Confirm the backend paths the
  UI calls are bundled, via a frozen build.

---

## 14. Before you add something — checklist

- [ ] **Passes the product principles?** Visible approval boundary for
      destructive/outward actions + "what just happened" visibility? (§0)
- [ ] Reflects real state, with no fake progress/completion theater? (§0·§7)
- [ ] Reuses an existing family (`.btn-*`, `.modal-*`, `.panel-btn`) — no new
      fork? (§1·§4)
- [ ] Colors/dimensions are tokens (`--bg`/`--accent`/`--sidebar-w`, …)? Zero raw
      hex / one-off rgba? (§2)
- [ ] Same-meaning tokens hold the same value across chat/settings? Zero
      undefined references like `var(--ok)`? (§2)
- [ ] Flat — no card-in-card, no gratuitous row dividers? (§1)
- [ ] On the remote page (chat), shell capabilities use the event/navigation
      pattern, not `invoke`? (§3)
- [ ] External links go to the OS browser (navigation hook), not inside the
      WebView? (§3)
- [ ] Both `en` and `ko` keys updated + `data-i18n*` exposed for new/changed
      strings? (§10)
- [ ] At narrow mobile widths, controls don't collapse, and input `font-size`
      ≥ 16px? (§5·§6)
- [ ] `cursor-pointer`, `aria-label`, `Esc`-to-close behave? (§12)
- [ ] UI/state changes verified by Playwright E2E before committing? (§13)
