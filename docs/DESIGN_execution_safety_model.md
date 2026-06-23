# Design — Execution & Safety Model: Host-First, Policy-Sandboxed (Docker-Optional)

> Status: **Proposed** (2026-06-23). Supersedes the implicit "Docker-preferred" execution assumption.
> Builds on `docs/plan_docker_free_local_exec.md` (host fallback — now largely implemented via INT-1840/1843)
> and `ARCHITECTURE.md`. Related memory: `project_self_modification`, `project_positioning_fear_not_terminal`,
> `reference_frozen_interpreter_local_exec`.

## 1. Decision

Make **host execution the single default path**. Reconstruct "the sandbox" as a **policy + guardrail layer,
not a container**. Demote Docker from default-when-present to an explicit opt-in (Phase A), and a candidate for
removal (Phase C).

Safety is provided by, in order of reliance:
**system-wide allow/deny policy → runtime guardrails → system-prompt constraints → permission/approval modes →
reversibility + visibility → deterministic gates for unattended (autonomous) runs.**

The gates are **deterministic, not an LLM auditor** (decision 2026-06-23): VEGA's self-modification is already
gated by a protected-tool list + static safety check + runtime test + explicit user approval (`self_improve.py`),
which is the Claude-Code model — approval and reversibility, not probabilistic review. (An earlier design note
proposed a "gemma-26B auditor"; it was never built and is explicitly rejected — deterministic gates suffice.)
Docker is an inconsistent leftover, not the chosen mechanism.

## 2. Rationale

**Empirical.** A coding agent (Claude Code) has been run on this machine for a long time with **no container
sandbox**. The effective safety boundary in practice was never isolation — it was *approval + guards + reversibility
+ visibility*. The accident rate from "no container" was lower than the cost of the Docker dependency.

**Docker is net technical debt here.** Verified this session:
- **"Looks broken" routing.** When the Docker daemon is up, `_docker_or_host` / `_office_exec` route through
  Docker. Docker mounts the user's home as `/host_home` **read-only** (only `/vega_data` is writable), so
  `office`/`pdf_create` writes to `~/` *fail* — a working task appears broken. Cold-start and missing-image add
  more spurious failure surface.
- **Hard dependency remnants.** `self_improve` patch testing and persistent skills
  (`sandbox_save_module` / `sandbox_list_skills`) call `sandbox_python` directly with **no host fallback**.
- **Maintenance + fragility.** `sandbox.py` owns image pull, `compose up`, and even `brew install --cask docker` /
  Docker Desktop installation flows — the most cross-platform-fragile surface in the app (esp. Windows).

**Product fit.** VEGA targets non-developer power users; principles are local-first, no-setup-tax, power-with-control.
Preferring or requiring Docker contradicts all three. The barrier for this audience is *fear* (breaking the system,
not knowing what happened) — the answer is **visible boundaries + reversibility**, not a container they can't see.

## 3. Threat model (what the sandbox is actually for)

The goal is **accident prevention + secret protection + reversibility**, not adversarial containment.

- A local personal agent executing code the user requested is not an untrusted multi-tenant workload.
- Even Docker-with-no-network still allowed home read access; it was never a true adversarial boundary here.
- Defending against a determined attacker who already has arbitrary code execution *and* intent is out of scope
  for a personal-agent sandbox (and Docker did not achieve it either).

So we optimize for: never leak secrets/keys, never make irreversible destructive changes without approval, always
be able to undo, always show what happened.

## 4. The Docker-less Sandbox = layered policy + guardrails

Defense in depth. Most layers already exist (see §5).

- **L1 — System-wide allow/deny policy** (`pipeline/path_guard.py`). The core of the user's proposal, already real
  for filesystem paths:
  - *Immutable* secret denylist the user cannot unblock: `.ssh`/`.gnupg`/`.aws`/keychains (dirs), `.env*`,
    `id_rsa`/`id_ed25519`, `*.pem`/`*.key`, `client_secret`/`service_account`/`refresh_token` substrings.
  - Default allowed roots + a **user-editable policy** (`access_policy.json`, settings UI): add allow roots,
    add extra deny paths. Priority: **hard deny > user deny > allow**. mtime-cached for instant effect.
  - *Gap to close:* extend the same policy idea beyond paths to a **command allowlist** and a **network-egress
    policy** so `bash_exec`/`host_exec` are governed by the same model the file layer already uses.
- **L2 — Runtime guardrails.** `_guard_prelude` hooks `open`/`os.remove`/`shutil.rmtree` in `python_exec`;
  `_check_python_safeguards` and the bash safeguard statically block destructive patterns; `rm` is rewritten to
  trash. *Gap:* the bash path's guard is weaker than python's — unify.
- **L3 — System-prompt constraints.** The model is told the boundaries (what not to touch, prefer trash, ask before
  irreversible actions). The prompt sets intent; L1/L2 are the hard stop when the model errs.
- **L4 — Permission / approval modes** (`web/sessions.py`). ask / plan / auto / yolo. Risky or irreversible actions
  gate on user approval in interactive sessions.
- **L5 — Reversibility + visibility.** Trash instead of `rm`, `.bak` before overwrite, git for code; every tool
  call, terminal output, and approval prompt is rendered in chat. This is what lets a non-developer recover from
  a mistake — the actual antidote to "fear".
- **L6 — Deterministic gates for autonomous runs.** Unattended runs (heartbeat, `self_improve`) lack the
  human-in-the-loop that interactive sessions have. The substitute is **deterministic** (not an LLM auditor):
  the existing `self_improve` chain — protected-tool list + static safety check (`_check_patch_safety`) +
  runtime test (`_test_patch`) + explicit user approval before `apply_patch`. Strengthen these (dry-run, diff,
  reversibility) rather than adding probabilistic review or relying on Docker for unattended safety.

