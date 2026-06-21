---
name: commit
description: Analyze changes and commit them as a conventional commit. Supports arguments like --no-push, --amend.
argument-hint: "[--no-push] [--amend]"
---

# Commit Workflow

Commit git changes in the current working folder (or the VEGA root if none). Run git commands directly via `bash_exec`.

## 1. Check Status
Run the following to understand what changed:
```
git status
git diff --stat
git log -5 --oneline
git branch --show-current
```
If there are no changes, reply "There are no changes to commit" and exit.

## 2. Staging
- If no files are staged, review the changed files and add only the relevant ones via `git add <file>`.
- Be careful with `git add -A`, as it may mix in unrelated changes. If the user wants to add everything, that's OK.

## 3. Commit Message
Analyze the changes and write the message in conventional commit format:
```
<type>(<scope>): <subject>

<body — what/why you changed, 2–4 lines>
```
- type: feat / fix / refactor / docs / chore / test / perf
- **Never include a Co-Authored-By line. Do not insert any AI/Claude attribution.** Commit only under the user's own name.
- Keep the subject in Korean or English, concise.

## 4. Run the Commit
Commit with `git commit -m "..."`. If the message is long, split the body across multiple `-m` flags.

## 5. Push (unless --no-push is in the arguments)
- If on the default branch (main/master), confirm with the user before pushing.
- On a feature branch, run `git push` (if there's no upstream, `git push -u origin <branch>`).
- If the `--no-push` argument is present, just commit and exit.
- If the `--amend` argument is present, amend the last commit (safe only if it hasn't been pushed).

## Wrap-up
Summarize what you committed in 1–2 lines and show the commit hash.
