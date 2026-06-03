# Repository Structure

## macOS Layout

```text
.
|-- main.swift                     # App entry point
|-- AppDelegate.swift              # App lifecycle, Status menu, coordinate actions
|-- AppState.swift                 # Shared state container
|-- ClipboardHelper.swift          # Clipboard utilities & keystroke simulation
|-- HotkeyManager.swift            # Unified Event Tap global shortcut routing
|-- PythonServerManager.swift      # Virtualenv bootstrap and Python daemon controller
|-- FloatingPromptMenu.swift       # SwiftUI floating menu for fuzzy prompt search
|-- CaptureOverlay.swift           # Transparent overlay crop captures & glassmorphic HUD
|-- ProcessingHUD.swift            # Screen-saver level HUD indicator
|-- OutputEditor.swift             # Review / edit window for text responses
|-- SplashScreen.swift             # Crimson red progressive loading splash screen
|-- ScreenshotActionEditor.swift   # Dynamic Actions Editor for visual prompts
|-- build_app.sh                   # App compilation, bundling and signing script
|-- requirements-vendor.txt        # Third-party Python dependencies
|-- config/                        # Shared configurations folder
|   `-- settings.ini.example       # Bootstrapping settings config
|-- lib/
|   `-- core/
|       |-- http_server.py         # Native python local API daemon
|       `-- server/                # Python core logic modules
|-- prompts/
|   `-- processors/
|       `-- screenshot_actions.json # Crop actions configuration
`-- docs/                          # Architecture & developer guides
```

## Component Roles

- **Swift UI/Tray Layer**: Houses the native macOS app bundle (`ImpressionistLLM.app`), status tray item, event tap listeners, and SwiftUI windows.
- **Python Backend Daemon**: Performs actual REST api calls, OpenRouter model queries, and text preprocessing inside a localized virtualenv (`.venv`).
- **Config & Prompts**: Contains localized user settings and action definitions for screenshot operations.
