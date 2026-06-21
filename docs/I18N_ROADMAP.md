# VEGA Multilingual (i18n) Support Roadmap

## Current Status (Phase 0 — Complete)

**2026-06-02** initial implementation complete.

- Inserted a `VEGA_STRINGS` object-based i18n system into `chat.html`, `dashboard.html`
- Added a language toggle button to the header (`KO` ↔ `EN`)
- Persist the selected language via `localStorage['vega_lang']`
- Mark static UI text with the `data-i18n`, `data-i18n-title`, `data-i18n-placeholder` attribute pattern
- Supported languages: **Korean (ko)**, **English (en)**

---

## Phase 1 — Full String Translation (priority: high)

**Goal**: replace all hardcoded Korean text with `data-i18n` markings.

### Remaining work
| File | Target area | Translation key count (estimated) |
|------|-----------|------------------|
| `chat.html` | MCP management modal | ~20 |
| `chat.html` | LLM provider modal | ~15 |
| `chat.html` | Context menu (dynamically generated) | ~10 |
| `chat.html` | Toast / error messages | ~30 |
| `chat.html` | File explorer panel | ~8 |
| `dashboard.html` | Dynamically generated text (renderMails, etc.) | ~15 |
| `install_wizard.html` | Entire install wizard | ~40 |

**Approach**: replace dynamically generated innerHTML strings with the `t(key)` helper function.

```js
// Recommended pattern — for dynamic strings
function t(key) {
  const lang = localStorage.getItem('vega_lang') || 'ko';
  return (VEGA_STRINGS[lang] || VEGA_STRINGS.ko)[key] || key;
}

// Usage example
el.innerHTML = `<button>${t('mcp_reload')}</button>`;
```

---

## Phase 2 — Externalize Translation Files (priority: medium)

**Goal**: separate translation strings from HTML to improve maintainability.

### Option A: JSON files (`data/i18n/`)
```
data/
  i18n/
    ko.json
    en.json
    ja.json   (added later)
```

Add a server endpoint `/api/i18n/{lang}`, fetched on page load.

**Pros**: translators can edit only the JSON without modifying HTML.  
**Cons**: an extra HTTP request, and possible text flicker on initial rendering (FOUC).

### Option B: keep inline + generate at build time
`scripts/generate_i18n.py` reads `data/i18n/*.json` and auto-inserts into the HTML.

**Recommendation**: use Option A in Phase 2, switch to Option B when bundling is introduced.

---

## Phase 3 — Additional Languages (priority: low)

| Language | Code | Notes |
|------|------|------|
| Japanese | `ja` | Low translation cost thanks to shared kanji |
| Chinese (Simplified) | `zh-CN` | Large user pool |
| Spanish | `es` | World's 2nd-largest language |

**Button UI change**: the toggle button needs to be replaced with a dropdown (`<select>`).

```html
<select id="lang-select">
  <option value="ko">한국어</option>
  <option value="en">English</option>
  <option value="ja">日本語</option>
  <option value="zh-CN">中文</option>
</select>
```

---

## Phase 4 — Agent Response Language Linkage (priority: low)

**Goal**: the UI language setting is also reflected in the agent system prompt, so the LLM responds in the selected language.

### Implementation direction
1. `POST /api/lang` endpoint — store the user's language setting on the server
2. `pipeline/session_store.py` — add a `preferred_lang` field to the session metadata
3. `llm_gateway.py` — insert a language directive into the system prompt:
   ```
   Respond in English. The user has set their preferred language to English.
   ```

### Considerations
- The language setting is a user-global setting, not per-session
- Managed via the `preferred_lang` field in `data/user_profile.json`
- Integrated into the profile-save logic in `web/routers/onboarding.py`

---

## Technical Debt and Constraints

| Item | Current status | Solution |
|------|-----------|--------|
| Dynamically generated text | Hardcoded Korean | Gradually introduce the `t()` helper function |
| No RTL language support | CSS `direction` not applied | Needed when adding Arabic·Hebrew |
| No pluralization handling | Simple handling such as "3개 세션" (3 sessions) | Introduce `Intl.PluralRules` if needed |
| Date/number locale | `Intl.DateTimeFormat` not used | Recommended to integrate in Phase 2 |

---

## References

- Current implementation location: `web/static/chat.html:5400~5533`, `web/static/dashboard.html:1030~1083`
- Translation key naming convention: `{component}_{element}` (e.g. `ob_title`, `card_events`)
- `localStorage` key: `vega_lang` (value: `"ko"` | `"en"`)
