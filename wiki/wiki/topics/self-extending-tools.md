---
title: "Self-Extending Tools Design"
tags: [tools, sandbox, self-improve, onboarding, registry]
sources: [concepts/tool-use-loop, entities/llm-gateway]
updated: 2026-06-21
status: active
---

# Self-Extending Tools Design

VEGA should be able to create, verify, register, and later reuse non-risky user-requested tools such as "CSV to JSON utility", "make a simple inpainting wrapper", or "add an STT helper".

This page is an implementation design, not a completed feature. It maps the deep-research create -> verify -> register -> retrieve loop onto the current VEGA codebase.

## Research Consensus

The deep-research run (`wf_1afecf38-c6e`, 8 findings / 27 sources / 0 refuted) supports one narrow architecture: create -> verify -> register -> retrieve, with registration blocked until verification passes.

| Finding | Primary sources | VEGA decision |
| --- | --- | --- |
| Keep an accumulated tool/skill library and retrieve before regenerating. | Voyager, LATM, CRAFT | Store a durable dynamic-tool catalog and search it before `tool_create` generates anything new. |
| Verification before registration is mandatory. | Voyager ablations, CRAFT, KTCE | Generated code must pass static safety, sandbox tests, contract checks, and auditor review before it reaches `TOOL_FUNCTIONS`. |
| Tracebacks improve repair loops, but failed repairs must roll back. | CREATOR, KTCE | Feed structured failure reports back to the maker model for capped retries; leave the previous registered version untouched on failure. |
| A stronger maker/auditor and lighter user model keeps costs down. | LATM | Route generation and auditing to the code-capable tier; expose the verified tool to normal/light sessions afterward. |
| Dynamic registration should not require restart. | Docker Dynamic MCP | Hot-add schemas/functions in place and call `invalidate_check_fn_cache()`. |
| External API tools can be pre-checked from specs before live credentials exist. | ToolEmu | Validate schema and request-shape dry-runs before UI/Keychain onboarding and live smoke tests. |
| Offline verification and runtime monitoring are separate layers. | VeriGuard | Use contract checks for admission and telemetry/error counters after registration. |

## Current Code Boundaries

- `pipeline.tools` is still the flat runtime registry. New first-class tools become entries in `TOOL_SCHEMAS` and `TOOL_FUNCTIONS`.
- `pipeline.tool_registry` filters schema exposure and dispatch with `WORKSPACE_TOOLSETS`, `filter_available_schemas()`, `dispatch_gate()`, and `invalidate_check_fn_cache()`.
- `pipeline.sandbox` already has persistent sandbox modules through `sandbox_save_module()` and inventory through `sandbox_list_skills()`, but it does not compile, test, or schema-register those modules.
- `pipeline.tools_code.vega_reload_tools()` hot-reloads static `pipeline/tools_*.py` modules, but dynamic user-generated tools need a narrower hot-add path that does not reload all tool modules.
- `pipeline.self_improve` already contains `_PROTECTED_TOOLS`, `_BLOCKED_PATTERNS`, `_check_patch_safety()`, sandbox patch testing, and traceback feedback patterns. The self-extending flow should reuse those guardrails but must not reuse the "patch existing tool" semantics directly.
- `web/routers/onboarding.py` and `pipeline.keychain` already define the UI-first secret onboarding pattern: key value is accepted by UI/API, stored in Keychain, and only configured/authenticated state is exposed back to the agent.

## Code Evidence

Current implementation points that this design depends on:

- `pipeline/self_improve.py` protects side-effect and execution tools through `_PROTECTED_TOOLS`, including `host_exec`, `sandbox_save_module`, and `sandbox_list_skills`.
- `pipeline/self_improve.py` blocks obvious unsafe code strings through `_BLOCKED_PATTERNS`, including subprocess, dynamic import, file open, eval/exec, and network clients.
- `pipeline/sandbox.py` persists Python modules into `/workspace/lib/<module>.py` through `sandbox_save_module()`, but it currently writes whatever code it receives after name sanitization.
- `pipeline/tools_code.py` exposes `sandbox_save_module`, `sandbox_list_skills`, and `vega_reload_tools`; `vega_reload_tools()` reloads static tool modules and refreshes the flat registry in place.
- `pipeline/tool_registry.py` owns `invalidate_check_fn_cache()` and workspace availability gates, so dynamic registration must invalidate this cache after hot-add or credential onboarding changes.
- `web/routers/onboarding.py` verifies user-supplied provider keys before calling `pipeline.keychain.set_secret()`, which is the pattern dynamic API tools should reuse instead of letting generated code touch raw secrets.

