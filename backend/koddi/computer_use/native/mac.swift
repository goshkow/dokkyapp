import AppKit
import ApplicationServices
import ScreenCaptureKit

struct Failure: Error { let code: String }
func fail(_ code: String) throws -> Never { throw Failure(code: code) }
func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
    return value
}
func children(_ element: AXUIElement) -> [AXUIElement] {
    return attribute(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
}
func description(_ element: AXUIElement) -> [String: Any] {
    let role = attribute(element, kAXRoleAttribute) as? String ?? ""
    let subrole = attribute(element, kAXSubroleAttribute) as? String ?? ""
    let secure = subrole == "AXSecureTextField"
    let title = secure ? "[protected]" : (attribute(element, kAXTitleAttribute) as? String ?? "")
    return ["role": role, "subrole": subrole, "title": String(title.prefix(160)), "protected": secure,
            "enabled": attribute(element, kAXEnabledAttribute) as? Bool ?? true,
            "identifier": String((attribute(element, kAXIdentifierAttribute) as? String ?? "").prefix(160))]
}
func identity(_ app: NSRunningApplication) -> String {
    return "\(app.processIdentifier):\(app.launchDate?.timeIntervalSince1970 ?? 0):\(app.bundleURL?.path ?? "")"
}
func elementAt(_ root: AXUIElement, _ path: String) throws -> AXUIElement {
    var element = root
    if path == "root" { return element }
    for part in path.split(separator: ".") {
        guard let index = Int(part), index >= 0 else { try fail("stale_element") }
        let values = children(element)
        guard index < values.count else { try fail("stale_element") }
        element = values[index]
    }
    return element
}
func frame(_ element: AXUIElement) -> CGRect? {
    guard let p = attribute(element, kAXPositionAttribute), let s = attribute(element, kAXSizeAttribute),
          CFGetTypeID(p) == AXValueGetTypeID(), CFGetTypeID(s) == AXValueGetTypeID() else { return nil }
    var point = CGPoint.zero
    var size = CGSize.zero
    guard AXValueGetValue(unsafeBitCast(p, to: AXValue.self), .cgPoint, &point),
          AXValueGetValue(unsafeBitCast(s, to: AXValue.self), .cgSize, &size) else { return nil }
    return CGRect(origin: point, size: size)
}
func postKey(_ pid: pid_t, _ code: CGKeyCode, _ down: Bool, _ flags: CGEventFlags) throws {
    guard let event = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down) else { try fail("input_failed") }
    event.flags = flags
    event.post(tap: .cghidEventTap)
    // процесс живёт недолго: без паузы системная очередь не успевает доставить нажатие
    usleep(20_000)
}

