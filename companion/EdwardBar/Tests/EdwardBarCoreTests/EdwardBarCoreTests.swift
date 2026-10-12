import Testing
@testable import EdwardBarCore
import Foundation

@Suite struct PresenceRulesTests {
    @Test func lockedWinsOverIdle() {
        #expect(PresenceRules.state(locked: true, idleSeconds: 0) == "locked")
    }
    @Test func idleAfterThreshold() {
        #expect(PresenceRules.state(locked: false, idleSeconds: 119) == "active")
        #expect(PresenceRules.state(locked: false, idleSeconds: 120) == "idle")
    }
    @Test func deviceIdIsUrlSafeAndStable() {
        #expect(PresenceRules.deviceId(from: "Sam's MacBook Pro") == "sam-s-macbook-pro")
        #expect(PresenceRules.deviceId(from: "  Studio!! ") == "studio")
        #expect(PresenceRules.deviceId(from: "") == "mac")
    }
}

@Suite struct SSEParserTests {
    @Test func parsesCompleteEventsAndKeepsPartialTrailer() {
        var p = SSEParser()
        let first = p.feed("event: state\ndata: {\"owner_present\": true}\n\nevent: interr")
        #expect(first == [SSEEvent(event: "state", data: "{\"owner_present\": true}")])
        let second = p.feed("upt\ndata: {\"delivery_id\": \"d1\"}\n\n")
        #expect(second == [SSEEvent(event: "interrupt", data: "{\"delivery_id\": \"d1\"}")])
    }
    @Test func decodesFrames() throws {
        let data = "{\"owner_present\": true, \"best_surface_for_interrupt\": \"menubar:x\", \"watching\": 1}".data(using: .utf8)!
        let s = try JSONDecoder().decode(AttentionSummary.self, from: data)
        #expect(s.owner_present && s.watching == 1)
    }
}

@Suite struct IconStateTests {
    @Test func priority() {
        #expect(IconState.derive(connected: false, pendingInterrupts: 3, watching: 3) == .offline)
        #expect(IconState.derive(connected: true, pendingInterrupts: 1, watching: 0) == .wantsAWord)
        #expect(IconState.derive(connected: true, pendingInterrupts: 0, watching: 2) == .watching)
        #expect(IconState.derive(connected: true, pendingInterrupts: 0, watching: 0) == .idle)
    }
}
