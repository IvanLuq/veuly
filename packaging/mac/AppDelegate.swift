// Veuly as a Mac app: one window around the page that app.py serves, a Dock icon that brings it back, a menu bar,
// and the app's own recording of this Mac's sound. The browser way of running Veuly (python3 app.py) is unchanged.
//
// Launch: start app.py (Server), show "starting", load the "Open:" link it prints. Closing the window hides it.
// Quit (Cmd+Q, the menu, logging out): ask the page what would be lost, ask the user if anything would, let the page
// save and send what it has, then stop the server this app started, and what it started.
// A server that stops by itself: the page stops recording and keeps what the server never got; "Start again" starts
// a new server on the same address, and the same page takes its session, sends what it kept and goes on.
import AppKit
import WebKit

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, MenuActions, BridgeOwner {
    let resources = Bundle.main.resourceURL ?? URL(fileURLWithPath: ".")
    lazy var texts = Texts(i18n: resources.appendingPathComponent("app/web/i18n.js"))
    let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
    let bridge = Bridge()
    let sound = SystemSound()
    var browser: Browser!
    var server: Server!
    var control: TestControl?
    private var recorders = 0                       // recorders running in the page
    private var awake: NSObjectProtocol?             // while one records: no App Nap, no idle sleep
    private var lastInput = Date.distantPast         // the user's last click or key press in the app
    private var quitting = false                     // the quit question was answered: the app is on its way out
    private var quitNow = false                      // nothing to ask (the server quit from the page): terminate at once
    private var pingTimer: Timer?
    private var startTimer: Timer?
    private var pingFailures = 0
    private var resumable = false                    // the page from before the server stopped is there to go on with
    private var showingStopped = false               // the window says the server stopped
    private var handedOver: Any?                     // what that page holds, for the page of a server on another port
    lazy var status = StatusMenu(texts: texts)       // the menu bar item and the Dock's badge (Presence.swift)
    let notifier = Notifier()
    private var watchTimer: Timer?
    private var watchTicks = 0
    private var watching = false
    private var writingActivity: NSObjectProtocol?  // while a note is written: no App Nap, so its notification is not late
    private var asleepAt: Date?                      // the Mac went to sleep then

    // MARK: launch

    func applicationWillFinishLaunching(_ notification: Notification) {
        if TestMode.on { NSApp.setActivationPolicy(.accessory) }      // no Dock icon, never in front of the user's work
        texts.onChange = { [weak self] in self?.rebuildMenus() }
        rebuildMenus()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        ProcessInfo.processInfo.disableSuddenTermination()       // quitting must stop the server first
        ProcessInfo.processInfo.disableAutomaticTermination("Veuly runs its library's server")
        Log.write("app: Veuly \(version) starting, macOS \(ProcessInfo.processInfo.operatingSystemVersionString)\(TestMode.on ? ", test mode" : "")")
        bridge.owner = self
        browser = Browser(texts: texts, bridge: bridge, version: version)
        browser.onFirstLoad = { [weak self] in
            self?.startTimer?.invalidate()
            self?.giveOver()
        }
        browser.onLeave = { [weak self] in
            self?.sound.stop()
            self?.pageRecording(false, force: true)
            self?.status.clear()
        }
        browser.onReloadAsked = { [weak self] in self?.reloadPage(nil) }
        NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown, .keyDown]) { [weak self] event in
            self?.lastInput = Date()
            return event
        }
        sound.onBlock = { [weak self] at, t, pcm in self?.browser.call("sound", ["at": at, "t": t, "pcm": pcm]) }
        server = Server(resources: resources)
        server.onLink = { [weak self] link, owned in self?.serverReady(link, owned: owned) }
        server.onExit = { [weak self] clean in self?.serverEnded(clean: clean) }
        if TestMode.on { startControl() }
        startPresence()
        browser.show()
        startServer()
    }

    private func startServer() {
        browser.forget()
        showingStopped = false
        browser.showStatus(title: texts("mac_starting"), busy: true)
        do {
            try server.start()
        } catch Server.Failure.noPython {
            browser.showStatus(title: texts("mac_no_python_title"), body: texts("mac_no_python_body"),
                               buttons: [(texts("mac_menu_quit"), { NSApp.terminate(nil) })])
            return
        } catch {
            serverFailed("\(error)")
            return
        }
        startTimer?.invalidate()
        startTimer = Main.after(45) { [weak self] in
            guard let self, !self.browser.loaded else { return }
            self.serverFailed("no link after 45 s")
        }
    }

    private func serverReady(_ link: URL, owned: Bool) {
        pingTimer?.invalidate()
        pingFailures = 0
        if !owned {          // another copy's server: watch that it is still there
            pingTimer = Main.after(3, repeats: true) { [weak self] in self?.ping() }
        }
        let again = resumable && link.port == browser.port
        let moved = resumable && !again && browser.port > 0
        resumable = false
        if again { resume(link) } else if moved { move(to: link) } else { browser.load(link) }
    }

    /// After "Start again" on another port (the old one was taken meanwhile): the page from before cannot reach the
    /// new server, so it hands over what only it holds, and the new page takes it (giveOver) once it has loaded.
    private func move(to link: URL) {
        var done = false
        let load: (Any?) -> Void = { [weak self] state in
            guard let self, !done else { return }
            done = true
            self.handedOver = state is [String: Any] ? state : nil
            Log.write("app: the server serves another port; the page \(self.handedOver == nil ? "is loaded again" : "hands over what it holds")")
            self.browser.load(link)
        }
        browser.call("handOver", done: load)
        Main.after(5) { load(nil) }
    }

    private func giveOver() {
        guard let state = handedOver else { return }
        handedOver = nil
        browser.call("takeOver", state) { value in
            Log.write("app: the new page \(value is [String: Any] ? "took over the recording and the notes" : "did not take them")")
        }
    }

    /// After "Start again": the page from before takes the new session, saves what was typed and sends what the
    /// recording kept, and shows again as it was. A page that does not answer is loaded afresh.
    private func resume(_ link: URL) {
        var settled = false
        let settle: (Bool) -> Void = { [weak self] resumed in
            guard let self, !settled else { return }
            settled = true
            Log.write("app: the page \(resumed ? "goes on with the new server" : "is loaded again")")
            if resumed {
                self.startTimer?.invalidate()
                self.browser.resumed()
            } else {
                self.browser.load(link)
            }
        }
        browser.call("resume", link.absoluteString) { value in settle(value is [String: Any]) }
        Main.after(30) { settle(false) }
    }

    private func ping() {
        guard let link = server.link, let url = URL(string: "http://127.0.0.1:\(link.port ?? 0)/api/ping") else { return }
        let config = URLSessionConfiguration.ephemeral
        config.connectionProxyDictionary = [:]      // straight to this computer, never through a proxy
        config.timeoutIntervalForRequest = 2
        URLSession(configuration: config).dataTask(with: url) { data, _, _ in
            let alive = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }?["app"] as? String == "transcapp"
            Main.run {
                self.pingFailures = alive ? 0 : self.pingFailures + 1
                guard self.pingFailures == 2 else { return }
                // Gone: quit from this window's page (it shows its "closed" sheet), or stopped some other way.
                self.browser.call("closed") { closed in self.serverEnded(clean: closed as? Bool == true) }
            }
        }.resume()
    }

    /// The server stopped by itself. Cleanly: the page's own Quit button, so the app quits too. Otherwise the
    /// window says so, and offers to start it again.
    private func serverEnded(clean: Bool) {
        if quitting { return }
        pingTimer?.invalidate()
        sound.stop()
        if clean {
            Log.write("app: the server quit from the page; quitting")
            quitNow = true
            NSApp.terminate(nil)
            return
        }
        serverFailed(server.lastLines.suffix(3).joined(separator: " | "))
    }

    private func serverFailed(_ why: String) {
        Log.write("app: the server stopped: \(why)")
        let page = browser.loaded                // a working page: it keeps what it has until a server is back
        browser.forget()
        resumable = resumable || page            // (a start that failed in turn keeps the page from before)
        pageRecording(false, force: true)
        status.clear()
        showStopped(recording: false)
        // The Dock icon bounces until the user comes to the app; the window does not jump in front of their work.
        NSApp.requestUserAttention(.criticalRequest)
        guard page else { return }
        // A recording stops in the page now (nothing records out of sight), and what the server never got stays there.
        browser.call("serverGone") { [weak self] state in
            guard let self, self.showingStopped, let state = state as? [String: Any] else { return }
            if state["recording"] as? Bool == true || (state["unsent"] as? Int ?? 0) > 0 { self.showStopped(recording: true) }
        }
    }

    private func showStopped(recording: Bool) {
        showingStopped = true
        browser.showStatus(title: texts("mac_server_stopped_title"),
                           body: texts(recording ? "mac_server_stopped_recording_body" : "mac_server_stopped_body"),
                           buttons: [(texts("mac_server_restart"), { [weak self] in self?.startServer() }),
                                     (texts("mac_menu_quit"), { NSApp.terminate(nil) })])
    }

    // MARK: the menu bar item, the Dock's badge, notifications, sleep and wake (Presence.swift)

    private func startPresence() {
        notifier.start()
        notifier.onOpen = { [weak self] slug, cid, ok in
            guard let self else { return }
            self.browser.show()
            self.browser.call("open", ["slug": slug, "cid": cid, "tab": ok ? "note" : ""])
        }
        status.onMute = { [weak self] in self?.browser.call("mute") { self?.apply(PageActivity($0)) } }
        status.onFinish = { [weak self] in
            self?.browser.show()
            self?.browser.call("askFinish")
        }
        status.onShow = { [weak self] in
            self?.browser.show()
            self?.browser.call("open")
        }
        // Every second while something goes on (the clock is the item's own), every few seconds otherwise.
        watchTimer = Main.after(1, repeats: true) { [weak self] in self?.watchPage() }
        let center = NSWorkspace.shared.notificationCenter
        center.addObserver(forName: NSWorkspace.willSleepNotification, object: nil, queue: nil) { [weak self] _ in Main.run { self?.willSleep() } }
        center.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: nil) { [weak self] _ in
            Main.run {
                guard let self else { return }
                self.didWake(slept: self.asleepAt.map { Date().timeIntervalSince($0) } ?? 0)
            }
        }
    }

    private func watchPage() {
        watchTicks += 1
        guard browser.loaded, !showingStopped, !quitting else {
            if status.shown { apply(PageActivity()) }
            return
        }
        guard !watching, status.shown || watchTicks % 4 == 0 else { return }
        watching = true
        browser.call("watch") { [weak self] value in
            self?.watching = false
            self?.apply(PageActivity(value))
        }
    }

    private func apply(_ activity: PageActivity) {
        status.update(activity)
        if !activity.writing.isEmpty, writingActivity == nil {
            writingActivity = ProcessInfo.processInfo.beginActivity(options: .userInitiatedAllowingIdleSystemSleep, reason: "Veuly is writing a note")
        } else if activity.writing.isEmpty, let a = writingActivity {
            ProcessInfo.processInfo.endActivity(a)
            writingActivity = nil
        }
        for news in activity.news {
            // The class on screen, in front: the page shows it arrive; anything else is said.
            if news.here, NSApp.isActive, browser.window.isKeyWindow { continue }
            notifier.show(news)
        }
    }

    /// Bridge: the user pressed Finish in the page (the first time, macOS asks whether Veuly may notify).
    func pageFinishing() { notifier.askOnce() }

    /// Bridge: Settings' "Open Veuly at login".
    func pageLogin(_ op: String, on: Bool, reply: @escaping (Any?, String?) -> Void) {
        switch op {
        case "set":
            do { reply(try LoginItem.set(on), nil) } catch {
                Log.write("login item: \(error.localizedDescription)")
                reply(nil, "failed")
            }
        case "settings":
            LoginItem.openSettings()
            reply(true, nil)
        default:
            reply(LoginItem.state(), nil)
        }
    }

    /// Bridge: "Keep in step with a folder": the person's choice, in a panel of the system.
    func pageChoosesFolder(reply: @escaping (Any?, String?) -> Void) {
        browser.chooseFolder { path in reply(path, nil) }
    }

    /// The Mac goes to sleep (the lid closed): the page sends what it recorded so far.
    private func willSleep() {
        asleepAt = Date()
        Log.write("app: the Mac goes to sleep\(recorders > 0 ? " while recording" : "")")
        if browser.loaded { browser.call("sleeping") }
    }

    /// It woke up `slept` seconds later: this Mac's sound is captured afresh (its device may have changed), and the
    /// page starts again what stopped and says how long nothing was recorded.
    private func didWake(slept: Double, done: ((Any?) -> Void)? = nil) {
        asleepAt = nil
        Log.write("app: the Mac woke up after \(Int(slept)) s")
        sound.wake()
        guard browser.loaded else { done?(nil); return }
        browser.call("woke", slept) { [weak self] value in
            if let state = value as? [String: Any], state["recording"] as? Bool == true {
                Log.write("app: the recording goes on after the sleep\(state["reopened"] as? Bool == true ? ", the microphone opened again" : "")")
            }
            self?.watchPage()
            done?(value)
        }
    }

    // MARK: the Dock icon, opening the app again

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        browser.show()
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    // MARK: quitting

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        // Nothing to ask: no page yet (still starting, or it could not start). The page from before a server that
        // stopped is asked, though: it may hold what that server never got.
        if quitNow || server == nil || (!server.running && !browser.loaded && !resumable) { return .terminateNow }
        if quitting { return .terminateLater }
        quitting = true
        var answered = false
        let decide: (Any?) -> Void = { [weak self] value in
            guard let self, !answered else { return }
            answered = true
            self.askBeforeQuitting(value as? [String: Any] ?? [:])
        }
        browser.call("quitState", done: decide)
        // A page that does not answer must not keep the app from quitting.
        Main.after(3) { decide(nil) }
        return .terminateLater
    }

    private func askBeforeQuitting(_ state: [String: Any]) {
        let recording = state["recording"] as? Bool ?? false, unsaved = state["unsaved"] as? Bool ?? false
        let unsent = state["unsent"] as? Bool ?? false        // recorded, and never reached the server (it stopped)
        let busy = (state["busy"] as? Bool ?? false) && server.owned && !showingStopped    // a note this app's server writes
        guard recording || unsent || unsaved || busy else { quitForGood(); return }
        let (title, body) = recording ? ("mac_quit_recording_title", "mac_quit_recording_body")
            : unsent ? ("mac_quit_unsent_title", "mac_quit_unsent_body")
            : unsaved ? ("mac_quit_unsaved_title", "mac_quit_unsaved_body") : ("mac_quit_busy_title", "mac_quit_busy_body")
        browser.show()
        browser.asker.ask(title: texts(title), body: texts(body),
                          buttons: [texts(recording ? "mac_keep_recording" : "cancel"), texts("mac_quit_anyway")], cancel: 0) { [weak self] answer in
            guard let self else { return }
            if answer == 1 {
                self.quitForGood()
            } else {
                self.quitting = false
                NSApp.reply(toApplicationShouldTerminate: false)
            }
        }
    }

    /// The page saves what was typed and sends the recording's last seconds; then the server this app started stops.
    private func quitForGood() {
        Log.write("app: quitting")
        var done = false
        let finish = { [weak self] in
            guard let self, !done else { return }
            done = true
            self.sound.stop()
            self.pageRecording(false, force: true)
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        Main.after(20) { finish() }     // whatever happens, the app ends
        guard !showingStopped else { finish(); return }        // the server is gone: nothing can be saved now
        browser.call("beforeQuit") { [weak self] _ in
            guard let self else { return }
            self.sound.stop()
            guard self.server.owned, self.server.running else { finish(); return }
            self.server.expectExit()
            self.browser.call("quitServer") { _ in self.server.stop(then: finish) }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        sound.stop()
        if server?.owned == true, server.running { server.stop {} }       // closes its stdin at least
        Log.write("app: ended")
    }

    // MARK: menus

    private func rebuildMenus() {
        NSApp.mainMenu = Menus.build(texts, target: self)
    }

    @objc func showAbout(_ sender: Any?) {
        if !TestMode.on { NSApp.activate() }
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: "Veuly", .applicationVersion: version, .version: ""])
    }

    @objc func showSettings(_ sender: Any?) {
        browser.show()
        browser.call("settings")
    }

    @objc func showHelp(_ sender: Any?) {
        browser.show()
        browser.call("info")
    }

    @objc func showMainWindow(_ sender: Any?) { browser.show() }

    /// Reloading a page that records would stop the recording: the page is asked first, as for quitting. Every reload
    /// of the page comes here: View › Reload, and one the page or WebKit starts (Browser's onReloadAsked).
    @objc func reloadPage(_ sender: Any?) {
        guard browser.loaded, let link = server.link else {
            if !server.running { startServer() }
            return
        }
        browser.call("quitState") { [weak self] value in
            guard let self else { return }
            let state = value as? [String: Any] ?? [:]
            let recording = state["recording"] as? Bool ?? false, unsaved = state["unsaved"] as? Bool ?? false
            // What was typed is saved and what was recorded is sent first, as before quitting; then the page loads
            // again with its link (with its token: a reload must not lose the session).
            let reload = {
                var done = false
                let load = { if !done { done = true; self.browser.load(link) } }
                self.browser.call("beforeQuit") { _ in load() }
                Main.after(8) { load() }
            }
            guard recording || unsaved else { reload(); return }
            let (title, body) = recording ? ("mac_reload_recording_title", "mac_reload_recording_body")
                : ("mac_reload_unsaved_title", "mac_reload_unsaved_body")
            self.browser.asker.ask(title: self.texts(title), body: self.texts(body),
                                   buttons: [self.texts("cancel"), self.texts("mac_reload")], cancel: 0) { if $0 == 1 { reload() } }
        }
    }

    @objc func zoomActual(_ sender: Any?) { browser.zoom(nil) }
    @objc func zoomIn(_ sender: Any?) { browser.zoom(0.1) }
    @objc func zoomOut(_ sender: Any?) { browser.zoom(-0.1) }

    // MARK: the page

    func isOwn(_ origin: WKSecurityOrigin) -> Bool { browser.isOwn(origin) }

    func pageLanguage(_ lang: String, work: Bool) { texts.set(lang: lang, work: work) }

    /// While a class records the Mac stays awake and the app is never napped, hidden window or not.
    func pageRecording(_ on: Bool) { pageRecording(on, force: false) }

    private func pageRecording(_ on: Bool, force: Bool) {
        recorders = force ? 0 : max(0, recorders + (on ? 1 : -1))
        if recorders > 0, awake == nil {
            awake = ProcessInfo.processInfo.beginActivity(options: [.userInitiated, .idleSystemSleepDisabled], reason: "Veuly is recording a class")
            Log.write("app: recording, the Mac stays awake")
        } else if recorders == 0, let a = awake {
            ProcessInfo.processInfo.endActivity(a)
            awake = nil
            Log.write("app: not recording")
        }
    }

    /// This Mac's sound starts only right after the user clicked or pressed a key in the app: a page cannot start
    /// recording everything the Mac plays by itself.
    func pageWantsSound(_ on: Bool, reply: @escaping (Any?, String?) -> Void) {
        guard on else {
            sound.stop()
            reply(true, nil)
            return
        }
        // The page asks in the click itself (Recorder.share() runs before any await), so two seconds is plenty.
        guard Date().timeIntervalSince(lastInput) < 2 else {
            Log.write("sound: refused, no click just before")
            reply(nil, "gesture")
            return
        }
        sound.start { error in reply(error == nil ? ["rate": 16000] : nil, error) }
    }

    func pageWantsPrint() { browser.printPage() }

    // MARK: test mode

    private func startControl() {
        guard let token = TestMode.token, TestMode.port > 0 else { Log.write("test mode needs VEULY_TEST_PORT"); exit(2) }
        control = TestControl(port: TestMode.port, token: token) { [weak self] request, reply in
            guard let self else { return }
            self.test(request, reply)
        }
        if control == nil { Log.write("test mode: port \(TestMode.port) is taken"); exit(2) }
        Log.write("test mode: control on 127.0.0.1:\(TestMode.port)")
    }

    private func test(_ request: TestControl.Request, _ reply: @escaping @MainActor (TestControl.Response) -> Void) {
        let body = request.body
        switch (request.method, request.path) {
        case ("GET", "/status"):
            reply(.json([
                "pid": Int(getpid()), "loaded": browser.loaded, "owned": server.owned, "serverRunning": server.running,
                "serverPid": Int(server.pid), "port": server.link?.port ?? 0, "visible": browser.window.isVisible,
                "occlusionVisible": browser.window.occlusionState.contains(.visible), "status": browser.status.shownTitle,
                "recorders": recorders, "awake": awake != nil, "sound": sound.running, "lang": texts.lang, "work": texts.work,
                "menus": NSApp.mainMenu?.items.map(\.title) ?? [], "localizations": Bundle.main.preferredLocalizations,
                "outside": browser.outside, "saved": browser.saved, "printed": browser.printed,
                "asked": browser.asker.asked.map { ["title": $0.title, "body": $0.body, "buttons": $0.buttons, "answer": $0.answer] },
                "zoom": browser.webView.pageZoom, "log": Log.location, "activation": NSApp.activationPolicy().rawValue,
                "statusButtons": browser.status.shownButtons, "frame": NSStringFromRect(browser.window.frame),
                "url": browser.webView.url?.path ?? "", "contextMenu": browser.webView.lastMenu, "statusBody": browser.status.shownBody,
                "statusItem": status.described, "notifications": notifier.shown, "notifyAsked": notifier.asked,
                "login": LoginItem.state(), "loginSettings": LoginItem.openedSettings, "serverChildren": server.children.map { Int($0) },
                "writingAwake": writingActivity != nil,
            ]))
        case ("POST", "/statusmenu"):
            reply(.json(["pressed": status.press(body["id"] as? String ?? "")]))
        case ("POST", "/notification"):
            reply(.json(["clicked": notifier.click(body["index"] as? Int ?? 0)]))
        case ("POST", "/sleep"):
            // What the Mac's sleep does (the system's notifications): the page told first, then `seconds` later by the
            // clock it woke up. The test's audio does not stop meanwhile, as a real one would.
            willSleep()
            Main.after(0.3) { self.didWake(slept: body["seconds"] as? Double ?? 60) { reply(.json(["value": $0 ?? NSNull()])) } }
        case ("POST", "/eval"):
            if body["gesture"] as? Bool == true { lastInput = Date() }   // what a click in the window would do
            let script = body["js"] as? String ?? "return null"
            browser.webView.callAsyncJavaScript(script, arguments: [:], in: nil, in: .page) { result in
                switch result {
                case .success(let value): reply(.json(["value": value]))
                case .failure(let error): reply(.json(["error": "\(error)"]))
                }
            }
        case ("GET", "/snapshot"):
            browser.webView.takeSnapshot(with: nil) { image, error in
                guard let image, let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
                      let png = rep.representation(using: .png, properties: [:]) else {
                    reply(.json(["error": "\(String(describing: error))"], status: 500))
                    return
                }
                reply(TestControl.Response(type: "image/png", body: png))
            }
        case ("POST", "/window"):
            if body["visible"] as? Bool == true { browser.show() } else { _ = browser.windowShouldClose(browser.window) }
            reply(.json(["visible": browser.window.isVisible]))
        case ("POST", "/reopen"):
            _ = applicationShouldHandleReopen(NSApp, hasVisibleWindows: browser.window.isVisible)
            reply(.json(["visible": browser.window.isVisible]))
        case ("POST", "/menu"):
            guard let item = Menus.find(body["id"] as? String ?? "", in: NSApp.mainMenu), let menu = item.menu else {
                reply(.json(["error": "no such item"], status: 404))
                return
            }
            reply(.json(["title": item.title]))            // first: the item may be Quit
            Main.run { menu.performActionForItem(at: menu.index(of: item)) }
        case ("POST", "/answers"):
            browser.asker.answers.append(contentsOf: body["buttons"] as? [Int] ?? [])
            browser.asker.typed.append(contentsOf: body["typed"] as? [String] ?? [])
            reply(.json(["ok": true]))
        case ("POST", "/files"):
            browser.nextFiles = (body["paths"] as? [String] ?? []).map { URL(fileURLWithPath: $0) }
            reply(.json(["ok": true]))
        case ("POST", "/folder"):
            browser.nextFolder = body["path"] as? String
            reply(.json(["ok": true]))
        case ("POST", "/sound"):
            if let delay = body["startDelay"] as? Double { sound.startDelay = delay }
            if let rate = body["deviceRate"] as? Double { sound.changeRate(rate, notify: body["notify"] as? Bool ?? false) }
            if let seconds = body["stall"] as? Double { sound.stall(seconds: seconds, mode: body["mode"] as? String ?? "gap") }
            reply(.json(["ok": true]))
        case ("GET", "/menus"):
            reply(.json((NSApp.mainMenu?.items ?? []).map { top in
                ["id": top.identifier?.rawValue ?? "", "title": top.title, "items": (top.submenu?.items ?? []).filter { !$0.isSeparatorItem }.map {
                    ["id": $0.identifier?.rawValue ?? "", "title": $0.title, "key": $0.keyEquivalent,
                     "modifiers": Int($0.keyEquivalentModifierMask.rawValue), "action": $0.action.map(NSStringFromSelector) ?? ""]
                }]
            }))
        case ("POST", "/press"):
            browser.status.press(body["index"] as? Int ?? 0)
            reply(.json(["ok": true]))
        case ("POST", "/key"):
            // Real key events into the window, the way the keyboard delivers them (the page cannot tell them apart).
            browser.window.makeFirstResponder(browser.webView)
            for stroke in body["keys"] as? [[String: Any]] ?? [] {
                let chars = stroke["chars"] as? String ?? ""
                var flags: NSEvent.ModifierFlags = []
                for m in stroke["modifiers"] as? [String] ?? [] {
                    flags.insert(["command": .command, "shift": .shift, "option": .option, "control": .control][m] ?? [])
                }
                let code = UInt16(stroke["code"] as? Int ?? 0)
                for type in [NSEvent.EventType.keyDown, .keyUp] {
                    if let event = NSEvent.keyEvent(with: type, location: .zero, modifierFlags: flags, timestamp: ProcessInfo.processInfo.systemUptime,
                                                    windowNumber: browser.window.windowNumber, context: nil, characters: chars,
                                                    charactersIgnoringModifiers: chars, isARepeat: false, keyCode: code) {
                        if flags.contains(.command), type == .keyDown, browser.window.performKeyEquivalent(with: event) { continue }
                        browser.window.sendEvent(event)
                    }
                }
            }
            reply(.json(["ok": true]))
        case ("POST", "/rightclick"):
            // A right click on the page at (x, y): WebKit builds its menu, which test mode writes down and never shows.
            let view = browser.webView, y = body["y"] as? Double ?? 0
            let point = view.convert(NSPoint(x: body["x"] as? Double ?? 0, y: view.isFlipped ? y : view.bounds.height - y), to: nil)
            view.lastMenu = [:]
            for type in [NSEvent.EventType.rightMouseDown, .rightMouseUp] {
                if let event = NSEvent.mouseEvent(with: type, location: point, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                                                  windowNumber: browser.window.windowNumber, context: nil, eventNumber: 1, clickCount: 1, pressure: 1) {
                    browser.window.sendEvent(event)
                }
            }
            reply(.json(["ok": true]))
        case ("POST", "/drop"):
            // Files dropped on the window at (x, y) of the page, through the web view's own drag-and-drop methods. The
            // files sit on a pasteboard of the test's own, never the user's.
            let paths = body["paths"] as? [String] ?? []
            let view = browser.webView, y = body["y"] as? Double ?? 0
            let point = NSPoint(x: body["x"] as? Double ?? 0, y: view.isFlipped ? y : view.bounds.height - y)   // page pixels, from the top
            let drag = TestDrag(paths: paths, at: view.convert(point, to: nil), in: browser.window)
            let entered = view.draggingEntered(drag)
            _ = view.draggingUpdated(drag)
            Main.after(0.3) {
                let accepted = view.performDragOperation(drag)
                view.concludeDragOperation(drag)
                reply(.json(["entered": Int(entered.rawValue), "performed": accepted]))
            }
        case ("POST", "/quit"):
            reply(.json(["ok": true]))
            Main.run { NSApp.terminate(nil) }
        default:
            reply(.json(["error": "unknown"], status: 404))
        }
    }
}
