---
name: documents
description: Review and update the core docs (README, CHANGELOG, ARCHITECTURE) of the current working folder to match code changes.
argument-hint: "[target folder, or blank = current working folder]"
---

# Documentation Maintenance Workflow

Review and update the core docs of the current working folder (or the path given as an argument if none). Work directly via `file_read`/`bash_exec`.

## Documents to Review
| Document | Purpose | Condition |
|------|------|------|
| README.md | Overview of installation, usage, and structure | Always |
| CHANGELOG.md | Change history (Keep a Changelog format) | If there are code changes |
| ARCHITECTURE.md | Convey module responsibilities and data flow to other LLMs | Always |

## Procedure
1. Use `bash_exec` to understand the folder structure: `ls -la` + a list of the main source files + `git log -10 --oneline` (if available).
2. If a document is missing, **create** it; if it exists, **update** it to match recent changes.
3. In CHANGELOG, organize the latest changes under `## [Unreleased]` as added/changed/fixed.
4. In README, fix anything that diverges from the actual code (install commands, usage examples).
5. In ARCHITECTURE, concisely cover per-directory responsibilities + the core data flow.

## Principles
- Don't write based on guesses; read the actual code and reflect it.
- Do not declare "done" before reviewing all four documents.
- Wrap-up: summarize in 1–3 lines which documents you created/updated.
