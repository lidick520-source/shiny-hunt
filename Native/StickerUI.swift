import SwiftUI
import AppKit
import CoreText

// 2026-10-23 sidebar redesign ("手账贴纸" look, approved on the design canvas):
// a sky hero with the sticker cat, hand lettering, a white stat capsule over the cloud edge,
// a taped card, and a floating dark dock whose round button is the main action.

enum Sticker {
    static let paper = Color(hex: 0xFBF8F4)
    static let ink = Color(hex: 0x16161D)
    static let muted = Color(hex: 0x5E5E6A)
    static let sky = Color(hex: 0xC7D8FA)
    static let shinySky = Color(hex: 0xFFD9E6)
    static let cloudPink = Color(hex: 0xFFD3E2)
    static let sun = Color(hex: 0xFFE27A)
    static let gold = Color(hex: 0xFFD43B)
    static let blue = Color(hex: 0x3D6FF2)
    static let blueText = Color(hex: 0x2A54C4)
    static let blueChip = Color(hex: 0xE3EBFE)
    static let chip = Color(hex: 0xF3EFEA)
    static let rule = Color(hex: 0xEEE9E2)
    static let empty = Color(hex: 0xEAE5DE)
    static let heart = Color(hex: 0xFF72A8)
    static let star = Color(hex: 0xFFE04A)
    static let fixText = Color(hex: 0x8A3D06)

    static func marker(_ size: CGFloat) -> Font { .custom("PermanentMarker-Regular", size: size) }
    static func script(_ size: CGFloat) -> Font { .custom("Caveat", size: size).weight(.bold) }
    static func number(_ size: CGFloat) -> Font { .custom("ArchivoBlack-Regular", size: size) }

    /// Fonts and the cat live in assets/ui next to the app; nothing is fetched from the network.
    static func registerFonts(root: URL) {
        let dir = root.appendingPathComponent("assets/ui/fonts")
        let files = (try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? []
        for url in files where ["ttf", "otf"].contains(url.pathExtension.lowercased()) {
            CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
        }
    }
    static func cat(root: URL) -> NSImage? { NSImage(contentsOf: root.appendingPathComponent("assets/ui/cat-box.png")) }
}

enum Screen { case idle, running, shiny }

extension Controller {
    var screen: Screen { shinyFound ? .shiny : (state.busy ? .running : .idle) }
    var spotInfo: SpotInfo? { state.spots.first(where: { $0.key == state.spot }) }
    var spotParts: (city: String, target: String) {
        let parts = (spotInfo?.name ?? "帆巴市 · 轻飘飘").components(separatedBy: " · ")
        return (parts.first ?? "", parts.count > 1 ? parts[1] : "")
    }
    var fixedMode: Bool { state.taskMode == "fixed" }
    var ppLeft: Int { max(0, state.ppEvery - state.ppCompleted) }
    var hoursMinutes: String {
        let h = state.elapsed / 3600, m = state.elapsed / 60 % 60
        return h > 0 ? "\(h) 小时 \(m) 分" : "\(m) 分钟"
    }
    /// Three marker lines; the first is blue, `accentLast` paints the last one pink.
    var lettering: (lines: [String], accentLast: Bool, sub: String) {
        switch screen {
        case .shiny:
            return (["WOW,", "IT'S", "SHINY!!"], true, "第 \(state.rounds + 1) 群里有闪光！已经停下，没点逃跑。")
        case .running:
            if state.recovering { return (["HEALING", "AT THE", "CENTER"], false, "PP 用完了，回中心补满再冲浪回来。") }
            if state.restRemaining > 0 { return (["TAKING", "A", "BREAK"], false, "刚换完频道，歇 \(minutes(state.restRemaining)) 后自己接着刷。") }
            let en = spotInfo?.en ?? ""
            let target = fixedMode || en.isEmpty ? "NONSTOP" : en
            let place = fixedMode ? "固定点位" : "\(spotParts.city)刷\(spotParts.target)"
            return (["SHINY", "HUNTING", target], false, "\(place)，已经 \(hoursMinutes)，还没出闪。")
        case .idle:
            if !connected { return (["HOLD", "ON A", "SECOND"], false, "正在启动后台…") }
            if !state.gameFound { return (["WHERE", "IS THE", "GAME?"], false, "先打开 PokeMMO，这里会自己找到它。") }
            if stoppedWithReason { return (["OOPS,", "IT", "STOPPED"], false, state.lastReason) }
            return (["READY", "FOR A", "SHINY?"], false, "站到刷怪点，选好模式和点位，按右下角的绿色按钮。")
        }
    }
}

// MARK: - Root

struct MainView: View {
    @EnvironmentObject var c: Controller
    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width, h = geo.size.height
            ZStack(alignment: .topLeading) {
                Sticker.paper
                Hero(width: w)
                TopRow().frame(width: w - 32).offset(x: 16, y: 14)
                Group {
                    switch c.screen {
                    case .running: RunningBody(width: w, height: h)
                    case .idle: IdleBody(width: w, height: h)
                    case .shiny: ShinyBody(width: w, height: h)
                    }
                }
                Dock().frame(width: w - 28).offset(x: 14, y: h - 16 - 64)
            }
            .frame(width: w, height: h, alignment: .topLeading)
            .clipped()
        }
        .foregroundStyle(Sticker.ink)
        .environment(\.colorScheme, .light)
        .sheet(isPresented: $c.showSettings) { SettingsSheet().environmentObject(c) }
        .sheet(isPresented: $c.showLogs) { LogsSheet().environmentObject(c) }
        .alert("发现闪光！", isPresented: $c.shinyAlert) {
            Button("知道了") {}
        } message: { Text("已停止，没有逃跑。去游戏里手动捕捉，抓完回到点位再按绿色按钮继续刷。") }
        .alert("需要处理", isPresented: Binding(get: { c.error != nil }, set: { if !$0 { c.error = nil } })) {
            Button("知道了") { c.error = nil }
        } message: { Text(c.error ?? "") }
    }
}

