import Cocoa
import SwiftUI

struct ActionInfo: Codable, Hashable, Identifiable {
    var id: String { label }
    var label: String
    var hotkey: String
    var model: String
    var prompt: String
}

struct PreprocessorConfig: Codable {
    var enabled: Bool
    var model: String?
    var fallback: String?
}

struct ActionsConfig: Codable {
    var activeLabel: String
    var actions: [ActionInfo]
    var preprocessor: PreprocessorConfig?
}

class CaptureManager {
    static let shared = CaptureManager()
    
    var overlayWindows: [CaptureWindow] = []
    private var capturedScreenshots: [NSScreen: NSImage] = [:]
    
    var activeAction: ActionInfo?
    var actionsConfig: ActionsConfig?
    var isMultiMode = false
    
    private init() {
        loadActions()
    }
    
    func loadActions() {
        let parentDir = (Bundle.main.bundlePath as NSString).deletingLastPathComponent
        let jsonPath = (parentDir as NSString).appendingPathComponent("prompts/processors/screenshot_actions.json")
        
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: jsonPath)),
              let config = try? JSONDecoder().decode(ActionsConfig.self, from: data) else {
            // Sane defaults
            let defaultActions = [
                ActionInfo(label: "OCR", hotkey: "1", model: "google/gemini-3-flash-preview", prompt: "OCR the screenshot only, whatever text is present reproduce it as output and nothing else"),
                ActionInfo(label: "DESCRIBE", hotkey: "2", model: "google/gemini-3-flash-preview", prompt: "Describe exactly what is seen in the provided image as it would be described in a radiology report..."),
                ActionInfo(label: "Anatomy", hotkey: "3", model: "openai/gpt-5.5", prompt: "Describe exactly what anatomy is being pinpointed by arrow...")
            ]
            self.actionsConfig = ActionsConfig(activeLabel: "OCR", actions: defaultActions, preprocessor: PreprocessorConfig(enabled: false, model: nil, fallback: nil))
            self.activeAction = defaultActions[0]
            return
        }
        
        self.actionsConfig = config
        self.activeAction = config.actions.first { $0.label == config.activeLabel } ?? config.actions.first
    }
    
    func saveActiveActionLabel(_ label: String) {
        if var config = actionsConfig {
            config.activeLabel = label
            self.actionsConfig = config
            saveConfig()
        }
    }
    
    func saveAction(originalLabel: String?, label: String, hotkey: String, model: String, prompt: String) {
        var actions = actionsConfig?.actions ?? []
        let newAction = ActionInfo(label: label, hotkey: hotkey, model: model, prompt: prompt)
        
        if let original = originalLabel, let index = actions.firstIndex(where: { $0.label == original }) {
            actions[index] = newAction
        } else {
            actions.append(newAction)
        }
        
        actionsConfig?.actions = actions
        if actionsConfig?.activeLabel == originalLabel {
            actionsConfig?.activeLabel = label
        }
        saveConfig()
    }
    
    func deleteAction(label: String) {
        var actions = actionsConfig?.actions ?? []
        actions.removeAll { $0.label == label }
        actionsConfig?.actions = actions
        
        if actionsConfig?.activeLabel == label {
            actionsConfig?.activeLabel = actions.first?.label ?? ""
        }
        saveConfig()
    }
    
    func saveConfig() {
        let parentDir = (Bundle.main.bundlePath as NSString).deletingLastPathComponent
        let jsonPath = (parentDir as NSString).appendingPathComponent("prompts/processors/screenshot_actions.json")
        
        if let config = actionsConfig {
            let encoder = JSONEncoder()
            encoder.outputFormatting = .prettyPrinted
            if let data = try? encoder.encode(config) {
                try? data.write(to: URL(fileURLWithPath: jsonPath))
            }
        }
        loadActions()
    }
    
    func togglePreprocessor() {
        if var config = actionsConfig {
            var prep = config.preprocessor ?? PreprocessorConfig(enabled: false, model: nil, fallback: nil)
            prep.enabled.toggle()
            config.preprocessor = prep
            self.actionsConfig = config
            saveConfig()
            NotificationCenter.default.post(name: NSNotification.Name("CaptureStateUpdated"), object: nil)
        }
    }
    
    func selectActionByHotkey(_ key: String) -> Bool {
        guard let config = actionsConfig else { return false }
        if let action = config.actions.first(where: { $0.hotkey == key }) {
            self.activeAction = action
            saveActiveActionLabel(action.label)
            return true
        }
        return false
    }
    
    func captureDisplayImage(screen: NSScreen) -> NSImage? {
        let tempPath = NSTemporaryDirectory() + "screenshot_\(UUID().uuidString).png"
        
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        
        let displayID = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID ?? 0
        if displayID != 0 {
            process.arguments = ["-x", "-D", "\(displayID)", tempPath]
        } else {
            process.arguments = ["-x", tempPath]
        }
        
        do {
            try process.run()
            process.waitUntilExit()
            
            if process.terminationStatus == 0 {
                if let image = NSImage(contentsOfFile: tempPath) {
                    try? FileManager.default.removeItem(atPath: tempPath)
                    return image
                }
            }
        } catch {
            print("CaptureManager: Failed to run screencapture: \(error)")
        }
        
        try? FileManager.default.removeItem(atPath: tempPath)
        return nil
    }
    
    func startCapture() {
        if AppState.shared.isProcessingScreenshot {
            return
        }
        
        if let frontmost = NSWorkspace.shared.frontmostApplication,
           frontmost.processIdentifier != ProcessInfo.processInfo.processIdentifier {
            AppState.shared.lastActiveApp = frontmost
        }
        
        isMultiMode = false
        AppState.shared.isScreenshotMode = true
        AppState.shared.screenshotImages.removeAll()
        
        capturedScreenshots.removeAll()
        overlayWindows.removeAll()
        
        let group = DispatchGroup()
        let lock = NSLock()
        
        // Grab display screenshots concurrently in background
        for screen in NSScreen.screens {
            group.enter()
            DispatchQueue.global(qos: .userInteractive).async { [weak self] in
                guard let self = self else {
                    group.leave()
                    return
                }
                if let nsImage = self.captureDisplayImage(screen: screen) {
                    lock.lock()
                    self.capturedScreenshots[screen] = nsImage
                    lock.unlock()
                }
                group.leave()
            }
        }
        
        // Show overlays as soon as all screens are captured
        group.notify(queue: .main) {
            guard AppState.shared.isScreenshotMode else { return }
            
            for screen in NSScreen.screens {
                let bgImage = self.capturedScreenshots[screen]
                let window = CaptureWindow(screen: screen, backgroundImage: bgImage, manager: self)
                self.overlayWindows.append(window)
                window.makeKeyAndOrderFront(nil)
            }
            
            // Show HUD overlay on primary screen
            if let primaryScreen = NSScreen.screens.first {
                let hud = CaptureHUDWindow(screen: primaryScreen, manager: self)
                hud.alphaValue = 0.0
                hud.makeKeyAndOrderFront(nil)
                
                NSAnimationContext.runAnimationGroup({ context in
                    context.duration = 0.25
                    context.timingFunction = CAMediaTimingFunction(name: .easeOut)
                    hud.animator().alphaValue = 1.0
                })
            }
            
            NSApp.activate(ignoringOtherApps: true)
        }
    }
    
    func stopCapture(cancelled: Bool) {
        guard AppState.shared.isScreenshotMode else { return }
        
        for window in overlayWindows {
            if let cv = window.captureView {
                cv.backgroundImage = nil
            }
            window.contentView = nil
            window.captureView = nil
            window.close()
        }
        overlayWindows.removeAll()
        capturedScreenshots.removeAll()
        
        if let hud = CaptureHUDWindow.shared {
            CaptureHUDWindow.shared = nil
            NSAnimationContext.runAnimationGroup({ context in
                context.duration = 0.15
                context.timingFunction = CAMediaTimingFunction(name: .easeIn)
                hud.animator().alphaValue = 0.0
            }, completionHandler: {
                hud.close()
            })
        }
        
        AppState.shared.isScreenshotMode = false
        
        if cancelled {
            AppState.shared.screenshotImages.removeAll()
            AppState.shared.isProcessingScreenshot = false
        }
        
        // Restore focus to previously active application
        if let lastApp = AppState.shared.lastActiveApp {
            lastApp.activate()
        }
    }
    
    func processScreenshotCrop(screen: NSScreen, rect: NSRect) {
        guard let bgImage = capturedScreenshots[screen] else { return }
        
        // Convert screen-local rect to image coordinates
        let imageSize = bgImage.size
        
        // Crop the image
        if let cropped = cropImage(image: bgImage, rect: rect, size: imageSize) {
            let maxEdge = CGFloat(Double(SettingsLoader(path: (self.scriptDir() as NSString).appendingPathComponent("config/settings.ini")).get(section: "Application", key: "ScreenshotMaxEdge", default: "2048")) ?? 2048)
            
            if let base64 = base64EncodedRepresentation(image: cropped, maxEdge: maxEdge) {
                AppState.shared.screenshotImages.append(base64)
            }
        }
    }
    
    private func scriptDir() -> String {
        return (Bundle.main.bundlePath as NSString).deletingLastPathComponent
    }
    
    private func cropImage(image: NSImage, rect: NSRect, size: NSSize) -> NSImage? {
        guard let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return nil }
        
        let scale = CGFloat(cgImage.width) / size.width
        let pixelRect = CGRect(
            x: rect.origin.x * scale,
            y: (size.height - rect.origin.y - rect.size.height) * scale,
            width: rect.size.width * scale,
            height: rect.size.height * scale
        )
        
        guard let croppedCgImage = cgImage.cropping(to: pixelRect) else { return nil }
        return NSImage(cgImage: croppedCgImage, size: pixelRect.size)
    }
    
    private func base64EncodedRepresentation(image: NSImage, maxEdge: CGFloat) -> String? {
        var finalImage = image
        
        if image.size.width > maxEdge || image.size.height > maxEdge {
            let ratio = image.size.width / image.size.height
            var newSize: NSSize
            if ratio > 1 {
                newSize = NSSize(width: maxEdge, height: maxEdge / ratio)
            } else {
                newSize = NSSize(width: maxEdge * ratio, height: maxEdge)
            }
            
            let newImage = NSImage(size: newSize)
            newImage.lockFocus()
            image.draw(in: NSRect(origin: .zero, size: newSize), from: .zero, operation: .copy, fraction: 1.0)
            newImage.unlockFocus()
            finalImage = newImage
        }
        
        guard let tiffData = finalImage.tiffRepresentation,
              let bitmapRep = NSBitmapImageRep(data: tiffData),
              let jpegData = bitmapRep.representation(using: .jpeg, properties: [.compressionFactor: 0.85]) else {
            return nil
        }
        
        return jpegData.base64EncodedString()
    }
}

