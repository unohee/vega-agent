---
name: skill
description: Create a new slash command (skill) via an interactive wizard. You can also describe the task to build directly as an argument.
argument-hint: "[description of the task to build]"
---

# Skill Creation Wizard

Create a new slash command (skill). Once it's complete, save it to `data/commands/` with the `skill_save` tool, and from then on the user can invoke it with `/name`.

## How to proceed
If a task description is given as an argument, use it as the starting point; otherwise start with "What task should this skill automate?"

**Agree through conversation** on the following (don't ask again about what's already clear):
1. **Name** — lowercase/digits/hyphens only. Short and verb-form recommended (e.g., `deploy`, `daily-report`, `clean-logs`).
2. **Description** — one line. Shown in autocomplete and lists.
3. **Argument** — if it takes an argument, a hint (e.g., `[branch-name]`, `[--dry-run]`). Omit if none.
4. **Body (instructions)** — the step-by-step instructions VEGA actually follows when `/name` is invoked. The most important part.

## Body writing principles
- Write it as **steps that are actually executable** with the tools VEGA has (`bash_exec`, `file_read`, `host_exec`, `web_search`, gmail/calendar/drive, etc.).
- For each step, specify "what, with which tool". No vague phrasing.
- If you use an argument, reference it in the body as `$ARGUMENTS` (it's substituted with the user argument at invocation time).
- For dangerous operations (delete/push/send), state "execute after user confirmation" in the body.

## Wrap-up
1. Show the user the agreed name/description/argument/body as a **preview** and get confirmation.
2. Once confirmed, call `skill_save(name, description, body, argument_hint)`.
3. On successful save, announce "You can now use it with `/name`" and show one example invocation.

To edit an existing skill, use `skill_save(..., overwrite=true)`; to delete one, use `skill_delete(name)`.