// MARK: - Hero: sky, sun, cat, lettering

struct Hero: View {
    @EnvironmentObject var c: Controller
    let width: CGFloat
    var body: some View {
        let shiny = c.screen == .shiny
        let skyColor = shiny ? Sticker.shinySky : Sticker.sky
        let r = width - 400   // everything on the right keeps its distance to the right edge
        ZStack(alignment: .topLeading) {
            UnevenRoundedRectangle(bottomLeadingRadius: 28, bottomTrailingRadius: 28)
                .fill(skyColor).frame(width: width, height: 262)
            blob(skyColor, -20, 222, 150, 84)
            blob(skyColor, 96, 236, 130, 74)
            blob(shiny ? Sticker.sky : Sticker.cloudPink, 252 + r, 236, 170, 80)
            blob(.white, 160 + r * 0.5, 268, 120, 52)
            if shiny {
                Circle().fill(Sticker.gold).frame(width: 210, height: 210)
                    .overlay(Circle().stroke(Sticker.gold.opacity(0.35), lineWidth: 28))
                    .offset(x: 196 + r, y: 30)
            } else {
                Circle().fill(Sticker.sun).frame(width: 176, height: 176).offset(x: 214 + r, y: 46)
            }
            Path { p in
                p.move(to: CGPoint(x: 30, y: 60)); p.addQuadCurve(to: CGPoint(x: 200, y: 70), control: CGPoint(x: 112, y: -18))
            }
            .stroke(Sticker.ink, lineWidth: 1.6).frame(width: 220, height: 220).offset(x: 186 + r, y: 24)
            Capsule().fill(Sticker.blue).frame(width: 3, height: 24).rotationEffect(.degrees(20)).offset(x: 208 + r, y: 92)
            Image(systemName: "heart").font(.system(size: 20, weight: .medium)).foregroundStyle(Color(hex: 0xFF5C8A))
                .rotationEffect(.degrees(-8)).offset(x: 196 + r * 0.5, y: 82)
            if shiny {
                sparkle(.white, 26, 188 + r, 50); sparkle(Color(hex: 0xFF2D55), 18, 370 + r, 250); sparkle(Sticker.blue, 14, 236 + r, 112)
            }
            if let cat = c.catImage {
                Image(nsImage: cat).resizable().interpolation(.high).aspectRatio(contentMode: .fit)
                    .frame(width: 222).shadow(color: Sticker.ink.opacity(0.18), radius: 8, y: 8)
                    .offset(x: 182 + r, y: 134)
            }
            Lettering().frame(width: 220 + max(0, r * 0.5), alignment: .leading).offset(x: 20, y: 72)
        }
        .frame(width: width, height: 330, alignment: .topLeading)
    }
    private func blob(_ color: Color, _ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat) -> some View {
        Ellipse().fill(color).frame(width: w, height: h).offset(x: x, y: y)
    }
    private func sparkle(_ color: Color, _ size: CGFloat, _ x: CGFloat, _ y: CGFloat) -> some View {
        Image(systemName: "sparkle").font(.system(size: size, weight: .bold)).foregroundStyle(color).offset(x: x, y: y)
    }
}

