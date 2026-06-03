import SwiftUI
import Cocoa

struct PromptsResponse: Decodable {
    let success: Bool
    let data: [PromptItem]
}

struct KeyGrabber: NSViewRepresentable {
    var onKeyGrabbed: (UInt32, UInt32) -> Void
    
    class KeyNSView: NSView {
        var onKeyGrabbed: ((UInt32, UInt32) -> Void)?
        
        override var acceptsFirstResponder: Bool { true }
        
        override func keyDown(with event: NSEvent) {
            let keyCode = UInt32(event.keyCode)
            
            // Map event modifier flags to Carbon modifiers
            var carbonFlags: UInt32 = 0
            let flags = event.modifierFlags
            if flags.contains(.control) { carbonFlags |= 4096 }
            if flags.contains(.option) { carbonFlags |= 2048 }
            if flags.contains(.shift) { carbonFlags |= 512 }
            if flags.contains(.command) { carbonFlags |= 256 }
            
            onKeyGrabbed?(keyCode, carbonFlags)
        }
    }
    
    func makeNSView(context: Context) -> KeyNSView {
        let view = KeyNSView()
        view.onKeyGrabbed = onKeyGrabbed
        DispatchQueue.main.async {
            view.window?.makeFirstResponder(view)
        }
        return view
    }
    
    func updateNSView(_ nsView: KeyNSView, context: Context) {
        nsView.onKeyGrabbed = onKeyGrabbed
    }
}

struct HotkeySettingsView: View {
    @State private var systemItems: [HotkeyItem] = []
    @State private var promptItems: [PromptHotkeyItem] = []
    @State private var allPrompts: [String] = []
    
    // Key Recording State
    @State private var recordingActionId: String? = nil
    @State private var recordingPromptName: String? = nil
    @State private var isRecordingNewPrompt = false
    
    // Modal Selection State
    @State private var selectedNewPromptName = ""
    @State private var showAddPromptPopover = false
    @State private var newPromptKeyCode: UInt32 = 0
    @State private var newPromptModifiers: UInt32 = 0
    
    // Notifications State
    @State private var errorMessage: String? = nil
    @State private var successMessage: String? = nil
    
    var onSave: () -> Void
    var onCancel: () -> Void
    
