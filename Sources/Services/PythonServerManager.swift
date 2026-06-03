import Cocoa

class SettingsLoader {
    private var config: [String: [String: String]] = [:]
    var configPath: String = ""
    
    init(path: String) {
        self.configPath = path
        load()
    }
    
    func load() {
        guard let content = try? String(contentsOfFile: configPath, encoding: .utf8) else {
            // If settings.ini doesn't exist, try bootstrapping from settings.ini.example
            let examplePath = configPath + ".example"
            if FileManager.default.fileExists(atPath: examplePath) {
                try? FileManager.default.copyItem(atPath: examplePath, toPath: configPath)
                if let content2 = try? String(contentsOfFile: configPath, encoding: .utf8) {
                    parse(content2)
                }
            }
            return
        }
        parse(content)
    }
    
    private func parse(_ content: String) {
        var currentSection = ""
        let lines = content.components(separatedBy: .newlines)
        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty || trimmed.hasPrefix(";") {
                continue
            }
            if trimmed.hasPrefix("[") && trimmed.hasSuffix("]") {
                currentSection = String(trimmed.dropFirst().dropLast()).trimmingCharacters(in: .whitespacesAndNewlines)
                if config[currentSection] == nil {
                    config[currentSection] = [:]
                }
            } else if !currentSection.isEmpty && trimmed.contains("=") {
                let parts = trimmed.split(separator: "=", maxSplits: 1)
                if parts.count == 2 {
                    let key = parts[0].trimmingCharacters(in: .whitespacesAndNewlines)
                    let value = parts[1].trimmingCharacters(in: .whitespacesAndNewlines)
                    config[currentSection]?[key] = value
                }
            }
        }
    }
    
    func get(section: String, key: String, default defaultValue: String = "") -> String {
        return config[section]?[key] ?? defaultValue
    }
    
    func getBool(section: String, key: String, default defaultValue: Bool = false) -> Bool {
        guard let val = config[section]?[key] else { return defaultValue }
        let lower = val.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        return lower == "true" || lower == "1" || lower == "yes" || lower == "on"
    }
}

class PythonServerManager {
    static let shared = PythonServerManager()
    
    var scriptDir: String = ""
    var tempDir: String = ""
    var port: Int = 58080
    var apiSecret: String = ""
    var apiKey: String = ""
    var defaultModel: String = "openai/gpt-5.5"
    
    private var serverProcess: Process?
    private var settings: SettingsLoader?
    
    private init() {
        // Resolve scriptDir relative to the app bundle location
        // For portable app, parent folder of the bundle:
        let bundlePath = Bundle.main.bundlePath
        let parentDir = (bundlePath as NSString).deletingLastPathComponent
        self.scriptDir = parentDir
        self.tempDir = (parentDir as NSString).appendingPathComponent("temp")
        
        let configPath = (parentDir as NSString).appendingPathComponent("config/settings.ini")
        self.settings = SettingsLoader(path: configPath)
        
        // Generate random API secret
        self.apiSecret = UUID().uuidString.replacingOccurrences(of: "-", with: "")
        
        resolveSettings()
    }
    
    func resolveSettings() {
        settings?.load()
        
        // Port
        if let envPort = ProcessInfo.processInfo.environment["SERVER_PORT"], let p = Int(envPort) {
            self.port = p
        } else if let portStr = settings?.get(section: "Server", key: "Port"), let p = Int(portStr) {
            self.port = p
        }
        
        // API Key
        if let envKey = ProcessInfo.processInfo.environment["OPENROUTER_API_KEY"], !envKey.isEmpty {
            self.apiKey = envKey
        } else {
            self.apiKey = settings?.get(section: "API", key: "APIKey") ?? ""
        }
        
        // Model
        self.defaultModel = settings?.get(section: "API", key: "DefaultModel", default: "openai/gpt-5.5") ?? "openai/gpt-5.5"
    }
    
    func isSetupNeeded() -> Bool {
        let venvPython = (scriptDir as NSString).appendingPathComponent("lib/core/.venv/bin/python")
        return !FileManager.default.fileExists(atPath: venvPython)
    }
    
