---
name: send-email
description: Compose an email draft with Gmail and send it after user confirmation.
argument-hint: "[recipient/purpose/body hint]"
---

# /send-email

Compose an email with the Gmail API and send it after user confirmation.

## Input
- User argument: everything that follows `/send-email`
- Example: `/send-email alisa@example.com Atlas Cloud 스폰서십 답장 영어로 짧게`

If the user argument contains a recipient, purpose, tone, or content to include, compose the email based on it. If information is missing, briefly ask only for the items you need.

## Procedure

1. **Identify requirements**
   - Extract the following information from the user argument.
     - Recipient email
     - Subject
     - Body purpose
     - Tone: formal/casual/business/brief, etc.
   - If any of the core information among recipient email, subject, and body purpose is missing, ask the user for more.
   - Use the single Google account connected in settings as the sending account (no separate selection).

2. **Check relevant context**
   - If the user mentions "the previous email", "reply", "the email that just came", "recent email", etc., search for the relevant email with `gmail_search`.
   - If needed, read the original with `gmail_read` and reflect the reply context.
   - If there are multiple search results, summarize the most relevant candidate and confirm it with the user.

3. **Draft the email**
   - Write the subject and body to match the user's purpose and context.
   - Write business emails clearly and concisely.
   - For negotiations/proposals/sensitive content, organize the conditions, scope, and items to confirm as bullets.
   - If the user does not specify Korean/English, choose the language that fits the context.

4. **Confirm before sending**
   - Show the user a preview in the following format.
     - From account
     - To
     - Subject
     - Body
   - Always confirm with "Send it as is?"

5. **Send or save as draft**
   - Call `gmail_send` only after the user explicitly approves.
   - Never send before approval.
   - If the user says "just save as draft", "as a draft", "save the draft", call `gmail_draft`.

6. **Report completion**
   - On successful send: "Sent."
   - On successful draft save: "Saved to your Gmail Drafts."
   - On error: summarize the error and suggest next steps.

## Safety rules
- Email sending (`gmail_send`) must only be executed after user confirmation.
- If the recipient/subject/body is ambiguous, do not send; ask a confirming question instead.
- For sensitive negotiations, contracts, or financial terms, do not arbitrarily finalize the content the user provided; write it as a "proposal/inquiry" instead.