    var body: some View {
        ZStack {
            VStack(spacing: 0) {
                // Header
                HStack {
                    Image(systemName: "keyboard")
                        .font(.system(size: 22))
                        .foregroundColor(.blue)
                    Text("Hotkey Settings")
                        .font(.system(size: 18, weight: .bold))
                    Spacer()
                }
                .padding(16)
                .background(Color.white.opacity(0.02))
                
                Divider()
                
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        // Section 1: System Actions
                        VStack(alignment: .leading, spacing: 10) {
                            Text("System Actions")
                                .font(.system(size: 14, weight: .bold))
                                .foregroundColor(.secondary)
                            
                            ForEach(systemItems) { item in
                                HStack {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(item.name)
                                            .font(.system(size: 13, weight: .semibold))
                                        Text(item.actionId == "show_menu" ? "Opens the prompt search menu" :
                                             item.actionId == "screenshot" ? "Opens the screenshot cropping overlay" :
                                             item.actionId == "cancel" ? "Cancels the active API completion request" :
                                             item.actionId == "clear_context" ? "Clears the active selection context buffer" :
                                             "Opens the active context selection manager")
                                            .font(.system(size: 11))
                                            .foregroundColor(.secondary)
                                    }
                                    
                                    Spacer()
                                    
                                    HStack(spacing: 8) {
                                        Text(KeyMap.fullDisplayString(keyCode: item.keyCode, modifiers: item.modifiers))
                                            .font(.system(size: 12, design: .monospaced))
                                            .padding(.horizontal, 8)
                                            .padding(.vertical, 4)
                                            .background(Color.white.opacity(0.1))
                                            .cornerRadius(4)
                                            .overlay(
                                                RoundedRectangle(cornerRadius: 4)
                                                    .stroke(Color.white.opacity(0.15), lineWidth: 1)
                                            )
                                        
                                        Button(action: {
                                            recordingActionId = item.id
                                            errorMessage = nil
                                            successMessage = nil
                                        }) {
                                            Text("Record")
                                                .font(.system(size: 11))
                                        }
                                        .buttonStyle(.bordered)
                                    }
                                }
                                .padding(10)
                                .background(Color.white.opacity(0.03))
                                .cornerRadius(8)
                            }
                        }
                        
                        Divider()
                        
                        // Section 2: Custom Prompt Hotkeys
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                Text("Prompt Shortcuts")
                                    .font(.system(size: 14, weight: .bold))
                                    .foregroundColor(.secondary)
                                Spacer()
                                Button(action: {
                                    if !allPrompts.isEmpty {
                                        selectedNewPromptName = allPrompts.first ?? ""
                                        newPromptKeyCode = 0
                                        newPromptModifiers = 0
                                        showAddPromptPopover = true
                                    } else {
                                        errorMessage = "No prompts available on the local server."
                                    }
                                }) {
                                    HStack(spacing: 4) {
                                        Image(systemName: "plus")
                                        Text("Add Binding")
                                    }
                                    .font(.system(size: 11))
                                }
                                .buttonStyle(.borderedProminent)
                                .popover(isPresented: $showAddPromptPopover) {
                                    VStack(alignment: .leading, spacing: 12) {
                                        Text("Bind Prompt Shortcut")
                                            .font(.system(size: 12, weight: .bold))
                                        
                                        Picker("Select Prompt", selection: $selectedNewPromptName) {
                                            ForEach(allPrompts.filter { p in !promptItems.contains { $0.promptName == p } }, id: \.self) { p in
                                                Text(p).tag(p)
                                            }
                                        }
                                        .pickerStyle(.menu)
                                        
                                        HStack {
                                            Text("Shortcut:")
                                            Spacer()
                                            if newPromptKeyCode == 0 {
                                                Text("Click Record")
                                                    .font(.system(size: 11))
                                                    .foregroundColor(.secondary)
                                            } else {
                                                Text(KeyMap.fullDisplayString(keyCode: newPromptKeyCode, modifiers: newPromptModifiers))
                                                    .font(.system(size: 11, design: .monospaced))
                                                    .bold()
                                            }
                                            Button(action: {
                                                isRecordingNewPrompt = true
                                            }) {
                                                Text("Record")
                                                    .font(.system(size: 10))
                                            }
                                        }
                                        
                                        HStack {
                                            Spacer()
                                            Button("Cancel") {
                                                showAddPromptPopover = false
                                            }
                                            .buttonStyle(.plain)
                                            .padding(.trailing, 8)
                                            
                                            Button("Add") {
                                                if newPromptKeyCode != 0 && !selectedNewPromptName.isEmpty {
                                                    let item = PromptHotkeyItem(promptName: selectedNewPromptName, keyCode: newPromptKeyCode, modifiers: newPromptModifiers)
                                                    promptItems.append(item)
                                                    showAddPromptPopover = false
                                                }
                                            }
                                            .disabled(newPromptKeyCode == 0 || selectedNewPromptName.isEmpty)
                                        }
                                    }
                                    .padding(12)
                                    .frame(width: 250)
                                }
                            }
                            
                            if promptItems.isEmpty {
                                Text("No custom prompt shortcuts bound. Click 'Add Binding' to configure.")
                                    .font(.system(size: 12))
                                    .foregroundColor(.secondary)
                                    .padding(.vertical, 8)
                            } else {
                                ForEach(promptItems) { item in
                                    HStack {
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(item.promptName)
                                                .font(.system(size: 13, weight: .semibold))
                                            Text("Executes the prompt automatically using selected text")
                                                .font(.system(size: 11))
                                                .foregroundColor(.secondary)
                                        }
                                        
                                        Spacer()
                                        
                                        HStack(spacing: 8) {
                                            Text(KeyMap.fullDisplayString(keyCode: item.keyCode, modifiers: item.modifiers))
                                                .font(.system(size: 12, design: .monospaced))
                                                .padding(.horizontal, 8)
                                                .padding(.vertical, 4)
                                                .background(Color.white.opacity(0.1))
                                                .cornerRadius(4)
                                                .overlay(
                                                    RoundedRectangle(cornerRadius: 4)
                                                        .stroke(Color.white.opacity(0.15), lineWidth: 1)
                                                )
                                            
                                            Button(action: {
                                                recordingPromptName = item.promptName
                                                errorMessage = nil
                                                successMessage = nil
                                            }) {
                                                Text("Record")
                                                    .font(.system(size: 11))
                                            }
                                            .buttonStyle(.bordered)
                                            
                                            Button(action: {
                                                promptItems.removeAll { $0.promptName == item.promptName }
                                            }) {
                                                Image(systemName: "trash")
                                                    .foregroundColor(.red.opacity(0.8))
                                            }
                                            .buttonStyle(.plain)
                                            .padding(.leading, 4)
                                        }
                                    }
                                    .padding(10)
                                    .background(Color.white.opacity(0.03))
                                    .cornerRadius(8)
                                }
                            }
                        }
                    }
                    .padding(16)
                }
                
                Divider()
                
                // Info / Feedback banner
                if let err = errorMessage {
                    Text(err)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(.red)
                        .padding(.vertical, 8)
                        .frame(maxWidth: .infinity)
                        .background(Color.red.opacity(0.1))
                } else if let success = successMessage {
                    Text(success)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(.green)
                        .padding(.vertical, 8)
                        .frame(maxWidth: .infinity)
                        .background(Color.green.opacity(0.1))
                }
                
                // Footer Buttons
                HStack {
                    Spacer()
                    Button("Cancel") {
                        onCancel()
                    }
                    .keyboardShortcut(.cancelAction)
                    
                    Button("Save Changes") {
                        validateAndSave()
                    }
                    .buttonStyle(.borderedProminent)
                }
                .padding(16)
                .background(Color.white.opacity(0.02))
            }
            
            // Blur & KeyGrabber overlay when recording
            if recordingActionId != nil || recordingPromptName != nil || isRecordingNewPrompt {
                ImpressionistGradientBackground()
                    .edgesIgnoringSafeArea(.all)
                
                VStack(spacing: 16) {
                    Image(systemName: "record.circle")
                        .font(.system(size: 40))
                        .foregroundColor(.red)
                        .scaleEffect(1.2)
                    
                    Text("Recording Shortcut")
                        .font(.system(size: 16, weight: .bold))
                    
                    Text("Press your desired key combination...\n(e.g. Option + Shift + S)")
                        .font(.system(size: 13))
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                    
                    Text("Press [Esc] to cancel")
                        .font(.system(size: 11))
                        .foregroundColor(.gray)
                    
                    KeyGrabber { keyCode, modifiers in
                        DispatchQueue.main.async {
                            if keyCode == 53 && modifiers == 0 { // Escape key cancels
                                recordingActionId = nil
                                recordingPromptName = nil
                                isRecordingNewPrompt = false
                                return
                            }
                            
                            // Prevent recording bare single letters without modifiers (to avoid blocking regular typing)
                            if modifiers == 0 && keyCode != 50 && keyCode != 53 { // Allow bare Backtick and Esc
                                errorMessage = "System shortcuts require at least one modifier key (Cmd, Opt, Ctrl, Shift)."
                                recordingActionId = nil
                                recordingPromptName = nil
                                isRecordingNewPrompt = false
                                return
                            }
                            
                            if let actionId = recordingActionId {
                                if let idx = systemItems.firstIndex(where: { $0.actionId == actionId }) {
                                    systemItems[idx].keyCode = keyCode
                                    systemItems[idx].modifiers = modifiers
                                }
                                recordingActionId = nil
                            } else if let promptName = recordingPromptName {
                                if let idx = promptItems.firstIndex(where: { $0.promptName == promptName }) {
                                    promptItems[idx].keyCode = keyCode
                                    promptItems[idx].modifiers = modifiers
                                }
                                recordingPromptName = nil
                            } else if isRecordingNewPrompt {
                                newPromptKeyCode = keyCode
                                newPromptModifiers = modifiers
                                isRecordingNewPrompt = false
                            }
                        }
                    }
                    .frame(width: 1, height: 1)
                }
                .padding(24)
                .background(ImpressionistGradientBackground())
                .cornerRadius(12)
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(Color.white.opacity(0.15), lineWidth: 1)
                )
                .shadow(radius: 20)
            }
        }
        .frame(width: 600, height: 500)
        .onAppear {
            loadConfig()
            loadPrompts()
        }
    }
    
    private func loadConfig() {
        let settings = HotkeySettings.shared
        settings.loadConfig()
        self.systemItems = settings.config.system
        self.promptItems = settings.config.prompts
    }
    
    private func loadPrompts() {
        let port = PythonServerManager.shared.port
        let secret = PythonServerManager.shared.apiSecret
        let url = URL(string: "http://127.0.0.1:\(port)/api/prompts")!
        
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue(secret, forHTTPHeaderField: "X-API-Secret")
        
        URLSession.shared.dataTask(with: request) { data, response, error in
            if let data = data {
                if let decoded = try? JSONDecoder().decode(PromptsResponse.self, from: data) {
                    DispatchQueue.main.async {
                        self.allPrompts = decoded.data.map { $0.name }
                    }
                }
            }
        }.resume()
    }
    
    private func validateAndSave() {
        // 1. Collision detection check
        var allKeys: [String: String] = [:] // "keyCode-modifiers" -> Source Name
        
        for item in systemItems {
            let key = "\(item.keyCode)-\(item.modifiers)"
            if let collision = allKeys[key] {
                errorMessage = "Collision: '\(item.name)' conflicts with '\(collision)'."
                return
            }
            allKeys[key] = item.name
        }
        
        for item in promptItems {
            let key = "\(item.keyCode)-\(item.modifiers)"
            if let collision = allKeys[key] {
                errorMessage = "Collision: Custom binding for '\(item.promptName)' conflicts with '\(collision)'."
                return
            }
            allKeys[key] = "Prompt: \(item.promptName)"
        }
        
        // 2. Save config
        let settings = HotkeySettings.shared
        settings.config.system = systemItems
        settings.config.prompts = promptItems
        settings.saveConfig()
        
        NSSound(named: "Hero")?.play()
        successMessage = "Hotkeys saved successfully! Re-registering keys..."
        errorMessage = nil
        
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
            onSave()
        }
    }
}

