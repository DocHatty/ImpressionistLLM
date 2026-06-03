import Foundation

struct HotkeyItem: Codable, Hashable, Identifiable {
    var id: String { actionId }
    var actionId: String
    var name: String
    var keyCode: UInt32
    var modifiers: UInt32
}

struct PromptHotkeyItem: Codable, Hashable, Identifiable {
    var id: String { promptName }
    var promptName: String
    var keyCode: UInt32
    var modifiers: UInt32
}

struct HotkeysConfig: Codable {
    var system: [HotkeyItem]
    var prompts: [PromptHotkeyItem]
}

class HotkeySettings {
    static let shared = HotkeySettings()
    
    var config: HotkeysConfig
    
    private init() {
        self.config = HotkeysConfig(system: [], prompts: [])
        loadConfig()
    }
    
    func configPath() -> URL {
        let parentDir = (Bundle.main.bundlePath as NSString).deletingLastPathComponent
        let path = (parentDir as NSString).appendingPathComponent("config/hotkeys.json")
        return URL(fileURLWithPath: path)
    }
    
    func loadConfig() {
        let url = configPath()
        if FileManager.default.fileExists(atPath: url.path) {
            if let data = try? Data(contentsOf: url),
               let decoded = try? JSONDecoder().decode(HotkeysConfig.self, from: data) {
                self.config = decoded
                return
            }
        }
        
        // Defaults if file doesn't exist or is corrupted
        let defaultSystem = [
            HotkeyItem(actionId: "show_menu", name: "Show Floating Menu", keyCode: 50, modifiers: 0), // Backtick
            HotkeyItem(actionId: "screenshot", name: "Start Screenshot Overlay", keyCode: 5, modifiers: 4096), // Ctrl+G
            HotkeyItem(actionId: "cancel", name: "Cancel Active Completion", keyCode: 53, modifiers: 0), // Escape
            HotkeyItem(actionId: "clear_context", name: "Clear Context", keyCode: 8, modifiers: 2048), // Opt+C
            HotkeyItem(actionId: "open_context", name: "Open Context Manager", keyCode: 9, modifiers: 2048) // Opt+V
        ]
        self.config = HotkeysConfig(system: defaultSystem, prompts: [])
        saveConfig()
    }
    
    func saveConfig() {
        let url = configPath()
        let encoder = JSONEncoder()
        encoder.outputFormatting = .prettyPrinted
        if let data = try? encoder.encode(self.config) {
            // Create config dir if needed
            let parentDir = url.deletingLastPathComponent()
            try? FileManager.default.createDirectory(at: parentDir, withIntermediateDirectories: true)
            try? data.write(to: url)
        }
    }
}

struct KeyMap {
    static func string(for keyCode: UInt32) -> String {
        switch keyCode {
        case 50: return "`"
        case 53: return "Esc"
        case 36: return "↵"
        case 48: return "Tab"
        case 49: return "Space"
        case 51: return "Delete"
        case 115: return "Home"
        case 119: return "End"
        case 116: return "PgUp"
        case 121: return "PgDn"
        case 123: return "←"
        case 124: return "→"
        case 125: return "↓"
        case 126: return "↑"
        case 122: return "F1"
        case 120: return "F2"
        case 99: return "F3"
        case 118: return "F4"
        case 96: return "F5"
        case 97: return "F6"
        case 98: return "F7"
        case 100: return "F8"
        case 101: return "F9"
        case 109: return "F10"
        case 103: return "F11"
        case 111: return "F12"
        // Letters
        case 0: return "A"
        case 1: return "S"
        case 2: return "D"
        case 3: return "F"
        case 4: return "H"
        case 5: return "G"
        case 6: return "Z"
        case 7: return "X"
        case 8: return "C"
        case 9: return "V"
        case 11: return "B"
        case 12: return "Q"
        case 13: return "W"
        case 14: return "E"
        case 15: return "R"
        case 16: return "Y"
        case 17: return "T"
        case 18: return "1"
        case 19: return "2"
        case 20: return "3"
        case 21: return "4"
        case 22: return "6"
        case 23: return "5"
        case 24: return "="
        case 25: return "9"
        case 26: return "7"
        case 27: return "-"
        case 28: return "8"
        case 29: return "0"
        case 30: return "]"
        case 31: return "O"
        case 32: return "U"
        case 33: return "["
        case 34: return "I"
        case 35: return "P"
        case 37: return "L"
        case 38: return "J"
        case 39: return "'"
        case 40: return "K"
        case 41: return ";"
        case 42: return "\\"
        case 43: return ","
        case 44: return "/"
        case 45: return "N"
        case 46: return "M"
        case 47: return "."
        default: return "Key \(keyCode)"
        }
    }
    
    static func modifiersString(for modifiers: UInt32) -> String {
        var parts: [String] = []
        if (modifiers & 256) != 0 { parts.append("⌘") }
        if (modifiers & 2048) != 0 { parts.append("⌥") }
        if (modifiers & 4096) != 0 { parts.append("⌃") }
        if (modifiers & 512) != 0 { parts.append("⇧") }
        return parts.joined()
    }
    
    static func fullDisplayString(keyCode: UInt32, modifiers: UInt32) -> String {
        let mods = modifiersString(for: modifiers)
        let key = string(for: keyCode)
        if mods.isEmpty {
            return key
        }
        return "\(mods) \(key)"
    }
}