struct Lettering: View {
    @EnvironmentObject var c: Controller
    var body: some View {
        let l = c.lettering
        VStack(alignment: .leading, spacing: 0) {
            Text("Dear trainer,").font(Sticker.script(26)).rotationEffect(.degrees(-4), anchor: .leading)
            VStack(alignment: .leading, spacing: -4) {
                ForEach(Array(l.lines.enumerated()), id: \.offset) { i, line in
                    Text(line).font(Sticker.marker(38)).lineLimit(1).minimumScaleFactor(0.6)
                        .foregroundStyle(i == 0 ? Sticker.blue : (i == 2 && l.accentLast ? Color(hex: 0xFF2D55) : Sticker.ink))
                }
            }
            .padding(.top, 2)
            .rotationEffect(.degrees(-3), anchor: .leading)
            Capsule().fill(Sticker.gold).frame(width: 170, height: 7).rotationEffect(.degrees(-3)).padding(.top, 4)
            Text(l.sub).font(.system(size: 13)).lineSpacing(4).lineLimit(3)
                .frame(maxWidth: 168, alignment: .leading).fixedSize(horizontal: false, vertical: true)
                .padding(.top, 8).textSelection(.enabled)
        }
    }
}

struct TopRow: View {
    @EnvironmentObject var c: Controller
    var body: some View {
        HStack(spacing: 8) {
            Text(pill).font(.system(size: 12, weight: .semibold))
                .padding(.horizontal, 11).padding(.vertical, 5)
                .background(Color.white.opacity(0.85), in: Capsule())
            Spacer()
            Button { c.showSettings = true } label: {
                Image(systemName: "square.grid.2x2").font(.system(size: 17, weight: .semibold)).foregroundStyle(.white)
                    .frame(width: 46, height: 46).background(Sticker.ink, in: Circle())
            }
            .buttonStyle(.plain).help("设置").accessibilityLabel("设置")
            .disabled(c.state.busy)
        }
    }
    private var pill: String {
        if c.screen == .shiny { return c.state.notificationEnabled && c.state.notificationConfigured ? "手机已通知" : "发现闪光" }
        if !c.connected { return "正在连接" }
        if c.state.busy { return "游戏在前台 · F8 停止" }
        return c.state.gameFound ? "已找到 PokeMMO" : "没找到游戏"
    }
}

// MARK: - Shared pieces

struct StatCapsule<Trailing: View>: View {
    let items: [(String, String)]
    @ViewBuilder let trailing: Trailing
    var body: some View {
        HStack(spacing: 0) {
            ForEach(Array(items.enumerated()), id: \.offset) { i, item in
                VStack(spacing: 1) {
                    Text(item.0).font(Sticker.number(22)).lineLimit(1).minimumScaleFactor(0.6)
                    Text(item.1).font(.system(size: 11)).foregroundStyle(Sticker.muted)
                }
                .frame(maxWidth: .infinity)
                if i < items.count - 1 { Rectangle().fill(Sticker.rule).frame(width: 1, height: 34) }
            }
            trailing
        }
        .padding(.leading, 10).padding(.trailing, 8)
        .frame(width: 300, height: 66)
        .background(Color.white, in: RoundedRectangle(cornerRadius: 22))
        .shadow(color: Sticker.ink.opacity(0.10), radius: 12, y: 10)
    }
}

struct RoundIconButton: View {
    let icon: String, label: String
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: icon).font(.system(size: 17, weight: .bold)).foregroundStyle(.white)
                .frame(width: 48, height: 48).background(Sticker.ink, in: Circle())
        }
        .buttonStyle(.plain).help(label).accessibilityLabel(label)
    }
}

