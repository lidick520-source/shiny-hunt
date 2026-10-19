import SwiftUI
import AppKit
import ApplicationServices
import CoreGraphics

// Explicit type alias keeps property-wrapper state compatible with SDKs that also expose a State macro.
typealias ViewState<Value> = SwiftUI.State<Value>

struct EngineState: Decodable {
    var busy = false, running = false, stopping = false
    var mode = "正在连接", elapsed = 0, rounds = 0, ppCompleted = 0, ppEvery = 6
    var recovering = false, recoveryIndex = 0, recoveryTotal = 20
    var recoveryLabel = "尚未开始恢复", confirmed = false, calibrated = false, previewRevision = 0
    var permissionStatus = "正在检查权限…", lastReason = ""
    var screenAllowed = false
    var restRemaining = 0, channelRemaining = 3000
    var auto = false, ppEnabled = false, channels = false
    var taskMode = "frillish"
    var notificationEnabled = false, notificationConfigured = false
    var gameFound = false, gameFrame: [Double] = [], gamePid = 0
    var protection = "", recoveriesTotal = 0, focusRegains = 0
    var spot = "fanba", spots: [SpotInfo] = [], routeTest = false
}
struct SpotInfo: Decodable, Hashable {
    var key: String
    var name: String
    var verified: Bool
}
struct LogEntry: Identifiable {
    let id = UUID()
    let time: String
    let message: String
}

final class NativeInputDriver {
    private let keyMap: [Int: CGKeyCode] = [
        0x34: 21, 0x35: 23, 0x36: 22, 0x39: 25, 0x20: 49,
        0x57: 13, 0x41: 0, 0x53: 1, 0x44: 2,
        0x28: 125, 0x0D: 36, 0x24: 115
    ]
    private var pressed = Set<CGKeyCode>()
    private var mouseDown: CGPoint?
    private var targetPid: pid_t?
    private var targetWindow: Int64?
    private var focusReturnPid: pid_t?

    // Background mode: deliver events straight to the game process so the user can keep
    // working in other apps. Without a pid, events go to the frontmost app as before.
    private func send(_ event: CGEvent, mouse: Bool = false) {
        if let pid = targetPid {
            if mouse, let window = targetWindow {
                // kCGMouseEventWindowUnderMousePointer(91) / ...ThatCanHandleThisEvent(92)
                if let a = CGEventField(rawValue: 91) { event.setIntegerValueField(a, value: window) }
                if let b = CGEventField(rawValue: 92) { event.setIntegerValueField(b, value: window) }
            }
            event.postToPid(pid)
        } else {
            event.post(tap: .cghidEventTap)
        }
    }

    // Accessibility can raise an app even when this helper is not the active app.
    private func bringToFront(_ pid: pid_t) -> Bool {
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetAttributeValue(app, kAXFrontmostAttribute as CFString, kCFBooleanTrue)
        NSRunningApplication(processIdentifier: pid)?.activate(options: [])
        for _ in 0..<40 {
            var value: CFTypeRef?
            if AXUIElementCopyAttributeValue(app, kAXFrontmostAttribute as CFString, &value) == .success,
               let front = value as? Bool, front { return true }
            usleep(25_000)
        }
        return false
    }

