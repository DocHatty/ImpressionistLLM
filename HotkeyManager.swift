import Carbon
import Cocoa
import CoreGraphics

class HotkeyManager {
    static let shared = HotkeyManager()
    
    struct HotkeyDef {
        let id: UInt32
        let keyCode: UInt32
        let modifiers: CGEventFlags
        let action: () -> Void
    }
    
    private var hotkeyDefs: [UInt32: HotkeyDef] = [:]
    
    // Event Tap variables
    private var eventTap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?
    
    private init() {}
    
    func register(id: UInt32, keyCode: UInt32, modifiers: UInt32, action: @escaping () -> Void) {
        unregister(id: id)
        
        // Convert Carbon modifiers to CGEventFlags
        var cgFlags = CGEventFlags()
        if (modifiers & 4096) != 0 { cgFlags.insert(.maskControl) }
        if (modifiers & 2048) != 0 { cgFlags.insert(.maskAlternate) }
        if (modifiers & 512) != 0 { cgFlags.insert(.maskShift) }
        if (modifiers & 256) != 0 { cgFlags.insert(.maskCommand) }
        
        let def = HotkeyDef(id: id, keyCode: keyCode, modifiers: cgFlags, action: action)
        hotkeyDefs[id] = def
        
        setupEventTapIfNeeded()
        
        DispatchQueue.global(qos: .utility).async {
            debugLog("HotkeyManager: Registered hotkey ID \(id), keyCode \(keyCode), modifiers \(modifiers) (converted to \(cgFlags.rawValue))")
        }
    }
    
    func unregister(id: UInt32) {
        hotkeyDefs.removeValue(forKey: id)
        if hotkeyDefs.isEmpty {
            stopEventTap()
        }
    }
    
    func unregisterAll() {
        hotkeyDefs.removeAll()
        stopEventTap()
    }
    
    func getHotkeyDefs() -> [HotkeyDef] {
        return Array(hotkeyDefs.values)
    }
    
    private func setupEventTapIfNeeded() {
        guard eventTap == nil else { return }
        
        let eventMask = (1 << CGEventType.keyDown.rawValue)
        
        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .defaultTap,
            eventsOfInterest: CGEventMask(eventMask),
            callback: { (proxy, type, event, refcon) -> Unmanaged<CGEvent>? in
                if type == .tapDisabledByTimeout {
                    DispatchQueue.global(qos: .utility).async {
                        debugLog("EventTap callback: Disabled by timeout! Re-enabling...")
                    }
                    if let tap = HotkeyManager.shared.eventTap {
                        CGEvent.tapEnable(tap: tap, enable: true)
                    }
                    return nil
                }
                if type == .tapDisabledByUserInput {
                    return nil
                }
                
                if type == .keyDown {
                    let keyCode = event.getIntegerValueField(.keyboardEventKeycode)
                    let flags = event.flags
                    
                    let modifiersInterest: CGEventFlags = [.maskCommand, .maskAlternate, .maskControl, .maskShift]
                    let eventModifiers = flags.intersection(modifiersInterest)
                    
                    // Look up matching hotkey definition
                    for def in HotkeyManager.shared.getHotkeyDefs() {
                        if def.keyCode == UInt32(keyCode) && eventModifiers == def.modifiers {
                            // If it is Escape (53), let active UI windows handle it natively if they are showing!
                            if keyCode == 53 {
                                let menuActive = FloatingPromptMenuWindow.shared != nil
                                let screenshotActive = AppState.shared.isScreenshotMode
                                
                                if menuActive || screenshotActive {
                                    return Unmanaged.passUnretained(event) // Pass through Escape natively
                                }
                                
                                // Otherwise, if LLM is processing in the background, consume it to abort the processing
                                let processingActive = AppState.shared.isProcessingLLM
                                if processingActive {
                                    DispatchQueue.global(qos: .utility).async {
                                        debugLog("EventTap callback: Captured Escape key to abort LLM completion, ID = \(def.id)")
                                    }
                                    HotkeyManager.shared.trigger(id: def.id)
                                    return nil // Consume Escape to abort LLM
                                }
                                
                                // Otherwise, let Escape pass through to other system apps
                                return Unmanaged.passUnretained(event)
                            }
                            
                            DispatchQueue.global(qos: .utility).async {
                                debugLog("EventTap callback: Captured hotkey key \(keyCode) with modifiers \(eventModifiers.rawValue), ID = \(def.id)")
                            }
                            HotkeyManager.shared.trigger(id: def.id)
                            return nil // Consume keypress
                        }
                    }
                }
                return Unmanaged.passUnretained(event)
            },
            userInfo: nil
        ) else {
            DispatchQueue.global(qos: .utility).async {
                debugLog("HotkeyManager: Failed to create event tap. Make sure Accessibility permissions are granted.")
            }
            // Retry in 2.0 seconds to automatically recover when permissions are toggled
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
                HotkeyManager.shared.setupEventTapIfNeeded()
            }
            return
        }
        
        self.eventTap = tap
        self.runLoopSource = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), runLoopSource, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
        debugLog("HotkeyManager: Event tap started successfully")
    }
    
    private func stopEventTap() {
        if let tap = eventTap {
            CGEvent.tapEnable(tap: tap, enable: false)
            CFMachPortInvalidate(tap)
        }
        if let source = runLoopSource {
            CFRunLoopRemoveSource(CFRunLoopGetCurrent(), source, .commonModes)
        }
        eventTap = nil
        runLoopSource = nil
        debugLog("HotkeyManager: Event tap stopped")
    }
    
    func trigger(id: UInt32) {
        DispatchQueue.main.async {
            self.hotkeyDefs[id]?.action()
        }
    }
}
