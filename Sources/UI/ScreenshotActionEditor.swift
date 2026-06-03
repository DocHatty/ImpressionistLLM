import SwiftUI
import Cocoa

struct ScreenshotActionEditorView: View {
    @State private var actions: [ActionInfo] = []
    @State private var selectedActionId: String? = nil
    
    @State private var label: String = ""
    @State private var hotkey: String = ""
    @State private var model: String = ""
    @State private var prompt: String = ""
    
    @State private var errorMessage: String? = nil
    @State private var successMessage: String? = nil
    
    var onDismiss: () -> Void
    
    let popularModels = [
        "google/gemini-3.5-flash",
        "google/gemini-3-flash-preview",
        "google/gemini-2.5-flash",
        "google/gemini-2.5-pro",
        "openai/gpt-5.5",
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "anthropic/claude-3.5-sonnet"
    ]
    
    var body: some View {
        HStack(spacing: 0) {
            // Sidebar List of Actions
            VStack(spacing: 0) {
                Text("CROP ACTIONS")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
                    .padding(.top, 16)
                    .padding(.bottom, 8)
                
                List(actions, id: \.label, selection: $selectedActionId) { action in
                    HStack(spacing: 8) {
                        Image(systemName: "rectangle.dashed")
                            .foregroundColor(.blue)
                            .font(.system(size: 12))
                        
                        Text(action.label)
                            .font(.system(size: 13, weight: .medium))
                        
                        Spacer()
                        
                        if !action.hotkey.isEmpty {
                            Text("[\(action.hotkey.uppercased())]")
                                .font(.system(size: 10, design: .monospaced))
                                .foregroundColor(.secondary)
                                .padding(.horizontal, 4)
                                .padding(.vertical, 1)
                                .background(Color.white.opacity(0.1))
                                .cornerRadius(3)
                        }
                    }
                    .tag(action.label)
                }
                .listStyle(SidebarListStyle())
                
                Divider()
                
                // Toolbar
                HStack(spacing: 12) {
                    Button(action: addNewAction) {
                        HStack(spacing: 4) {
                            Image(systemName: "plus")
                            Text("Add")
                        }
                        .font(.system(size: 11, weight: .semibold))
                    }
                    .buttonStyle(.plain)
                    
                    Button(action: deleteSelectedAction) {
                        HStack(spacing: 4) {
                            Image(systemName: "trash")
                            Text("Delete")
                        }
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(selectedActionId == nil || actions.count <= 1 ? .secondary : .red)
                    }
                    .buttonStyle(.plain)
                    .disabled(selectedActionId == nil || actions.count <= 1)
                    
                    Spacer()
                }
                .padding(12)
                .background(ImpressionistGradientBackground())
            }
            .frame(width: 200)
            
            Divider()
            
            // Detail Editor Panel
            if selectedActionId != nil {
                VStack(alignment: .leading, spacing: 16) {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 16) {
                            // Section 1: Identification & Shortcut
                            VStack(alignment: .leading, spacing: 10) {
                                Text("Identification & Shortcut")
                                    .font(.system(size: 11, weight: .bold))
                                    .foregroundColor(.secondary)
                                
                                HStack(spacing: 16) {
                                    VStack(alignment: .leading, spacing: 6) {
                                        Text("Action Label")
                                            .font(.system(size: 11, weight: .semibold))
                                        TextField("e.g. SPINE, OCR", text: $label)
                                            .textFieldStyle(RoundedBorderTextFieldStyle())
                                    }
                                    .frame(maxWidth: .infinity)
                                    
                                    VStack(alignment: .leading, spacing: 6) {
                                        Text("Hotkey Trigger")
                                            .font(.system(size: 11, weight: .semibold))
                                        TextField("Single letter/digit", text: $hotkey)
                                            .textFieldStyle(RoundedBorderTextFieldStyle())
                                            .onChange(of: hotkey) { _, newValue in
                                                let clean = newValue.trimmingCharacters(in: .whitespaces).lowercased()
                                                if clean.count > 1 {
                                                    hotkey = String(clean.prefix(1))
                                                } else {
                                                    hotkey = clean
                                                }
                                            }
                                    }
                                    .frame(width: 140)
                                }
                            }
                            .padding(12)
                            .background(Color.white.opacity(0.04))
                            .cornerRadius(8)
                            
                            // Section 2: Model Configuration
                            VStack(alignment: .leading, spacing: 10) {
                                Text("Model Configuration")
                                    .font(.system(size: 11, weight: .bold))
                                    .foregroundColor(.secondary)
                                
                                VStack(alignment: .leading, spacing: 6) {
                                    Text("LLM Model Name")
                                        .font(.system(size: 11, weight: .semibold))
                                    HStack {
                                        TextField("Model path identifier", text: $model)
                                            .textFieldStyle(RoundedBorderTextFieldStyle())
                                        
                                        Menu {
                                            ForEach(popularModels, id: \.self) { item in
                                                Button(item) {
                                                    model = item
                                                }
                                            }
                                        } label: {
                                            Image(systemName: "chevron.down.circle")
                                                .font(.system(size: 14))
                                        }
                                        .menuStyle(BorderlessButtonMenuStyle())
                                        .frame(width: 24)
                                    }
                                }
                            }
                            .padding(12)
                            .background(Color.white.opacity(0.04))
                            .cornerRadius(8)
                            
                            // Section 3: Prompt Instructions
                            VStack(alignment: .leading, spacing: 10) {
                                Text("System Prompt Instructions")
                                    .font(.system(size: 11, weight: .bold))
                                    .foregroundColor(.secondary)
                                
                                TextEditor(text: $prompt)
                                    .font(.system(.body, design: .monospaced))
                                    .frame(minHeight: 180)
                                    .padding(6)
                                    .background(Color(NSColor.textBackgroundColor).opacity(0.8))
                                    .cornerRadius(6)
                                    .overlay(
                                        RoundedRectangle(cornerRadius: 6)
                                            .stroke(Color.gray.opacity(0.2), lineWidth: 1)
                                    )
                            }
                            .padding(12)
                            .background(Color.white.opacity(0.04))
                            .cornerRadius(8)
                        }
                        .padding(.trailing, 4) // Spacing for ScrollBar
                    }
                    
                    // Messages Panel
                    if let err = errorMessage {
                        HStack {
                            Image(systemName: "exclamationmark.octagon.fill")
                                .foregroundColor(.red)
                            Text(err)
                                .font(.system(size: 12))
                                .foregroundColor(.red)
                        }
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.red.opacity(0.1))
                        .cornerRadius(6)
                    }
                    
                    if let succ = successMessage {
                        HStack {
                            Image(systemName: "checkmark.seal.fill")
                                .foregroundColor(.green)
                            Text(succ)
                                .font(.system(size: 12))
                                .foregroundColor(.green)
                        }
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.green.opacity(0.1))
                        .cornerRadius(6)
                    }
                    