    func perform(_ item: [String: Any]) -> String? {
        guard AXIsProcessTrusted() else { return "Mac刷闪助手没有辅助功能权限" }
        let operation = item["operation"] as? String ?? ""
        if let pid = item["pid"] as? Int { targetPid = pid_t(pid) } else if operation != "release" { targetPid = nil }
        if let window = item["window"] as? Int { targetWindow = Int64(window) } else if operation != "release" { targetWindow = nil }
        switch operation {
        case "key":
            guard let raw = item["key"] as? Int, let code = keyMap[raw],
                  let down = item["down"] as? Bool,
                  let event = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down) else {
                return "无法创建键盘事件"
            }
            send(event)
            if down { pressed.insert(code) } else { pressed.remove(code) }
        case "repeat":
            // Auto-repeat keyDown for a key this driver is already holding (PP recovery movement).
            guard let raw = item["key"] as? Int, let code = keyMap[raw] else { return "无法创建长按重复事件" }
            guard pressed.contains(code) else { return "无法重复未按住的方向键" }
            guard let event = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true) else { return "无法创建长按重复事件" }
            event.setIntegerValueField(.keyboardEventAutorepeat, value: 1)
            send(event)
        case "move":
            guard let x = item["x"] as? Double, let y = item["y"] as? Double else { return "鼠标坐标无效" }
            let point = CGPoint(x: x, y: y)
            if targetPid == nil {
                guard CGWarpMouseCursorPosition(point) == .success else { return "无法移动鼠标" }
            }
            if let event = CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left) { send(event, mouse: true) }
        case "mouse":
            guard let x = item["x"] as? Double, let y = item["y"] as? Double,
                  let down = item["down"] as? Bool else { return "鼠标参数无效" }
            let point = CGPoint(x: x, y: y)
            guard let event = CGEvent(mouseEventSource: nil, mouseType: down ? .leftMouseDown : .leftMouseUp,
                                      mouseCursorPosition: point, mouseButton: .left) else { return "无法创建鼠标事件" }
            event.setIntegerValueField(.mouseEventClickState, value: 1)
            send(event, mouse: true)
            mouseDown = down ? point : nil
        case "focusClick":
            // PokeMMO ignores mouse events delivered while it is in the background.
            // Bring it forward just for this click, then give focus and the cursor back.
            guard let pid = targetPid, let x = item["x"] as? Double, let y = item["y"] as? Double else { return "点击参数无效" }
            let point = CGPoint(x: x, y: y)
            let previous = NSWorkspace.shared.frontmostApplication
            let savedCursor = CGEvent(source: nil)?.location
            if previous?.processIdentifier != pid {
                guard bringToFront(pid) else { return "无法把游戏短暂切到前台完成点击" }
                usleep(120_000)
            }
            CGWarpMouseCursorPosition(point)
            CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
            usleep(40_000)
            guard let downEvent = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left),
                  let upEvent = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left) else { return "无法创建鼠标事件" }
            downEvent.setIntegerValueField(.mouseEventClickState, value: 1)
            upEvent.setIntegerValueField(.mouseEventClickState, value: 1)
            downEvent.post(tap: .cghidEventTap)
            usleep(100_000)
            upEvent.post(tap: .cghidEventTap)
            usleep(120_000)
            if let previous, previous.processIdentifier != pid {
                _ = bringToFront(previous.processIdentifier)
                if let savedCursor { CGWarpMouseCursorPosition(savedCursor) }
            }
        case "focusBegin":
            // Held movement keys only work while PokeMMO is frontmost: raise it for the PP walk.
            guard let pid = targetPid else { return "缺少游戏进程" }
            let previous = NSWorkspace.shared.frontmostApplication?.processIdentifier
            if previous != pid {
                guard bringToFront(pid) else { return "无法把游戏切到前台执行PP恢复路线" }
                focusReturnPid = previous
                usleep(200_000)
            }
        case "activate":
            // Raise the game through Accessibility: macOS may refuse a plain activation request
            // coming from the background engine process (21:49 real run).
            guard let pid = targetPid else { return "缺少游戏进程" }
            if NSWorkspace.shared.frontmostApplication?.processIdentifier != pid {
                guard bringToFront(pid) else { return "无法把游戏切到前台" }
            }
        case "focusEnd":
            if let back = focusReturnPid { _ = bringToFront(back) }
            focusReturnPid = nil
        case "release":
            for code in pressed {
                if let event = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false) { send(event) }
            }
            pressed.removeAll()
            if let point = mouseDown {
                if let event = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left) { send(event, mouse: true) }
                mouseDown = nil
            }
        default:return "未知的原生输入操作"
        }
        return nil
    }
}

@MainActor final class Controller: ObservableObject {
    @Published var state = EngineState()
    @Published var logs: [LogEntry] = []
    @Published var connected = false
    @Published var error: String?
    @Published var preview: NSImage?
    @Published var page = "控制台"
    @Published var pending = false
    @Published var showSettings = false
    @Published var showLogs = false
    @Published var shinyAlert = false
    private var dockedFor = ""
    let root: URL
    private var process: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    private var stderr: FileHandle?
    private var buffer = Data()
    private var previewRevision = 0
    private var quitting = false
    private let inputDriver = NativeInputDriver()
    var permissionText: String {
        state.permissionStatus.split(separator: "\n").map { line in
            line.contains("辅助功能") ? "辅助功能：\(AXIsProcessTrusted() ? "已授权" : "未授权")" : String(line)
        }.joined(separator: "\n")
    }

