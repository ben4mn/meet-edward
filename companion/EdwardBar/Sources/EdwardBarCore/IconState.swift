import Foundation

/// What the status item shows. Ordered by priority: an unacked word from
/// Edward beats everything; a held thread ("watching") beats plain idle.
public enum IconState: String, Equatable {
    case offline      // no stream
    case idle         // connected, nothing happening
    case watching     // Edward is holding a thread through its settle window
    case wantsAWord   // an interrupt is pending on this device

    public var symbolName: String {
        switch self {
        case .offline: return "circle.dashed"
        case .idle: return "circle"
        case .watching: return "eye"
        case .wantsAWord: return "bubble.left.fill"
        }
    }

    public static func derive(connected: Bool, pendingInterrupts: Int, watching: Int) -> IconState {
        if !connected { return .offline }
        if pendingInterrupts > 0 { return .wantsAWord }
        if watching > 0 { return .watching }
        return .idle
    }
}