## Scope

Allowed generated tools:

- Pure logic transforms: parsing, conversion, formatting, validation, summarization helpers, deterministic file/content generation.
- Read-only API wrappers: search/read/list/fetch style calls after credentials are onboarded through UI/Keychain.
- Generated-output helpers: image/text/file generation that writes only to VEGA data dir or an explicitly user-provided project/workspace output path.

Disallowed generated tools:

- Shell execution, subprocess orchestration, arbitrary filesystem mutation, deletion, credential reads, browser automation with side effects, sending messages, trading/order execution, and any write to external systems.
- Any tool that edits the self-extending infrastructure itself.
- Any tool that asks the agent to see, print, transform, or store raw secrets.

## Proposed Modules

Add these modules instead of expanding `pipeline.tools.py` further:

- `pipeline/dynamic_tools/models.py`
  - Typed records for `DynamicToolSpec`, `DynamicToolArtifact`, `VerificationReport`, and `CatalogEntry`.
- `pipeline/dynamic_tools/catalog.py`
  - Loads/saves `<data_dir>/dynamic_tools/catalog.json`.
  - Provides name lookup, description search, dedup scoring, and quality/bloat counters.
- `pipeline/dynamic_tools/safety.py`
  - Wraps `pipeline.self_improve._check_patch_safety()` and adds AST checks for imports, file writes, network clients, global side effects, and schema/function mismatch.
- `pipeline/dynamic_tools/verifier.py`
  - Runs generated tests in sandbox, validates JSON schema, imports the generated module, executes deterministic test cases, and emits a structured report.
- `pipeline/dynamic_tools/runtime.py`
  - Loads verified artifacts from disk, creates sandbox-backed wrapper callables, updates `TOOL_FUNCTIONS` and `TOOL_SCHEMAS` in place, and calls `invalidate_check_fn_cache()`.
- `pipeline/dynamic_tools/workflow.py`
  - Orchestrates create -> verify -> register -> retrieve with retry/traceback feedback.

Add only one static management tool in v1:

- `tool_create`
  - Inputs: natural-language request, optional scope (`session` or `profile`), optional example inputs/outputs.
  - Outputs: existing-tool reuse decision, pending-key onboarding state, registered tool name, or structured verification failure.
  - Must be added to `_PROTECTED_TOOLS` before launch so generated tools cannot modify their own creation infrastructure.

Persist generated code under the user data dir rather than the repo:

```text
<data_dir>/dynamic_tools/
  tools/<tool_name>.py
  tests/<tool_name>_test.py
  reports/<tool_name>.json
  catalog.json
```

The repo `data/` directory may carry only examples or default seed catalog entries. User-created artifacts belong in the user data dir so app updates do not overwrite them.

## Tool Creation Workflow

1. Classify the request.
   - If the requested action is risky or has external side effects, refuse creation and suggest a manual integration path.
   - If a similar catalog entry already exists, route to update/reuse instead of creating a duplicate.
2. Produce a strict tool contract.
   - Name, description, JSON schema, return envelope, required packages, auth needs, and safety class.
   - Contract must say whether the tool is pure logic, read-only API, or generated-output helper.
3. Generate implementation and tests.
   - Maker tier should be the stronger code model.
   - Required outputs: one Python function, one JSON schema, deterministic unit tests, and short inline docstring.
4. Run pre-registration verification.
   - Static blocklist and AST checks.
   - Syntax compile and import check.
   - Generated tests in sandbox.
   - Contract check: function signature matches schema, return value is JSON-serializable dict, no raw secret fields, no undeclared side effects.
   - For read-only API wrappers, run spec dry-run first and require UI/Keychain onboarding before any live smoke test.