    init() {
        root = Bundle.main.bundleURL.deletingLastPathComponent()
        launch()
    }
    func launch() {
        let local = root.appendingPathComponent(".runtime-python")
        let configured = (try? String(contentsOf: local, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
        let python = configured.flatMap { FileManager.default.isExecutableFile(atPath: $0) ? $0 : nil }
            ?? root.appendingPathComponent(".venv/bin/python").path
        guard FileManager.default.isExecutableFile(atPath: python) else {
            error = "运行环境未准备好。请先双击同目录的「启动助手.command」。"
            state.mode = "需要安装运行环境"
            return
        }
        let task = Process(), toEngine = Pipe(), fromEngine = Pipe(), errors = Pipe()
        task.executableURL = URL(fileURLWithPath: python)
        task.arguments = ["-u", root.appendingPathComponent("native_bridge.py").path]
        task.currentDirectoryURL = root
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPYCACHEPREFIX"] = root.appendingPathComponent(".cache").path
        task.environment = env
        task.standardInput = toEngine
        task.standardOutput = fromEngine
        task.standardError = errors
        input = toEngine.fileHandleForWriting
        output = fromEngine.fileHandleForReading
        stderr = errors.fileHandleForReading
        output?.readabilityHandler = { [weak self] handle in
            let bytes = handle.availableData
            Task { @MainActor in self?.receive(bytes) }
        }
        stderr?.readabilityHandler = { [weak self] handle in
            let bytes = handle.availableData
            guard !bytes.isEmpty, let text = String(data: bytes, encoding: .utf8) else { return }
            Task { @MainActor in self?.appendLog(text.trimmingCharacters(in: .whitespacesAndNewlines), time: "后台") }
        }
        task.terminationHandler = { [weak self] task in
            Task { @MainActor in
                guard let self else { return }
                self.connected = false
                self.pending = false
                self.state.busy = false
                self.state.running = false
                self.state.mode = "连接已关闭"
                if !self.quitting { self.error = "后台已退出（\(task.terminationStatus)）。请查看日志并重新启动助手。" }
            }
        }
        do { try task.run(); process = task }
        catch { self.error = "启动失败：\(error.localizedDescription)"; state.mode = "启动失败" }
    }
    func receive(_ bytes: Data) {
        guard !bytes.isEmpty else { return }
        buffer.append(bytes)
        while let newline = buffer.firstIndex(of: 10) {
            let line = Data(buffer[..<newline]); buffer.removeSubrange(...newline)
            guard let item = try? JSONSerialization.jsonObject(with: line) as? [String: Any], let type = item["type"] as? String else { continue }
            switch type {
            case "state":
                if let next = try? JSONDecoder().decode(EngineState.self, from: line) {
                    let before = state.protection
                    state = next; connected = true
                    if next.protection == "闪光" && before != "闪光" { alertShiny() }
                    dockIfNeeded()
                    if next.previewRevision != previewRevision {
                        previewRevision = next.previewRevision
                        preview = NSImage(contentsOf: root.appendingPathComponent("preview.png"))
                    }
                }
            case "log": appendLog(item["message"] as? String ?? "", time: item["time"] as? String ?? "")
            case "nativeInput":
                let message = inputDriver.perform(item)
                replyNativeInput(id: item["id"] as? String ?? "", message: message)
            case "error": error = item["message"] as? String; pending = false
            case "ack": pending = false
            default: break
            }
        }
    }
    func appendLog(_ text: String, time: String) {
        logs.append(LogEntry(time: time, message: text))
        if logs.count > 800 { logs.removeFirst(logs.count - 800) }
    }
    func send(_ action: String, _ values: [String: Any] = [:]) {
        guard let input, process?.isRunning == true else { return }
        var message = values; message["action"] = action
        guard var data = try? JSONSerialization.data(withJSONObject: message) else { return }
        data.append(10)
        do { try input.write(contentsOf: data); pending = true }
        catch { self.error = "无法连接后台：\(error.localizedDescription)"; pending = false }
    }
    private func replyNativeInput(id: String, message: String?) {
        guard let input else { return }
        var result: [String: Any] = ["action":"native_input_result", "id":id, "ok":message == nil]
        if let message { result["message"] = message }
        guard var data = try? JSONSerialization.data(withJSONObject: result) else { return }
        data.append(10)
        try? input.write(contentsOf: data)
    }
    func set(_ key: String, _ value: Bool) { send("settings", [key: value]) }

    // 出闪提醒 on this Mac (2026-10-19): sound, bounce the Dock icon, bring the assistant forward, pop up.
    func alertShiny() {
        NSApp.requestUserAttention(.criticalRequest)
        NSApp.activate(ignoringOtherApps: true)
        for i in 0..<6 {
            DispatchQueue.main.asyncAfter(deadline: .now() + Double(i) * 1.3) { NSSound(named: "Glass")?.play() }
        }
        shinyAlert = true
    }

    // Keep the assistant beside the game on the same screen: dock 8 pt right of the game window.
    // If there is no room on the right (and no task is running), move the game to the left edge first.
    func dockIfNeeded() {
        guard state.gameFound, state.gameFrame.count == 4 else { return }
        let key = state.gameFrame.map { String(Int($0)) }.joined(separator: ",") + (state.running ? "r" : "i")
        guard key != dockedFor else { return }
        dockedFor = key
        dock()
    }
    func dock() {
        guard let window = NSApp.windows.first(where: { $0.isVisible && $0.canBecomeMain }),
              let screen = NSScreen.screens.first else { return }
        let full = screen.frame, visible = screen.visibleFrame
        let f = state.gameFrame
        var game = CGRect(x: f[0], y: f[1], width: f[2], height: f[3])   // CG coordinates: top-left origin
        let panel: CGFloat = 400, gap: CGFloat = 8
        if game.maxX + gap + panel > full.maxX && !state.busy {
            let target = CGPoint(x: full.minX, y: full.maxY - visible.maxY)
            if moveGame(pid_t(state.gamePid), to: target) { game.origin = target }
        }
        let width = max(320, min(panel, full.maxX - game.maxX - gap))
        let height = min(game.height, visible.height)
        let top = full.maxY - game.minY                                    // Cocoa y of the game window's top edge
        window.setFrame(NSRect(x: game.maxX + gap, y: top - height, width: width, height: height), display: true, animate: true)
    }
    private func moveGame(_ pid: pid_t, to point: CGPoint) -> Bool {
        guard pid > 0 else { return false }
        let app = AXUIElementCreateApplication(pid)
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(app, kAXWindowsAttribute as CFString, &value) == .success,
              let windows = value as? [AXUIElement], let first = windows.first else { return false }
        var position = point
        guard let axPoint = AXValueCreate(.cgPoint, &position) else { return false }
        return AXUIElementSetAttributeValue(first, kAXPositionAttribute as CFString, axPoint) == .success
    }
    func reveal(_ name: String = "") {
        let url = name.isEmpty ? root : root.appendingPathComponent(name)
        NSWorkspace.shared.activateFileViewerSelecting([FileManager.default.fileExists(atPath: url.path) ? url : root])
    }
    func shutdown() {
        quitting = true
        send("shutdown")
        try? input?.close()
        // EOF is the fallback stop signal, even if the shutdown command was lost.
    }
}

final class Lifecycle: NSObject, NSApplicationDelegate {
    static var controller: Controller?
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        Lifecycle.controller?.shutdown()
        return .terminateNow
    }
}

@main struct ShinyHuntApp: App {
    @NSApplicationDelegateAdaptor(Lifecycle.self) var delegate
    @StateObject var controller = Controller()
    var body: some Scene {
        WindowGroup("刷闪助手") {
            MainView().environmentObject(controller)
                .onAppear { Lifecycle.controller = controller }
                .frame(minWidth: 320, idealWidth: 400, maxWidth: 400, minHeight: 600, idealHeight: 796)
        }
        .defaultSize(width: 400, height: 796)
        .windowResizability(.contentSize)
        .windowStyle(.titleBar)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandMenu("任务") {
                Button("停止任务") { controller.send("stop") }.keyboardShortcut(".", modifiers: .command)
                Button("查看日志") { controller.showLogs = true }.keyboardShortcut("l", modifiers: .command)
                Button("设置") { controller.showSettings = true }.keyboardShortcut(",", modifiers: .command)
            }
        }
    }
}

