# Operations

## Compilation & Bundling

To compile, link, and codesign the application into a standalone macOS App Bundle:

```bash
./build_app.sh
```

This script:
1. Cleans the previous build and creates the App Bundle directory structure (`ImpressionistLLM.app/Contents/MacOS`).
2. Compiles the Swift source files with high optimizations.
3. Generates the `Info.plist` configuration.
4. Automatically signs the bundle using the local development certificate `"ImpressionistLLM Local Development"`.

## Run/Launch

Launch the application directly or from the terminal:

```bash
./ImpressionistLLM.app/Contents/MacOS/ImpressionistLLM > logs/app_stdout.log 2> logs/app_stderr.log &
```

Standard logs will write to:
- `logs/app_debug.log`: Swift App startup, hotkeys, and event capture logs.
- `logs/server_startup.log`: Python daemon startup logs and package installation outputs.
- `logs/openrouter_sdk.log`: API request details and responses.

## Python Bootstrapping

On first run, the Swift app detects if the virtual environment is absent. It automatically:
1. Installs a local `.venv` environment under `lib/core/.venv/` using the system `/usr/bin/python3`.
2. Bootstraps core modules using `requirements-vendor.txt`.
3. Health-checks the port to verify that the local endpoints are fully operational.

## Codesigning & Keychain Access

To bypass manual codesign password prompts when building the app:
1. Make sure the certificate is imported using the `-A` flag:
   ```bash
   security import cert/codesign.p12 -k ~/Library/Keychains/login.keychain-db -P password -A
   ```
2. Enable Accessibility permissions for the compiled binary in **System Settings > Privacy & Security > Accessibility**. The self-signed certificate signature ensures these permissions persist across recompilations.