## 4b. Persistent development workspace — an accumulating catalog

**Decision.** VEGA's code execution and self-authored tooling live in a **persistent workspace under
Application Support**: `data_dir()/workspace/` (`~/Library/Application Support/VEGA/workspace/`, already a
path_guard-allowed root; overridable via `VEGA_DATA_DIR`). Not a temp dir, not a Docker volume, not the
developer's `~/dev`.

**Rationale — same principle as memory curation.** An agent that recreates scratch tooling every run accumulates
redundant, throwaway scripts (the tool-equivalent of duplicate memories). A durable, host-visible workspace turns
that into a **catalog that accrues**: each module/script VEGA builds is kept and *consulted before building a new
one*, so capability compounds instead of duplicating. This is the user's explicit intent: every build grows the
development catalog rather than spawning another one-off tool.

**Structure (proposed).**
- `workspace/skills/` — reusable modules VEGA authors (persistent skills; replaces the Docker `sandbox_sandbox_lib` volume)
- `workspace/site-packages/` — packages VEGA installs for its own use (replaces the Docker packages volume)
- `workspace/history/` — execution history (replaces the in-container `/workspace/history`)
- `workspace/projects/` — multi-file development work
- `workspace/CATALOG.md` — an index VEGA maintains and **reads first** ("list before create")

**Consequences.**
- `python_exec`/`bash_exec` default their CWD and `PYTHONPATH` to this workspace, so VEGA's own modules are
  importable across runs **without Docker**.
- Persistent skills (`sandbox_save_module` / `sandbox_list_skills`) read/write here on the host — this is precisely
  what removes their Docker dependency (the L6 gap in §5). Today these artifacts are trapped in opaque Docker
  volumes (`sandbox_sandbox_lib`, `/workspace/history`), invisible to the user and lost without the container.
- "List before create" is enforced by system-prompt instruction + a catalog-read affordance, mirroring memory
  dedup curation.

## 5. Current state — what exists vs gaps

| Layer | Exists | Gap |
|---|---|---|
| L1 path allow/deny | `path_guard` immutable denylist + user policy (`access_policy.json`) + settings UI | No command allowlist / network-egress policy |
| L1 routing | Host fallback in `_docker_or_host`, `_office_exec` (INT-1840); frozen interpreter re-entry (INT-1843) | **Docker still preferred when present** → should flip to host-first / opt-in |
| L2 guardrails | `_guard_prelude` (python), `_check_python_safeguards`, bash safeguard, trash-not-rm | Bash guard weaker than python; not all paths share one guard |
| L4 permission | `sessions.py` modes (ask/plan/auto/yolo) | Not wired to a per-tool risk classification |
| L5 reversibility/visibility | trash, `.bak`, git, chat rendering of tool calls/outputs | — |
| L6 autonomous | deterministic gates: protected-list + static check + runtime test + user approval (`self_improve`) | `self_improve` + persistent skills were **Docker-only, no host path** → resolved by the App Support workspace (§4b). (No LLM auditor — the earlier "gemma auditor" note was never built, explicitly dropped.) |

## 6. Migration phases

**Phase A — Host-first flip (immediate, low-risk, reversible).**
- Default all execution to host. Use Docker *only* on explicit opt-in (e.g. `VEGA_USE_DOCKER=1` or a settings
  toggle), instead of auto-routing whenever the daemon happens to be up.
- Establish `data_dir()/workspace/` (App Support) as the execution CWD + `PYTHONPATH` (§4b). Migrate persistent
  skills/history out of Docker volumes into this host workspace; seed `CATALOG.md`.
- Give `self_improve` and persistent skills a host execution path (remove their hard Docker dependency).
- Clean up error surfacing so an absent/!ok Docker never makes a normal task "look broken".
- *Verify:* with Docker running, `office`/`pdf_create`/`python_exec` write to `~/` successfully (no `/host_home`
  read-only failure); self_improve patch test runs without Docker.

**Phase B — Policy hardening (the real "sandbox without Docker").**
- Extend `path_guard` policy to a **command allowlist** and **network-egress policy**; apply uniformly to
  `bash_exec`/`host_exec`/`python_exec`.
- Unify the runtime guard so bash and python share one enforcement path.
- Surface the active mode in the UI ("host execution — policy-guarded"), and wire permission modes to a per-tool
  risk classification.
- Strengthen the deterministic gates for unattended runs (dry-run + diff + reversibility on `self_improve`). No LLM auditor.

**Phase C — Docker removal (deferred, optional).**
- Once A/B are proven, delete `sandbox.py`, Docker management, and `sandbox_*` tools. Large, deliberate change;
  do in its own PR/session. Skip if keeping Docker as a pure opt-in is judged worthwhile.

Order: A → B; C only after A/B are stable. Phase A alone resolves the "harness looks broken" problem.

## 7. Decisions (resolved 2026-06-23)

- **Opt-in vs removal.** → **Full removal (Phase C).** Docker is removed, not kept as a dormant opt-in — the
  policy+guardrail layer is the sandbox. (Power users who truly want container isolation can re-add it from git
  history when a real need appears.)
- **Command/network policy scope.** → **Denylist + warn** by default (usable for a personal agent), with the
  secret/key denylist remaining immutable. Not a strict command allowlist.
- **Autonomous strictness.** → **No LLM auditor.** Deterministic gates (protected-list + static check + runtime
  test + user approval). Open sub-question: how much `self_improve` approval can be safely automated for truly
  unattended runs (heartbeat) without a human.

## 8. Non-goals

- True adversarial containment (Docker did not provide it here either).
- Removing the ability of a power user to opt into stronger isolation if they want it.