// Palette from the approved design (2026-10-18): green #62D141 with dark text, Apple pink #FF2D55.
extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255, blue: Double(hex & 0xFF) / 255)
    }
    static let ground = Color(hex: 0xF3F3EF)
    static let panel = Color.white
    static let hairline = Color(hex: 0xE2E2DC)
    static let ink = Color(hex: 0x1B1E1D)
    static let inkSoft = Color(hex: 0x4A504D)
    static let inkMuted = Color(hex: 0x5C625F)
    static let go = Color(hex: 0x62D141)
    static let onGo = Color(hex: 0x06240F)
    static let goTint = Color(hex: 0xE8F8E1)
    static let goText = Color(hex: 0x1B6B32)
    static let stop = Color(hex: 0xFF2D55)
    static let fixTint = Color(hex: 0xFDF0E1)
    static let fixText = Color(hex: 0x6B2F05)
    static let shinyTint = Color(hex: 0xFFF7E8)
    static let shinyEdge = Color(hex: 0xC98A12)
    static let shinyText = Color(hex: 0x5A3A00)
    static let chip = Color(hex: 0xE9E9E3)
}

func duration(_ seconds: Int) -> String {
    String(format: "%02d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60)
}
func minutes(_ seconds: Int) -> String {
    String(format: "%02d:%02d", seconds / 60, seconds % 60)
}

struct Panel<Content: View>: View {
    var padding: CGFloat = 16
    @ViewBuilder let content: Content
    var body: some View {
        content.padding(padding).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.panel, in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Color.hairline))
    }
}

enum FeedKind { case normal, recovery, shiny, stop }
struct FeedItem: Identifiable {
    let id: UUID
    let time: String
    let title: String
    let detail: String
    let kind: FeedKind
}

