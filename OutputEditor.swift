import SwiftUI
import Cocoa

struct OutputEditorView: View {
    @State var text: String
    var promptName: String
    var onSave: (String) -> Void
    var onCancel: () -> Void
    
    var body: some View {
        VStack(spacing: 0) {
            // Title Header
            HStack {
                Text("Editor - \(promptName)")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundColor(.primary)
                Spacer()
                Button(action: onCancel) {
                    Image(systemName: "xmark")
                        .foregroundColor(.gray)
                }
                .buttonStyle(PlainButtonStyle())
            }
            .padding(.vertical, 12)
            .background(ImpressionistGradientBackground())
            
            // Text Editor Area
            TextEditor(text: $text)
                .font(.system(.body, design: .monospaced))
                .padding(8)
                .background(Color(NSColor.textBackgroundColor))
            
            Divider()
            
            // Action Buttons
            HStack(spacing: 12) {
                Button("Cancel") {
                    onCancel()
                }
                .keyboardShortcut(.cancelAction)
                
                Spacer()
                
                Button("Copy Only") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(text, forType: .string)
                    ProcessingHUD.showNotification("Copied to clipboard!", style: "success")
                    onCancel()
                }
                
                Button("Paste & Exit") {
                    onSave(text)
                }
                .buttonStyle(.borderedProminent)
                .tint(.blue)
                .keyboardShortcut(.defaultAction)
            }
            .padding(12)
            .background(ImpressionistGradientBackground())
        }
        .background(VisualEffectView(material: .windowBackground, blendingMode: .withinWindow))
        .frame(minWidth: 480, minHeight: 320)
    }
}

class OutputEditorWindow: NSWindow {
    static var shared: OutputEditorWindow?
    
    convenience init(text: String, promptName: String, onSave: @escaping (String) -> Void) {
        let view = OutputEditorView(
            text: text,
            promptName: promptName,
            onSave: { editedText in
                onSave(editedText)
                OutputEditor.hide()
            },
            onCancel: {
                OutputEditor.hide()
            }
        )
        
        self.init(
            contentRect: NSRect(x: 0, y: 0, width: 500, height: 380),
            styleMask: [.titled, .closable, .resizable],
            backing: .buffered,
            defer: false
        )
        
        self.contentView = NSHostingView(rootView: view)
        self.title = "ImpressionistLLM Output Editor"
        self.isMovableByWindowBackground = true
        self.hasShadow = true
        self.isReleasedWhenClosed = false
        self.level = .floating
        self.center()
        
        OutputEditorWindow.shared = self
    }
}

class OutputEditor {
    static func show(text: String, promptName: String, onSave: @escaping (String) -> Void) {
        DispatchQueue.main.async {
            OutputEditorWindow.shared?.close()
            OutputEditorWindow.shared = nil
            
            let window = OutputEditorWindow(text: text, promptName: promptName, onSave: onSave)
            window.alphaValue = 0.0
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            
            NSAnimationContext.runAnimationGroup({ context in
                context.duration = 0.2
                context.timingFunction = CAMediaTimingFunction(name: .easeOut)
                window.animator().alphaValue = 1.0
            })
        }
    }
    
    static func hide() {
        DispatchQueue.main.async {
            guard let window = OutputEditorWindow.shared else { return }
            OutputEditorWindow.shared = nil
            
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