/// Taped white card; the content decides its own layout.
struct TapedCard<Content: View>: View {
    var tape: Color = Sticker.gold.opacity(0.8)
    var sticker = true
    @ViewBuilder let content: Content
    var body: some View {
        content
            .padding(.top, 26).padding(.horizontal, 20).padding(.bottom, 16)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(Color.white, in: RoundedRectangle(cornerRadius: 26))
            .shadow(color: Sticker.ink.opacity(0.07), radius: 13, y: 10)
            .overlay(alignment: .topLeading) {
                Rectangle().fill(tape).frame(width: 100, height: 24).rotationEffect(.degrees(-4)).offset(x: 24, y: -12)
            }
            .overlay(alignment: .topTrailing) { if sticker { HeartStar().offset(x: -18, y: -12) } }
    }
}

/// The small heart + star sticker, flat like the cat's props, with a white sticker rim.
struct HeartStar: View {
    var body: some View {
        ZStack(alignment: .topLeading) {
            rimmed("heart.fill", Sticker.heart, 44).rotationEffect(.degrees(-10))
            rimmed("star.fill", Sticker.star, 30).rotationEffect(.degrees(12)).offset(x: 48, y: -4)
        }
        .frame(width: 96, height: 70, alignment: .topLeading)
        .shadow(color: Sticker.ink.opacity(0.16), radius: 5, y: 5)
        .accessibilityHidden(true)
    }
    private func rimmed(_ symbol: String, _ color: Color, _ size: CGFloat) -> some View {
        ZStack {
            Image(systemName: symbol).font(.system(size: size + 12)).foregroundStyle(.white)
            Image(systemName: symbol).font(.system(size: size)).foregroundStyle(color)
        }
    }
}

struct Chip: View {
    let text: String
    var accent = false
    var body: some View {
        HStack(spacing: 4) {
            if accent { Image(systemName: "sparkle").font(.system(size: 10, weight: .bold)) }
            Text(text).font(.system(size: 12, weight: accent ? .semibold : .regular))
        }
        .foregroundStyle(accent ? Sticker.blueText : Sticker.ink)
        .padding(.horizontal, 11).padding(.vertical, 5)
        .background(accent ? Sticker.blueChip : Sticker.chip, in: Capsule())
    }
}

/// Space between the content above and the dock, so the card ends 12 pt above the dock.
private func cardHeight(top: CGFloat, height: CGFloat) -> CGFloat { max(120, height - top - 16 - 64 - 12) }

// MARK: - Running

