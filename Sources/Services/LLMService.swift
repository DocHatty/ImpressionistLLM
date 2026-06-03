import Cocoa

class LLMService {
    static let shared = LLMService()
    
    var activeCompletionTask: URLSessionDataTask?
    
    private init() {}
    
    func ensureServerHealthy(completion: @escaping (Bool) -> Void) {
        if PythonServerManager.shared.checkHealth() {
            completion(true)
            return
        }
        
        debugLog("LLMService: Local server unhealthy. Attempting restart...")
        DispatchQueue.main.async {
            ProcessingHUD.show("Backend offline. Restarting...")
        }
        
        DispatchQueue.global(qos: .userInitiated).async {
            PythonServerManager.shared.startServer(statusCallback: { msg in
                DispatchQueue.main.async {
                    ProcessingHUD.show(msg)
                }
            }, completion: { success in
                DispatchQueue.main.async {
                    ProcessingHUD.hide()
                    if success {
                        ProcessingHUD.showNotification("Backend restarted!", style: "success")
                    } else {
                        ProcessingHUD.showNotification("Failed to restart backend server", style: "error")
                    }
                }
                completion(success)
            })
        }
    }
    
    func executePrompt(_ name: String) {
        guard !AppState.shared.isProcessingLLM else {
            debugLog("LLMService: Already processing a prompt, ignoring duplicate execution")
            return
        }
        
        guard let selectedText = ClipboardHelper.copySelectedText() else {
            ProcessingHUD.showNotification("No text selected. Select text and try again.", style: "error")
            return
        }
        
        // Lock immediately before async health check
        AppState.shared.isProcessingLLM = true
        
        ensureServerHealthy { [weak self] healthy in
            guard healthy else {
                DispatchQueue.main.async { AppState.shared.isProcessingLLM = false }
                return
            }
            
            guard let self = self else { return }
            let port = PythonServerManager.shared.port
            let secret = PythonServerManager.shared.apiSecret
            let url = URL(string: "http://127.0.0.1:\(port)/api/prompt/\(self.urlEncode(name))")!
            
            var request = URLRequest(url: url)
            request.httpMethod = "GET"
            request.setValue(secret, forHTTPHeaderField: "X-API-Secret")
            
            DispatchQueue.main.async {
                ProcessingHUD.show("Loading prompt \(name)...")
            }
            
            URLSession.shared.dataTask(with: request) { data, response, error in
                ProcessingHUD.hide()
                
                guard let data = data else {
                    ProcessingHUD.showNotification("Failed to connect to local server", style: "error")
                    return
                }
                
                struct PromptDetail: Decodable {
                    let success: Bool
                    let data: PromptDetailData
                }
                struct PromptDetailData: Decodable {
                    let name: String
                    let model: String
                    let content: String
                    let output_settings: OutputSettings
                }
                struct OutputSettings: Decodable {
                    let useEditWindow: Bool?
                    let useChatSession: Bool?
                }
                
                guard let decoded = try? JSONDecoder().decode(PromptDetail.self, from: data) else {
                    ProcessingHUD.showNotification("Failed to parse prompt details", style: "error")
                    return
                }
                
                let detail = decoded.data
                let systemPrompt = detail.content
                let model = detail.model
                let useChat = detail.output_settings.useChatSession ?? false
                let useEdit = detail.output_settings.useEditWindow ?? false
                
                // Inject context if active
                var userPrompt = selectedText
                if AppState.shared.isContextActive {
                    userPrompt = "CONTEXT:\n\(AppState.shared.contextBuffer)\n\n---\n\nUSER REQUEST:\n\(selectedText)"
                }
                
                if useChat {
                    self.openChatSession(promptName: detail.name, model: model, systemPrompt: systemPrompt, userPrompt: userPrompt)
                    return
                }
                
                self.sendLLMRequest(promptName: detail.name, model: model, systemPrompt: systemPrompt, userPrompt: userPrompt, useEdit: useEdit)
            }.resume()
        }
    }
    