class CaptureView: NSView {
    var backgroundImage: NSImage?
    var manager: CaptureManager
    
    var startPoint: NSPoint?
    var currentPoint: NSPoint?
    var selectionRect: NSRect? {
        didSet {
            updateMaskLayer()
        }
    }
    
    var isMultiMode: Bool {
        get { manager.isMultiMode }
        set { manager.isMultiMode = newValue }
    }
    
    private var dimmingLayer: CAShapeLayer!
    private var borderLayer: CAShapeLayer!
    
    init(frame frameRect: NSRect, backgroundImage: NSImage?, manager: CaptureManager) {
        self.backgroundImage = backgroundImage
        self.manager = manager
        super.init(frame: frameRect)
        
        self.wantsLayer = true
        if let cgImage = backgroundImage?.cgImage(forProposedRect: nil, context: nil, hints: nil) {
            self.layer?.contents = cgImage
        }
        
        setupLayers()
    }
    
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
    
    private func setupLayers() {
        dimmingLayer = CAShapeLayer()
        dimmingLayer.fillColor = NSColor.black.withAlphaComponent(0.4).cgColor
        dimmingLayer.fillRule = .evenOdd
        self.layer?.addSublayer(dimmingLayer)
        
        borderLayer = CAShapeLayer()
        borderLayer.strokeColor = NSColor.systemBlue.cgColor
        borderLayer.fillColor = NSColor.clear.cgColor
        borderLayer.lineWidth = 2.0
        self.layer?.addSublayer(borderLayer)
        
        dimmingLayer.frame = bounds
        borderLayer.frame = bounds
        updateMaskLayer()
    }
    
