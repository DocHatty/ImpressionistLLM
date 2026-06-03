import SwiftUI
import Cocoa

struct ProcessingHUDView: View {
    @ObservedObject var state = AppState.shared
    
    var body: some View {
        HStack(spacing: 16) {
            ProgressView()
                .progressViewStyle(CircularProgressViewStyle(tint: .white))
                .scaleEffect(1.2)
            
            VStack(alignment: .leading, spacing: 2) {
                Text(state.processingStatus.isEmpty ? "Processing..." : state.processingStatus)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.white)
                
                Text("Press [Esc] to Cancel")
                    .font(.system(size: 11, weight: .regular))
                    .foregroundColor(.white.opacity(0.6))
            }
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 16)
        .background(ImpressionistGradientBackground())
        .cornerRadius(12)
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.white.opacity(0.15), lineWidth: 1)
        )
    }
}

class ProcessingHUDWindow: NSWindow {
    static var shared: ProcessingHUDWindow?
    
    convenience init() {
        let view = ProcessingHUDView()
        
        self.init(
            contentRect: NSRect(x: 0, y: 0, width: 300, height: 80),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        
        self.contentView = NSHostingView(rootView: view)
        self.isMovableByWindowBackground = true
        self.backgroundColor = .clear
        self.isOpaque = false
        self.hasShadow = true
        self.isReleasedWhenClosed = false
        self.level = .statusBar
        self.ignoresMouseEvents = false
        
        ProcessingHUDWindow.shared = self
    }
    
    override var canBecomeKey: Bool {
        return false // Don't steal key focus so user can keep typing elsewhere!
    }
}

class ProcessingHUD {
    static var activeNotifications: [NSWindow] = []
    
    static func show(_ status: String) {
        DispatchQueue.main.async {
            AppState.shared.isProcessingLLM = true
            AppState.shared.processingStatus = status
            
            if ProcessingHUDWindow.shared == nil {
                let window = ProcessingHUDWindow()
                
                // Position centered on primary screen
                if let screen = NSScreen.main {
                    let frame = screen.visibleFrame
                    let x = frame.minX + (frame.width - 300) / 2
                    let y = frame.minY + 80 // Position near the bottom center of screen
                    window.setFrame(NSRect(x: x, y: y, width: 300, height: 80), display: true)
                }
                
                window.alphaValue = 0.0
                window.orderFront(nil)
                
                NSAnimationContext.runAnimationGroup({ context in
                    context.duration = 0.25
                    context.timingFunction = CAMediaTimingFunction(name: .easeOut)
                    window.animator().alphaValue = 1.0
                })
            }
        }
    }
    
    static func update(_ status: String) {
        DispatchQueue.main.async {
            AppState.shared.processingStatus = status
        }
    }
    
    static func hide() {
        DispatchQueue.main.async {
            AppState.shared.isProcessingLLM = false
            AppState.shared.processingStatus = ""
            
            guard let window = ProcessingHUDWindow.shared else { return }
            ProcessingHUDWindow.shared = nil
            
            NSAnimationContext.runAnimationGroup({ context in
                context.duration = 0.2
                context.timingFunction = CAMediaTimingFunction(name: .easeIn)
                window.animator().alphaValue = 0.0
            }, completionHandler: {
                window.close()
            })
        }
    }
    
    // Shows a quick notification tooltip that fades out
    static func showNotification(_ message: String, style: String = "info", duration: TimeInterval = 2.0) {
        DispatchQueue.main.async {
            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 260, height: 50),
                styleMask: [.borderless],
                backing: .buffered,
                defer: false
            )
            window.isReleasedWhenClosed = false
            
            let color = style == "error" ? Color.red : (style == "warning" ? Color.orange : Color.blue)
            
            let view = HStack(spacing: 12) {
                Image(systemName: style == "error" ? "exclamationmark.triangle.fill" : (style == "warning" ? "exclamationmark.circle.fill" : "info.circle.fill"))
                    .foregroundColor(color)
                    .font(.system(size: 16))
                
                Text(message)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundColor(.white)
                
                Spacer()
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(ImpressionistGradientBackground())
            .cornerRadius(10)
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(Color.white.opacity(0.15), lineWidth: 1)
            )
            
            window.contentView = NSHostingView(rootView: view)
            window.backgroundColor = .clear
            window.isOpaque = false
            window.hasShadow = true
            window.level = .statusBar
            window.ignoresMouseEvents = true
            
            if let screen = NSScreen.main {
                let frame = screen.visibleFrame
                let x = frame.maxX - 280
                let y = frame.maxY - 70
                window.setFrame(NSRect(x: x, y: y, width: 260, height: 50), display: true)
            }
            
            window.orderFront(nil)
            
            // Retain the window strongly by adding it to our active list
            activeNotifications.append(window)
            
            // Fade out and close
            DispatchQueue.main.asyncAfter(deadline: .now() + duration) {
                NSAnimationContext.runAnimationGroup({ context in
                    context.duration = 0.5
                    window.animator().alphaValue = 0.0
                }, completionHandler: {
                    window.close()
                    // Release the window by removing it from our active list
                    activeNotifications.removeAll { $0 === window }
                })
            }
        }
    }
}
