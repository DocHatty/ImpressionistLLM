# Prompts Directory

This directory contains two kinds of files:

- user-editable prompt files and metadata
- prompt-manager web assets served by the local Python server

## Prompt File Format

Put the model ID on the first physical line, then a blank line, then the system prompt:

```text
openai/gpt-5.5

Rewrite the selected text clearly and concisely.
```

The AutoHotkey loader tolerates leading blank/comment lines, but the web prompt manager reads the first line as the model. For consistent behavior, keep the model on line 1.

Optional examples use these delimiters:

```text
---EXAMPLES---

---EXAMPLE---
input text
---OUTPUT---
expected output
```

## Sidecar Metadata

Prompt-manager settings live under `prompts\_meta`:

- `<prompt>.params.json`: parameter mode and user parameters
- `<prompt>.output.json`: output routing settings

The base name must match the prompt file exactly.

## Naming

- Preserve existing prompt names unless the related sidecars and user workflow are updated together.
- Use lowercase kebab-case for new prompt files.
- Do not use Windows reserved names such as `CON`, `PRN`, or `NUL`.
- Do not prefix model IDs with legacy marker characters.

## Web Assets

`prompt_manager.html`, `chat.html`, `context_manager.html`, and their CSS/JS files are served from this directory by `lib\core\server\static_files.py`. Do not move them without updating server routes, HTML references, and verification.

