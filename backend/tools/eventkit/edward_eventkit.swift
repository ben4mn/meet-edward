// edward-eventkit — Edward's EventKit helper (2026-10-11; docs/architecture/computer.md).
//
// The one write path to the owner's calendar and Reminders: Edward adds, moves and
// deletes events and adds, checks off and deletes reminders when the owner asks
// (`schedule`'s event_* / reminder_* actions). Reads go through Calendar's
// and Reminders' own databases (calendar_service, reminders_service); this
// binary only writes, plus three reads that never prompt (access, calendars,
// lists) and the read-back of a saved event's invitees.
//
//   edward-eventkit <command>     input: one JSON object on stdin — never argv,
//                                 where `ps` would show an event's title and notes
//   access                        authorization status only; never prompts
//   request events|reminders      asks macOS for access — manual use only, never
//                                 called by the backend (a dialog would hang a turn)
//   calendars | lists             the event calendars / reminder lists
//   event_add | event_update | event_delete
//   reminder_add | reminder_complete | reminder_delete
//   selftest                      JSON, dates, the embedded plist and the attendee
//                                 probe; never constructs an EKEventStore
// Prints exactly one JSON line. Exit 0 ok, 2 usage or bad input, 3 failure
// (not_found, read_only, ambiguous, save_failed), 4 access.
//
// Build: backend/scripts/build_eventkit.sh. It embeds Info.plist as the
// __TEXT,__info_plist section: EventKit refuses a process whose main bundle
// lacks the usage strings, and for a command-line tool that section is the
// bundle. TCC attributes the access to the responsible process — the
// backend's python (python3.12 on the original host) when the backend spawns this.

import EventKit
import Foundation

// MARK: - output

func emit(_ obj: [String: Any]) {
    let data = (try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]))
        ?? Data("{\"code\":\"encode\",\"error\":\"couldn't encode the reply\",\"ok\":false}".utf8)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

struct Failure: Error {
    let exitCode: Int32
    let body: [String: Any]
}

func failure(_ code: String, _ message: String, exit: Int32 = 3, extra: [String: Any] = [:]) -> Failure {
    var body: [String: Any] = ["ok": false, "code": code, "error": message]
    for (k, v) in extra { body[k] = v }
    return Failure(exitCode: exit, body: body)
}

func badInput(_ message: String) -> Failure { failure("bad_input", message, exit: 2) }

// MARK: - input

func readInput() throws -> [String: Any] {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    let text = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    if text.isEmpty { return [:] }
    guard let obj = try? JSONSerialization.jsonObject(with: Data(text.utf8)), let dict = obj as? [String: Any] else {
        throw badInput("the input must be one JSON object")
    }
    return dict
}

func str(_ d: [String: Any], _ key: String) -> String? {
    guard let v = d[key] as? String else { return nil }
    let t = v.trimmingCharacters(in: .whitespacesAndNewlines)
    return t.isEmpty ? nil : t
}