class HotkeySettingsWindow: NSWindow {
    static var shared: HotkeySettingsWindow?
    
    static func show() {
        if shared != nil {
            shared?.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        
        let view = HotkeySettingsView(
            onSave: {
                // Re-register dynamically
                if let appDelegate = NSApp.delegate as? AppDelegate {
                    appDelegate.registerGlobalHotkeys()
                }
                HotkeySettingsWindow.hide()
            },
            onCancel: {
                HotkeySettingsWindow.hide()
            }
        )
        
        let window = HotkeySettingsWindow(
            contentRect: NSRect(x: 0, y: 0, width: 600, height: 500),
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        
        window.center()
        window.title = "Hotkey Settings"
        window.contentView = NSHostingView(rootView: view)
        window.isReleasedWhenClosed = false
        window.alphaValue = 0.0
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        
        shared = window
        
        NSAnimationContext.runAnimationGroup({ context in
            context.duration = 0.2
            context.timingFunction = CAMediaTimingFunction(name: .easeOut)
            window.animator().alphaValue = 1.0
        })
    }
    
    static func hide() {
        guard let window = shared else { return }
        shared = nil
        NSAnimationContext.runAnimationGroup({ context in
            context.duration = 0.15
            context.timingFunction = CAMediaTimingFunction(name: .easeIn)
            window.animator().alphaValue = 0.0
        }, completionHandler: {
            window.close()
        })
    }
}
