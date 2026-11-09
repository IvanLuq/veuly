// How the app shows itself on the Mac while it works, outside its window: a menu bar item while a class records or a
// note is being written (the time, Mute, Finish, Show Veuly; "Writing the note… 40%"), a badge on the Dock icon
// while recording, a notification when a note is ready or could not be written, and opening at login. What goes on
// comes from the page (VeulyMac.watch in web/native.js); everything it shows is a mac_* text of web/i18n.js.
//
// Test mode puts nothing on the user's screen and asks macOS nothing: the menu bar item is a menu that is never shown
// (the test reads and presses it), notifications are written down instead of shown, and the login item is a stand-in.
import AppKit
import ServiceManagement
@preconcurrency import UserNotifications

/// What the page says goes on (VeulyMac.watch): a class recording in this window, the classes being written, and the
/// ones that finished or failed since the app last asked.
struct PageActivity {
    struct Recording: Equatable {
        var slug = "", cid = "", title = "", project = false, seconds = 0.0, muted = false, finishing = false
        var headsups = 0        // heads-ups waiting in the pane: only the number leaves the window, a shared screen may show this
        /// What its menu shows, apart from the time (which changes every second and is drawn in place).
        var menu: Recording { var r = self; r.seconds = 0; return r }
    }
    struct Writing: Equatable { var slug = "", cid = "", title = "", project = false, step = "", progress = -1 }
    struct News { var slug = "", cid = "", title = "", body = "", ok = false, here = false }
    var recording: Recording?
    var writing: [Writing] = []
    var news: [News] = []

    init() {}

    init(_ value: Any?) {
        guard let all = value as? [String: Any] else { return }
        func text(_ d: [String: Any], _ k: String) -> String { d[k] as? String ?? "" }
        if let r = all["recording"] as? [String: Any] {
            recording = Recording(slug: text(r, "slug"), cid: text(r, "cid"), title: text(r, "title"), project: r["project"] as? Bool ?? false,
                                  seconds: r["seconds"] as? Double ?? 0, muted: r["muted"] as? Bool ?? false, finishing: r["finishing"] as? Bool ?? false,
                                  headsups: (r["headsups"] as? NSNumber)?.intValue ?? 0)
        }
        writing = (all["writing"] as? [[String: Any]] ?? []).map {
            Writing(slug: text($0, "slug"), cid: text($0, "cid"), title: text($0, "title"), project: $0["project"] as? Bool ?? false,
                    step: text($0, "step"), progress: ($0["progress"] as? NSNumber)?.intValue ?? -1)
        }
        news = (all["news"] as? [[String: Any]] ?? []).map {
            News(slug: text($0, "slug"), cid: text($0, "cid"), title: text($0, "title"), body: text($0, "body"),
                 ok: $0["ok"] as? Bool ?? false, here: $0["here"] as? Bool ?? false)
        }
    }
}

/// The menu bar item, and the Dock's badge. Shown only while something goes on; gone otherwise.
@MainActor
final class StatusMenu: NSObject {
    private let texts: Texts
    private var item: NSStatusItem?                // test mode never makes one: only the menu below exists
    let menu = NSMenu()
    private var now = PageActivity()
    private var counted = (seconds: 0.0, at: 0.0)     // the recording's seconds the page gave, and the uptime then
    private var clock: Timer?
    private(set) var title = ""                       // what the item shows next to its icon (the test reads it)
    private(set) var rebuilds = 0                     // how many times its menu was made again (the test reads it)
    var onMute: (() -> Void)?
    var onFinish: (() -> Void)?
    var onShow: (() -> Void)?

    init(texts: Texts) {
        self.texts = texts
        super.init()
        menu.autoenablesItems = false
    }

    var shown: Bool { now.recording != nil || !now.writing.isEmpty }
    var badge: String? { NSApp.dockTile.badgeLabel }

    func update(_ activity: PageActivity) {
        let before = now
        now = activity
        if let r = activity.recording {
            // The page's clock (performance.now) stops while the Mac sleeps, as the recording does: so does the uptime.
            counted = (r.seconds, ProcessInfo.processInfo.systemUptime)
        }
        let badge: String? = activity.recording != nil ? "●" : nil
        if NSApp.dockTile.badgeLabel != badge { NSApp.dockTile.badgeLabel = badge }
        // The menu is made again only when what it offers changes: never for the time alone, so a menu the user has
        // open stays as it is under the pointer.
        if before.recording?.menu != activity.recording?.menu || before.writing != activity.writing || menu.items.isEmpty { rebuild() }
        draw()
        if activity.recording != nil, !(activity.recording?.finishing ?? true), clock == nil {
            clock = Main.after(1, repeats: true) { [weak self] in self?.draw() }
        } else if activity.recording == nil || activity.recording?.finishing == true {
            clock?.invalidate()
            clock = nil
        }
    }

