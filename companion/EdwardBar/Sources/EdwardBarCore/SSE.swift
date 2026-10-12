import Foundation

/// One server-sent event as the backend's `format_sse` emits it.
public struct SSEEvent: Equatable {
    public let event: String
    public let data: String
    public init(event: String, data: String) { self.event = event; self.data = data }
}

/// Incremental parser for a `text/event-stream` body. Feed it chunks; it
/// returns complete events and keeps any partial trailer for the next chunk.
public struct SSEParser {
    private var buffer = ""
    public init() {}

    public mutating func feed(_ chunk: String) -> [SSEEvent] {
        buffer += chunk
        var events: [SSEEvent] = []
        while let range = buffer.range(of: "\n\n") {
            let block = String(buffer[buffer.startIndex..<range.lowerBound])
            buffer = String(buffer[range.upperBound...])
            var name = "message"
            var data: [String] = []
            for line in block.split(separator: "\n", omittingEmptySubsequences: false) {
                if line.hasPrefix("event: ") { name = String(line.dropFirst(7)) }
                else if line.hasPrefix("data: ") { data.append(String(line.dropFirst(6))) }
                else if line.hasPrefix("data:") { data.append(String(line.dropFirst(5))) }
            }
            if !data.isEmpty || name != "message" {
                events.append(SSEEvent(event: name, data: data.joined(separator: "\n")))
            }
        }
        return events
    }
}

/// The `state` frame the backend streams (subset the bar cares about).
public struct AttentionSummary: Codable, Equatable {
    public var owner_present: Bool
    public var active_surface: String?
    public var best_surface_for_interrupt: String
    public var watching: Int?
    public var reason: String?
}

/// An `interrupt` frame: Edward wants a word on this device.
public struct InterruptFrame: Codable, Equatable {
    public var delivery_id: String
    public var title: String
    public var body: String
    public var url: String
    public var urgency: String
}
