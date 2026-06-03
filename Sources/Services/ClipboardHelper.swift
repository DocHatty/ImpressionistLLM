import Cocoa

class ClipboardHelper {
    private static func getSelectedTextViaAccessibility(pid: pid_t?) -> String? {
        let appElement: AXUIElement
        if let pid = pid {
            appElement = AXUIElementCreateApplication(pid)
        } else {
            appElement = AXUIElementCreateSystemWide()
        }
        
        var focusedElementValue: CFTypeRef?
        let err = AXUIElementCopyAttributeValue(appElement, kAXFocusedUIElementAttribute as CFString, &focusedElementValue)
        
        guard err == .success, let focusedElement = focusedElementValue else {
            if pid != nil {
                // Fallback to system-wide
                return getSelectedTextViaAccessibility(pid: nil)
            }
            return nil
        }
        
        let element = focusedElement as! AXUIElement
        
        // Layer 1: Check selected text attribute directly
        var selectedTextValue: CFTypeRef?
        let errText = AXUIElementCopyAttributeValue(element, kAXSelectedTextAttribute as CFString, &selectedTextValue)
        if errText == .success, let selectedText = selectedTextValue as? String, !selectedText.isEmpty {
            return selectedText
        }
        
        // Layer 1.5: Native Copy action. If supported, run it and monitor pasteboard.
        var actionsValue: CFArray?
        let errActions = AXUIElementCopyActionNames(element, &actionsValue)
        if errActions == .success, let actions = actionsValue as? [String], actions.contains("AXCopy") {
            let originalItems = backupClipboard()
            let pasteboard = NSPasteboard.general
            let originalChangeCount = pasteboard.clearContents()
            
            let errPerform = AXUIElementPerformAction(element, "AXCopy" as CFString)
            if errPerform == .success {
                let deadline = Date().addingTimeInterval(0.3)
                while Date() < deadline {
                    if pasteboard.changeCount > originalChangeCount {
                        if let text = pasteboard.string(forType: .string), !text.isEmpty {
                            restoreClipboard(originalItems)
                            return text
                        }
                    }
                    Thread.sleep(forTimeInterval: 0.02)
                }
            }
            restoreClipboard(originalItems)
        }
        
        if pid != nil {
            // Fallback to system-wide
            return getSelectedTextViaAccessibility(pid: nil)
        }
        
        return nil
    }

    static func copySelectedText() -> String? {
        let lastApp = AppState.shared.lastActiveApp
        let lastPid = lastApp?.processIdentifier
        
        // Try Layer 1 & 1.5: Native Accessibility API (fastest, preserves clipboard)
        if let text = getSelectedTextViaAccessibility(pid: lastPid), !text.isEmpty {
            debugLog("ClipboardHelper: Grabbed selected text natively via Accessibility API")
            return text
        }
        
        debugLog("ClipboardHelper: Accessibility selected text empty. Falling back to clipboard simulation...")
        
        // 1. Force focus transition back to the previously active application
        if let lastApp = lastApp {
            lastApp.activate()
            let start = Date()
            while Date().timeIntervalSince(start) < 0.8 {
                if let frontmost = NSWorkspace.shared.frontmostApplication,
                   frontmost.processIdentifier == lastApp.processIdentifier {
                    break
                }
                Thread.sleep(forTimeInterval: 0.02)
            }
        } else {
            // Fallback: wait for focus to transition away from our app
            let start = Date()
            let ourPid = ProcessInfo.processInfo.processIdentifier
            while Date().timeIntervalSince(start) < 0.8 {
                if let frontmost = NSWorkspace.shared.frontmostApplication {
                    if frontmost.processIdentifier != ourPid {
                        break
                    }
                }
                Thread.sleep(forTimeInterval: 0.02)
            }
        }
        // Small buffer to allow target application to complete window key activation
        Thread.sleep(forTimeInterval: 0.12)
        
        let pasteboard = NSPasteboard.general
        let originalItems = backupClipboard()
        let originalChangeCount = pasteboard.changeCount
        
        // Attempt 1: Direct PID-targeted key simulation
        if let targetPid = lastPid {
            debugLog("ClipboardHelper: Attempting target PID (\(targetPid)) Cmd+C simulation")
            pasteboard.clearContents()
            simulateKeyPress(key: 8, modifiers: .maskCommand, toPid: targetPid)
            
            let deadline = Date().addingTimeInterval(0.4)
            while Date() < deadline {
                if pasteboard.changeCount > originalChangeCount {
                    if let text = pasteboard.string(forType: .string), !text.isEmpty {
                        restoreClipboard(originalItems)
                        return text
                    }
                }
                Thread.sleep(forTimeInterval: 0.02)
            }
        }
        
        // Attempt 2: System-wide event tap fallback (requires active focus)
        debugLog("ClipboardHelper: Falling back to system-wide Cmd+C simulation")
        pasteboard.clearContents()
        simulateKeyPress(key: 8, modifiers: .maskCommand, toPid: nil)
        
        let deadline = Date().addingTimeInterval(0.6)
        while Date() < deadline {
            if pasteboard.changeCount > originalChangeCount {
                if let text = pasteboard.string(forType: .string), !text.isEmpty {
                    restoreClipboard(originalItems)
                    return text
                }
            }
            Thread.sleep(forTimeInterval: 0.02)
        }
        
        restoreClipboard(originalItems)
        return nil
    }
    
