import Cocoa
import SwiftUI
import ApplicationServices
import Carbon

func debugLog(_ message: String) {
    let parentDir = (Bundle.main.bundlePath as NSString).deletingLastPathComponent
    let logPath = (parentDir as NSString).appendingPathComponent("logs/app_debug.log")
    let line = "\(Date()): \(message)\n"
    if let fileHandle = FileHandle(forWritingAtPath: logPath) {
        fileHandle.seekToEndOfFile()
        if let data = line.data(using: .utf8) {
            fileHandle.write(data)
        }
        try? fileHandle.synchronize()
    } else {
        try? line.write(toFile: logPath, atomically: true, encoding: .utf8)
    }
}

class AppDelegate: NSObject, NSApplicationDelegate {
    
    func applicationDidFinishLaunching(_ aNotification: Notification) {
        debugLog("applicationDidFinishLaunching called")
        // 1. Show Splash Screen
        SplashScreen.show()
        
        // 2. Initialize State
        _ = AppState.shared
        
        // 3. Setup Menubar UI
        StatusMenuManager.shared.setupStatusMenu()
        
        // 4. Setup Python Backend Server (in background)
        setupPythonServer()
        
        // 5. Register global notification observers
        NotificationCenter.default.addObserver(self, selector: #selector(onScreenshotCaptureFinished), name: NSNotification.Name("ScreenshotCaptureFinished"), object: nil)
        
        NSWorkspace.shared.notificationCenter.addObserver(
            self,
            selector: #selector(workspaceDidActivateApplication(_:)),
            name: NSWorkspace.didActivateApplicationNotification,
            object: nil
        )
        
        // 6. Check Accessibility Permissions
        checkAccessibilityPermissions()
    }
    
    func applicationWillTerminate(_ aNotification: Notification) {
        HotkeyManager.shared.unregisterAll()
        MouseMonitor.shared.stopMonitoring()
        PythonServerManager.shared.stopServer()
        BrowserManager.shared.terminateAll() // Auto-close any lingering chat/prompt manager sessions
    }
    
    // ===== SYSTEM WORKFLOWS =====
    
    private func updateSplash(status: String) {
        var progress = 10.0
        let lower = status.lowercased()
        if lower.contains("creating") || lower.contains("virtual environment") {
            progress = 20.0
        } else if lower.contains("installing") || lower.contains("pip") || lower.contains("dependencies") {
            progress = 45.0
        } else if lower.contains("starting") || lower.contains("backend") {
            progress = 75.0
        } else if lower.contains("verifying") || lower.contains("health") {
            progress = 90.0
        } else if lower.contains("ready") || lower.contains("success") {
            progress = 100.0
        }
        SplashScreen.update(status: status, progress: progress)
    }
    
    func setupPythonServer() {
        if PythonServerManager.shared.isSetupNeeded() {
            SplashScreen.update(status: "Running Python environment setup... (takes ~15s)", progress: 10.0)
            PythonServerManager.shared.setupVenv(statusCallback: { [weak self] msg in
                self?.updateSplash(status: msg)
            }, completion: { [weak self] success in
                if success {
                    self?.startServerAndRegisterKeys()
                } else {
                    DispatchQueue.main.async {
                        SplashScreen.update(status: "Python setup failed", progress: 0.0)
                        SplashScreen.hide()
                        ProcessingHUD.showNotification("Python setup failed. Check venv status.", style: "error")
                    }
                }
            })
        } else {
            self.startServerAndRegisterKeys()
        }
    }
    
    private func startServerAndRegisterKeys() {
        debugLog("startServerAndRegisterKeys called")
        SplashScreen.update(status: "Starting local backend server...", progress: 70.0)
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            PythonServerManager.shared.startServer(statusCallback: { msg in
                self?.updateSplash(status: msg)
            }, completion: { success in
                debugLog("startServer completed with success: \(success)")
                DispatchQueue.main.async {
                    if success {
                        SplashScreen.update(status: "ImpressionistLLM ready!", progress: 100.0)
                        SplashScreen.hide()
                        ProcessingHUD.showNotification("ImpressionistLLM ready!", style: "success")
                        self?.registerGlobalHotkeys()
                        MouseMonitor.shared.startMonitoring()
                    } else {
                        SplashScreen.update(status: "Backend failed to start", progress: 0.0)
                        SplashScreen.hide()
                        ProcessingHUD.showNotification("Backend server failed to start", style: "error")
                    }
                }
            })
        }
    }
    