    func setupVenv(statusCallback: @escaping (String) -> Void, completion: @escaping (Bool) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let venvPath = (self.scriptDir as NSString).appendingPathComponent("lib/core/.venv")
            let venvPython = (self.scriptDir as NSString).appendingPathComponent("lib/core/.venv/bin/python")
            
            if !FileManager.default.fileExists(atPath: venvPython) {
                statusCallback("Creating Python virtual environment...")
                let process = Process()
                process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
                process.arguments = ["-m", "venv", venvPath]
                
                do {
                    try process.run()
                    process.waitUntilExit()
                    if process.terminationStatus != 0 {
                        completion(false)
                        return
                    }
                } catch {
                    print("PythonServerManager Error: Failed to create venv: \(error)")
                    completion(false)
                    return
                }
            }
            
            statusCallback("Installing dependencies (takes ~15s)...")
            let pipPath = (self.scriptDir as NSString).appendingPathComponent("lib/core/.venv/bin/pip")
            let reqPath = (self.scriptDir as NSString).appendingPathComponent("requirements-vendor.txt")
            
            let process = Process()
            process.executableURL = URL(fileURLWithPath: pipPath)
            process.arguments = ["install", "-r", reqPath]
            
            do {
                try process.run()
                process.waitUntilExit()
                completion(process.terminationStatus == 0)
            } catch {
                print("PythonServerManager Error: Failed to run pip install: \(error)")
                completion(false)
            }
        }
    }
    
    func startServer(statusCallback: @escaping (String) -> Void, completion: @escaping (Bool) -> Void) {
        killExistingServerOnPort()
        
        let portFilePath = (self.tempDir as NSString).appendingPathComponent("server_port.txt")
        try? FileManager.default.removeItem(atPath: portFilePath)
        
        let serverScript = (self.scriptDir as NSString).appendingPathComponent("lib/core/http_server.py")
        let venvPython = (self.scriptDir as NSString).appendingPathComponent("lib/core/.venv/bin/python")
        
        if !FileManager.default.fileExists(atPath: venvPython) {
            print("PythonServerManager Error: Python executable not found at path: \(venvPython)")
            completion(false)
            return
        }
        
        statusCallback("Starting local backend server...")
        
        let process = Process()
        process.executableURL = URL(fileURLWithPath: venvPython)
        process.arguments = [serverScript, self.scriptDir, self.tempDir]
        process.currentDirectoryURL = URL(fileURLWithPath: self.scriptDir)
        
        // Environment variables
        var env = ProcessInfo.processInfo.environment
        env["IMPRESSIONIST_API_SECRET"] = self.apiSecret
        env["OPENROUTER_API_KEY"] = self.apiKey
        process.environment = env
        
        // Create logs directory
        let logsDir = (self.scriptDir as NSString).appendingPathComponent("logs")
        try? FileManager.default.createDirectory(atPath: logsDir, withIntermediateDirectories: true)
        
        let logPath = (logsDir as NSString).appendingPathComponent("server_startup.log")
        FileManager.default.createFile(atPath: logPath, contents: nil)
        
        if let fileHandle = FileHandle(forWritingAtPath: logPath) {
            process.standardOutput = fileHandle
            process.standardError = fileHandle
        }
        
        do {
            try process.run()
            self.serverProcess = process
            
            // Wait for server health verification
            statusCallback("Verifying server health...")
            let start = Date()
            var ok = false
            var portRead = false
            while Date().timeIntervalSince(start) < 8.0 {
                if !portRead {
                    if FileManager.default.fileExists(atPath: portFilePath),
                       let portStr = try? String(contentsOfFile: portFilePath, encoding: .utf8) {
                        let trimmed = portStr.trimmingCharacters(in: .whitespacesAndNewlines)
                        if let p = Int(trimmed) {
                            self.port = p
                            portRead = true
                            print("PythonServerManager: Server bound to dynamic port \(p)")
                        }
                    }
                }
                
                if portRead && self.checkHealth() {
                    ok = true
                    break
                }
                Thread.sleep(forTimeInterval: 0.25)
            }
            
            completion(ok)
        } catch {
            print("PythonServerManager Error: Failed to run Python server process: \(error)")
            completion(false)
        }
    }
    
    func stopServer() {
        guard let process = serverProcess, process.isRunning else { return }
        
        // Ask server to shutdown gracefully via API
        let shutdownURL = URL(string: "http://127.0.0.1:\(port)/api/shutdown")!
        var request = URLRequest(url: shutdownURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(apiSecret, forHTTPHeaderField: "X-API-Secret")
        request.httpBody = "{}".data(using: .utf8)
        
        let sem = DispatchSemaphore(value: 0)
        let task = URLSession.shared.dataTask(with: request) { _, _, _ in
            sem.signal()
        }
        task.resume()
        _ = sem.wait(timeout: .now() + 1.0)
        
        // Kill if still running
        if process.isRunning {
            process.terminate()
        }
        serverProcess = nil
    }
    
    func checkHealth() -> Bool {
        let url = URL(string: "http://127.0.0.1:\(port)/health")!
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 0.8
        
        let sem = DispatchSemaphore(value: 0)
        var healthy = false
        
        let task = URLSession.shared.dataTask(with: request) { data, response, error in
            if let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 {
                healthy = true
            }
            sem.signal()
        }
        task.resume()
        _ = sem.wait(timeout: .now() + 1.0)
        return healthy
    }
    
    private func killExistingServerOnPort() {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        process.arguments = ["-t", "-i:\(port)"]
        
        let pipe = Pipe()
        process.standardOutput = pipe
        
        do {
            try process.run()
            process.waitUntilExit()
            
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            if let output = String(data: data, encoding: .utf8) {
                let pids = output.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                for pid in pids {
                    if let pidInt = Int32(pid), pidInt != ProcessInfo.processInfo.processIdentifier {
                        print("PythonServerManager: Killing process \(pidInt) on port \(port)")
                        kill(pidInt, SIGKILL)
                    }
                }
            }
        } catch {}
    }
}