    override func setFrameSize(_ newSize: NSSize) {
        super.setFrameSize(newSize)
        dimmingLayer?.frame = bounds
        borderLayer?.frame = bounds
        updateMaskLayer()
    }
    
    private func updateMaskLayer() {
        // Use CATransaction to disable implicit animations so the mask tracks the cursor instantly
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        
        let path = CGMutablePath()
        path.addRect(bounds) // Outer bounds
        
        if let selRect = selectionRect {
            path.addRect(selRect) // Inner rect creates a hole due to .evenOdd fill rule
            borderLayer.path = CGPath(rect: selRect, transform: nil)
        } else {
            borderLayer.path = nil
        }
        
        dimmingLayer.path = path
        CATransaction.commit()
    }
    
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        return true
    }
    
    override func mouseDown(with event: NSEvent) {
        startPoint = convert(event.locationInWindow, from: nil)
        currentPoint = startPoint
        selectionRect = nil
    }
    
    override func mouseDragged(with event: NSEvent) {
        guard let start = startPoint else { return }
        currentPoint = convert(event.locationInWindow, from: nil)
        
        let minX = min(start.x, currentPoint!.x)
        let minY = min(start.y, currentPoint!.y)
        let width = abs(start.x - currentPoint!.x)
        let height = abs(start.y - currentPoint!.y)
        
        selectionRect = NSRect(x: minX, y: minY, width: width, height: height)
    }
    
    override func mouseUp(with event: NSEvent) {
        guard let rect = selectionRect, rect.width > 5, rect.height > 5, let screen = window?.screen else {
            // Cancel / reset selection if too small
            startPoint = nil
            currentPoint = nil
            selectionRect = nil
            return
        }
        
        manager.processScreenshotCrop(screen: screen, rect: rect)
        
        startPoint = nil
        currentPoint = nil
        selectionRect = nil
        
        if !isMultiMode {
            // Run processing immediately
            manager.stopCapture(cancelled: false)
            NotificationCenter.default.post(name: NSNotification.Name("ScreenshotCaptureFinished"), object: nil)
        }
    }
}

