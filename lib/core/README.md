# Core Directory

`lib\core` contains shared AutoHotkey infrastructure plus runtime pieces required by the local Python server.

## First-Party Code

- `*.ahk`: shared AHK infrastructure such as config loading, logging, cache, retry, process cleanup, local HTTP helpers, and resource guards.
- `server\`: first-party Python server modules.
- `guards\`: AHK resource guard helpers.

## Runtime-Owned Folders

- `python\`: embedded CPython runtime used to launch `http_server.py`.
- `vendor_site_packages\`: vendored third-party Python packages generated from `requirements-vendor.txt`.

Do not manually edit files inside runtime-owned folders. Update the setup or vendoring scripts, regenerate, then run `scripts\verify.ps1`.

## Path Contracts

- `ImpressionistLLM.ahk` expects Python at `lib\core\python\python.exe`.
- `PromptManagerCore.ahk` starts `lib\core\http_server.py`.
- Server modules import packages from `lib\core\vendor_site_packages`.
- Static web assets are currently served from `prompts`.