struct RunningBody: View {
    @EnvironmentObject var c: Controller
    let width: CGFloat, height: CGFloat
    var body: some View {
        let s = c.state
        ZStack(alignment: .topLeading) {
            StatCapsule(items: [
                ("\(s.rounds)", "遇到几群"),
                (c.fixedMode ? "∞" : "\(c.ppLeft)", c.fixedMode ? "PP果自补" : "还剩几轮"),
                (channelValue, s.restRemaining > 0 ? "正在休息" : "换频道")
            ]) {
                RoundIconButton(icon: "arrow.right", label: "完整日志") { c.showLogs = true }
            }
            .offset(x: 16, y: 318)

            progress.frame(width: width - 36).offset(x: 18, y: 404)

            TapedCard {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(alignment: .bottom, spacing: 10) {
                        Text("\(s.rounds + 1)").font(Sticker.number(50)).lineLimit(1).minimumScaleFactor(0.5)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("这一轮").font(.system(size: 12, weight: .bold)).foregroundStyle(Sticker.blue)
                            Text(c.subline).font(.system(size: 12)).foregroundStyle(Sticker.muted).lineLimit(2)
                        }
                        .padding(.bottom, 2).padding(.trailing, 80)
                    }
                    HStack(spacing: 6) {
                        Chip(text: c.fixedMode ? "PP果定点" : "白嫖闪", accent: true)
                        if s.notificationEnabled { Chip(text: "出闪提醒") }
                        if s.channels && !s.routeTest { Chip(text: "随机换频道") }
                    }
                    RecentLines()
                }
            }
            .frame(width: width - 32, height: cardHeight(top: 466, height: height))
            .offset(x: 16, y: 466)
        }
    }
    private var channelValue: String {
        if c.state.restRemaining > 0 { return "\(max(1, c.state.restRemaining / 60))′" }
        if !c.state.channels { return "关" }
        return "\(max(0, c.state.channelRemaining / 60))′"
    }
    @ViewBuilder private var progress: some View {
        let s = c.state
        if s.recovering {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("回中心 第 \(s.recoveryIndex) / \(s.recoveryTotal) 步").font(.system(size: 12, weight: .bold))
                    Spacer()
                    Text(s.recoveryLabel).font(.system(size: 12)).foregroundStyle(Sticker.muted).lineLimit(1)
                }
                GeometryReader { g in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Sticker.empty)
                        Capsule().fill(Sticker.blue).frame(width: g.size.width * CGFloat(s.recoveryIndex) / CGFloat(max(1, s.recoveryTotal)))
                    }
                }
                .frame(height: 12)
            }
        } else if c.fixedMode {
            HStack {
                Text("PP果定点").font(.system(size: 12, weight: .bold))
                Spacer()
                Text("不回中心，一直刷").font(.system(size: 12)).foregroundStyle(Sticker.muted)
            }
        } else {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("补 PP 前的 \(s.ppEvery) 轮").font(.system(size: 12, weight: .bold))
                    Spacer()
                    Text("刷完自己回中心").font(.system(size: 12)).foregroundStyle(Sticker.muted)
                }
                HStack(spacing: 6) {
                    ForEach(0..<max(1, s.ppEvery), id: \.self) { i in
                        Capsule().fill(i < s.ppCompleted ? Sticker.blue : (i == s.ppCompleted ? Sticker.gold : Sticker.empty))
                            .overlay(Capsule().stroke(i == s.ppCompleted ? Sticker.ink : .clear, lineWidth: 2))
                            .frame(height: 12)
                    }
                }
                .accessibilityElement().accessibilityLabel("补 PP 前的 \(s.ppEvery) 轮：已刷 \(s.ppCompleted) 轮")
            }
        }
    }
}

/// The three newest meaningful events (rounds, hospital, channel, recoveries) under the round number.
struct RecentLines: View {
    @EnvironmentObject var c: Controller
    var body: some View {
        let items = Array(c.feed.prefix(3))
        VStack(alignment: .leading, spacing: 7) {
            if items.isEmpty {
                Text("开始后，每一轮、回中心和自动恢复都会写在这里。").font(.system(size: 14)).foregroundStyle(Sticker.muted)
            }
            ForEach(items) { item in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(String(item.time.prefix(5))).font(.system(size: 12)).monospacedDigit()
                        .foregroundStyle(item.kind == .recovery ? Sticker.fixText : Sticker.muted).frame(width: 38, alignment: .leading)
                    Text(item.title).font(.system(size: 13)).lineLimit(1)
                        .foregroundStyle(item.kind == .recovery ? Sticker.fixText : (item.kind == .stop ? Color(hex: 0x9F1239) : Sticker.ink))
                }
            }
        }
        .textSelection(.enabled)
    }
}

// MARK: - Idle