                    Divider()
                    
                    // Action Buttons
                    HStack {
                        Spacer()
                        
                        Button("Close") {
                            onDismiss()
                        }
                        .keyboardShortcut(.cancelAction)
                        
                        Button("Save Changes") {
                            saveSelectedAction()
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.blue)
                        .keyboardShortcut("s", modifiers: .command)
                        .disabled(label.trimmingCharacters(in: .whitespaces).isEmpty || prompt.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
                .padding(20)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                VStack(spacing: 12) {
                    Image(systemName: "rectangle.dashed.badge.record")
                        .font(.system(size: 36))
                        .foregroundColor(.secondary)
                    Text("Select an action from the sidebar or add a new one.")
                        .foregroundColor(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .background(VisualEffectView(material: .windowBackground, blendingMode: .withinWindow))
        .frame(minWidth: 700, minHeight: 450)
        .onAppear {
            loadConfig()
        }
        .onChange(of: selectedActionId) { _, newId in
            errorMessage = nil
            successMessage = nil
            if let action = actions.first(where: { $0.label == newId }) {
                label = action.label
                hotkey = action.hotkey
                model = action.model
                prompt = action.prompt
            } else {
                label = ""
                hotkey = ""
                model = ""
                prompt = ""
            }
        }
    }
    
    private func loadConfig() {
        CaptureManager.shared.loadActions()
        if let config = CaptureManager.shared.actionsConfig {
            actions = config.actions
            selectedActionId = config.activeLabel
        }
    }
    
    private func addNewAction() {
        let defaultModel = CaptureManager.shared.actionsConfig?.actions.first?.model ?? "google/gemini-3.5-flash"
        let newLabel = "NEW_ACTION_\(actions.count + 1)"
        
        CaptureManager.shared.saveAction(originalLabel: nil, label: newLabel, hotkey: "", model: defaultModel, prompt: "Enter prompt instructions here")
        loadConfig()
        selectedActionId = newLabel
        successMessage = "Created action \(newLabel). Please customize it and click Save."
    }
    
    private func deleteSelectedAction() {
        guard let id = selectedActionId else { return }
        CaptureManager.shared.deleteAction(label: id)
        loadConfig()
        successMessage = "Action deleted successfully."
    }
    
    private func saveSelectedAction() {
        guard let original = selectedActionId else { return }
        errorMessage = nil
        successMessage = nil
        
        let cleanLabel = label.trimmingCharacters(in: .whitespaces)
        let cleanHotkey = hotkey.trimmingCharacters(in: .whitespaces).lowercased()
        
        // Validation
        if !cleanHotkey.isEmpty {
            let reserved = ["r", "m", "e"]
            if reserved.contains(cleanHotkey) {
                errorMessage = "Hotkey '\(hotkey)' is reserved (R, M, E for HUD control)"
                return
            }
            if actions.contains(where: { $0.label != original && $0.hotkey.lowercased() == cleanHotkey }) {
                errorMessage = "Hotkey '\(hotkey)' is already in use by another action"
                return
            }
        }
        
        CaptureManager.shared.saveAction(originalLabel: original, label: cleanLabel, hotkey: cleanHotkey, model: model, prompt: prompt)
        loadConfig()
        selectedActionId = cleanLabel
        successMessage = "Action saved successfully!"
        
        // Triggers status updates across other windows
        NotificationCenter.default.post(name: NSNotification.Name("CaptureStateUpdated"), object: nil)
    }
}

class ScreenshotActionEditorWindow: NSWindow {
    static var shared: ScreenshotActionEditorWindow?
    
    convenience init(onDismiss: @escaping () -> Void) {
        let view = ScreenshotActionEditorView(onDismiss: {
            onDismiss()
        })
        
        self.init(
            contentRect: NSRect(x: 0, y: 0, width: 750, height: 500),
            styleMask: [.titled, .closable, .resizable],
            backing: .buffered,
            defer: false
        )
        
        self.contentView = NSHostingView(rootView: view)
        self.title = "Screenshot Actions Editor"
        self.isMovableByWindowBackground = true
        self.hasShadow = true
        self.isReleasedWhenClosed = false
        self.level = .floating
        self.center()
        
        ScreenshotActionEditorWindow.shared = self
    }
}

class ScreenshotActionEditor {
    static func show() {
        DispatchQueue.main.async {
            if ScreenshotActionEditorWindow.shared == nil {
                let window = ScreenshotActionEditorWindow(onDismiss: {
                    ScreenshotActionEditor.hide()
                })
                window.alphaValue = 0.0
                window.makeKeyAndOrderFront(nil)
                
                NSAnimationContext.runAnimationGroup({ context in
                    context.duration = 0.2
                    context.timingFunction = CAMediaTimingFunction(name: .easeOut)
                    window.animator().alphaValue = 1.0
                })
            } else {
                ScreenshotActionEditorWindow.shared?.makeKeyAndOrderFront(nil)
            }
            NSApp.activate(ignoringOtherApps: true)
        }
    }
    
    static func hide() {
        DispatchQueue.main.async {
            guard let window = ScreenshotActionEditorWindow.shared else { return }
            ScreenshotActionEditorWindow.shared = nil
            
            NSAnimationContext.runAnimationGroup({ context in
                context.duration = 0.15
                context.timingFunction = CAMediaTimingFunction(name: .easeIn)
                window.animator().alphaValue = 0.0
            }, completionHandler: {
                window.close()
            })
        }
    }
}