5. Retry with traceback feedback.
   - Feed only the structured error and traceback snippet back to the maker.
   - Cap retries. If quality does not improve, do not register.
6. Register only verified artifacts.
   - Save artifact, report, and catalog entry.
   - Hot-add wrappers to `TOOL_FUNCTIONS` and schemas to `TOOL_SCHEMAS`.
   - Call `pipeline.tool_registry.invalidate_check_fn_cache()`.

## Runtime Registration Shape

Dynamic tools should not directly import arbitrary generated modules into the host process for execution. The host registry should hold a small stable wrapper:

```python
def _dynamic_tool_wrapper(**kwargs):
    # Run generated tool inside sandbox with JSON args.
    # Parse the last JSON object from stdout.
    # Return {"error": "..."} on sandbox/import/runtime failure.
```

The wrapper can import the generated module inside the sandbox from `/workspace/lib` or from a mounted dynamic-tools dir. The host process owns schema exposure, catalog lookup, telemetry, and error normalization.

For hot registration:

```python
TOOL_FUNCTIONS[spec.name] = wrapper
TOOL_SCHEMAS.append(spec.schema)
invalidate_check_fn_cache()
```

If a tool is updated, replace the function in place and replace the schema by name. Do not append duplicate schemas.

## Catalog And Dedup

Catalog entry fields:

- `name`
- `description`
- `schema`
- `safety_class`
- `artifact_path`
- `test_path`
- `report_path`
- `created_at`, `updated_at`
- `version`
- `usage_count`
- `last_error`
- `quality`: test count, code size, dependency count, retry count, and last verification status
- `embedding`: optional description embedding when an embedding provider is configured

Dedup should use a conservative two-step gate:

1. Fast lexical comparison over normalized name, description, and schema parameter names.
2. Optional embedding similarity when available.

Initial policy:

- High similarity and compatible schema: reuse existing tool.
- High similarity but missing behavior: update existing tool through the same verification gate.
- Ambiguous similarity: ask the user whether to reuse or create.
- Low similarity: create new tool.

Do not make dedup depend on embeddings only. VEGA must still work with no embedding provider.

## External API Tools And Secret Boundary

Generated API tools may declare a required key, but they must not receive or store the key directly.

Flow:

1. Generated spec declares `auth.key_env`, `auth.label`, `auth.verify_url`, and `auth.verify_header`.
2. UI prompts the user for the key, following the existing onboarding pattern.
3. Server stores the key through `pipeline.keychain`.
4. Dynamic tool `check_fn` probes only configured/authenticated state.
5. Runtime wrapper resolves the key inside trusted host code and passes only a short-lived request context into the sandbox, or executes the HTTP call through a host-owned read-only proxy.

Preferred first implementation: do not allow generated sandbox code to perform live network calls. Instead, generated code builds a validated request spec, and a trusted host helper performs the read-only HTTP request after auth and URL checks.

## Safety Gates

Static gates:

- Reuse `_BLOCKED_PATTERNS` from `pipeline.self_improve`.
- Add AST checks for `Import`, `ImportFrom`, `Call`, `Attribute`, and global assignment.
- Reject `open`, `Path.write_*`, `os`, `subprocess`, `socket`, dynamic imports, `eval`, `exec`, and any module outside an allowlist.
- Reject non-empty top-level executable statements except constants, imports from allowlist, function/class definitions, and schema/test constants.
- Reject empty files, zero-byte artifacts, syntax errors, and missing callable.

Sandbox gates:

- Compile generated module.
- Import generated module.
- Run generated tests.
- Run at least one contract smoke call generated from the JSON schema.
- Enforce timeout, output-size cap, JSON-serializable result, and no raw secret-looking values in stdout/result.

Auditor gate:

- Check whether implementation satisfies contract and whether tests cover success plus at least one bad input path.
- Auditor failure is blocking in early versions. If this is too slow, keep auditor as a strict-release gate and rely on sandbox tests for session-scoped drafts.

## Rollback