class CaptureWindow: NSWindow {
    var captureView: CaptureView?
    var manager: CaptureManager
    
    init(screen: NSScreen, backgroundImage: NSImage?, manager: CaptureManager) {
        self.manager = manager
        super.init(
            contentRect: screen.frame,
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        self.setFrame(screen.frame, display: true)
        self.isReleasedWhenClosed = false
        
        self.captureView = CaptureView(frame: screen.frame, backgroundImage: backgroundImage, manager: manager)
        self.contentView = captureView
        
        self.isOpaque = false
        self.backgroundColor = .clear
        self.hasShadow = false
        self.level = .screenSaver
        self.ignoresMouseEvents = false
        self.acceptsMouseMovedEvents = true
        
        // Register notifications for state updates
        NotificationCenter.default.addObserver(self, selector: #selector(stateUpdated), name: NSNotification.Name("CaptureStateUpdated"), object: nil)
    }
    
    override var canBecomeKey: Bool {
        return true
    }
    
    override var canBecomeMain: Bool {
        return true
    }
    
    @objc func stateUpdated() {
        captureView?.needsDisplay = true
    }
    
    override func resignKey() {
        super.resignKey()
    }
    
    override func keyDown(with event: NSEvent) {
        guard AppState.shared.isScreenshotMode else { return }
        let keyCode = event.keyCode
        
        if keyCode == 53 { // Escape
            manager.stopCapture(cancelled: true)
            return
        }
        
        if keyCode == 46 { // 'm' - Toggle multi-mode
            manager.isMultiMode.toggle()
            NotificationCenter.default.post(name: NSNotification.Name("CaptureStateUpdated"), object: nil)
            return
        }
        
        if keyCode == 14 { // 'e' - Open Screenshot Action Editor
            manager.stopCapture(cancelled: true)
            ScreenshotActionEditor.show()
            return
        }
        
        if keyCode == 15 { // 'r' - Toggle preprocessor router
            manager.togglePreprocessor()
            return
        }
        
        if keyCode == 36 { // Enter
            // Process current captures
            if AppState.shared.screenshotImages.count > 0 {
                manager.stopCapture(cancelled: false)
                NotificationCenter.default.post(name: NSNotification.Name("ScreenshotCaptureFinished"), object: nil)
            } else {
                // If nothing captured, cancel
                manager.stopCapture(cancelled: true)
            }
            return
        }
        
        // Mapping character actions
        if let char = event.charactersIgnoringModifiers?.lowercased(), !char.isEmpty {
            if manager.selectActionByHotkey(char) {
                NotificationCenter.default.post(name: NSNotification.Name("CaptureStateUpdated"), object: nil)
            }
        }
    }
    
    deinit {
        NotificationCenter.default.removeObserver(self)
    }
}

struct CaptureHUDView: View {
    @ObservedObject var appState = AppState.shared
    var manager: CaptureManager
    
    var body: some View {
        HStack(spacing: 12) {
            // Crops Count Icon & Label
            HStack(spacing: 6) {
                Image(systemName: appState.screenshotImages.isEmpty ? "camera.shutter.button" : "camera.shutter.button.fill")
                    .font(.system(size: 13))
                    .foregroundColor(appState.screenshotImages.isEmpty ? .secondary : .green)
                Text("\(appState.screenshotImages.count) Crop\(appState.screenshotImages.count == 1 ? "" : "s")")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundColor(appState.screenshotImages.isEmpty ? .secondary : .primary)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(Color.white.opacity(0.06))
            .cornerRadius(8)
            
            Divider()
                .frame(height: 20)
                
            // Compact Dropdown Action Selector Menu
            Menu {
                if let actions = manager.actionsConfig?.actions {
                    ForEach(actions, id: \.label) { action in
                        Button(action: {
                            guard AppState.shared.isScreenshotMode else { return }
                            manager.activeAction = action
                            manager.saveActiveActionLabel(action.label)
                            NotificationCenter.default.post(name: NSNotification.Name("CaptureStateUpdated"), object: nil)
                        }) {
                            HStack {
                                Text(action.label)
                                if !action.hotkey.isEmpty {
                                    Spacer()
                                    Text("[\(action.hotkey.uppercased())]")
                                }
                            }
                        }
                    }
                }
                
                Divider()
                
                Button("Edit Actions...") {
                    manager.stopCapture(cancelled: true)
                    ScreenshotActionEditor.show()
                }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "wand.and.stars")
                        .font(.system(size: 11))
                        .foregroundColor(.white.opacity(0.9))
                    Text(manager.activeAction?.label ?? "Select Action")
                        .font(.system(size: 12, weight: .bold))
                    if let hotkey = manager.activeAction?.hotkey, !hotkey.isEmpty {
                        Text("[\(hotkey.uppercased())]")
                            .font(.system(size: 10, design: .monospaced))
                            .opacity(0.8)
                    }
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.system(size: 9))
                        .foregroundColor(.white.opacity(0.7))
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(
                    LinearGradient(
                        colors: [Color.blue, Color.blue.opacity(0.8)],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .foregroundColor(.white)
                .cornerRadius(8)
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(Color.white.opacity(0.15), lineWidth: 1)
                )
            }
            .menuStyle(.borderlessButton)
            .frame(width: 155)
            
            Divider()
                .frame(height: 20)
            
            // Soft-Glow State Toggles
            HStack(spacing: 8) {
                // Router (Rtr) Toggle
                let rtrEnabled = manager.actionsConfig?.preprocessor?.enabled ?? false
                Button(action: {
                    guard AppState.shared.isScreenshotMode else { return }
                    manager.togglePreprocessor()
                }) {
                    HStack(spacing: 4) {
                        Image(systemName: "arrow.triangle.branch")
                            .font(.system(size: 11))
                            .foregroundColor(rtrEnabled ? .green : .secondary)
                        Text(rtrEnabled ? "Router: ON" : "Router: OFF")
                            .font(.system(size: 11, weight: .semibold))
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(rtrEnabled ? Color.green.opacity(0.18) : Color.white.opacity(0.06))
                    .foregroundColor(rtrEnabled ? .green : .primary)
                    .cornerRadius(8)
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(rtrEnabled ? Color.green.opacity(0.35) : Color.white.opacity(0.05), lineWidth: 1)
                    )
                }
                .buttonStyle(.plain)
                
                // Multi-Crop Toggle
                let multiEnabled = manager.isMultiMode
                Button(action: {
                    guard AppState.shared.isScreenshotMode else { return }
                    manager.isMultiMode.toggle()
                    NotificationCenter.default.post(name: NSNotification.Name("CaptureStateUpdated"), object: nil)
                }) {
                    HStack(spacing: 4) {
                        Image(systemName: "plus.square.on.square")
                            .font(.system(size: 11))
                            .foregroundColor(multiEnabled ? .orange : .secondary)
                        Text(multiEnabled ? "Multi-Crop: ON" : "Multi-Crop: OFF")
                            .font(.system(size: 11, weight: .semibold))
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(multiEnabled ? Color.orange.opacity(0.18) : Color.white.opacity(0.06))
                    .foregroundColor(multiEnabled ? .orange : .primary)
                    .cornerRadius(8)
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(multiEnabled ? Color.orange.opacity(0.35) : Color.white.opacity(0.05), lineWidth: 1)
                    )
                }
                .buttonStyle(.plain)
            }
            
            Divider()
                .frame(height: 20)
            
            // Operations: Edit, Cancel, Process
            HStack(spacing: 8) {
                // Edit Settings
                Button(action: {
                    guard AppState.shared.isScreenshotMode else { return }
                    manager.stopCapture(cancelled: true)
                    ScreenshotActionEditor.show()
                }) {
                    HStack(spacing: 4) {
                        Image(systemName: "slider.horizontal.3")
                            .font(.system(size: 11))
                        Text("Edit [E]")
                            .font(.system(size: 11, weight: .semibold))
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(Color.white.opacity(0.06))
                    .foregroundColor(.primary)
                    .cornerRadius(8)
                }
                .buttonStyle(.plain)
                
                // Cancel Capture
                Button(action: {
                    guard AppState.shared.isScreenshotMode else { return }
                    manager.stopCapture(cancelled: true)
                }) {
                    HStack(spacing: 4) {
                        Image(systemName: "xmark.circle")
                            .font(.system(size: 11))
                        Text("Cancel [Esc]")
                            .font(.system(size: 11, weight: .semibold))
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(Color.red.opacity(0.12))
                    .foregroundColor(.red)
                    .cornerRadius(8)
                }
                .buttonStyle(.plain)
                
                // Process / Analyze Crops
                let hasCrops = !appState.screenshotImages.isEmpty
                Button(action: {
                    guard AppState.shared.isScreenshotMode else { return }
                    if hasCrops {
                        manager.stopCapture(cancelled: false)
                        NotificationCenter.default.post(name: NSNotification.Name("ScreenshotCaptureFinished"), object: nil)
                    }
                }) {
                    HStack(spacing: 4) {
                        Image(systemName: "sparkles")
                            .font(.system(size: 11))
                        Text("Process [Enter]")
                            .font(.system(size: 11, weight: .bold))
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(
                        hasCrops ? 
                        LinearGradient(colors: [Color.green, Color.green.opacity(0.8)], startPoint: .top, endPoint: .bottom) :
                        LinearGradient(colors: [Color.gray.opacity(0.2), Color.gray.opacity(0.15)], startPoint: .top, endPoint: .bottom)
                    )
                    .foregroundColor(hasCrops ? .white : .white.opacity(0.4))
                    .cornerRadius(8)
                    .shadow(color: hasCrops ? Color.green.opacity(0.3) : Color.clear, radius: 4, x: 0, y: 2)
                }
                .buttonStyle(.plain)
                .disabled(!hasCrops)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(ImpressionistGradientBackground())
        .cornerRadius(12)
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.white.opacity(0.15), lineWidth: 1)
        )
    }
}

class CaptureHUDWindow: NSWindow {
    static var shared: CaptureHUDWindow?
    var manager: CaptureManager
    
    init(screen: NSScreen, manager: CaptureManager) {
        self.manager = manager
        let screenFrame = screen.frame
        let hudWidth: CGFloat = 840
        let hudHeight: CGFloat = 55
        let x = screenFrame.minX + (screenFrame.width - hudWidth) / 2
        let y = screenFrame.maxY - hudHeight - 20
        
        super.init(
            contentRect: NSRect(x: x, y: y, width: hudWidth, height: hudHeight),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        
        self.isReleasedWhenClosed = false
        self.isOpaque = false
        self.backgroundColor = .clear
        self.hasShadow = true
        self.level = .screenSaver
        self.ignoresMouseEvents = false
        self.acceptsMouseMovedEvents = true
        
        let view = CaptureHUDView(manager: manager)
        self.contentView = NSHostingView(rootView: view)
        
        CaptureHUDWindow.shared = self
    }
    
    override var canBecomeKey: Bool {
        return true
    }
    
    override func keyDown(with event: NSEvent) {
        if let keyOverlay = manager.overlayWindows.first(where: { $0.isKeyWindow }) {
            keyOverlay.keyDown(with: event)
        } else if let firstOverlay = manager.overlayWindows.first {
            firstOverlay.keyDown(with: event)
        } else {
            super.keyDown(with: event)
        }
    }
}
