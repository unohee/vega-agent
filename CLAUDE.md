# CLAUDE.md

Guidance for AI coding agents working on VEGA Agent.

## Product Direction

VEGA is not just another chat client and not just another developer agent framework.
It is the missing middle between easy-but-limited desktop AI apps and powerful-but-hard
terminal AI setups.

The target user is a non-developer LLM power user, or someone who wants to become one:
people who already see the value of Claude, ChatGPT, Codex-style agents, OpenRouter,
local models, MCP, app connectors, and work memory, but do not want to live in a
terminal or maintain shell scripts, daemons, config files, and custom glue code.

Core positioning:

> Power-user AI, without the terminal.

> Terminal-level AI power. Desktop-app simple.

> VEGA is the AI workspace between chat apps and command lines.

The founder/user can already use LLMs effectively through terminal-level control.
VEGA exists because most people cannot access that same level of AI leverage without
learning CLI tools, bash, MCP configuration, local services, and operational details.

## Design Principles

- **Local-first by default**: the core app must remain useful without a cloud account.
- **No setup tax**: avoid requiring users to manually wire providers, MCP servers,
  local daemons, scripts, and permissions when the product can make a sane default.
- **Power with control**: local file access, tool execution, app integrations, and
  remote actions need visible permission boundaries and approval flows.
- **Bring your models**: Claude, ChatGPT, Codex-style providers, OpenRouter, and local
  models are interchangeable engines; VEGA owns the workspace, context, memory, and
  workflow layer.
- **Do not become a toy desktop app**: ease of use must not come from removing the
  power-user surface. Make the power safe and comprehensible instead.
- **Cloud is additive**: paid sync, backup, remote access, connector brokering, and
  team policy should extend the local core, not make it dependent on the cloud.

## Audience Language

Use product language that non-developer power users understand:

- AI workspace
- no terminal required
- connect your models, files, apps, and memory
- local-first
- private by default
- desktop app with power-user workflows
- works with your existing AI accounts

Avoid leading with engineering language in user-facing copy:

- LLM orchestration
- agent control plane
- MCP-first
- daemon architecture
- provider abstraction
- tool execution framework

These terms are useful internally, but they should not be the first thing a user sees.

## Free / Pro / Team Boundary

Expected product tiers:

- **Free / Local**: local desktop app, local sessions and memory, BYOK/provider setup,
  local tools, basic app connectors, automatic updates.
- **Pro**: account login, multi-Mac sync, encrypted backup, remote access, mobile/web
  clients, managed connector onboarding, premium support.
- **Team / Enterprise**: workspace policy, audit logs, SSO, centralized connector
  management, device policy, support workflows.

Do not degrade the local free product into a demo. VEGA must be genuinely useful as
a local app; paid features should mainly sell continuity, reliability, sync, remote
reach, and administration.

## Implementation Bias

When making product or architecture changes, prefer decisions that preserve this shape:

- user data under the user data directory, not in the app bundle
- secrets in Keychain or a secure backend, never in public source
- model/provider switching without losing session and memory continuity
- UI-first onboarding for integrations that would otherwise require CLI setup
- explicit approval for high-risk local or remote actions
- automatic update support for installed desktop users

If a change makes VEGA more powerful only for developers but harder for the target
non-developer power user, reconsider the UX or provide a desktop/onboarding path.
