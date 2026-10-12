// EdwardBar — Edward in the menu bar of whichever Mac the owner is at.
//
// What it does (and nothing more):
//   * every 30s posts a presence beacon (idle seconds, locked/active) so
//     Edward knows this is the screen the owner is looking at;
//   * keeps an SSE stream open so an "Edward wants a word" lands here first
//     — the icon changes, the menu shows the title, a click opens the chat
//     and acknowledges the delivery (which stops it escalating to a push);
//   * optionally reports the frontmost app (off by default; toggle in menu).
//
// Config: ~/.config/edward/presence.env with EDWARD_URL and EDWARD_TOKEN
// (the widget token from Settings → Widget). Same file the shell beacon uses.
import AppKit
import CoreGraphics
import EdwardBarCore
import Foundation

// MARK: - config

struct Config {
    let url: String
    let token: String
    static func load() -> Config? {
        let path = (ProcessInfo.processInfo.environment["EDWARD_PRESENCE_ENV"]
            ?? NSString(string: "~/.config/edward/presence.env").expandingTildeInPath)
        guard let text = try? String(contentsOfFile: path, encoding: .utf8) else { return nil }
        var kv: [String: String] = [:]
        for line in text.split(separator: "\n") {
            let parts = line.split(separator: "=", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
            if parts.count == 2 { kv[parts[0]] = parts[1].trimmingCharacters(in: CharacterSet(charactersIn: "\"'")) }
        }
        guard let u = kv["EDWARD_URL"], let t = kv["EDWARD_TOKEN"] else { return nil }
        return Config(url: u.hasSuffix("/") ? String(u.dropLast()) : u, token: t)
    }
}

// MARK: - local signals

func idleSeconds() -> Int {
    Int(CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: .init(rawValue: ~0)!))
}

func screenLocked() -> Bool {
    guard let dict = CGSessionCopyCurrentDictionary() as? [String: Any] else { return false }
    if let v = dict["CGSSessionScreenIsLocked"] as? Bool { return v }
    if let n = dict["CGSSessionScreenIsLocked"] as? Int { return n != 0 }
    return false
}

func computerName() -> String {
    (Host.current().localizedName ?? "Mac")
}

// MARK: - app

