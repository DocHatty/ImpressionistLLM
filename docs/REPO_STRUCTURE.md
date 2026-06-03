# Repository Structure

## macOS Layout

```text
.
|-- Sources/                          # Swift source, grouped by responsibility
|   |-- App/                          # Lifecycle, shared state, theming
|   |   |-- main.swift                #   App entry point
|   |   |-- AppDelegate.swift         #   Lifecycle, status menu, action routing
|   |   |-- AppState.swift            #   Shared state container
|   |   `-- Theme.swift               #   Colors and styling constants
|   |-- Services/                     # Non-UI logic and OS integration
|   |   |-- ClipboardHelper.swift     #   Clipboard utilities & keystroke simulation
|   |   |-- HotkeyManager.swift       #   Unified Event Tap global shortcut routing
|   |   |-- PythonServerManager.swift #   Virtualenv bootstrap and daemon controller
|   |   |-- HotkeySettings.swift      #   Hotkey config load/save model
|   |   |-- BrowserManager.swift      #   In-app web sessions (chat / context manager)
|   |   |-- LLMService.swift          #   Prompt execution and OpenRouter request flow
|   |   `-- MouseMonitor.swift        #   Pointer-driven context selection
|   `-- UI/                           # SwiftUI / AppKit windows and overlays
|       |-- FloatingPromptMenu.swift  #   Fuzzy-search floating prompt menu
|       |-- CaptureOverlay.swift      #   Transparent crop captures & glassmorphic HUD
|       |-- ProcessingHUD.swift       #   Screen-saver level HUD indicator
|       |-- OutputEditor.swift        #   Review / edit window for text responses
|       |-- SplashScreen.swift        #   Progressive loading splash screen
|       |-- ScreenshotActionEditor.swift # Dynamic editor for visual prompts
|       |-- HotkeySettingsView.swift  #   Hotkey configuration window
|       `-- StatusMenuManager.swift   #   Menu-bar item and menu builder
|-- build_app.sh                      # App compilation, bundling and signing script
|-- requirements-vendor.txt           # Third-party Python dependencies
|-- LoadingScreen.png                 # Splash art (loaded at runtime from repo root)
|-- config/                           # Shared configuration
|   `-- settings.ini.example          #   Bootstrapping settings template
|-- lib/
|   `-- core/
|       |-- http_server.py            #   Native Python local API daemon
|       `-- server/                   #   Python core logic modules
|-- prompts/
|   `-- processors/
|       `-- screenshot_actions.json   #   Crop actions configuration
|-- scripts/                          # Model probes and validation helpers
|-- cert/                             # Local code-signing materials
`-- docs/                             # Architecture & developer guides
```

> **Runtime contract:** the compiled `ImpressionistLLM.app` is produced **inside the repo root** and resolves `config/`, `lib/core/`, `prompts/`, `logs/`, and `LoadingScreen.png` relative to its sibling directory. Those paths are load-bearing at runtime and must stay at the repo root; only the Swift sources (compiled into the binary) are free to be reorganized.

## Component Roles

- **`Sources/App`**: App bootstrap, the shared `AppState`, and theming.
- **`Sources/Services`**: Hotkey routing, clipboard/keystroke integration, the Python daemon controller, and the OpenRouter request flow.
- **`Sources/UI`**: The native macOS surfaces — menu-bar item, floating menu, capture overlays, HUD, editors, and settings windows.
- **Python Backend Daemon** (`lib/core`): Performs REST calls, OpenRouter model queries, and image preprocessing inside a local virtualenv (`.venv`).
- **Config & Prompts**: User settings and the action definitions that drive screenshot/vision operations.