    func openChatSession(promptName: String, model: String, systemPrompt: String, userPrompt: String) {
        let port = PythonServerManager.shared.port
        let secret = PythonServerManager.shared.apiSecret
        let ticket = createLaunchTicket()
        let sid = "\(Int(Date().timeIntervalSince1970))-\(Int.random(in: 100000...999999))"
        
        // Register session on server
        let initURL = URL(string: "http://127.0.0.1:\(port)/api/chat/init")!
        var request = URLRequest(url: initURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(secret, forHTTPHeaderField: "X-API-Secret")
        
        let initPayload: [String: Any] = [
            "sid": sid,
            "title": "Chat - \(promptName)",
            "model": model,
            "system": systemPrompt,
            "user": userPrompt,
            "assistant": "",
            "autorun": true
        ]
        request.httpBody = try? JSONSerialization.data(withJSONObject: initPayload)
        
        let sem = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: request) { _, _, _ in
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 1.0)
        
        let url = "http://127.0.0.1:\(port)/chat?sid=\(sid)&ticket=\(ticket)"
        DispatchQueue.main.async {
            BrowserManager.shared.openInAppMode(url: url)
        }
        
        // Clear context on completion
        if AppState.shared.isContextActive {
            AppState.shared.clearContext()
        }
    }
    
    func sendLLMRequest(promptName: String, model: String, systemPrompt: String, userPrompt: String, useEdit: Bool) {
        ProcessingHUD.show("Processing \(promptName)...")
        
        let port = PythonServerManager.shared.port
        let secret = PythonServerManager.shared.apiSecret
        let url = URL(string: "http://127.0.0.1:\(port)/api/llm/complete")!
        
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(secret, forHTTPHeaderField: "X-API-Secret")
        
        let requestObj: [String: Any] = [
            "model": model,
            "messages": [
                ["role": "system", "content": systemPrompt],
                ["role": "user", "content": userPrompt]
            ]
        ]
        
        let payload: [String: Any] = ["request": requestObj]
        guard let bodyData = try? JSONSerialization.data(withJSONObject: payload) else {
            ProcessingHUD.hide()
            ProcessingHUD.showNotification("Failed to serialize request", style: "error")
            return
        }
        request.httpBody = bodyData
        
        let session = URLSession.shared
        let task = session.dataTask(with: request) { [weak self] data, response, error in
            defer {
                DispatchQueue.main.async {
                    AppState.shared.isProcessingLLM = false
                }
            }
            ProcessingHUD.hide()
            
            if let error = error {
                if (error as NSError).code != NSURLErrorCancelled {
                    ProcessingHUD.showNotification("Request failed: \(error.localizedDescription)", style: "error")
                    NSSound.beep()
                }
                return
            }
            
            guard let data = data,
                  let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
                let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                ProcessingHUD.showNotification("Server error: \(code)", style: "error")
                NSSound.beep()
                return
            }
            
            struct CompleteResponse: Decodable {
                let response: String?
                let choices: [Choice]?
            }
            struct Choice: Decodable {
                let message: Message
            }
            struct Message: Decodable {
                let content: String
            }
            
            var resultText = ""
            if let decoded = try? JSONDecoder().decode(CompleteResponse.self, from: data) {
                if let resp = decoded.response {
                    resultText = resp
                } else if let choice = decoded.choices?.first {
                    resultText = choice.message.content
                }
            }
            
            if resultText.isEmpty {
                if let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let resp = dict["response"] as? String {
                    resultText = resp
                }
            }
            
            if resultText.isEmpty {
                ProcessingHUD.showNotification("Received empty response", style: "error")
                NSSound.beep()
                return
            }
            
            self?.playSuccessSound()
            
            DispatchQueue.main.async {
                if useEdit {
                    OutputEditor.show(text: resultText, promptName: promptName) { editedText in
                        ClipboardHelper.pasteText(editedText)
                    }
                } else {
                    ClipboardHelper.pasteText(resultText)
                    ProcessingHUD.showNotification("Response pasted!", style: "success")
                }
                
                if AppState.shared.isContextActive {
                    AppState.shared.clearContext()
                }
            }
        }
        
