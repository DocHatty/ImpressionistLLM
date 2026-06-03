import Foundation
import AppKit

class AppState: ObservableObject {
    static let shared = AppState()
    
    // Screenshot State
    @Published var isScreenshotMode = false
    @Published var screenshotImages: [String] = []
    @Published var isProcessingScreenshot = false
    @Published var expectedScreenshotCount = 1
    @Published var lastProcessedScreenshotText = ""
    var lastProcessTime: Date?
    
    // Context State
    @Published var contextSegments: [String] = []
    @Published var contextCharCount = 0
    @Published var contextSelectionCount = 0
    @Published var contextLastCaptureTime: Date?
    
    // Global LLM processing state
    @Published var isProcessingLLM = false
    @Published var processingStatus = ""
    
    // Splash Screen State
    @Published var splashStatus = "Initializing system..."
    @Published var splashProgress = 0.0
    
    // Output target track
    var lastActiveWindowHwnd: CGWindowID?
    
    // Active app tracking for focus transitions
    var lastActiveApp: NSRunningApplication?
    
    private init() {}
    
    // Context Computed Properties
    var isContextActive: Bool {
        guard let lastTime = contextLastCaptureTime else { return false }
        let elapsed = Date().timeIntervalSince(lastTime)
        if elapsed > 300 { // 5 minutes timeout
            clearContext()
            return false
        }
        return !contextSegments.isEmpty
    }
    
    var contextBuffer: String {
        // Rebuild context buffer cleanly
        return contextSegments.joined()
    }
    
    func appendContext(_ text: String) {
        DispatchQueue.main.async {
            let segmentNumber = self.contextSelectionCount + 1
            var segmentName = "Segment \(segmentNumber)"
            
            if text.contains("FINDINGS:") || text.contains("IMPRESSION:") {
                segmentName = "Medical Report \(segmentNumber)"
            } else if text.contains("___") || text.contains("[]") {
                segmentName = "Template \(segmentNumber)"
            } else if text.count < 100 && text.contains("?") {
                segmentName = "Question \(segmentNumber)"
            }
            
            let separator = self.contextSegments.isEmpty ? "" : "\n\n--- \(segmentName) ---\n"
            let segmentContent = separator + text
            
            self.contextSegments.append(segmentContent)
            self.contextCharCount += segmentContent.count
            self.contextSelectionCount += 1
            self.contextLastCaptureTime = Date()
        }
    }
    
    func clearContext() {
        DispatchQueue.main.async {
            self.contextSegments.removeAll()
            self.contextCharCount = 0
            self.contextSelectionCount = 0
            self.contextLastCaptureTime = nil
        }
    }
    
    func resetScreenshotState() {
        DispatchQueue.main.async {
            self.isScreenshotMode = false
            self.screenshotImages.removeAll()
            self.isProcessingScreenshot = false
            self.expectedScreenshotCount = 1
        }
    }
    
    func resetAll() {
        resetScreenshotState()
        clearContext()
        DispatchQueue.main.async {
            self.isProcessingLLM = false
            self.processingStatus = ""
        }
    }
}