    func registerGlobalHotkeys() {
        HotkeyManager.shared.unregisterAll()
        
        let settings = HotkeySettings.shared
        settings.loadConfig()
        
        // Register System Actions from config
        for item in settings.config.system {
            switch item.actionId {
            case "show_menu":
                HotkeyManager.shared.register(id: 1, keyCode: item.keyCode, modifiers: item.modifiers) { [weak self] in
                    self?.showPromptMenu()
                }
            case "screenshot":
                HotkeyManager.shared.register(id: 2, keyCode: item.keyCode, modifiers: item.modifiers) {
                    CaptureManager.shared.startCapture()
                }
            case "cancel":
                HotkeyManager.shared.register(id: 3, keyCode: item.keyCode, modifiers: item.modifiers) {
                    LLMService.shared.abortActiveProcessing()
                }
            case "clear_context":
                HotkeyManager.shared.register(id: 4, keyCode: item.keyCode, modifiers: item.modifiers) {
                    AppState.shared.clearContext()
                    ProcessingHUD.showNotification("Context cleared", style: "success")
                    NSSound(named: "Glass")?.play()
                }
            case "open_context":
                HotkeyManager.shared.register(id: 5, keyCode: item.keyCode, modifiers: item.modifiers) {
                    BrowserManager.shared.openContextManager()
                }
            default:
                break
            }
        }
        
        // Register Custom Prompt Actions from config
        var currentId: UInt32 = 100
        for item in settings.config.prompts {
            let promptName = item.promptName
            HotkeyManager.shared.register(id: currentId, keyCode: item.keyCode, modifiers: item.modifiers) {
                LLMService.shared.executePrompt(promptName)
            }
            currentId += 1
        }
        
        StatusMenuManager.shared.rebuildStatusMenu()
    }
    
    func showPromptMenu() {
        debugLog("showPromptMenu triggered!")
        if let frontmost = NSWorkspace.shared.frontmostApplication,
           frontmost.processIdentifier != ProcessInfo.processInfo.processIdentifier {
            AppState.shared.lastActiveApp = frontmost
            debugLog("Captured frontmost app before menu: \(frontmost.localizedName ?? "") (PID: \(frontmost.processIdentifier))")
        }
        
        FloatingPromptMenu.show { promptName in
            FloatingPromptMenu.hide()
            if promptName == "+ Add Context/Grounding" {
                MouseMonitor.shared.captureContextSelection(minLen: 1, showSilently: false)
            } else {
                LLMService.shared.executePrompt(promptName)
            }
        }
    }
    
    @objc func onScreenshotCaptureFinished() {
        LLMService.shared.onScreenshotCaptureFinished()
    }
    
    @objc func workspaceDidActivateApplication(_ notification: Notification) {
        guard let userInfo = notification.userInfo,
              let app = userInfo[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication else {
            return
        }
        if app.processIdentifier != ProcessInfo.processInfo.processIdentifier {
            AppState.shared.lastActiveApp = app
            debugLog("Tracked active app change: \(app.localizedName ?? "") (PID: \(app.processIdentifier))")
        }
    }
    
    private func checkAccessibilityPermissions() {
        let options = ["AXTrustedCheckOptionPrompt" as CFString: true] as CFDictionary
        let accessEnabled = AXIsProcessTrustedWithOptions(options)
        debugLog("checkAccessibilityPermissions: trusted = \(accessEnabled)")
        if !accessEnabled {
            print("AppDelegate: Accessibility permissions not enabled yet")
        }
    }
}