Do not rely on per-tool `.bak` files as the main rollback boundary. Use append-only versioned artifacts:

```text
tools/<name>/v1/tool.py
tools/<name>/v1/schema.json
tools/<name>/v1/report.json
tools/<name>/current.json
```

Registration points to `current.json`. Updating a tool writes `vN+1`, verifies it, then atomically switches `current.json`. Failed verification leaves the previous version registered.

## Session Scope Vs Profile Scope

Start with two scopes:

- `session`: available only in the current conversation/session, lower persistence risk, useful for one-off utilities.
- `profile`: durable across app restarts and exposed in normal tool schema retrieval.

Promotion from session to profile requires a stricter verification report and explicit user confirmation until enough telemetry proves the flow stable.

## Implementation Plan

Phase 1: pure logic, profile catalog disabled by default.

- Add `dynamic_tools` package.
- Add `tool_create` management tool in `pipeline.tools_code`.
- Generate only pure Python logic tools with no network and no file writes.
- Persist to user data dir, run sandbox verification, hot-add into the flat registry.
- Tests: catalog load/save, duplicate schema replacement, safety rejections, successful pure tool registration, failing generated test prevents registration.
- Acceptance: no generated tool can register if its artifact is empty, syntactically invalid, unsafe by static checks, has no schema-matching callable, fails generated tests, or returns non-JSON-serializable output.

Phase 2: catalog retrieval and session/profile scopes.

- Add dedup lookup before creation.
- Add session-scoped tools and promotion path.
- Add telemetry counters and bloat cap.
- Acceptance: duplicate schema names are replaced in place, similar requests reuse/update instead of appending a second catalog entry, and session tools disappear after session scope ends.

Phase 3: read-only API wrappers.

- Add UI/Keychain onboarding metadata.
- Add read-only host HTTP proxy and URL allowlist/SSRF guard.
- Add dry-run request-spec validation before live smoke tests.
- Acceptance: generated code never receives raw API keys, request specs are validated before live network, and unauthenticated tools are hidden or return a connect hint.

Phase 4: update existing generated tools.

- Add verified in-place update.
- Add rollback to previous version.
- Add quality regression rejection.
- Acceptance: failed updates leave the previous `current.json` and registered wrapper intact.

## Minimum Test Matrix

- `test_catalog_round_trip`: catalog entries survive save/load and preserve schema/version/report paths.
- `test_safety_rejects_execution_and_secret_access`: generated code containing subprocess, open, Keychain access, dynamic import, eval/exec, or network clients is rejected before sandbox execution.
- `test_empty_or_syntax_error_artifact_rejected`: zero-byte files and syntax errors cannot produce a verification report with `ok=true`.
- `test_verified_pure_tool_hot_adds_once`: a simple deterministic generated tool appears in `TOOL_FUNCTIONS` and `TOOL_SCHEMAS`, and repeated registration replaces by name rather than appending duplicates.
- `test_generated_test_failure_blocks_registration`: failing tests produce a structured report and no registry mutation.
- `test_contract_smoke_call_blocks_bad_return`: non-dict or non-JSON-serializable return values are rejected.
- `test_profile_update_is_atomic`: v2 write failure keeps v1 current and callable.
- `test_api_tool_requires_onboarding`: read-only API tool specs expose a connect hint until the declared key is configured through Keychain.

## Open Decisions

- Dedup thresholds: start lexical-only, then tune with real catalog examples before enabling embedding similarity decisions.
- Auditor strength: decide whether gemma auditor is blocking for every session tool or only for profile promotion.
- HTTP execution boundary: prefer host-owned read-only proxy; allowing generated sandbox networking should stay out of v1.
- UI entry point: reuse onboarding modal for API keys, but tool creation status probably needs a separate progress surface in chat.

## Non-Goals

- No self-modifying changes to `pipeline.tools.py`, `pipeline.tools_code.py`, or `pipeline.self_improve.py` from generated tools.
- No generated tools with destructive filesystem or external write side effects.
- No raw secret exposure to the agent transcript, generated code, sandbox stdout, or catalog.
- No guarantee that generated tools are correct without verification. Unverified artifacts must not be registered.