    static func pasteText(_ text: String) {
        DispatchQueue.global(qos: .userInitiated).async {
            // Force focus transition back to the previously active application
            let lastApp = AppState.shared.lastActiveApp
            if let lastApp = lastApp {
                lastApp.activate()
                let start = Date()
                while Date().timeIntervalSince(start) < 0.8 {
                    if let frontmost = NSWorkspace.shared.frontmostApplication,
                       frontmost.processIdentifier == lastApp.processIdentifier {
                        break
                    }
                    Thread.sleep(forTimeInterval: 0.02)
                }
            } else {
                // Fallback: wait for focus to transition away from our app
                let start = Date()
                let ourPid = ProcessInfo.processInfo.processIdentifier
                while Date().timeIntervalSince(start) < 0.8 {
                    if let frontmost = NSWorkspace.shared.frontmostApplication {
                        if frontmost.processIdentifier != ourPid {
                            break
                        }
                    }
                    Thread.sleep(forTimeInterval: 0.02)
                }
            }
            Thread.sleep(forTimeInterval: 0.12)
            
            let pasteboard = NSPasteboard.general
            
            // Backup
            let originalItems = backupClipboard()
            
            pasteboard.clearContents()
            pasteboard.setString(text, forType: .string)
            
            // Simulate Command+V (virtual key 9 = 'v')
            if let targetPid = lastApp?.processIdentifier {
                debugLog("ClipboardHelper: Attempting target PID (\(targetPid)) Cmd+V simulation")
                simulateKeyPress(key: 9, modifiers: .maskCommand, toPid: targetPid)
            } else {
                debugLog("ClipboardHelper: Attempting system-wide Cmd+V simulation")
                simulateKeyPress(key: 9, modifiers: .maskCommand, toPid: nil)
            }
            
            // Wait 300ms for paste operation to complete before restoring clipboard
            Thread.sleep(forTimeInterval: 0.3)
            
            restoreClipboard(originalItems)
        }
    }
    
    private static func backupClipboard() -> [NSPasteboardItem]? {
        let pasteboard = NSPasteboard.general
        return pasteboard.pasteboardItems?.map { item -> NSPasteboardItem in
            let newItem = NSPasteboardItem()
            for type in item.types {
                if let data = item.data(forType: type) {
                    newItem.setData(data, forType: type)
                }
            }
            return newItem
        }
    }
    
    private static func restoreClipboard(_ items: [NSPasteboardItem]?) {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        if let items = items {
            pasteboard.writeObjects(items)
        }
    }
    
    private static func simulateKeyPress(key: UInt16, modifiers: CGEventFlags, toPid pid: pid_t?) {
        let source = CGEventSource(stateID: .privateState)
        
        var modifierDownEvents: [CGEvent] = []
        var modifierUpEvents: [CGEvent] = []
        
        // Helper to add modifier key events
        func addModifier(virtualKey: UInt16, flag: CGEventFlags) {
            if modifiers.contains(flag) {
                if let down = CGEvent(keyboardEventSource: source, virtualKey: virtualKey, keyDown: true) {
                    down.flags = flag
                    modifierDownEvents.append(down)
                }
                if let up = CGEvent(keyboardEventSource: source, virtualKey: virtualKey, keyDown: false) {
                    up.flags = []
                    modifierUpEvents.insert(up, at: 0) // Release in reverse order
                }
            }
        }
        
        addModifier(virtualKey: 55, flag: .maskCommand)
        addModifier(virtualKey: 58, flag: .maskAlternate)
        addModifier(virtualKey: 59, flag: .maskControl)
        addModifier(virtualKey: 56, flag: .maskShift)
        
        let keyDown = CGEvent(keyboardEventSource: source, virtualKey: key, keyDown: true)
        let keyUp = CGEvent(keyboardEventSource: source, virtualKey: key, keyDown: false)
        
        keyDown?.flags = modifiers
        keyUp?.flags = modifiers
        
        // Function to post event
        func postEvent(_ event: CGEvent) {
            if let targetPid = pid {
                event.postToPid(targetPid)
            } else {
                event.post(tap: .cgSessionEventTap)
            }
        }
        
        // 1. Post all modifiers down
        for event in modifierDownEvents {
            postEvent(event)
            Thread.sleep(forTimeInterval: 0.01)
        }
        
        // 2. Post character key down
        if let kd = keyDown {
            postEvent(kd)
        }
        
        Thread.sleep(forTimeInterval: 0.02) // physical hold duration
        
        // 3. Post character key up
        if let ku = keyUp {
            postEvent(ku)
        }
        
        Thread.sleep(forTimeInterval: 0.01)
        
        // 4. Post all modifiers up
        for event in modifierUpEvents {
            postEvent(event)
            Thread.sleep(forTimeInterval: 0.01)
        }
    }
}
