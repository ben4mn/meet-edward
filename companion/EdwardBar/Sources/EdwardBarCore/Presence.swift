import Foundation

/// What this Mac tells Edward about itself. Mirrors the backend's
/// `presence_logic.normalize_beacon` contract (kind "menubar").
public struct BeaconPayload: Codable, Equatable {
    public var token: String
    public var device_id: String
    public var device_name: String
    public var kind: String = "menubar"
    public var state: String
    public var idle_seconds: Int
    public var app: String?
    public var capabilities: [String] = ["toast", "open_chat"]

    public init(token: String, deviceId: String, deviceName: String, state: String, idleSeconds: Int, app: String?) {
        self.token = token
        self.device_id = deviceId
        self.device_name = deviceName
        self.state = state
        self.idle_seconds = idleSeconds
        self.app = app
    }
}

public enum PresenceRules {
    /// Same threshold as the backend's ACTIVE_IDLE_MAX_SECONDS.
    public static let idleAfterSeconds = 120

    /// Pure: raw inputs → the state string the backend expects.
    public static func state(locked: Bool, idleSeconds: Int) -> String {
        if locked { return "locked" }
        return idleSeconds >= idleAfterSeconds ? "idle" : "active"
    }

    /// A stable, URL-safe device id from the computer name ("Sam's MacBook Pro" → "sam-s-macbook-pro").
    public static func deviceId(from computerName: String) -> String {
        let lowered = computerName.lowercased()
        var out = ""
        var lastDash = false
        for ch in lowered {
            if ch.isLetter || ch.isNumber {
                out.append(ch); lastDash = false
            } else if !lastDash && !out.isEmpty {
                out.append("-"); lastDash = true
            }
        }
        while out.hasSuffix("-") { out.removeLast() }
        return out.isEmpty ? "mac" : out
    }
}