        self.activeCompletionTask = task
        task.resume()
    }
    
    func onScreenshotCaptureFinished() {
        guard !AppState.shared.isProcessingScreenshot else {
            debugLog("LLMService: Already processing screenshot, ignoring duplicate notification")
            return
        }
        
        // Immediately lock state synchronously to prevent rapid double-firing during the health check
        AppState.shared.isProcessingScreenshot = true
        
        let images = AppState.shared.screenshotImages
        if images.isEmpty {
            AppState.shared.isProcessingScreenshot = false
            return
        }
        
        guard let action = CaptureManager.shared.activeAction else {
            AppState.shared.isProcessingScreenshot = false
            return
        }
        
        ensureServerHealthy { [weak self] healthy in
            guard healthy else {
                DispatchQueue.main.async {
                    AppState.shared.isProcessingScreenshot = false
                    AppState.shared.resetScreenshotState()
                }
                return
            }
            
            guard let self = self else { return }
            
            DispatchQueue.main.async {
                ProcessingHUD.show("Vision: Analyzing captures...")
            }
            
            var contentParts: [[String: Any]] = []
            for img in images {
                contentParts.append([
                    "type": "image_url",
                    "image_url": ["url": "data:image/jpeg;base64,\(img)"]
                ])
            }
            
            var promptText = "Analyze the image(s) above using the system instructions."
            if !action.prompt.isEmpty {
                promptText = action.prompt
            }
            
            if AppState.shared.isContextActive {
                promptText += "\n\n=== ADDITIONAL CONTEXT PROVIDED ===\n\(AppState.shared.contextBuffer)\n=== END OF CONTEXT ==="
            }
            
            contentParts.append([
                "type": "text",
                "text": promptText
            ])
            
            let requestObj: [String: Any] = [
                "model": action.model,
                "messages": [
                    ["role": "user", "content": contentParts]
                ]
            ]
            
            let payload: [String: Any] = ["request": requestObj]
            
            let port = PythonServerManager.shared.port
            let secret = PythonServerManager.shared.apiSecret
            let url = URL(string: "http://127.0.0.1:\(port)/api/llm/complete")!
            
            var request = URLRequest(url: url)
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.setValue(secret, forHTTPHeaderField: "X-API-Secret")
            
            guard let bodyData = try? JSONSerialization.data(withJSONObject: payload) else {
                DispatchQueue.main.async {
                    AppState.shared.isProcessingScreenshot = false
                }
                ProcessingHUD.hide()
                ProcessingHUD.showNotification("Failed to serialize request", style: "error")
                return
            }
            request.httpBody = bodyData
            
            let session = URLSession.shared
            let task = session.dataTask(with: request) { [weak self] data, response, error in
                defer {
                    DispatchQueue.main.async {
                        AppState.shared.isProcessingScreenshot = false
                    }
                }
                ProcessingHUD.hide()
                
                if let error = error {
                    if (error as NSError).code != NSURLErrorCancelled {
                        ProcessingHUD.showNotification("Vision request failed: \(error.localizedDescription)", style: "error")
                        NSSound.beep()
                    }
                    DispatchQueue.main.async {
                        AppState.shared.resetScreenshotState()
                    }
                    return
                }
                
                guard let data = data,
                      let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
                    let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                    ProcessingHUD.showNotification("Vision API error: \(code)", style: "error")
                    NSSound.beep()
                    DispatchQueue.main.async {
                        AppState.shared.resetScreenshotState()
                    }
                    return
                }
                
                struct CompleteResponse: Decodable {
                    let response: String?
                    let choices: [Choice]?
                }
                struct Choice: Decodable {
                    let message: Message
                }
                struct Message: Decodable {
                    let content: String
                }
                
                var resultText = ""
                if let decoded = try? JSONDecoder().decode(CompleteResponse.self, from: data) {
                    if let resp = decoded.response {
                        resultText = resp
                    } else if let choice = decoded.choices?.first {
                        resultText = choice.message.content
                    }
                }
                
                if resultText.isEmpty {
                    if let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                       let resp = dict["response"] as? String {
                        resultText = resp
                    }
                }
                
                if resultText.isEmpty {
                    ProcessingHUD.showNotification("Vision empty response", style: "error")
                    NSSound.beep()
                    DispatchQueue.main.async {
                        AppState.shared.resetScreenshotState()
                    }
                    return
                }
                
                self?.playSuccessSound()
                
                DispatchQueue.main.async {
                    ClipboardHelper.pasteText(resultText)
                    ProcessingHUD.showNotification("Vision processing complete!", style: "success")
                    AppState.shared.resetScreenshotState()
                    
                    if AppState.shared.isContextActive {
                        AppState.shared.clearContext()
                    }
                }
            }
            
            self.activeCompletionTask = task
            task.resume()
        }
    }
    
    func abortActiveProcessing() {
        if AppState.shared.isProcessingLLM || activeCompletionTask != nil {
            activeCompletionTask?.cancel()
            activeCompletionTask = nil
            ProcessingHUD.hide()
            ProcessingHUD.showNotification("Cancelled. Ready for next prompt.", style: "warning")
        }
    }
    
    private func playSuccessSound() {
        NSSound(named: "Glass")?.play()
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
    
    private func urlEncode(_ string: String) -> String {
        return string.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? string
    }
}
