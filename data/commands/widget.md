---
name: widget
description: Add a custom widget to the Agent View (home) interactively. You can also describe the widget to build directly as an argument.
argument-hint: "[description of the widget to build]"
---

# Widget Creation Wizard

Create a custom widget at the bottom of the Agent View (home screen). Once it's complete, save it with the `widget_save` tool and it will appear from the next refresh.

## Widget types
- **stat** — a large number + label (e.g., unread mail count)
- **list** — a list of items (title + subtitle)
- **text** — text/markdown (briefings, etc.)
- **action** — a form (input boxes) + run button + result area. Lets the user invoke a slash command through an office-friendly GUI.

## Creating an action widget (UI for automating repetitive tasks)

When the user wants to use a repetitive task such as "extract YouTube video metadata" through a GUI:

1. **First create the slash command** (`skill_save` tool) — in the body, reference input values as `${name}` or `$name`.
   e.g.) in the `/youtube-meta` body, something like `yt-dlp --print "%(title)s ..." ${url}`.
2. **Then create an action widget that calls that slash command** (`widget_save` tool, type='action').
   - `skill`: the slash command name created above (e.g. 'youtube-meta')
   - `inputs`: an array of input form fields. Each field is `{name, label, type:'text'|'url'|'number'|'textarea', placeholder?, required?}`.
   - `span`: since it holds a form + result, usually 2 or 3.
3. The user sees only that card on the home screen, enters input → Run → sees the result directly (without going through chat).

## Available data sources (choose only from these — security whitelist)
| source | content | suitable type |
|--------|------|-----------|
| `clock` | current time | stat |
| `session_count` | number of conversation sessions | stat |
| `skill_count` | number/list of custom skills | stat/list |
| `git_status` | number/list of changed git files in the working folder | stat/list |
| `mail_count` | number/list of important emails | stat/list |
| `project_count` | number/list of tracked projects | stat/list |
| `today_brief` | today's briefing body | text |

If you need new data that's not in the list above, tell the user "That data source isn't in the whitelist yet — it needs a handler added on the server", and suggest an alternative using the closest existing source. Arbitrary URLs/commands cannot be put into a widget.

## How to proceed
If the argument contains a widget description, use it as the starting point; otherwise start with "What widget should I make?". Agree on the following (don't ask again about what's already clear):
1. **id** — lowercase/digits/hyphens (e.g., `mail-today`)
2. **title + icon (emoji)**
3. **type** (stat/list/text/action)
4. **data source** (for stat/list/text, from the table above) or static text
5. **span** — width of 1–3 columns (text/list usually 2, action also recommended 2–3)
6. **for action type, additionally**: skill (slash command name) + inputs (array of form fields)

## Wrap-up
1. Show the agreed content as a preview and get confirmation.
2. For action type, call `skill_save` first, then `widget_save` — the two are a pair.
3. Once confirmed, call `widget_save(widget_id, title, type, ...)`.
4. After saving, announce "Refresh the Agent View to see it."

To edit, use `widget_save(..., overwrite=true)`; to delete, use `widget_delete(widget_id)`.
