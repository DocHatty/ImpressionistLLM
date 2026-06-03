import Cocoa

class MouseMonitor {
    static let shared = MouseMonitor()
    
    private var leftMouseUpMonitor: Any?
    
    private init() {}
    
    func startMonitoring() {
        stopMonitoring()
        
        // Capture context via Option (Alt) or Control + Drag release
        leftMouseUpMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseUp]) { [weak self] event in
            if event.modifierFlags.contains(.option) || event.modifierFlags.contains(.control) {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                    self?.captureContextSelection(minLen: 15, showSilently: true)
                }
            }
        }
    }
    
    func stopMonitoring() {
        if let monitor = leftMouseUpMonitor {
            NSEvent.removeMonitor(monitor)
            leftMouseUpMonitor = nil
        }
    }
    
    func captureContextSelection(minLen: Int, showSilently: Bool = false) {
        DispatchQueue.global(qos: .userInitiated).async {
            guard let selectedText = ClipboardHelper.copySelectedText(), selectedText.count >= minLen else {
                if !showSilently {
                    DispatchQueue.main.async {
                        ProcessingHUD.showNotification("No text selected. Select text and try again.", style: "error")
                    }
                }
                return
            }
            
            DispatchQueue.main.async {
                AppState.shared.appendContext(selectedText)
                NSSound(named: "Hero")?.play()
                
                let segments = AppState.shared.contextSelectionCount
                let charCount = AppState.shared.contextCharCount
                ProcessingHUD.showNotification("CONTEXT ACTIVE: \(charCount) chars (\(segments) segments)", style: "info")
            }
        }
    }
}
