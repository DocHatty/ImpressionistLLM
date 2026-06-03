import Cocoa

class StatusMenuManager: NSObject {
    static let shared = StatusMenuManager()
    
    var statusItem: NSStatusItem?
    
    private override init() {
        super.init()
    }
    
    func setupStatusMenu() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem?.button {
            let parentDir = (Bundle.main.bundlePath as NSString).deletingLastPathComponent
            let icoPath = (parentDir as NSString).appendingPathComponent("ImpressionistLLM.ico")
            if FileManager.default.fileExists(atPath: icoPath), let iconImage = NSImage(contentsOfFile: icoPath) {
                iconImage.size = NSSize(width: 18, height: 18)
                button.image = iconImage
            } else {
                button.image = NSImage(systemSymbolName: "brain.head.profile", accessibilityDescription: "ImpressionistLLM")
            }
        }
        
        rebuildStatusMenu()
    }
    
    func rebuildStatusMenu() {
        let menu = NSMenu()
        
        let settings = HotkeySettings.shared
        
        func getShortcutString(actionId: String, defaultVal: String) -> String {
            if let item = settings.config.system.first(where: { $0.actionId == actionId }) {
                return KeyMap.fullDisplayString(keyCode: item.keyCode, modifiers: item.modifiers)
            }
            return defaultVal
        }
        
        let menuKey = getShortcutString(actionId: "show_menu", defaultVal: "`")
        let screenshotKey = getShortcutString(actionId: "screenshot", defaultVal: "Ctrl+G")
        let openContextKey = getShortcutString(actionId: "open_context", defaultVal: "Opt+V")
        let clearContextKey = getShortcutString(actionId: "clear_context", defaultVal: "Opt+C")
        
        func addItem(_ title: String, action: Selector) {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
            item.target = self
            menu.addItem(item)
        }
        
        addItem("Show Floating Menu (\(menuKey))", action: #selector(menuShowPromptSelector))
        addItem("Start Screenshot Overlay (\(screenshotKey))", action: #selector(menuStartScreenshot))
        menu.addItem(NSMenuItem.separator())
        addItem("Open Chat Session", action: #selector(menuOpenChat))
        addItem("Open Context Manager (\(openContextKey))", action: #selector(menuOpenContext))
        addItem("Open Prompt Manager", action: #selector(menuOpenPromptManager))
        addItem("Open Debug Console", action: #selector(menuOpenDebug))
        menu.addItem(NSMenuItem.separator())
        addItem("Clear Context (\(clearContextKey))", action: #selector(menuClearContext))
        addItem("Hotkey Settings...", action: #selector(menuOpenHotkeySettings))
        addItem("Open Settings", action: #selector(menuOpenSettings))
        menu.addItem(NSMenuItem.separator())
        addItem("Quit", action: #selector(menuQuit))
        
        statusItem?.menu = menu
    }
    
    // ===== Menu Action Targets =====
    
    @objc func menuShowPromptSelector() {
        if let appDelegate = NSApp.delegate as? AppDelegate {
            // Delay slightly to let the status menu close and focus restore settle
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.18) {
                appDelegate.showPromptMenu()
            }
        }
    }
    
    @objc func menuStartScreenshot() {
        CaptureManager.shared.startCapture()
    }
    
    @objc func menuOpenChat() {
        BrowserManager.shared.openChatUI()
    }
    
    @objc func menuOpenContext() {
        BrowserManager.shared.openContextManager()
    }
    
    @objc func menuOpenPromptManager() {
        BrowserManager.shared.openPromptManager()
    }
    
    @objc func menuOpenDebug() {
        BrowserManager.shared.openDebugConsole()
    }
    
    @objc func menuClearContext() {
        AppState.shared.clearContext()
    }
    
    @objc func menuOpenHotkeySettings() {
        HotkeySettingsWindow.show()
    }
    
    @objc func menuOpenSettings() {
        let parentDir = (Bundle.main.bundlePath as NSString).deletingLastPathComponent
        let settingsPath = (parentDir as NSString).appendingPathComponent("config/settings.ini")
        let url = URL(fileURLWithPath: settingsPath)
        NSWorkspace.shared.open(url)
    }
    
    @objc func menuQuit() {
        NSApplication.shared.terminate(nil)
    }
}
