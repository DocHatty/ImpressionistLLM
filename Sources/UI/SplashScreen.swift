import SwiftUI
import Cocoa

struct SplashScreenView: View {
    @ObservedObject var state = AppState.shared
    let imagePath: String
    
    var body: some View {
        ZStack {
            // Painting Background
            if let nsImage = NSImage(contentsOfFile: imagePath) {
                Image(nsImage: nsImage)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } else {
                Color(red: 28/255, green: 25/255, blue: 23/255) // Slate fallback matching .ahk
            }
            
            // HUD panel at the bottom
            VStack {
                Spacer()
                VStack(spacing: 12) {
                    Text(state.splashStatus)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(Color(red: 250/255, green: 250/255, blue: 249/255)) // FAFAF9
                        .multilineTextAlignment(.center)
                    
                    ProgressView(value: state.splashProgress, total: 100)
                        .accentColor(Color(red: 190/255, green: 18/255, blue: 60/255)) // Crimson Red #be123c
                        .background(Color.white.opacity(0.1))
                        .scaleEffect(x: 1, y: 1.5, anchor: .center)
                        .padding(.horizontal, 20)
                }
                .padding(.vertical, 16)
                .padding(.horizontal, 20)
                .background(ImpressionistGradientBackground())
                .cornerRadius(8)
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(Color.white.opacity(0.15), lineWidth: 1)
                )
                .padding(.horizontal, 40)
                .padding(.bottom, 45)
            }
        }
        .frame(width: 600, height: 600)
        .cornerRadius(12) // Rounded corners for modern look
    }
}

class SplashScreenWindow: NSWindow {
    static var shared: SplashScreenWindow?
    
    convenience init(imagePath: String) {
        let view = SplashScreenView(imagePath: imagePath)
        
        self.init(
            contentRect: NSRect(x: 0, y: 0, width: 600, height: 600),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        
        self.contentView = NSHostingView(rootView: view)
        self.backgroundColor = .clear
        self.isOpaque = false
        self.hasShadow = true
        self.isReleasedWhenClosed = false
        self.level = .floating
        self.ignoresMouseEvents = true // Allow clicks to pass through loading screen
        
        SplashScreenWindow.shared = self
    }
    
    override var canBecomeKey: Bool {
        return false // Splash screen shouldn't steal focus
    }
}

class SplashScreen {
    static func show() {
        DispatchQueue.main.async {
            guard SplashScreenWindow.shared == nil else { return }
            
            let parentDir = (Bundle.main.bundlePath as NSString).deletingLastPathComponent
            let imgPath = (parentDir as NSString).appendingPathComponent("LoadingScreen.png")
            
            let window = SplashScreenWindow(imagePath: imgPath)
            
            // Position centered on primary screen
            if let screen = NSScreen.main {
                let frame = screen.frame
                let x = frame.minX + (frame.width - 600) / 2
                let y = frame.minY + (frame.height - 600) / 2
                window.setFrame(NSRect(x: x, y: y, width: 600, height: 600), display: true)
            }
            
            window.alphaValue = 0.0
            window.orderFront(nil)
            
            // Fade-in animation
            NSAnimationContext.runAnimationGroup({ context in
                context.duration = 0.3
                window.animator().alphaValue = 1.0
            })
        }
    }
    
    static func update(status: String, progress: Double) {
        DispatchQueue.main.async {
            AppState.shared.splashStatus = status
            AppState.shared.splashProgress = progress
        }
    }
    
    static func hide() {
        DispatchQueue.main.async {
            guard let window = SplashScreenWindow.shared else { return }
            
            // Brief sleep to let the "Ready!" state be observed
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
                NSAnimationContext.runAnimationGroup({ context in
                    context.duration = 0.4
                    window.animator().alphaValue = 0.0
                }, completionHandler: {
                    window.close()
                    SplashScreenWindow.shared = nil
                })
            }
        }
    }
}
