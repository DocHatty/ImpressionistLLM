import SwiftUI
import Cocoa

struct VisualEffectView: NSViewRepresentable {
    var material: NSVisualEffectView.Material = .hudWindow
    var blendingMode: NSVisualEffectView.BlendingMode = .withinWindow
    
    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = material
        view.blendingMode = blendingMode
        view.state = .active
        return view
    }
    
    func updateNSView(_ nsView: NSVisualEffectView, context: Context) {
        nsView.material = material
        nsView.blendingMode = blendingMode
    }
}

struct PromptItem: Identifiable, Decodable {
    var id: String { name }
    let name: String
}

struct FloatingPromptMenuView: View {
    @State private var searchText = ""
    @State private var prompts: [PromptItem] = []
    @State private var selectedIndex = 0
    @State private var hoveredIndex: Int? = nil
    
    var onSelect: (String) -> Void
    var onDismiss: () -> Void
    
    var filteredPrompts: [PromptItem] {
        var list: [PromptItem] = []
        if searchText.isEmpty {
            list.append(PromptItem(name: "+ Add Context/Grounding"))
        }
        
        let filtered = prompts.filter { $0.name.localizedCaseInsensitiveContains(searchText) }
        if searchText.isEmpty {
            list.append(contentsOf: prompts)
        } else {
            list.append(contentsOf: filtered)
        }
        return list
    }
    
    func itemImageColor(at index: Int) -> Color {
        guard index < filteredPrompts.count else { return .blue }
        let name = filteredPrompts[index].name
        if name == "+ Add Context/Grounding" {
            return selectedIndex == index ? .white : .green
        }
        return selectedIndex == index ? .white : .blue
    }
    
    func itemTextColor(at index: Int) -> Color {
        return selectedIndex == index ? .white : .primary
    }
    
    func itemBgColor(at index: Int) -> Color {
        guard index < filteredPrompts.count else { return .clear }
        let name = filteredPrompts[index].name
        if selectedIndex == index {
            return name == "+ Add Context/Grounding" ? Color.green : Color.blue
        } else if hoveredIndex == index {
            return name == "+ Add Context/Grounding" ? Color.green.opacity(0.12) : Color.white.opacity(0.08)
        } else {
            return name == "+ Add Context/Grounding" ? Color.green.opacity(0.06) : Color.clear
        }
    }
    
    var body: some View {
        VStack(spacing: 0) {
            // Search field
            HStack {
                Image(systemName: "magnifyingglass")
                    .foregroundColor(.gray)
                TextField("Search prompts...", text: $searchText)
                    .textFieldStyle(PlainTextFieldStyle())
                    .font(.system(size: 14))
                if !searchText.isEmpty {
                    Button(action: { searchText = "" }) {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundColor(.gray)
                    }
                    .buttonStyle(PlainButtonStyle())
                }
            }
            .padding(10)
            .background(Color.black.opacity(0.2))
            .cornerRadius(8)
            .padding([.top, .leading, .trailing], 12)
            
            // List of prompts
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(spacing: 2) {
                        if filteredPrompts.isEmpty {
                            Text("No prompts found")
                                .foregroundColor(.gray)
                                .font(.system(size: 13, weight: .light))
                                .padding(.top, 20)
                        } else {
                            ForEach(0..<filteredPrompts.count, id: \.self) { index in
                                let prompt = filteredPrompts[index]
                                let isContextAdd = prompt.name == "+ Add Context/Grounding"
                                HStack {
                                    Image(systemName: isContextAdd ? "plus.circle.fill" : "doc.text.fill")
                                        .foregroundColor(self.itemImageColor(at: index))
                                        .font(.system(size: 13))
                                    
                                    Text(prompt.name)
                                        .font(.system(size: 13, weight: isContextAdd ? .bold : .medium))
                                        .foregroundColor(self.itemTextColor(at: index))
                                    
                                    Spacer()
                                    
                                    if selectedIndex == index {
                                        Text("↵")
                                            .font(.system(size: 12))
                                            .foregroundColor(.white.opacity(0.8))
                                    }
                                }
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .background(
                                    RoundedRectangle(cornerRadius: 6)
                                        .fill(self.itemBgColor(at: index))
                                )
                                .contentShape(Rectangle())
                                .onTapGesture {
                                    selectedIndex = index
                                    onSelect(prompt.name)
                                }
                                .onHover { isHovering in
                                    if isHovering {
                                        hoveredIndex = index
                                    } else if hoveredIndex == index {
                                        hoveredIndex = nil
                                    }
                                }
                                .id(index)
                            }
                        }
                    }
                    .padding(12)
                }
                .frame(maxHeight: 280)
                .onChange(of: selectedIndex) { _, newIndex in
                    withAnimation(.easeOut(duration: 0.15)) {
                        proxy.scrollTo(newIndex, anchor: .center)
                    }
                }
            }
        }
        .frame(width: 320)
        .background(ImpressionistGradientBackground())
        .cornerRadius(12)
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.white.opacity(0.15), lineWidth: 1)
        )
        .onAppear {
            loadPrompts()
            // Reset state
            selectedIndex = 0
            searchText = ""
        }
        .onReceive(NotificationCenter.default.publisher(for: NSNotification.Name("MenuKeyDown"))) { notification in
            guard let event = notification.object as? NSEvent else { return }
            handleKeyEvent(event)
        }
    }
    
    private func handleKeyEvent(_ event: NSEvent) {
        let count = filteredPrompts.count
        if count == 0 { return }
        
        switch event.keyCode {
        case 125: // Arrow Down
            selectedIndex = (selectedIndex + 1) % count
        case 126: // Arrow Up
            selectedIndex = (selectedIndex - 1 + count) % count
        case 36: // Enter
            if selectedIndex < count {
                onSelect(filteredPrompts[selectedIndex].name)
            }
        case 53: // Escape
            onDismiss()
        default:
            break
        }
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
                struct PromptsResponse: Decodable {
                    let success: Bool
                    let data: [PromptItem]
                }
                
                if let decoded = try? JSONDecoder().decode(PromptsResponse.self, from: data) {
                    DispatchQueue.main.async {
                        self.prompts = decoded.data
                    }
                }
            }
        }.resume()
    }
}