    /// Nothing goes on any more that the app can see (the page went, the server stopped).
    func clear() { update(PageActivity()) }

    private var seconds: Double {
        guard let r = now.recording else { return 0 }
        return r.finishing ? r.seconds : counted.seconds + ProcessInfo.processInfo.systemUptime - counted.at
    }

    static func clockText(_ seconds: Double) -> String {
        let s = seconds.isFinite ? max(0, Int(min(seconds, 1e7))) : 0      // whatever the page sent, never a crash
        return s >= 3600 ? String(format: "%d:%02d:%02d", s / 3600, s % 3600 / 60, s % 60) : String(format: "%d:%02d", s / 60, s % 60)
    }

    private func writingLine(_ w: PageActivity.Writing) -> String {
        let doing = w.progress >= 0 ? texts("mac_status_writing_pct", work: w.project, ["pct": String(w.progress)])
            : texts("mac_status_writing", work: w.project)
        return w.title.isEmpty ? doing : "\(w.title) · \(doing)"
    }

    private func draw() {
        if let r = now.recording {
            title = r.finishing ? texts("saving") : Self.clockText(seconds) + (r.headsups > 0 ? " · \(r.headsups)" : "")
        } else if let w = now.writing.first {
            title = w.progress >= 0 ? "\(w.progress)%" : ""
        } else {
            title = ""
        }
        if let first = menu.items.first, first.identifier?.rawValue == "status-recording", let r = now.recording {
            first.title = texts("mac_status_recording", work: r.project, ["title": r.title.isEmpty ? "Veuly" : r.title, "time": Self.clockText(seconds)])
        }
        guard !TestMode.on else { return }
        if shown, item == nil {
            item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
            item?.menu = menu
        } else if !shown, let gone = item {
            NSStatusBar.system.removeStatusItem(gone)
            item = nil
        }
        guard let button = item?.button else { return }
        let text = NSMutableAttributedString()
        if now.recording != nil {
            text.append(NSAttributedString(string: "●", attributes: [.foregroundColor: NSColor.systemRed, .font: NSFont.menuBarFont(ofSize: 0)]))
            button.image = nil
        } else {
            button.image = NSImage(systemSymbolName: "doc.text", accessibilityDescription: texts("mac_status_writing", work: now.writing.first?.project ?? false))
            button.imagePosition = .imageLeading
        }
        if !title.isEmpty {
            text.append(NSAttributedString(string: (text.length > 0 || button.image != nil ? " " : "") + title,
                                           attributes: [.font: NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .regular)]))
        }
        button.attributedTitle = text
    }

    private func rebuild() {
        rebuilds += 1
        menu.removeAllItems()
        func add(_ title: String, id: String, enabled: Bool = true, action: Selector? = nil) {
            let i = NSMenuItem(title: title, action: action, keyEquivalent: "")
            i.identifier = NSUserInterfaceItemIdentifier(id)
            i.target = action == nil ? nil : self
            i.isEnabled = enabled && action != nil
            menu.addItem(i)
        }
        if let r = now.recording {
            add(texts("mac_status_recording", work: r.project, ["title": r.title.isEmpty ? "Veuly" : r.title, "time": Self.clockText(seconds)]), id: "status-recording")
            if r.headsups > 0 { add(texts("mac_status_headsups", work: r.project, ["count": String(r.headsups)]), id: "status-headsups", action: #selector(show(_:))) }
            add(texts(r.muted ? "unmute" : "mute"), id: "status-mute", enabled: !r.finishing, action: #selector(mute(_:)))
            add(texts("mac_status_finish", work: r.project), id: "status-finish", enabled: !r.finishing, action: #selector(finish(_:)))
            menu.addItem(.separator())
        }
        for (n, w) in now.writing.enumerated() { add(writingLine(w), id: "status-writing-\(n)") }
        if !now.writing.isEmpty { menu.addItem(.separator()) }
        add(texts("mac_status_show"), id: "status-show", action: #selector(show(_:)))
    }

    @objc private func mute(_ sender: Any?) { onMute?() }
    @objc private func finish(_ sender: Any?) { onFinish?() }
    @objc private func show(_ sender: Any?) { onShow?() }

    /// Test mode: what the item shows, and its menu.
    var described: [String: Any] {
        ["shown": shown, "title": title, "badge": badge ?? "", "rebuilds": rebuilds,
         "items": menu.items.filter { !$0.isSeparatorItem }.map { ["id": $0.identifier?.rawValue ?? "", "title": $0.title, "enabled": $0.isEnabled] }]
    }

    /// Test mode: a click on the item with this identifier.
    func press(_ id: String) -> Bool {
        guard let i = menu.items.first(where: { $0.identifier?.rawValue == id }), i.isEnabled, let action = i.action else { return false }
        NSApp.sendAction(action, to: i.target, from: i)
        return true
    }
}

/// A notification when a note is ready or could not be written. macOS asks the user whether Veuly may notify the
/// first time a class is finished in the app (never at launch); a click on one shows that class.
@MainActor
final class Notifier: NSObject, UNUserNotificationCenterDelegate {
    var onOpen: ((String, String, Bool) -> Void)?       // slug, cid, whether its note is there
    private(set) var asked = 0                          // test mode: how many times the app would have asked
    private(set) var shown: [[String: Any]] = []        // test mode: what would have been shown
    private var askedHere = false

    func start() {
        guard !TestMode.on, Bundle.main.bundleIdentifier != nil else { return }      // (the center needs a bundle)
        UNUserNotificationCenter.current().delegate = self      // asks nothing: clicks on its notifications come here
    }

    /// The first Finish: macOS asks the user once, if it never did.
    func askOnce() {
        guard !askedHere else { return }
        askedHere = true
        if TestMode.on { asked += 1; return }
        guard Bundle.main.bundleIdentifier != nil else { return }
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            guard settings.authorizationStatus == .notDetermined else { return }
            center.requestAuthorization(options: [.alert, .sound]) { granted, _ in Log.write("notifications: \(granted ? "allowed" : "not allowed")") }
        }
    }

    func show(_ news: PageActivity.News) {
        let info: [String: Any] = ["slug": news.slug, "cid": news.cid, "ok": news.ok]
        if TestMode.on {
            shown.append(["title": news.title, "body": news.body, "ok": news.ok, "slug": news.slug, "cid": news.cid])
            return
        }
        guard Bundle.main.bundleIdentifier != nil else { return }
        let content = UNMutableNotificationContent()
        content.title = news.title
        content.body = news.body
        content.userInfo = info
        if !news.ok { content.sound = .default }
        let request = UNNotificationRequest(identifier: "\(news.slug)/\(news.cid)", content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request) { error in if let error { Log.write("notifications: \(error.localizedDescription)") } }
    }

    /// Test mode: a click on the notification shown at `index`.
    func click(_ index: Int) -> Bool {
        guard index >= 0, index < shown.count, let slug = shown[index]["slug"] as? String, let cid = shown[index]["cid"] as? String else { return false }
        onOpen?(slug, cid, shown[index]["ok"] as? Bool ?? false)
        return true
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                            withCompletionHandler completionHandler: @escaping () -> Void) {
        let info = response.notification.request.content.userInfo
        let slug = info["slug"] as? String ?? "", cid = info["cid"] as? String ?? "", ok = info["ok"] as? Bool ?? false
        Main.run { self.onOpen?(slug, cid, ok) }
        completionHandler()
    }

    /// Also while Veuly is in front: the page may be on another class.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                            withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list])
    }
}

/// "Open Veuly at login" (Settings, in the app only): the system's own login item for the app (SMAppService). Test
/// mode keeps a stand-in instead, so a test never adds a login item.
@MainActor
enum LoginItem {
    private static var standIn = false
    private(set) static var openedSettings = 0          // test mode: System Settings would have opened

    /// on: Veuly opens at login; approve: the user still has to allow it in System Settings › General › Login Items.
    static func state() -> [String: Any] {
        if TestMode.on { return ["on": standIn, "approve": false] }
        let status = SMAppService.mainApp.status
        return ["on": status == .enabled || status == .requiresApproval, "approve": status == .requiresApproval]
    }

    static func set(_ on: Bool) throws -> [String: Any] {
        if TestMode.on {
            standIn = on
        } else if on {
            try SMAppService.mainApp.register()
        } else {
            try SMAppService.mainApp.unregister()
        }
        Log.write("login item: \(on ? "on" : "off")")
        return state()
    }

    static func openSettings() {
        if TestMode.on { openedSettings += 1; return }
        SMAppService.openSystemSettingsLoginItems()
    }
}
