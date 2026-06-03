# Configuration

`settings.ini.example` is the tracked template. `settings.ini` is local machine config and is ignored because it can contain an OpenRouter key.

## Key Loading Order

1. OS environment variable `OPENROUTER_API_KEY`
2. `[API] APIKey` in `settings.ini`

The `.env.example` file at the repository root documents environment variables for shells or launchers. The app does not automatically load `.env`.

## OpenRouter Routing Defaults

The app defaults OpenRouter provider routing to `ProviderDataCollection=deny` for clinical text and screenshots. This asks OpenRouter to avoid providers that collect prompts. `ProviderZDR=true` is stricter and opt-in because it can reduce availability.

## Local Setup

If `settings.ini` is missing, `ConfigLoader.ahk` bootstraps it from `settings.ini.example`.

Keep real keys out of:

- `settings.ini.example`
- docs
- scripts
- prompt files
- zip/archive copies intended for sharing
