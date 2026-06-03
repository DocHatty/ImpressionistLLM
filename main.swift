import Cocoa

let delegate = AppDelegate()
NSApplication.shared.delegate = delegate
withExtendedLifetime(delegate) {
    NSApplication.shared.run()
}