extension Controller {
    var shinyFound: Bool { state.protection == "闪光" && !state.busy }
    var stoppedWithReason: Bool { !state.busy && !state.lastReason.isEmpty && state.lastReason.contains("停止") }
    var headline: String {
        if !connected { return "正在连接…" }
        if state.stopping { return "正在停止…" }
        if state.busy && state.routeTest { return "测试回院路线" }
        if state.busy {
            switch state.mode {
            case "补充PP": return "去医院补 PP"
            case "切换频道": return "换频道"
            case "休息": return "换频道后休息"
            case "自动恢复": return "自动恢复中"
            case "寻找游戏窗口", "切换到游戏", "OCR预热与地图检查", "正在启动": return "正在接管游戏"
            default: return "刷怪中"
            }
        }
        if stoppedWithReason { return "已停止" }
        return state.gameFound ? "准备就绪" : "没找到游戏"
    }
    var subline: String {
        if !connected { return "正在启动后台引擎" }
        if state.busy {
            if state.recovering { return "第 \(state.recoveryIndex) / \(state.recoveryTotal) 步：\(state.recoveryLabel)" }
            if state.restRemaining > 0 { return "休息剩余 \(minutes(state.restRemaining))，之后自动继续" }
            return logs.last?.message.trimmingCharacters(in: .whitespaces) ?? state.mode
        }
        if stoppedWithReason { return state.lastReason }
        if !state.gameFound { return "先打开 PokeMMO（窗口模式，内容区 1024×768），这里会自动找到它。" }
        if state.taskMode == "fixed" { return "站在你的固定点位后点开始：只按 5、查闪光、逃跑，一直循环。" }
        let name = state.spots.first(where: { $0.key == state.spot })?.name ?? "刷怪点"
        return "站在「\(name)」的刷怪点后点开始。游戏会一直保持在前台，弹窗、走偏会自己处理。"
    }
    // Important lines from the engine log, newest first, grouped like the design's activity list.
    var feed: [FeedItem] {
        var items: [FeedItem] = []
        let all = logs
        var index = 0
        while index < all.count {
            let entry = all[index]
            let text = entry.message.trimmingCharacters(in: .whitespaces)
            if text.hasPrefix("出现问题：") {
                var steps: [String] = []
                var next = index + 1
                while next < all.count, all[next].message.hasPrefix("  ") || all[next].message.hasPrefix("不停止") {
                    let line = all[next].message.trimmingCharacters(in: .whitespaces)
                    if !line.hasPrefix("自动恢复") && !line.hasPrefix("不停止") { steps.append(line) }
                    next += 1
                }
                let reason = String(text.dropFirst("出现问题：".count))
                items.append(FeedItem(id: entry.id, time: entry.time, title: "自动恢复：" + reason, detail: steps.joined(separator: " → "), kind: .recovery))
                index = next
                continue
            }
            if text.contains("闪光") && (text.contains("命中") || text.contains("已停止")) {
                items.append(FeedItem(id: entry.id, time: entry.time, title: "命中闪光规则，立即停止", detail: "闪光永远不会被自动恢复跳过", kind: .shiny))
            } else if text.contains("已自动切回前台") {
                items.append(FeedItem(id: entry.id, time: entry.time, title: "游戏被切到后台，已切回", detail: "", kind: .normal))
            } else if text.contains("轮完成") {
                items.append(FeedItem(id: entry.id, time: entry.time, title: text.components(separatedBy: "；").first ?? text, detail: "", kind: .normal))
            } else if text.contains("PP 已用") {
                items.append(FeedItem(id: entry.id, time: entry.time, title: "PP 用完，开始去医院", detail: "", kind: .normal))
            } else if text.contains("去医院补PP完成") {
                items.append(FeedItem(id: entry.id, time: entry.time, title: "PP 已补满，回到刷怪点", detail: "", kind: .normal))
            } else if text.contains("已切到频道") {
                items.append(FeedItem(id: entry.id, time: entry.time, title: text.components(separatedBy: "，").first ?? text, detail: text.components(separatedBy: "；").dropFirst().joined(), kind: .normal))
            } else if text.contains("已停止") && !text.contains("自动恢复") {
                items.append(FeedItem(id: entry.id, time: entry.time, title: text, detail: "", kind: .stop))
            }
            index += 1
        }
        return Array(items.reversed().prefix(40))
    }
}

struct MainView: View {
    @EnvironmentObject var c: Controller
    var body: some View {
        VStack(spacing: 0) {
            HeaderBar()
            VStack(spacing: 10) {
                if c.shinyFound { ShinyCard() } else { StatusCard() }
                Metrics()
                Features()
                FeedPanel()
            }
            .padding(12)
            .frame(maxHeight: .infinity, alignment: .top)
        }
        .background(Color.ground)
        .foregroundStyle(Color.ink)
        .sheet(isPresented: $c.showSettings) { SettingsSheet().environmentObject(c) }
        .sheet(isPresented: $c.showLogs) { LogsSheet().environmentObject(c) }
        .alert("发现闪光！", isPresented: $c.shinyAlert) {
            Button("知道了") {}
        } message: { Text("已停止，没有逃跑。去游戏里手动捕捉，抓完回到点位再点「继续刷」。") }
        .alert("需要处理", isPresented: Binding(get: { c.error != nil }, set: { if !$0 { c.error = nil } })) {
            Button("知道了") { c.error = nil }
        } message: { Text(c.error ?? "") }
    }
}

struct HeaderBar: View {
    @EnvironmentObject var c: Controller
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "sparkle").font(.system(size: 17, weight: .semibold)).foregroundStyle(Color.go)
            Text("刷闪助手").font(.system(size: 15, weight: .bold))
            HStack(spacing: 6) {
                Circle().fill(c.state.gameFound ? Color.go : Color(hex: 0x9AA09D)).frame(width: 7, height: 7)
                Text(c.state.gameFound ? (c.state.busy ? "游戏在前台" : "已找到 PokeMMO") : "没找到游戏")
                    .font(.system(size: 12, weight: .medium))
            }
            .padding(.horizontal, 9).padding(.vertical, 4)
            .background(c.state.gameFound ? Color.goTint : Color.chip, in: Capsule())
            .foregroundStyle(c.state.gameFound ? Color.goText : Color.inkSoft)
            Spacer()
            if c.state.busy {
                Text("F8 停止").font(.system(size: 12)).foregroundStyle(Color.inkSoft)
            } else {
                Button { c.showSettings = true } label: {
                    Image(systemName: "gearshape").font(.system(size: 16)).frame(width: 36, height: 36)
                }
                .buttonStyle(.plain).foregroundStyle(Color.inkSoft).help("设置")
                .accessibilityLabel("设置")
            }
        }
        .padding(.horizontal, 14).frame(height: 52)
        .background(Color(hex: 0xFAFAF8))
        .overlay(alignment: .bottom) { Rectangle().fill(Color.hairline).frame(height: 1) }
    }
}

