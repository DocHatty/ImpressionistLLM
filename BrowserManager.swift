import Cocoa

class BrowserManager {
    static let shared = BrowserManager()
    
    private var activeProcesses: [Process] = []
    
    private init() {}
    
    func openPromptManager() {
        let port = PythonServerManager.shared.port
        let ticket = createLaunchTicket()
        let url = "http://127.0.0.1:\(port)/?ticket=\(ticket)"
        openInAppMode(url: url)
    }
    
    func openContextManager() {
        let port = PythonServerManager.shared.port
        let ticket = createLaunchTicket()
        
        let contextBuffer = AppState.shared.contextBuffer
        let encodedContent = contextBuffer.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? ""
        let count = AppState.shared.contextSelectionCount
        
        let url = "http://127.0.0.1:\(port)/context?content=\(encodedContent)&count=\(count)&ticket=\(ticket)"
        openInAppMode(url: url)
    }
    
    func openChatUI() {
        let port = PythonServerManager.shared.port
        let ticket = createLaunchTicket()
        let sid = "\(Date().timeIntervalSince1970)-\(Int.random(in: 100000...999999))"
        
        // Pre-initialize chat session on server
        let initURL = URL(string: "http://127.0.0.1:\(port)/api/chat/init")!
        var request = URLRequest(url: initURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(PythonServerManager.shared.apiSecret, forHTTPHeaderField: "X-API-Secret")
        
        let initPayload: [String: Any] = [
            "sid": sid,
            "title": "Chat - Manual",
            "model": PythonServerManager.shared.defaultModel,
            "system": "You are a helpful AI assistant.",
            "user": "",
            "assistant": "",
            "autorun": false
        ]
        request.httpBody = try? JSONSerialization.data(withJSONObject: initPayload)
        
        let sem = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: request) { _, _, _ in
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 1.0)
        
        let url = "http://127.0.0.1:\(port)/chat?sid=\(sid)&ticket=\(ticket)"
        openInAppMode(url: url)
    }
    
    func openDebugConsole() {
        let port = PythonServerManager.shared.port
        let ticket = createLaunchTicket()
        let url = "http://127.0.0.1:\(port)/debug?ticket=\(ticket)"
        openInAppMode(url: url)
    }
    
    private func createLaunchTicket() -> String {
        let port = PythonServerManager.shared.port
        let secret = PythonServerManager.shared.apiSecret
        let url = URL(string: "http://127.0.0.1:\(port)/api/tickets/create")!
        
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(secret, forHTTPHeaderField: "X-API-Secret")
        request.httpBody = "{}".data(using: .utf8)
        
        var ticket = ""
        let sem = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: request) { data, _, _ in
            if let data = data {
                struct TicketResponse: Decodable {
                    let ticket: String
                }
                if let decoded = try? JSONDecoder().decode(TicketResponse.self, from: data) {
                    ticket = decoded.ticket
                }
            }
            sem.signal()
        }.resume()
        
        _ = sem.wait(timeout: .now() + 1.0)
        return ticket
    }
    
    func openInAppMode(url: String) {
        let chromePath = "/Applications/Google Chrome.app"
        let edgePath = "/Applications/Microsoft Edge.app"
        
        let process = Process()
        if FileManager.default.fileExists(atPath: chromePath) {
            process.executableURL = URL(fileURLWithPath: chromePath + "/Contents/MacOS/Google Chrome")
            process.arguments = ["--app=\(url)"]
        } else if FileManager.default.fileExists(atPath: edgePath) {
            process.executableURL = URL(fileURLWithPath: edgePath + "/Contents/MacOS/Microsoft Edge")
            process.arguments = ["--app=\(url)"]
        } else {
            if let nsUrl = URL(string: url) {
                NSWorkspace.shared.open(nsUrl)
            }
            return
        }
        
        do {
            try process.run()
            activeProcesses.append(process)
        } catch {
            print("Failed to launch browser: \(error)")
        }
    }
    
    func terminateAll() {
        for process in activeProcesses {
            if process.isRunning {
                process.terminate()
            }
        }
        activeProcesses.removeAll()
    }
}