final class BarApp: NSObject, NSApplicationDelegate {
    let config: Config
    let deviceId: String
    let deviceName: String
    var item: NSStatusItem!
    var menu = NSMenu()
    var stateLine = NSMenuItem(title: "Connecting…", action: nil, keyEquivalent: "")
    var wordItem = NSMenuItem(title: "", action: #selector(openWord), keyEquivalent: "")
    var shareAppItem = NSMenuItem(title: "Share frontmost app with Edward", action: #selector(toggleShareApp), keyEquivalent: "")
    var pending: [InterruptFrame] = []
    var watching = 0
    var connected = false
    var shareApp = UserDefaults.standard.bool(forKey: "shareApp")
    var streamTask: Task<Void, Never>?

    init(config: Config) {
        self.config = config
        self.deviceName = computerName()
        self.deviceId = PresenceRules.deviceId(from: deviceName)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        stateLine.isEnabled = false
        wordItem.target = self; wordItem.isHidden = true
        shareAppItem.target = self; shareAppItem.state = shareApp ? .on : .off
        menu.addItem(stateLine)
        menu.addItem(wordItem)
        menu.addItem(.separator())
        menu.addItem(withTitle: "Open Edward", action: #selector(openChat), keyEquivalent: "e").target = self
        menu.addItem(shareAppItem)
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit EdwardBar", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.menu = menu
        render()
        beacon()
        Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in self?.beacon() }
        streamTask = Task { await self.streamLoop() }
    }

    func render() {
        let state = IconState.derive(connected: connected, pendingInterrupts: pending.count, watching: watching)
        if let img = NSImage(systemSymbolName: state.symbolName, accessibilityDescription: "Edward") {
            img.isTemplate = true
            item.button?.image = img
        }
        item.button?.toolTip = "Edward · \(state.rawValue)"
        if let w = pending.first {
            wordItem.title = "Edward: \(w.title) — \(w.body)".prefix(90) + ""
            wordItem.isHidden = false
        } else {
            wordItem.isHidden = true
        }
    }

    // MARK: beacon

    func beacon() {
        let idle = idleSeconds()
        let payload = BeaconPayload(
            token: config.token, deviceId: deviceId, deviceName: deviceName,
            state: PresenceRules.state(locked: screenLocked(), idleSeconds: idle), idleSeconds: idle,
            app: shareApp ? NSWorkspace.shared.frontmostApplication?.localizedName : nil)
        guard let body = try? JSONEncoder().encode(payload), let url = URL(string: config.url + "/api/presence/beacon") else { return }
        var req = URLRequest(url: url); req.httpMethod = "POST"; req.timeoutInterval = 5
        req.setValue("application/json", forHTTPHeaderField: "Content-Type"); req.httpBody = body
        URLSession.shared.dataTask(with: req).resume()
    }

    // MARK: stream

    func streamLoop() async {
        while !Task.isCancelled {
            guard var comps = URLComponents(string: config.url + "/api/presence/stream") else { return }
            comps.queryItems = [URLQueryItem(name: "token", value: config.token), URLQueryItem(name: "device_id", value: deviceId)]
            guard let url = comps.url else { return }
            do {
                let (bytes, response) = try await URLSession.shared.bytes(from: url)
                guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
                await MainActor.run { self.connected = true; self.stateLine.title = "Connected"; self.render() }
                var parser = SSEParser()
                for try await line in bytes.lines {
                    for ev in parser.feed(line + "\n") { await handle(ev) }
                }
            } catch {
                await MainActor.run { self.connected = false; self.stateLine.title = "Reconnecting…"; self.render() }
                try? await Task.sleep(nanoseconds: 5_000_000_000)
            }
        }
    }

    func handle(_ ev: SSEEvent) async {
        let data = Data(ev.data.utf8)
        switch ev.event {
        case "state":
            if let s = try? JSONDecoder().decode(AttentionSummary.self, from: data) {
                await MainActor.run {
                    self.watching = s.watching ?? 0
                    self.stateLine.title = s.owner_present ? "You're here · \(s.active_surface ?? "?")" : "You're away"
                    if self.watching > 0 { self.stateLine.title += " · watching \(self.watching)" }
                    self.render()
                }
            }
        case "interrupt":
            if let f = try? JSONDecoder().decode(InterruptFrame.self, from: data) {
                await MainActor.run {
                    if !self.pending.contains(where: { $0.delivery_id == f.delivery_id }) { self.pending.append(f) }
                    NSSound.beep()
                    self.render()
                }
            }
        default: break
        }
    }

    // MARK: actions

    @objc func openWord() {
        guard let w = pending.first else { return }
        ack(w.delivery_id, source: "menubar-click")
        pending.removeFirst()
        if let u = URL(string: config.url + w.url) { NSWorkspace.shared.open(u) }
        render()
    }

    @objc func openChat() {
        if let u = URL(string: config.url + "/chat") { NSWorkspace.shared.open(u) }
    }

    @objc func toggleShareApp() {
        shareApp.toggle()
        UserDefaults.standard.set(shareApp, forKey: "shareApp")
        shareAppItem.state = shareApp ? .on : .off
        beacon()
    }

    func ack(_ deliveryId: String, source: String) {
        guard let url = URL(string: config.url + "/api/push/ack") else { return }
        var req = URLRequest(url: url); req.httpMethod = "POST"; req.timeoutInterval = 5
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["delivery_id": deliveryId, "source": source, "token": config.token])
        URLSession.shared.dataTask(with: req).resume()
    }
}

guard let config = Config.load() else {
    FileHandle.standardError.write("EdwardBar: create ~/.config/edward/presence.env with EDWARD_URL and EDWARD_TOKEN\n".data(using: .utf8)!)
    exit(2)
}
let app = NSApplication.shared
let delegate = BarApp(config: config)
app.delegate = delegate
app.run()