struct BigButton: View {
    let title: String, icon: String, fill: Color, text: Color
    var disabled = false
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: icon).font(.system(size: 14, weight: .bold))
                Text(title).font(.system(size: 17, weight: .bold))
            }
            .frame(maxWidth: .infinity).frame(height: 50)
            .background(fill.opacity(disabled ? 0.45 : 1), in: RoundedRectangle(cornerRadius: 12))
            .foregroundStyle(text)
        }
        .buttonStyle(.plain).disabled(disabled)
    }
}

struct StatusCard: View {
    @EnvironmentObject var c: Controller
    var body: some View {
        Panel {
            VStack(alignment: .leading, spacing: 11) {
                HStack(spacing: 8) {
                    Circle().fill(c.state.busy ? Color.go : Color(hex: 0x9AA09D)).frame(width: 9, height: 9)
                        .overlay(Circle().stroke(c.state.busy ? Color.goTint : Color.clear, lineWidth: 4))
                    Text(c.state.busy ? "运行中" : (c.stoppedWithReason ? "已停止" : "待机"))
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(c.state.busy ? Color.goText : Color.inkMuted)
                }
                Text(c.headline).font(.system(size: 24, weight: .bold))
                Text(c.subline).font(.system(size: 13)).foregroundStyle(Color.inkSoft)
                    .lineLimit(3).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                if c.state.busy && c.state.recovering {
                    ProgressView(value: Double(c.state.recoveryIndex), total: Double(max(1, c.state.recoveryTotal)))
                        .tint(Color.go)
                }
                if c.state.busy {
                    BigButton(title: c.state.stopping ? "正在停止…" : "停止", icon: "stop.fill", fill: .stop, text: .white,
                              disabled: c.state.stopping) { c.send("stop") }
                } else {
                    BigButton(title: "开始刷闪", icon: "play.fill", fill: .go, text: .onGo,
                              disabled: !c.connected || c.pending || !c.state.gameFound) { c.send("start") }
                }
            }
        }
    }
}

struct ShinyCard: View {
    @EnvironmentObject var c: Controller
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                Image(systemName: "star.fill").font(.system(size: 22)).foregroundStyle(.white)
                    .frame(width: 44, height: 44).background(Color.shinyEdge, in: RoundedRectangle(cornerRadius: 12))
                VStack(alignment: .leading, spacing: 2) {
                    Text("发现闪光！").font(.system(size: 24, weight: .bold))
                    Text("第 \(c.state.rounds + 1) 轮 · 战斗菜单还开着，没有逃跑").font(.system(size: 12))
                }
            }
            Text("去游戏里手动捕捉。抓完回到刷怪点，点「继续刷」接着计数。").font(.system(size: 13))
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                BigButton(title: "切到游戏", icon: "gamecontroller.fill", fill: .go, text: .onGo) { c.send("focus_game") }
                Button { c.send("start") } label: {
                    Text("继续刷").font(.system(size: 16, weight: .semibold)).frame(maxWidth: .infinity).frame(height: 50)
                        .background(Color.white, in: RoundedRectangle(cornerRadius: 12))
                        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color(hex: 0xCFCFC6)))
                }.buttonStyle(.plain).disabled(!c.connected || c.pending)
            }
            Button("看当时的截图") { c.reveal("diagnostics") }.buttonStyle(.link).frame(maxWidth: .infinity)
        }
        .foregroundStyle(Color.shinyText)
        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.shinyTint, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Color.shinyEdge, lineWidth: 2))
    }
}

struct Tile: View {
    let title: String, value: String
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.system(size: 11)).foregroundStyle(Color.inkMuted)
            Text(value).font(.system(size: 19, weight: .semibold, design: .monospaced)).monospacedDigit()
                .lineLimit(1).minimumScaleFactor(0.7)
        }
        .padding(.horizontal, 13).padding(.vertical, 11).frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.panel, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color.hairline))
    }
}

struct Metrics: View {
    @EnvironmentObject var c: Controller
    var channelValue: String {
        if !c.state.channels { return "未开启" }
        if c.state.restRemaining > 0 { return "休息 " + minutes(c.state.restRemaining) }
        return minutes(c.state.channelRemaining)
    }
    var body: some View {
        Grid(horizontalSpacing: 8, verticalSpacing: 8) {
            GridRow {
                Tile(title: "运行时长", value: duration(c.state.elapsed))
                Tile(title: "已遇到", value: "\(c.state.rounds) 轮")
            }
            GridRow {
                Tile(title: "PP", value: c.state.taskMode == "fixed" ? "PP果自补" : "\(c.state.ppCompleted) / \(c.state.ppEvery)")
                if c.shinyFound {
                    Tile(title: "自动恢复", value: "\(c.state.recoveriesTotal) 次")
                } else {
                    Tile(title: "下次换频道", value: channelValue)
                }
            }
        }
    }
}

