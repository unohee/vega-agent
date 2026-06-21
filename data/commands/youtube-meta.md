---
name: youtube-meta
description: Extract metadata such as title, channel, thumbnail, and length from a YouTube video URL (oEmbed + web_fetch)
argument-hint: "<url>"
---

# YouTube Metadata Extraction

## 🚫 Absolutely forbidden tools
**bash_exec, python_exec, host_exec, sandbox_exec — never call these tools.**
The sandbox has internet blocked, and host_exec hangs forever because the widget has no approval UI.
Use only **`web_fetch`**.

## Input
- `${url}` — YouTube video URL (when invoked from an action widget)
- `$ARGUMENTS` — when invoked directly from chat

## Procedure (web_fetch only, at most 2 calls)

**Important: call web_fetch exactly twice. Do not call the same URL twice.**

1. Normalize the URL: if it's in the form `youtu.be/<id>` or `youtube.com/shorts/<id>`, convert it to `https://www.youtube.com/watch?v=<id>`.
   If the URL is empty, fall back to `$ARGUMENTS`. If both are empty, respond with a short error.

2. **web_fetch call #1**: `https://www.youtube.com/oembed?url=<URL>&format=json`
   From the response JSON, extract: `title`, `author_name`, `author_url`, `thumbnail_url`

3. **web_fetch call #2**: `<original URL>` (the YouTube page)
   From the response body, extract all at once with regex (use "—" on match failure):
   - length: `"lengthSeconds":"(\d+)"` → seconds → m:ss format
   - view count: `"viewCount":"(\d+)"` → 1,234,567 format
   - upload date: `"uploadDate":"(20\d\d-\d\d-\d\d)"`
   - description: `"shortDescription":"((?:[^"\\]|\\.)*)"` — first 500 chars (unescape \\n, \\")

4. **Immediately write up the data gathered from the 2 fetches as markdown and respond.** No additional fetch calls. No messages like "I called the tool" either.

## ⛔ Absolutely forbidden (response wrap-up)
- After the markdown output, do not append any additional text.
- No follow-up suggestions like "today's briefing", "project progress", "shall I check once more?".
- No tacking on notes with "Note:", "Aside:", etc.
- End with the output format above as a single block. The last line must be `🔗 <URL>`. Nothing comes after it.

## Output format

```
## 🎬 <title>

| Field | Value |
|------|------|
| Channel | [<author_name>](<author_url>) |
| Uploaded | <upload_date> |
| Views | <viewCount, comma thousands> |
| Length | <m:ss> |

![](<thumbnail_url>)

**Description**

> <description 500 chars, '…' if longer>

🔗 <original URL>
```

## Errors
- oEmbed response is not JSON or contains 'Unauthorized'/'Not Found': "private/deleted/age-restricted"
- web_fetch starts with "fetch 실패": "YouTube access failed — check the URL"
- URL format invalid (not a youtube.com/youtu.be domain): "Not a valid YouTube URL"