@main struct ComputerWorker {
    @MainActor static func run(_ request: [String: Any]) async throws -> [String: Any] {
        let action = request["action"] as? String ?? ""
        if action == "status" {
            return ["ok": true, "platform": "macOS", "accessibility": AXIsProcessTrusted(),
                    "screen_capture": CGPreflightScreenCaptureAccess(), "capture_min_macos": "14.0",
                    "session_locked": NSWorkspace.shared.frontmostApplication?.bundleIdentifier == "com.apple.loginwindow"]
        }
        guard NSWorkspace.shared.frontmostApplication?.bundleIdentifier != "com.apple.loginwindow" else { try fail("interactive_session_locked") }
        guard AXIsProcessTrusted() else { try fail("accessibility_permission_required") }
        if action == "launch" {
            guard let path = request["application"] as? String, path.hasPrefix("/"), path.hasSuffix(".app") else { try fail("invalid_application") }
            let config = NSWorkspace.OpenConfiguration()
            config.activates = true
            let launched = try await NSWorkspace.shared.openApplication(at: URL(fileURLWithPath: path), configuration: config)
            return ["ok": true, "pid": launched.processIdentifier, "identity": identity(launched)]
        }
        guard let pid = request["pid"] as? Int32, pid > 0,
              let app = NSRunningApplication(processIdentifier: pid), !app.isTerminated else { try fail("application_unavailable") }
        let root = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(root, 0.1)
        if action != "observe" {
            guard let expected = request["identity"] as? String, expected == identity(app) else { try fail("stale_application") }
        }
        if action == "observe" {
            var nodes = [[String: Any]]()
            var queue: [(AXUIElement, String, Int)] = [(root, "root", 0)]
            let deadline = Date().addingTimeInterval(3)
            while !queue.isEmpty && nodes.count < 160 && Date() < deadline {
                let (element, path, depth) = queue.removeFirst()
                var node = description(element)
                node["path"] = path
                // текст меток и полей лежит в AXValue, а не в заголовке; поля пароля не читаются
                if !(node["protected"] as? Bool ?? false), let text = attribute(element, kAXValueAttribute) as? String, !text.isEmpty {
                    node["value"] = String(text.prefix(160))
                }
                if let f = frame(element) { node["frame"] = [f.minX, f.minY, f.width, f.height] }
                nodes.append(node)
                if depth < 7 && !(node["protected"] as? Bool ?? false) {
                    for (index, child) in children(element).prefix(160).enumerated() {
                        queue.append((child, path == "root" ? "\(index)" : "\(path).\(index)", depth + 1))
                    }
                }
            }
            return ["ok": true, "pid": pid, "identity": identity(app), "elements": nodes, "truncated": !queue.isEmpty]
        }
        if action == "focus" {
            if NSWorkspace.shared.frontmostApplication?.processIdentifier != pid {
                _ = app.activate(options: [])
                try await Task.sleep(nanoseconds: 250_000_000)
            }
            guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else { try fail("focus_failed") }
            return ["ok": true]
        }
        if action == "screenshot" {
            guard CGPreflightScreenCaptureAccess() else { try fail("screen_capture_permission_required") }
            guard #available(macOS 14.0, *) else { try fail("capture_requires_macos_14") }
            _ = NSApplication.shared  // ScreenCaptureKit в консольном процессе падает без подключения к оконному серверу
            let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
            guard let windowID = request["window_id"] as? UInt32,
                  let window = content.windows.first(where: { $0.windowID == windowID && $0.owningApplication?.processID == pid }) else {
                return ["ok": false, "error_code": "select_window", "windows": content.windows.filter { $0.owningApplication?.processID == pid }.prefix(20).map { ["window_id": $0.windowID, "title": String(($0.title ?? "").prefix(160))] }]
            }
            let config = SCStreamConfiguration()
            let scale = min(1, 1600 / max(window.frame.width, window.frame.height, 1))
            config.width = max(1, Int(window.frame.width * scale))
            config.height = max(1, Int(window.frame.height * scale))
            config.showsCursor = false
            config.ignoreShadowsSingleWindow = true
            let image = try await SCScreenshotManager.captureImage(contentFilter: SCContentFilter(desktopIndependentWindow: window), configuration: config)
            let bitmap = NSBitmapImageRep(cgImage: image)
            guard let png = bitmap.representation(using: .png, properties: [:]), png.count <= 1_500_000 else { try fail("capture_too_large") }
            return ["ok": true, "png_base64": png.base64EncodedString(), "width": image.width, "height": image.height,
                    "coordinate_space": "global_points", "window_frame": [window.frame.minX, window.frame.minY, window.frame.width, window.frame.height]]
        }
        if action != "focus_window" && NSWorkspace.shared.frontmostApplication?.processIdentifier != pid { try fail("target_not_foreground") }
        if action == "press" || action == "type_element" || action == "focus_window" {
            guard let path = request["element_path"] as? String, let expected = request["expected"] as? [String: Any] else { try fail("stale_element") }
            let element = try elementAt(root, path)
            let actual = description(element)
            if let expectedFrame = request["expected_frame"] as? [Double] {
                guard let actualFrame = frame(element) else { try fail("stale_element") }
                guard expectedFrame == [actualFrame.minX, actualFrame.minY, actualFrame.width, actualFrame.height] else { try fail("stale_element") }
            }
            guard NSDictionary(dictionary: actual).isEqual(to: expected), !(actual["protected"] as? Bool ?? true) else { try fail("stale_element") }
            if action == "focus_window" {
                guard actual["role"] as? String == kAXWindowRole else { try fail("not_a_window") }
                _ = app.activate(options: [])
                guard AXUIElementPerformAction(element, kAXRaiseAction as CFString) == .success else { try fail("focus_failed") }
                _ = AXUIElementSetAttributeValue(root, kAXFocusedWindowAttribute as CFString, element)
                try await Task.sleep(nanoseconds: 200_000_000)
                guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else { try fail("focus_failed") }
            } else if action == "press" {
                guard AXUIElementPerformAction(element, kAXPressAction as CFString) == .success else { try fail("element_not_pressable") }
            } else {
                guard let text = request["text"] as? String,
                      AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, text as CFString) == .success else { try fail("element_not_editable") }
            }
            return ["ok": true]
        }
        if let focused = attribute(root, kAXFocusedUIElementAttribute), CFGetTypeID(focused) == AXUIElementGetTypeID(),
           description(unsafeBitCast(focused, to: AXUIElement.self))["protected"] as? Bool == true { try fail("protected_input") }
        // клавиши уходят в активное приложение: если фокус перехватили, текст не должен попасть в чужое окно
        if action == "type" || action == "hotkey" {
            guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else { try fail("target_not_frontmost") }
        }
        if action == "type" {
            // без поля ввода в фокусе система отвечает звуком ошибки на каждый символ: лучше отказать
            guard let focused = attribute(root, kAXFocusedUIElementAttribute), CFGetTypeID(focused) == AXUIElementGetTypeID(),
                  ["AXTextField", "AXTextArea", "AXComboBox", "AXSearchField", "AXWebArea"].contains(attribute(unsafeBitCast(focused, to: AXUIElement.self), kAXRoleAttribute) as? String ?? "") else { try fail("no_text_focus") }
            guard let text = request["text"] as? String, text.utf16.count <= 2000 else { try fail("invalid_text") }
            // сначала вставка через доступность: так текст попадает в поле без звуков и потерь
            if AXUIElementSetAttributeValue(unsafeBitCast(focused, to: AXUIElement.self), kAXSelectedTextAttribute as CFString, text as CFString) == .success {
                return ["ok": true, "observe_before_next_action": true]
            }
            // по одному символу (суррогатная пара остаётся вместе): длинная строка в одном событии доходит не всегда
            for character in text {
                var chars = Array(String(character).utf16)
                for down in [true, false] {
                    guard let event = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: down) else { try fail("input_failed") }
                    event.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: &chars)
                    event.post(tap: .cghidEventTap)
                    usleep(8_000)
                }
            }
            usleep(60_000)
        } else if action == "hotkey" {
            let codes: [String: CGKeyCode] = ["enter":36,"tab":48,"escape":53,"backspace":51,"space":49,"left":123,"right":124,"down":125,"up":126,"a":0,"c":8,"v":9,"x":7,"z":6,"s":1,"f":3,"w":13,"q":12,"l":37,"r":15,"b":11,"d":2,"e":14,"g":5,"h":4,"i":34,"j":38,"k":40,"m":46,"n":45,"o":31,"p":35,"t":17,"u":32,"y":16]
            guard let keys = request["keys"] as? [String], let last = keys.last, let code = codes[last] else { try fail("invalid_keys") }
            let modifiers: [String: CGEventFlags] = ["cmd":.maskCommand,"ctrl":.maskControl,"alt":.maskAlternate,"shift":.maskShift]
            var flags: CGEventFlags = []
            for key in keys.dropLast() { guard let flag = modifiers[key] else { try fail("invalid_keys") }; flags.formUnion(flag) }
            try postKey(pid, code, true, flags)
            try postKey(pid, code, false, flags)
        } else if action == "scroll" || action == "click" || action == "move" {
            guard let x = request["x"] as? Double, let y = request["y"] as? Double else { try fail("invalid_position") }
            let point = CGPoint(x: x, y: y)
            let windows = attribute(root, kAXWindowsAttribute) as? [AXUIElement] ?? []
            guard windows.contains(where: { frame($0)?.contains(point) == true }) else { try fail("position_outside_target") }
            // поверх выбранного окна мог появиться чужой диалог
            var hit: AXUIElement?
            let system = AXUIElementCreateSystemWide()
            guard AXUIElementCopyElementAtPosition(system, Float(x), Float(y), &hit) == .success, let hit else { try fail("position_unavailable") }
            var hitPid: pid_t = 0
            guard AXUIElementGetPid(hit, &hitPid) == .success, hitPid == pid else { try fail("position_occluded") }
            if action == "scroll" {
                guard let delta = request["delta_y"] as? Int32, abs(Int64(delta)) <= 2000,
                      let event = CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 1, wheel1: delta, wheel2: 0, wheel3: 0) else { try fail("invalid_scroll") }
                event.location = point
                event.post(tap: .cghidEventTap)
            } else {
                let types: [CGEventType] = action == "move" ? [.mouseMoved] : [.leftMouseDown, .leftMouseUp]
                for type in types {
                    guard let event = CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point, mouseButton: .left) else { try fail("input_failed") }
                    event.setIntegerValueField(.mouseEventClickState, value: 1)
                    event.post(tap: .cghidEventTap)
                    usleep(40_000)
                }
            }
        } else { try fail("unsupported_action") }
        return ["ok": true, "observe_before_next_action": true]
    }
    static func main() async {
        do {
            let data = FileHandle.standardInput.readDataToEndOfFile()
            guard data.count <= 20000, let request = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { try fail("invalid_request") }
            let result = try await run(request)
            let output = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
            FileHandle.standardOutput.write(output)
        } catch {
            let code = (error as? Failure)?.code ?? "native_operation_failed"
            let output = try! JSONSerialization.data(withJSONObject: ["ok": false, "error_code": code])
            FileHandle.standardOutput.write(output)
        }
    }
}
