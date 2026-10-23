import AppKit

final class Delegate: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    var window: NSWindow!
    let button = NSButton(title: "Count 0", target: nil, action: nil)
    let input = NSTextField(string: "")
    let label = NSTextField(labelWithString: "Typed: empty")
    var count = 0
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 200, y: 200, width: 520, height: 360), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Dokky Computer Use Fixture"
        let content = window.contentView!
        button.frame = NSRect(x: 30, y: 260, width: 160, height: 36)
        button.target = self
        button.action = #selector(increment)
        input.frame = NSRect(x: 30, y: 200, width: 440, height: 32)
        input.placeholderString = "Unicode test"
        input.delegate = self
        input.setAccessibilityIdentifier("dokky-input")
        label.frame = NSRect(x: 30, y: 155, width: 440, height: 30)
        content.addSubview(button)
        content.addSubview(input)
        content.addSubview(label)
        let scroll = NSScrollView(frame: NSRect(x: 30, y: 20, width: 440, height: 115))
        let text = NSTextView(frame: NSRect(x: 0, y: 0, width: 420, height: 600))
        text.string = (1...30).map { "Fixture line \($0)" }.joined(separator: "\n")
        scroll.documentView = text
        scroll.hasVerticalScroller = true
        content.addSubview(scroll)
        // без меню Правка Cmd+A не обрабатывается и система отвечает звуком ошибки
        let menu = NSMenu()
        let appItem = NSMenuItem()
        menu.addItem(appItem)
        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        menu.addItem(editItem)
        NSApp.mainMenu = menu
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    @objc func increment() { count += 1; button.title = "Count \(count)" }
    func controlTextDidChange(_ obj: Notification) { label.stringValue = "Typed: " + input.stringValue }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}
let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