struct FeatureRow: View {
    let title: String, detail: String
    let isOn: Bool
    let enabled: Bool
    let set: (Bool) -> Void
    var body: some View {
        Toggle(isOn: Binding(get: { isOn }, set: set)) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 14, weight: .medium))
                Text(detail).font(.system(size: 12)).foregroundStyle(Color.inkMuted)
            }
        }
        .toggleStyle(.switch).tint(Color.go).disabled(!enabled)
    }
}

struct Features: View {
    @EnvironmentObject var c: Controller
    private var canChange: Bool { !c.state.busy && c.connected && !c.pending }
    private var spotName: String { c.state.spots.first(where: { $0.key == c.state.spot })?.name ?? "帆巴市 · 轻飘飘" }
    var body: some View {
        if c.state.busy {
            HStack(spacing: 6) {
                chip(c.state.taskMode == "fixed" ? "PP果定点" : "白嫖闪 · " + spotName)
                if c.state.notificationEnabled { chip("出闪提醒") }
                if c.state.channels && !c.state.routeTest { chip("换频道 40–50 分") }
                Spacer(minLength: 0)
            }
        } else {
            Panel(padding: 14) {
                VStack(alignment: .leading, spacing: 12) {
                    Text("模式").font(.system(size: 14, weight: .bold))
                    Picker("模式", selection: Binding(get: { c.state.taskMode }, set: { c.send("settings", ["task_mode": $0]) })) {
                        Text("白嫖闪").tag("frillish")
                        Text("PP果定点").tag("fixed")
                    }
                    .pickerStyle(.segmented).labelsHidden().disabled(!canChange)
                    if c.state.taskMode == "fixed" {
                        Text("站在固定点位，只按 5 → 查闪光 → 逃跑，一直循环；不回院、不走路。")
                            .font(.system(size: 12)).foregroundStyle(Color.inkMuted).fixedSize(horizontal: false, vertical: true)
                    } else {
                        Picker("点位", selection: Binding(get: { c.state.spot }, set: { c.send("settings", ["spot": $0]) })) {
                            ForEach(c.state.spots, id: \.key) { spot in
                                Text(spot.verified ? spot.name : spot.name + "（待实测）").tag(spot.key)
                            }
                        }
                        .disabled(!canChange)
                        Text("每 6 轮按 4 回院补 PP，走回点位后冲浪继续刷。")
                            .font(.system(size: 12)).foregroundStyle(Color.inkMuted).fixedSize(horizontal: false, vertical: true)
                        Button("测试回院路线（走一遍就停，每步截图）") { c.send("test_route") }
                            .buttonStyle(.link).font(.system(size: 12))
                            .disabled(!canChange || !c.state.gameFound)
                    }
                    Divider()
                    Text("通用").font(.system(size: 14, weight: .bold))
                    FeatureRow(title: "出闪提醒", detail: "发现闪光立即停止；喵提醒通知手机，电脑上响铃弹窗",
                               isOn: c.state.notificationEnabled, enabled: canChange) { c.send("notification", ["enabled": $0]) }
                    FeatureRow(title: "随机换频道", detail: "每 40–50 分钟随机换一次，换完休息 3–8 分钟",
                               isOn: c.state.channels, enabled: canChange) { c.set("channels", $0) }
                }
            }
        }
    }
    private func chip(_ text: String) -> some View {
        Text(text).font(.system(size: 12)).foregroundStyle(Color(hex: 0x33393A))
            .padding(.horizontal, 9).padding(.vertical, 5)
            .background(Color.chip, in: RoundedRectangle(cornerRadius: 8))
    }
}

struct FeedPanel: View {
    @EnvironmentObject var c: Controller
    var body: some View {
        let items = c.feed
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text("动态").font(.system(size: 14, weight: .bold))
                if c.state.recoveriesTotal > 0 {
                    Text("自动恢复 \(c.state.recoveriesTotal) 次").font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Color(hex: 0x8A3D06)).padding(.horizontal, 8).padding(.vertical, 3)
                        .background(Color.fixTint, in: Capsule())
                }
                if c.state.focusRegains > 0 {
                    Text("切回前台 \(c.state.focusRegains) 次").font(.system(size: 11))
                        .foregroundStyle(Color(hex: 0x33393A)).padding(.horizontal, 8).padding(.vertical, 3)
                        .background(Color.chip, in: Capsule())
                }
                Spacer()
                Button("完整日志") { c.showLogs = true }.buttonStyle(.link).font(.system(size: 12))
            }
            if items.isEmpty {
                Text("开始后，每一轮、去医院的每一步\n和自动恢复都会记在这里")
                    .font(.system(size: 13)).foregroundStyle(Color.inkMuted).multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(items) { item in FeedRow(item: item) }
                    }
                }
            }
        }
        .padding(14).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color.panel, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Color.hairline))
    }
}

