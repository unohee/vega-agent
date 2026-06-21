---
name: git-clean
description: Check for untracked files in the current working folder and clean them up after user confirmation.
---

# /git-clean

Check for files/directories that Git is not tracking (untracked) in the current working folder, and clean them up after user confirmation.

## Execution Procedure

1. Use `bash_exec` to check the Git status of the current working folder.
   - Command to run:
     ```bash
     git status --short
     ```
   - Identify the untracked files/directories shown with `??` in the output.

2. If there are no untracked items, tell the user "There are no untracked files to clean up" and exit.

3. If there are untracked items, use `bash_exec` to show the to-be-deleted list as a dry-run.
   - Command to run:
     ```bash
     git clean -nd
     ```
   - Summarize the files/directories scheduled for deletion and show them to the user.

4. Since this is a dangerous operation, always get user confirmation before actually deleting.
   - Example confirmation prompt:
     "Shall I delete the items above with `git clean -fd`?"

5. Once the user approves, use `bash_exec` to run the actual cleanup.
   - Command to run:
     ```bash
     git clean -fd
     ```

6. After cleanup, use `bash_exec` to check the status again.
   - Command to run:
     ```bash
     git status --short
     ```
   - Summarize the result in 1–3 lines.

## Caution

- Do not touch changes to tracked files.
- Do not delete ignored files either. That is, do not use `git clean -fdx`.
- Always run the deletion only after user confirmation.