struct IdleBody: View {
    @EnvironmentObject var c: Controller
    let width: CGFloat, height: CGFloat
    private var canChange: Bool { c.connected && !c.pending && !c.state.busy }
    var body: some View {
        let fixed = c.fixedMode
        ZStack(alignment: .topLeading) {
            modeSwitch.offset(x: 16, y: 318)
            HeartStar().offset(x: 296 + (width - 400), y: 300)

            if fixed {
                VStack(alignment: .leading, spacing: 4) {
                    Text("PP果定点").font(.system(size: 12, weight: .bold))
                    Text("站在任意固定点位，只按 5、查闪光、逃跑，一直循环。不回中心，走偏了就停。")
                        .font(.system(size: 12)).foregroundStyle(Sticker.muted).fixedSize(horizontal: false, vertical: true)
                }
                .frame(width: width - 36, alignment: .leading).offset(x: 18, y: 402)
            } else {
                spotPicker.frame(width: width - 36).offset(x: 18, y: 402)
            }

            TapedCard(tape: Sticker.sky.opacity(0.95), sticker: false) {
                VStack(alignment: .leading, spacing: 12) {
                    toggle("出闪提醒", "出闪立刻停，手机收通知，电脑响铃", c.state.notificationEnabled) { c.send("notification", ["enabled": $0]) }
                    Rectangle().fill(Color(hex: 0xF0EBE4)).frame(height: 1)
                    toggle("随机换频道", "每 40 到 50 分钟换一次，歇 3 到 8 分钟", c.state.channels) { c.set("channels", $0) }
                    if !fixed {
                        Button("先测一遍回院路线（走一趟就停，每步截图）") { c.send("test_route") }
                            .buttonStyle(.link).font(.system(size: 12, weight: .semibold))
                            .disabled(!canChange || !c.state.gameFound)
                    }
                }
            }
            .frame(width: width - 32, height: cardHeight(top: fixed ? 470 : 494, height: height))
            .offset(x: 16, y: fixed ? 470 : 494)
        }
    }
    private var modeSwitch: some View {
        HStack(spacing: 4) {
            modeButton("白嫖闪", "6 轮回中心补 PP", "frillish")
            modeButton("PP果定点", "原地一直刷", "fixed")
        }
        .padding(8).frame(width: 300, height: 66)
        .background(Color.white, in: RoundedRectangle(cornerRadius: 22))
        .shadow(color: Sticker.ink.opacity(0.10), radius: 12, y: 10)
    }
    private func modeButton(_ title: String, _ detail: String, _ tag: String) -> some View {
        let on = c.state.taskMode == tag
        return Button { c.send("settings", ["task_mode": tag]) } label: {
            VStack(spacing: 1) {
                Text(title).font(.system(size: 15, weight: .bold))
                Text(detail).font(.system(size: 11)).foregroundStyle(on ? Color(hex: 0xC8C8D2) : Sticker.muted)
            }
            .foregroundStyle(on ? Color.white : Sticker.ink)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(on ? Sticker.ink : Color.clear, in: RoundedRectangle(cornerRadius: 16))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain).disabled(!canChange)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
    private var spotPicker: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("去哪刷").font(.system(size: 12, weight: .bold))
            HStack(spacing: 8) {
                ForEach(c.state.spots, id: \.key) { spot in
                    let on = spot.key == c.state.spot
                    Button { c.send("settings", ["spot": spot.key]) } label: {
                        HStack(spacing: 6) {
                            Text(spot.name.replacingOccurrences(of: " · ", with: " ")).font(.system(size: 14, weight: .bold)).lineLimit(1).minimumScaleFactor(0.8)
                            if !spot.verified {
                                Text("待实测").font(.system(size: 10)).foregroundStyle(Color(hex: 0x3D2E00))
                                    .padding(.horizontal, 6).padding(.vertical, 1).background(Sticker.sun, in: Capsule())
                            }
                        }
                        .foregroundStyle(on ? Color.white : Sticker.ink)
                        .frame(maxWidth: .infinity).frame(height: 44)
                        .background(on ? Sticker.blue : Color.white, in: RoundedRectangle(cornerRadius: 14))
                        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(on ? Color.clear : Color(hex: 0xE6E0D8), lineWidth: 2))
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain).disabled(!canChange)
                    .accessibilityAddTraits(on ? .isSelected : [])
                }
            }
        }
    }
    private func toggle(_ title: String, _ detail: String, _ isOn: Bool, _ set: @escaping (Bool) -> Void) -> some View {
        Toggle(isOn: Binding(get: { isOn }, set: set)) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 15, weight: .bold))
                Text(detail).font(.system(size: 12)).foregroundStyle(Sticker.muted)
            }
        }
        .toggleStyle(.switch).tint(Color.go).disabled(!canChange)
    }
}

// MARK: - Shiny