func strList(_ d: [String: Any], _ key: String) -> [String] {
    let raw = (d[key] as? [Any]) ?? []
    return raw.compactMap { ($0 as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
}

// MARK: - dates

func parseInstant(_ s: String) -> Date? {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let d = f.date(from: s) { return d }
    f.formatOptions = [.withInternetDateTime]
    return f.date(from: s)
}

func gregorian(_ tz: TimeZone) -> Calendar {
    var c = Calendar(identifier: .gregorian)
    c.timeZone = tz
    return c
}

/// "YYYY-MM-DD" → its components, rejecting days that don't exist (Feb 30).
func parseDay(_ s: String) -> DateComponents? {
    let parts = s.split(separator: "-", omittingEmptySubsequences: false)
    guard parts.count == 3, parts[0].count == 4, parts[1].count == 2, parts[2].count == 2,
          let y = Int(parts[0]), let m = Int(parts[1]), let d = Int(parts[2]) else { return nil }
    let utc = gregorian(TimeZone(identifier: "UTC")!)
    guard let date = utc.date(from: DateComponents(year: y, month: m, day: d)) else { return nil }
    let back = utc.dateComponents([.year, .month, .day], from: date)
    guard back.year == y, back.month == m, back.day == d else { return nil }
    return DateComponents(year: y, month: m, day: d)
}

/// Local midnight of a day in a zone.
func midnight(_ day: DateComponents, _ tz: TimeZone) -> Date? {
    gregorian(tz).date(from: DateComponents(year: day.year, month: day.month, day: day.day))
}

func zone(_ d: [String: Any]) throws -> TimeZone {
    guard let name = str(d, "timezone") else { return TimeZone.current }
    guard let tz = TimeZone(identifier: name) else { throw badInput("unknown time zone \(name)") }
    return tz
}

func iso(_ date: Date?) -> Any {
    guard let date else { return NSNull() }
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    return f.string(from: date)
}

func instant(_ d: [String: Any], _ key: String) throws -> Date? {
    guard let s = str(d, key) else { return nil }
    guard let date = parseInstant(s) else { throw badInput("\(key) must be an ISO-8601 time (got \(s))") }
    return date
}

func day(_ d: [String: Any], _ key: String) throws -> DateComponents? {
    guard let s = str(d, key) else { return nil }
    guard let comps = parseDay(s) else { throw badInput("\(key) must be a date like 2026-10-12 (got \(s))") }
    return comps
}

// MARK: - access

func statusName(_ s: EKAuthorizationStatus) -> String {
    switch s.rawValue {
    case 0: return "notDetermined"
    case 1: return "restricted"
    case 2: return "denied"
    case 3: return "fullAccess"
    case 4: return "writeOnly"
    default: return "unknown"
    }
}

func accessStatus(_ type: EKEntityType) -> String {
    statusName(EKEventStore.authorizationStatus(for: type))
}

/// Checked before any store is made; never requests.
func requireAccess(_ type: EKEntityType, allowWriteOnly: Bool = false) throws {
    let s = accessStatus(type)
    if s == "fullAccess" || (allowWriteOnly && s == "writeOnly") { return }
    let entity = type == .event ? "event" : "reminder"
    if s == "notDetermined" {
        throw failure("access", "macOS hasn't been asked for \(type == .event ? "calendar" : "Reminders") access yet — "
                      + "at this Mac run backend/scripts/eventkit_request_access.sh and click Allow.",
                      exit: 4, extra: ["entity": entity, "status": s])
    }
    if type == .event {
        throw failure("access", "I can't change the calendar — macOS hasn't given me access (\(s)). On this Mac: "
                      + "System Settings → Privacy & Security → Calendars → the backend's python → Full Access.",
                      exit: 4, extra: ["entity": "event", "status": s])
    }
    throw failure("access", "I can't change Reminders — macOS hasn't given me access (\(s)). On this Mac: "
                  + "System Settings → Privacy & Security → Reminders → turn on the backend's python.",
                  exit: 4, extra: ["entity": "reminder", "status": s])
}

// MARK: - calendars and lists

func defaultCalendar(_ store: EKEventStore, _ type: EKEntityType) -> EKCalendar? {
    type == .event ? store.defaultCalendarForNewEvents : store.defaultCalendarForNewReminders()
}

func calendarList(_ store: EKEventStore, _ type: EKEntityType) -> [String: Any] {
    let def = defaultCalendar(store, type)
    let items: [[String: Any]] = store.calendars(for: type).map { c in
        ["id": c.calendarIdentifier, "title": c.title, "source": c.source?.title ?? "",
         "writable": c.allowsContentModifications, "default": c.calendarIdentifier == def?.calendarIdentifier]
    }
    return ["ok": true, "default": def?.calendarIdentifier ?? NSNull(), "items": items]
}

func describe(_ c: EKCalendar) -> String {
    let source = c.source?.title ?? ""
    return source.isEmpty ? c.title : "\(c.title) (\(source))"
}

/// A calendar or list by name — exact (case-insensitive) first, then a
/// unique contains — or the default. Honest errors name the writable ones.
func pickCalendar(_ store: EKEventStore, _ type: EKEntityType, _ name: String?) throws -> EKCalendar {
    let kind = type == .event ? "calendar" : "list"
    let all = store.calendars(for: type)
    let writable = all.filter { $0.allowsContentModifications }.map { $0.title }.joined(separator: ", ")
    guard let name else {
        guard let def = defaultCalendar(store, type) else {
            throw failure("not_found", "there's no default \(kind) — say which: \(writable)")
        }
        guard def.allowsContentModifications else {
            throw failure("read_only", "the default \(kind) “\(def.title)” is read-only — say which: \(writable)")
        }
        return def
    }
    let lower = name.lowercased()
    var matches = all.filter { $0.title.lowercased() == lower }
    if matches.isEmpty { matches = all.filter { $0.title.lowercased().contains(lower) } }
    if matches.count > 1 {
        let open = matches.filter { $0.allowsContentModifications }
        if open.count == 1 { return open[0] }
        throw failure("ambiguous", "more than one \(kind) matches “\(name)”: "
                      + matches.map(describe).joined(separator: ", ") + " — say which")
    }
    guard let c = matches.first else {
        throw failure("not_found", "no \(kind) is called “\(name)” — the ones I can write to: \(writable)")
    }
    guard c.allowsContentModifications else {
        throw failure("read_only", "“\(c.title)” is read-only — the ones I can write to: \(writable)")
    }
    return c
}

// MARK: - events

func emailOf(_ p: EKParticipant) -> String? {
    let s = p.url.absoluteString
    if s.lowercased().hasPrefix("mailto:") { return String(s.dropFirst(7)) }
    return s.isEmpty ? nil : s
}

func eventDict(_ e: EKEvent) -> [String: Any] {
    ["id": e.calendarItemIdentifier, "external_id": e.calendarItemExternalIdentifier ?? NSNull(),
     "calendar": e.calendar?.title ?? "", "title": e.title ?? "", "start": iso(e.startDate), "end": iso(e.endDate),
     "all_day": e.isAllDay, "recurring": e.hasRecurrenceRules,
     "attendees": (e.attendees ?? []).compactMap(emailOf)]
}

/// Whether this EventKit can attach invitees at all. There is no public API:
/// this looks up the private class and selectors only — never KVC first,
/// since Swift cannot catch NSUnknownKeyException.
func attendeeCapability() -> Bool {
    guard let cls = NSClassFromString("EKAttendee") as? NSObject.Type else { return false }
    let canAttach = EKEvent.instancesRespond(to: Selector(("setAttendees:")))
        || EKEvent.instancesRespond(to: Selector(("addAttendee:")))
    return canAttach && cls.instancesRespond(to: Selector(("setEmailAddress:")))
}

/// Best effort: attaches invitees through the private API when it exists;
/// otherwise every address comes back as failed, honestly.
func invite(_ event: EKEvent, _ emails: [String]) -> (attached: [String], failed: [[String: String]]) {
    if emails.isEmpty { return ([], []) }
    guard attendeeCapability(), let cls = NSClassFromString("EKAttendee") as? NSObject.Type else {
        return ([], emails.map { ["email": $0, "reason": "Calendar's API here can't add invitees"] })
    }
    var made: [NSObject] = []
    var attached: [String] = []
    var failed: [[String: String]] = []
    for email in emails {
        guard email.contains("@"), !email.contains(" ") else {
            failed.append(["email": email, "reason": "that isn't an email address"])
            continue
        }
        let attendee = cls.init()
        attendee.setValue(email, forKey: "emailAddress")
        made.append(attendee)
        attached.append(email)
    }
    if made.isEmpty { return ([], failed) }
    if event.responds(to: Selector(("addAttendee:"))) {
        for a in made { _ = event.perform(Selector(("addAttendee:")), with: a) }
    } else {
        let existing: [NSObject] = (event.attendees ?? []).map { $0 as NSObject }
        event.setValue(existing + made, forKey: "attendees")
    }
    return (attached, failed)
}

/// The saved event's invitees as Calendar kept them: read back through a
/// fresh store after the commit — never off the object invite() just set,
/// which would always still have them (review 2026-10-11). nil when the
/// event can't be read back.
func persistedAttendees(_ event: EKEvent) -> Set<String>? {
    let fresh = EKEventStore()
    let id = event.calendarItemIdentifier
    var found: EKEvent? = nil
    if let start = event.startDate, let end = event.endDate {
        let cals = event.calendar.flatMap { fresh.calendar(withIdentifier: $0.calendarIdentifier) }.map { [$0] }
        let pred = fresh.predicateForEvents(withStart: start.addingTimeInterval(-60), end: end.addingTimeInterval(60),
                                            calendars: cals)
        found = fresh.events(matching: pred).filter { $0.calendarItemIdentifier == id }
            .min(by: { abs($0.startDate.timeIntervalSince(start)) < abs($1.startDate.timeIntervalSince(start)) })
    }
    if found == nil { found = fresh.calendarItem(withIdentifier: id) as? EKEvent }
    guard let saved = found else { return nil }
    return Set((saved.attendees ?? []).compactMap(emailOf).map { $0.lowercased() })
}

/// After the save: an invitee counts as saved only if the read-back event
/// still has them; one it lost failed; with no read-back, unverified.
/// Whether Calendar's server then sends the invitation isn't visible here.
func classifyInvites(_ attached: [String], _ failed: [[String: String]], persisted: Set<String>?)
    -> (saved: [String], unverified: [String], failed: [[String: String]]) {
    guard let kept = persisted else { return ([], attached, failed) }
    let saved = attached.filter { kept.contains($0.lowercased()) }
    let dropped = attached.filter { !kept.contains($0.lowercased()) }
        .map { ["email": $0, "reason": "Calendar didn't keep the invitee"] }
    return (saved, [], failed + dropped)
}

func inviteReply(_ event: EKEvent, _ attached: [String], _ failed: [[String: String]]) -> [String: Any] {
    let (saved, unverified, notSaved) = classifyInvites(attached, failed,
                                                        persisted: attached.isEmpty ? [] : persistedAttendees(event))
    return ["attendees_saved": saved, "attendees_unverified": unverified, "attendees_failed": notSaved]
}

func save(_ store: EKEventStore, _ event: EKEvent, span: EKSpan, withInvites: Bool) throws {
    do {
        try store.save(event, span: span, commit: true)
    } catch {
        let why = error.localizedDescription
        if withInvites {
            throw failure("save_failed", "Calendar wouldn't save it with invitees (\(why)) — try again without "
                          + "attendees, then email them the details")
        }
        throw failure("save_failed", "Calendar wouldn't save it (\(why))")
    }
}

/// A repeating event needs its occurrence: without one EventKit hands back
/// the series' master, and "this" occurrence of it is the first — maybe
/// years back (review 2026-10-11).
func needsOccurrence(recurring: Bool, occurrence: Date?) -> Bool {
    recurring && occurrence == nil
}

/// The event an id names; a repeating one only with its occurrence (else
/// the helper asks which, never guesses).
func findEvent(_ store: EKEventStore, id: String, occurrence: Date?, title: String?) throws -> EKEvent {
    let ev = try locateEvent(store, id: id, occurrence: occurrence, title: title)
    if needsOccurrence(recurring: ev.hasRecurrenceRules, occurrence: occurrence) {
        throw failure("ambiguous", "“\(ev.title ?? "that event")” repeats — read the calendar again and use the id "
                      + "of the one you mean (span=future reaches the ones after it too)")
    }
    return ev
}

/// Finds an event by EventKit's identifier, an occurrence for a recurring one,
/// then by the external identifier or a unique title around the occurrence.
func locateEvent(_ store: EKEventStore, id: String, occurrence: Date?, title: String?) throws -> EKEvent {
    if let ev = store.calendarItem(withIdentifier: id) as? EKEvent {
        guard let occ = occurrence, ev.hasRecurrenceRules else { return ev }
        let cals = ev.calendar.map { [$0] }
        let pred = store.predicateForEvents(withStart: occ.addingTimeInterval(-60), end: occ.addingTimeInterval(60),
                                            calendars: cals)
        if let o = store.events(matching: pred).first(where: {
            $0.calendarItemIdentifier == id && abs(($0.occurrenceDate ?? $0.startDate).timeIntervalSince(occ)) < 1
        }) {
            return o
        }
    }
    let center = occurrence ?? Date()
    let pred = store.predicateForEvents(withStart: center.addingTimeInterval(-86_400),
                                        end: center.addingTimeInterval(86_400), calendars: nil)
    let around = store.events(matching: pred)
    let byId = around.filter { $0.calendarItemIdentifier == id || $0.calendarItemExternalIdentifier == id }
    if let best = byId.min(by: {
        abs($0.startDate.timeIntervalSince(center)) < abs($1.startDate.timeIntervalSince(center))
    }) {
        return best
    }
    if let title {
        let lower = title.lowercased()
        let named = around.filter { ($0.title ?? "").lowercased() == lower }
        let atTime = occurrence.map { occ in named.filter { abs($0.startDate.timeIntervalSince(occ)) < 1 } } ?? []
        if atTime.count == 1 { return atTime[0] }
        if named.count == 1 { return named[0] }
        if named.count > 1 {
            throw failure("ambiguous", "more than one event called “\(title)” is around then — read the calendar "
                          + "again and use its id")
        }
    }
    throw failure("not_found", "I couldn't find that event on the calendar — read the calendar again for its id")
}

func eventTimes(_ event: EKEvent, _ d: [String: Any], tz: TimeZone, isNew: Bool) throws -> Bool {
    if let first = try day(d, "date") {
        let last = try day(d, "end_date") ?? first
        guard let start = midnight(first, tz), let end = midnight(last, tz) else { throw badInput("bad date") }
        guard end >= start else { throw badInput("end_date must be on or after date") }
        event.isAllDay = true
        event.timeZone = nil
        event.startDate = start
        event.endDate = end
        return true
    }
    if let start = try instant(d, "start") {
        let wasAllDay = event.isAllDay
        var end = try instant(d, "end")
        if end == nil {
            if isNew || wasAllDay {
                end = start.addingTimeInterval(3600)
            } else {
                end = start.addingTimeInterval(event.endDate.timeIntervalSince(event.startDate))
            }
        }
        guard let end, end > start else { throw badInput("end must be after start") }
        event.isAllDay = false
        event.timeZone = tz
        event.startDate = start
        event.endDate = end
        return true
    }
    if let end = try instant(d, "end") {
        guard !isNew else { throw badInput("event_add needs start (ISO-8601) or date (YYYY-MM-DD)") }
        guard end > event.startDate else { throw badInput("end must be after the event's start") }
        event.endDate = end
        return true
    }
    if isNew { throw badInput("event_add needs start (ISO-8601) or date (YYYY-MM-DD)") }
    return false
}

func eventAdd(_ d: [String: Any]) throws -> [String: Any] {
    guard let title = str(d, "title") else { throw badInput("event_add needs a title") }
    let tz = try zone(d)
    try requireAccess(.event, allowWriteOnly: true)
    let store = EKEventStore()
    let event = EKEvent(eventStore: store)
    event.title = title
    _ = try eventTimes(event, d, tz: tz, isNew: true)
    if accessStatus(.event) == "writeOnly" {
        if str(d, "calendar") != nil {
            throw failure("access", "I can only add to the default calendar — macOS gave me write-only access. On "
                          + "this Mac: System Settings → Privacy & Security → Calendars → the backend's python → Full Access.",
                          exit: 4, extra: ["entity": "event", "status": "writeOnly"])
        }
        guard let def = store.defaultCalendarForNewEvents else { throw failure("not_found", "there's no default calendar") }
        event.calendar = def
    } else {
        event.calendar = try pickCalendar(store, .event, str(d, "calendar"))
    }
    if let location = str(d, "location") { event.location = location }
    if let notes = str(d, "notes") { event.notes = notes }
    let (attached, failed) = invite(event, strList(d, "attendees"))
    try save(store, event, span: .thisEvent, withInvites: !attached.isEmpty)
    var reply: [String: Any] = ["ok": true, "event": eventDict(event)]
    reply.merge(inviteReply(event, attached, failed)) { _, new in new }
    return reply
}

func span(_ d: [String: Any]) throws -> EKSpan {
    switch str(d, "span") ?? "this" {
    case "this": return .thisEvent
    case "future": return .futureEvents
    default: throw badInput("span is this or future")
    }
}

func eventUpdate(_ d: [String: Any]) throws -> [String: Any] {
    guard let id = str(d, "id") else { throw badInput("event_update needs id") }
    let tz = try zone(d)
    let occurrence = try instant(d, "occurrence")
    let which = try span(d)
    try requireAccess(.event)
    let store = EKEventStore()
    let event = try findEvent(store, id: id, occurrence: occurrence, title: str(d, "hint_title"))
    guard event.calendar?.allowsContentModifications ?? false else {
        throw failure("read_only", "“\(event.title ?? "that event")” is on a read-only calendar (\(event.calendar?.title ?? "?"))")
    }
    let before = eventDict(event)
    var changed: [String] = []
    if let title = str(d, "title") { event.title = title; changed.append("title") }
    if try eventTimes(event, d, tz: tz, isNew: false) { changed.append("time") }
    if d["location"] is String { event.location = str(d, "location"); changed.append("location") }
    if d["notes"] is String { event.notes = str(d, "notes"); changed.append("notes") }
    if let name = str(d, "calendar") { event.calendar = try pickCalendar(store, .event, name); changed.append("calendar") }
    let (attached, failed) = invite(event, strList(d, "attendees"))
    if !strList(d, "attendees").isEmpty { changed.append("attendees") }
    guard !changed.isEmpty else { throw badInput("event_update needs something to change") }
    try save(store, event, span: which, withInvites: !attached.isEmpty)
    var reply: [String: Any] = ["ok": true, "event": eventDict(event), "before": before, "changed": changed]
    reply.merge(inviteReply(event, attached, failed)) { _, new in new }
    return reply
}

func eventDelete(_ d: [String: Any]) throws -> [String: Any] {
    guard let id = str(d, "id") else { throw badInput("event_delete needs id") }
    let occurrence = try instant(d, "occurrence")
    let which = try span(d)
    try requireAccess(.event)
    let store = EKEventStore()
    let event = try findEvent(store, id: id, occurrence: occurrence, title: str(d, "hint_title"))
    guard event.calendar?.allowsContentModifications ?? false else {
        throw failure("read_only", "“\(event.title ?? "that event")” is on a read-only calendar (\(event.calendar?.title ?? "?"))")
    }
    let gone = eventDict(event)
    do {
        try store.remove(event, span: which, commit: true)
    } catch {
        throw failure("save_failed", "Calendar wouldn't delete it (\(error.localizedDescription))")
    }
    return ["ok": true, "event": gone]
}

// MARK: - reminders

func reminderDict(_ r: EKReminder) -> [String: Any] {
    var due: Any = NSNull()
    if let c = r.dueDateComponents {
        if c.hour != nil, let date = gregorian(c.timeZone ?? TimeZone.current).date(from: c) {
            due = iso(date)
        } else if let y = c.year, let m = c.month, let dd = c.day {
            due = String(format: "%04d-%02d-%02d", y, m, dd)
        }
    }
    return ["id": r.calendarItemIdentifier, "external_id": r.calendarItemExternalIdentifier ?? NSNull(),
            "title": r.title ?? "", "list": r.calendar?.title ?? "", "due": due, "completed": r.isCompleted]
}

func incompleteReminders(_ store: EKEventStore, _ calendars: [EKCalendar]?) async -> [EKReminder] {
    await withCheckedContinuation { cont in
        let pred = store.predicateForIncompleteReminders(withDueDateStarting: nil, ending: nil, calendars: calendars)
        store.fetchReminders(matching: pred) { found in cont.resume(returning: found ?? []) }
    }
}

func findReminder(_ store: EKEventStore, ids: [String], title: String?, list: String?) async throws -> EKReminder {
    for id in ids {
        if let r = store.calendarItem(withIdentifier: id) as? EKReminder { return r }
    }
    var calendars: [EKCalendar]? = nil
    if let list {
        let named = store.calendars(for: .reminder).filter { $0.title.lowercased() == list.lowercased() }
        if !named.isEmpty { calendars = named }
    }
    let open = await incompleteReminders(store, calendars)
    let wanted = Set(ids.map { $0.lowercased() })
    if let r = open.first(where: {
        wanted.contains($0.calendarItemIdentifier.lowercased())
            || ($0.calendarItemExternalIdentifier.map { wanted.contains($0.lowercased()) } ?? false)
    }) {
        return r
    }
    if let title {
        let named = open.filter { ($0.title ?? "").lowercased() == title.lowercased() }
        if named.count == 1 { return named[0] }
        if named.count > 1 {
            let lists = named.map { "“\($0.title ?? "")” in \($0.calendar?.title ?? "?")" }.joined(separator: ", ")
            throw failure("ambiguous", "more than one open reminder matches: \(lists) — read the reminders again "
                          + "and use its id")
        }
    }
    throw failure("not_found", "I couldn't find that reminder among the open ones — read the reminders again for its id")
}

func reminderAdd(_ d: [String: Any]) throws -> [String: Any] {
    guard let title = str(d, "title") else { throw badInput("reminder_add needs a title") }
    let tz = try zone(d)
    let due = try instant(d, "due")
    let dueDay = try day(d, "due_date")
    try requireAccess(.reminder)
    let store = EKEventStore()
    let reminder = EKReminder(eventStore: store)
    reminder.title = title
    reminder.calendar = try pickCalendar(store, .reminder, str(d, "list"))
    if let notes = str(d, "notes") { reminder.notes = notes }
    if let due {
        var comps = gregorian(tz).dateComponents([.year, .month, .day, .hour, .minute], from: due)
        comps.timeZone = tz
        reminder.dueDateComponents = comps
        reminder.addAlarm(EKAlarm(absoluteDate: due))
    } else if let dueDay {
        reminder.dueDateComponents = DateComponents(year: dueDay.year, month: dueDay.month, day: dueDay.day)
    }
    do {
        try store.save(reminder, commit: true)
    } catch {
        throw failure("save_failed", "Reminders wouldn't save it (\(error.localizedDescription))")
    }
    return ["ok": true, "reminder": reminderDict(reminder)]
}

func reminderChange(_ d: [String: Any], complete: Bool) async throws -> [String: Any] {
    let ids = strList(d, "ids")
    guard !ids.isEmpty || str(d, "title") != nil else { throw badInput("needs ids (or a title)") }
    try requireAccess(.reminder)
    let store = EKEventStore()
    let reminder = try await findReminder(store, ids: ids, title: str(d, "title"), list: str(d, "list"))
    guard reminder.calendar?.allowsContentModifications ?? false else {
        throw failure("read_only", "“\(reminder.title ?? "that reminder")” is in a read-only list")
    }
    let info = reminderDict(reminder)
    do {
        if complete {
            reminder.isCompleted = true
            try store.save(reminder, commit: true)
        } else {
            try store.remove(reminder, commit: true)
        }
    } catch {
        throw failure("save_failed", "Reminders wouldn't \(complete ? "check it off" : "delete it") "
                      + "(\(error.localizedDescription))")
    }
    return ["ok": true, "reminder": info]
}

// MARK: - selftest

func selftest() -> [String: Any] {
    var checks: [String: Bool] = [:]
    let sample = #"{"title":"Dentist","attendees":["a@b.com"," "],"timezone":"America/Chicago"}"#
    if let obj = try? JSONSerialization.jsonObject(with: Data(sample.utf8)) as? [String: Any] {
        checks["json"] = str(obj, "title") == "Dentist" && strList(obj, "attendees") == ["a@b.com"]
            && (try? zone(obj))?.identifier == "America/Chicago"
    } else {
        checks["json"] = false
    }
    let utc = parseInstant("2026-10-12T14:00:00Z")
    checks["iso"] = utc != nil && utc == parseInstant("2026-10-12T09:00:00-05:00")
        && parseInstant("2026-10-12T14:00:00.250Z") != nil && parseInstant("tomorrow") == nil
    let chicago = TimeZone(identifier: "America/Chicago")!
    checks["date"] = parseDay("2026-10-12").flatMap { midnight($0, chicago) } == parseInstant("2026-10-12T00:00:00-05:00")
        && parseDay("2026-02-30") == nil && parseDay("2026-1-2") == nil
    let kept = classifyInvites(["a@b.com", "C@d.com"], [["email": "x", "reason": "r"]], persisted: ["a@b.com", "c@d.com"])
    let lost = classifyInvites(["a@b.com", "c@d.com"], [], persisted: ["a@b.com"])
    let unread = classifyInvites(["a@b.com"], [], persisted: nil)
    checks["invites"] = kept.saved == ["a@b.com", "C@d.com"] && kept.unverified.isEmpty && kept.failed.count == 1
        && lost.saved == ["a@b.com"] && lost.failed.map { $0["email"] ?? "" } == ["c@d.com"]
        && unread.saved.isEmpty && unread.unverified == ["a@b.com"] && unread.failed.isEmpty
    checks["occurrence"] = needsOccurrence(recurring: true, occurrence: nil)
        && !needsOccurrence(recurring: true, occurrence: Date()) && !needsOccurrence(recurring: false, occurrence: nil)
    let info = Bundle.main.infoDictionary ?? [:]
    checks["plist"] = info["NSCalendarsFullAccessUsageDescription"] != nil
        && info["NSRemindersFullAccessUsageDescription"] != nil
    let ok = checks.values.allSatisfy { $0 }
    return ["ok": ok, "checks": checks, "attendees_api": attendeeCapability(),
            "bundle_id": Bundle.main.bundleIdentifier ?? NSNull()]
}

// MARK: - main

func run(_ command: String, _ rest: [String]) async throws -> [String: Any] {
    switch command {
    case "selftest":
        return selftest()
    case "access":
        return ["ok": true, "calendar": accessStatus(.event), "reminders": accessStatus(.reminder)]
    case "request":
        let store = EKEventStore()
        let granted: Bool
        switch rest.first ?? "" {
        case "events": granted = try await store.requestFullAccessToEvents()
        case "reminders": granted = try await store.requestFullAccessToReminders()
        default: throw failure("usage", "request events|reminders", exit: 2)
        }
        return ["ok": granted, "granted": granted, "calendar": accessStatus(.event),
                "reminders": accessStatus(.reminder)]
    case "calendars":
        try requireAccess(.event)
        return calendarList(EKEventStore(), .event)
    case "lists":
        try requireAccess(.reminder)
        return calendarList(EKEventStore(), .reminder)
    case "event_add":
        return try eventAdd(try readInput())
    case "event_update":
        return try eventUpdate(try readInput())
    case "event_delete":
        return try eventDelete(try readInput())
    case "reminder_add":
        return try reminderAdd(try readInput())
    case "reminder_complete":
        return try await reminderChange(try readInput(), complete: true)
    case "reminder_delete":
        return try await reminderChange(try readInput(), complete: false)
    default:
        throw failure("usage", "unknown command \(command) — access | request events|reminders | calendars | lists | "
                      + "event_add | event_update | event_delete | reminder_add | reminder_complete | "
                      + "reminder_delete | selftest", exit: 2)
    }
}

@main
struct EdwardEventKit {
    static func main() async {
        let args = CommandLine.arguments
        guard args.count >= 2 else {
            emit(["ok": false, "code": "usage", "error": "usage: edward-eventkit <command> (input as JSON on stdin)"])
            exit(2)
        }
        do {
            let reply = try await run(args[1], Array(args.dropFirst(2)))
            emit(reply)
            exit((reply["ok"] as? Bool) == true ? 0 : 3)
        } catch let f as Failure {
            emit(f.body)
            exit(f.exitCode)
        } catch {
            emit(["ok": false, "code": "failed", "error": "\(error.localizedDescription)"])
            exit(3)
        }
    }
}
