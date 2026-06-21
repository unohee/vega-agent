# VEGA Agent Wiki — Schema

Development knowledge base for the VEGA Agent harness. A structured wiki, owned and maintained by the LLM, capturing architecture decisions, module behavior, bug records, experiment results, and operational know-how.

## Directory

```
wiki/
├── raw/          # Copies of external sources (git log, Linear export, etc.)
├── wiki/         # LLM-owned — recommended reading for humans
│   ├── index.md
│   ├── log.md
│   ├── entities/   # Modules, systems, people (pipeline/, server.py, LM Studio, etc.)
│   ├── concepts/   # Core concepts and patterns (tool-use loop, CE mode, compaction, etc.)
│   ├── sources/    # 1:1 source pages (commits, bug reports)
│   ├── topics/     # Topic-level syntheses (STT integration, i18n, multi-provider, etc.)
│   └── syntheses/  # Comparisons, analyses, recommendations (provider selection, performance trade-offs, etc.)
└── SCHEMA.md
```

## Naming

- All pages: kebab-case (`stt-gateway-design.md`)
- Commit-based: `commit-{sha7}.md`
- Bugs/issues: `bug-{keyword}.md`
- Features: the topic name as-is (`multi-provider-routing.md`)

## Frontmatter

```yaml
---
title: "Human-readable title"
tags: [stt, provider, pipeline]
sources: [commit-0b73449, bug-session-store]
updated: 2026-06-02
status: active | archived | superseded
---
```

## Cross-references

- Within the same wiki: `[[concepts/tool-use-loop]]` or `[[entities/pipeline-streaming]]`
- Commit: `[0b73449](../..)`
- External docs: `[ARCHITECTURE.md](../../ARCHITECTURE.md)`

## Workflow

- **The primary source of truth is the code + git commit messages + ARCHITECTURE.md**. The wiki focuses on reorganization, cross-referencing, and contradiction detection.
- **After a new feature or bug fix**, update the relevant sources/topics pages.
- **When a pitfall (landmine) is discovered**, record it immediately in concepts/ or topics/ — to prevent repeated mistakes.

## Marking contradictions

```markdown
> ⚠ [[sources/commit-abc1234]] concluded X was the cause. [[sources/commit-def5678]] fixed it, finding Y was the actual cause.
```

## Human vs LLM responsibility

- **LLM**: Authoring/updating all pages, cross-referencing, managing index/log.
- **Human**: Deciding SCHEMA changes, adding/removing major categories, directing work priorities.