struct ShinyBody: View {
    @EnvironmentObject var c: Controller
    let width: CGFloat, height: CGFloat
    var body: some View {
        let s = c.state
        ZStack(alignment: .topLeading) {
            StatCapsule(items: [
                ("\(s.rounds + 1)", "遇到几群"),
                (String(format: "%d:%02d", s.elapsed / 3600, s.elapsed / 60 % 60), "用了多久"),
                ("\(s.recoveriesTotal)", "自动恢复")
            ]) {
                RoundIconButton(icon: "photo", label: "看出闪那一刻的截图") { c.reveal("diagnostics") }
            }
            .offset(x: 16, y: 318)

            TapedCard(tape: Sticker.heart.opacity(0.7)) {
                VStack(alignment: .leading, spacing: 12) {
                    Text("接下来").font(.system(size: 18, weight: .bold)).padding(.top, 2)
                    VStack(alignment: .leading, spacing: 9) {
                        step(1, "切到游戏，手动把它抓住")
                        step(2, "抓完回到刷怪点")
                        step(3, "按右下角绿色按钮继续刷")
                    }
                    Spacer(minLength: 0)
                    Button { c.send("focus_game") } label: {
                        Text("切到游戏").font(.system(size: 15, weight: .bold)).foregroundStyle(.white)
                            .frame(maxWidth: .infinity).frame(height: 50)
                            .background(Sticker.ink, in: RoundedRectangle(cornerRadius: 16))
                    }
                    .buttonStyle(.plain)
                }
            }
            .frame(width: width - 32, height: cardHeight(top: 412, height: height))
            .offset(x: 16, y: 412)
        }
    }
    private func step(_ n: Int, _ text: String) -> some View {
        HStack(spacing: 10) {
            Text("\(n)").font(.system(size: 12, weight: .bold)).foregroundStyle(.white)
                .frame(width: 24, height: 24).background(Sticker.ink, in: Circle())
            Text(text).font(.system(size: 14))
        }
    }
}

// MARK: - Dock

struct Dock: View {
    @EnvironmentObject var c: Controller
    var body: some View {
        HStack(spacing: 0) {
            item("doc.text", "今天", active: true) {}
            divider
            item("list.bullet.rectangle", "日志", badge: c.state.recoveriesTotal > 0 ? "\(c.state.recoveriesTotal)" : nil, badgeColor: Sticker.sun) { c.showLogs = true }
            divider
            item("photo.on.rectangle", "截图") { c.reveal("diagnostics") }
            fab
        }
        .padding(.leading, 12).padding(.trailing, 7)
        .frame(height: 64)
        .background(Sticker.ink, in: Capsule())
        .shadow(color: Sticker.ink.opacity(0.28), radius: 12, y: 12)
    }
    private var divider: some View { Rectangle().fill(Color(hex: 0x3A3A46)).frame(width: 1, height: 22) }
    private func item(_ icon: String, _ title: String, active: Bool = false, badge: String? = nil, badgeColor: Color = Sticker.sun, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: icon).font(.system(size: 14))
                Text(title).font(.system(size: 13, weight: active ? .bold : .regular))
                if let badge {
                    Text(badge).font(.system(size: 11, weight: .bold)).foregroundStyle(Sticker.ink)
                        .frame(minWidth: 18, minHeight: 18).background(badgeColor, in: Capsule())
                }
            }
            .foregroundStyle(active ? Color.white : Color(hex: 0xC8C8D2))
            .frame(maxWidth: .infinity).frame(height: 44).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
    @ViewBuilder private var fab: some View {
        if c.state.busy {
            Button { c.send("stop") } label: {
                RoundedRectangle(cornerRadius: 3).fill(.white).frame(width: 15, height: 15)
                    .frame(width: 54, height: 54).background(Color.stop, in: Circle())
            }
            .buttonStyle(.plain).disabled(c.state.stopping)
            .help("停止（F8）").accessibilityLabel(c.state.stopping ? "正在停止" : "停止刷闪")
            .opacity(c.state.stopping ? 0.5 : 1)
        } else {
            let label = c.screen == .shiny ? "抓完了，继续刷" : "开始刷闪"
            Button { c.send("start") } label: {
                Image(systemName: "play.fill").font(.system(size: 20)).foregroundStyle(Color.onGo)
                    .frame(width: 54, height: 54).background(Color.go, in: Circle())
            }
            .buttonStyle(.plain).disabled(!c.connected || c.pending || !c.state.gameFound)
            .opacity(!c.connected || c.pending || !c.state.gameFound ? 0.45 : 1)
            .help(label).accessibilityLabel(label)
        }
    }
}
