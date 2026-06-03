#!/bin/bash
set -e

echo "=== Building ImpressionistLLM.app ==="

APP_NAME="ImpressionistLLM"
BUNDLE_DIR="${APP_NAME}.app"
CONTENTS_DIR="${BUNDLE_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"

# 1. Clean previous build
echo "Cleaning old build files..."
rm -rf "${BUNDLE_DIR}"
rm -f "${APP_NAME}"

# 2. Create bundle directories
echo "Creating app bundle directory structure..."
mkdir -p "${MACOS_DIR}"
mkdir -p "${RESOURCES_DIR}"

# 3. Compile Swift source files
echo "Compiling Swift source files..."
swiftc -O -o "${MACOS_DIR}/${APP_NAME}" \
    main.swift \
    AppDelegate.swift \
    AppState.swift \
    ClipboardHelper.swift \
    HotkeyManager.swift \
    PythonServerManager.swift \
    FloatingPromptMenu.swift \
    CaptureOverlay.swift \
    ProcessingHUD.swift \
    OutputEditor.swift \
    SplashScreen.swift \
    ScreenshotActionEditor.swift \
    HotkeySettings.swift \
    HotkeySettingsView.swift \
    BrowserManager.swift \
    LLMService.swift \
    MouseMonitor.swift \
    StatusMenuManager.swift \
    Theme.swift \
    -framework AppKit \
    -framework Carbon \
    -framework SwiftUI \
    -framework Combine \
    -framework CoreGraphics

# 4. Generate Info.plist
echo "Generating Info.plist..."
cat > "${CONTENTS_DIR}/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>${APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>com.radai.${APP_NAME}</string>
    <key>CFBundleName</key>
    <string>${APP_NAME}</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
EOF

# Ensure execute permissions on binary
chmod +x "${MACOS_DIR}/${APP_NAME}"

# 5. Codesign the app bundle
echo "Signing the application bundle..."
# Ensure the certificate is imported and trusted in the user's login keychain
if ! security find-identity -p codesigning -v | grep -q "ImpressionistLLM Local Development"; then
    echo "Certificate not found in keychain. Importing cert/codesign.p12..."
    security import cert/codesign.p12 -k ~/Library/Keychains/login.keychain-db -P password -A 2>/dev/null || true
    security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "" ~/Library/Keychains/login.keychain-db 2>/dev/null || true
fi

# Establish code-signing root trust in the user's login keychain (does not require administrator privileges)
echo "Establishing root trust for the user..."
security add-trusted-cert -d -r trustRoot -p codeSign -k ~/Library/Keychains/login.keychain-db cert/codesign.crt >/dev/null 2>&1 || true

if security find-identity -p codesigning -v | grep -q "ImpressionistLLM Local Development"; then
    echo "Using certificate 'ImpressionistLLM Local Development' for signing."
    codesign --force --deep --sign "ImpressionistLLM Local Development" "${BUNDLE_DIR}"
else
    echo "Warning: Failed to use local development certificate. Falling back to ad-hoc signing."
    codesign --force --deep --sign - "${BUNDLE_DIR}"
fi

# Verify signature trust settings
echo "Verifying signature trust settings..."
if security find-certificate -c "ImpressionistLLM Local Development" /Library/Keychains/System.keychain >/dev/null 2>&1 || \
   security find-certificate -c "ImpressionistLLM Local Development" ~/Library/Keychains/login.keychain-db >/dev/null 2>&1; then
    echo "Application certificate is trusted in the keychain."
else
    echo "Warning: Application certificate could not be verified in the keychain."
fi

echo "=== Build Complete: ${BUNDLE_DIR} created successfully! ==="