struct FeedRow: View {
    let item: FeedItem
    var tint: Color? {
        switch item.kind {
        case .recovery: return .fixTint
        case .shiny: return .shinyTint
        default: return nil
        }
    }
    var textColor: Color {
        switch item.kind {
        case .recovery: return .fixText
        case .shiny: return .shinyText
        case .stop: return Color(hex: 0x9F1239)
        case .normal: return .ink
        }
    }
    var body: some View {
        HStack(alignment: .top, spacing: 9) {
            Text(String(item.time.prefix(5))).font(.system(size: 11, design: .monospaced))
                .foregroundStyle(tint == nil ? Color.inkMuted : textColor).frame(width: 36, alignment: .leading).padding(.top, 2)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title).font(.system(size: 13, weight: tint == nil ? .regular : .semibold))
                if !item.detail.isEmpty { Text(item.detail).font(.system(size: 12)) }
            }
            .foregroundStyle(textColor).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .textSelection(.enabled)
        .padding(.vertical, 8).padding(.horizontal, tint == nil ? 0 : 9)
        .background(tint ?? Color.clear, in: RoundedRectangle(cornerRadius: 10))
        .padding(.vertical, tint == nil ? 0 : 3)
        .overlay(alignment: .bottom) { if tint == nil { Rectangle().fill(Color(hex: 0xF0F0EC)).frame(height: 1) } }
    }
}

struct SettingsSheet: View {
    @EnvironmentObject var c: Controller
    @Environment(\.dismiss) private var dismiss
    @ViewState private var code = ""
    @ViewState private var resetPP = false
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("设置").font(.system(size: 17, weight: .bold))
                Spacer()
                Button("完成") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            VStack(alignment: .leading, spacing: 14) {
            Panel(padding: 14) {
                VStack(alignment: .leading, spacing: 10) {
                    Text("系统权限").font(.system(size: 14, weight: .bold))
                    Text(c.permissionText).font(.system(size: 13)).lineSpacing(5)
                    HStack {
                        Button("检查 / 申请权限") { c.send("permissions") }
                        Button("刷新") { c.send("check_permissions") }
                    }
                }
            }
            Panel(padding: 14) {
                VStack(alignment: .leading, spacing: 10) {
                    Toggle("喵提醒（闪光、验证码时通知手机）", isOn: Binding(get: { c.state.notificationEnabled }, set: { c.send("notification", ["enabled": $0]) }))
                        .toggleStyle(.switch).tint(Color.go)
                    HStack {
                        SecureField(c.state.notificationConfigured ? "已配置喵码；输入可替换" : "输入喵码", text: $code).textFieldStyle(.roundedBorder)
                        Button("保存") { c.send("notification", ["enabled": c.state.notificationEnabled, "code": code]); code = "" }
                            .disabled(code.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                    Button("测试本机提示") { c.send("test_notification") }
                }
            }
            Panel(padding: 14) {
                VStack(alignment: .leading, spacing: 10) {
                    Text("计数与文件").font(.system(size: 14, weight: .bold))
                    HStack {
                        Button("清零运行计时") { c.send("reset_timer") }
                        Button("我已手动补满 PP…") { resetPP = true }
                    }
                    HStack {
                        Button("打开助手文件夹") { c.reveal() }
                        Button("问题截图") { c.reveal("diagnostics") }
                    }
                }
            }
            }
            .disabled(c.state.busy || !c.connected)
        }
        .padding(18).frame(width: 380)
        .confirmationDialog("确认已手动补满 PP，并回到刷怪起点？", isPresented: $resetPP) {
            Button("已补满，重置 PP 计数") { c.send("reset_pp") }
            Button("取消", role: .cancel) {}
        }
    }
}

struct LogsSheet: View {
    @EnvironmentObject var c: Controller
    @Environment(\.dismiss) private var dismiss
    @ViewState private var query = ""
    var filtered: [LogEntry] { c.logs.filter { query.isEmpty || $0.message.localizedCaseInsensitiveContains(query) } }
    var body: some View {
        VStack(spacing: 10) {
            HStack {
                Text("完整日志").font(.system(size: 17, weight: .bold))
                Spacer()
                Button("日志文件") { c.reveal("run.log") }
                Button("完成") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            TextField("搜索日志", text: $query).textFieldStyle(.roundedBorder)
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(filtered) { entry in
                            HStack(alignment: .top, spacing: 10) {
                                Text(entry.time).foregroundStyle(Color.inkMuted).frame(width: 60, alignment: .leading)
                                Text(entry.message).frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                            .padding(.horizontal, 10).padding(.vertical, 6)
                            Divider().opacity(0.4)
                        }
                        Color.clear.frame(height: 1).id("end")
                    }
                }
                .background(Color.white, in: RoundedRectangle(cornerRadius: 10))
                .onAppear { proxy.scrollTo("end", anchor: .bottom) }
                .onChange(of: c.logs.count) { proxy.scrollTo("end", anchor: .bottom) }
            }
            Text("显示最近 800 条；完整记录在日志文件里。").font(.system(size: 11)).foregroundStyle(Color.inkMuted)
        }
        .padding(16).frame(width: 380, height: 640)
    }
}