class FloatingPromptMenuWindow: NSWindow {
    static var shared: FloatingPromptMenuWindow?
    
    convenience init(onSelect: @escaping (String) -> Void) {
        let view = FloatingPromptMenuView(
            onSelect: onSelect,
            onDismiss: {
                FloatingPromptMenuWindow.shared?.close()
                FloatingPromptMenuWindow.shared = nil
            }
        )
        
        self.init(
            contentRect: NSRect(x: 0, y: 0, width: 320, height: 360),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        
        self.contentView = NSHostingView(rootView: view)
        self.isMovableByWindowBackground = false
        self.backgroundColor = .clear
        self.isOpaque = false
        self.hasShadow = true
        self.isReleasedWhenClosed = false
        self.level = .floating
        self.ignoresMouseEvents = false
        
        FloatingPromptMenuWindow.shared = self
    }
    
    override var canBecomeKey: Bool {
        return true
    }
    
    override var canBecomeMain: Bool {
        return true
    }
    
    override func keyDown(with event: NSEvent) {
        // Forward to SwiftUI notification
        NotificationCenter.default.post(name: NSNotification.Name("MenuKeyDown"), object: event)
        if event.keyCode != 36 && event.keyCode != 53 && event.keyCode != 125 && event.keyCode != 126 {
            super.keyDown(with: event)
        }
    }
    
    override func resignKey() {
        super.resignKey()
        // Automatically close when losing focus
        FloatingPromptMenu.hide()
        
        // Restore focus to previously active application
        if let lastApp = AppState.shared.lastActiveApp {
            lastApp.activate()
        }
    }
}

class FloatingPromptMenu {
    static func show(onSelect: @escaping (String) -> Void) {
        if FloatingPromptMenuWindow.shared != nil {
            hide()
            return
        }
        let mouseLocation = NSEvent.mouseLocation
        let window = FloatingPromptMenuWindow(onSelect: onSelect)
        
        // Position centered around mouse pointer
        let width: CGFloat = 320
        let height: CGFloat = 360
        let x = mouseLocation.x - (width / 2)
        let y = mouseLocation.y - (height / 2)
        
        // Clamp to screen bounds
        if let screen = NSScreen.screens.first(where: { NSMouseInRect(mouseLocation, $0.frame, false) }) {
            let workArea = screen.visibleFrame
            let rx = max(workArea.minX, min(x, workArea.maxX - width))
            let ry = max(workArea.minY, min(y, workArea.maxY - height))
            window.setFrame(NSRect(x: rx, y: ry, width: width, height: height), display: true)
        } else {
            window.setFrame(NSRect(x: x, y: y, width: width, height: height), display: true)
        }
        
        window.alphaValue = 0.0
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        
        // Smooth fade-in animation
        NSAnimationContext.runAnimationGroup({ context in
            context.duration = 0.2
            context.timingFunction = CAMediaTimingFunction(name: .easeOut)
            window.animator().alphaValue = 1.0
        })
    }
    
    static func hide() {
        guard let window = FloatingPromptMenuWindow.shared else { return }
        FloatingPromptMenuWindow.shared = nil // Prevent double-hide
        
        // Smooth fade-out animation
        NSAnimationContext.runAnimationGroup({ context in
            context.duration = 0.15
            context.timingFunction = CAMediaTimingFunction(name: .easeIn)
            window.animator().alphaValue = 0.0
        }, completionHandler: {
            window.close()
        })
    }
}
