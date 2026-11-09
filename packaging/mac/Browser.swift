// The window and the web view that shows the page app.py serves, and what a browser would otherwise give the page:
// file pickers, downloads (with a save panel), alert/confirm/prompt, the microphone's permission, printing, zoom.
// Only the app's own address (http://127.0.0.1:<port>) loads in the window; any other link opens in the user's browser.
import AppKit
import WebKit

/// Questions the app asks the user: sheets on the window, or, in test mode, answered from what the test queued
/// and written down for the test to read.
@MainActor
final class Asker {
    struct Asked { let title: String, body: String, buttons: [String], answer: Int }
    private(set) var asked: [Asked] = []
    var answers: [Int] = []           // test mode: which button to press, next question first (default: the first)
    var typed: [String] = []          // test mode: what prompt() gets
    weak var window: NSWindow?

    /// `buttons[0]` is the default (Return); `cancel` is the one Escape presses.
    func ask(title: String, body: String, buttons: [String], cancel: Int? = nil, done: @escaping (Int) -> Void) {
        if TestMode.on {
            let answer = answers.isEmpty ? 0 : answers.removeFirst()
            asked.append(Asked(title: title, body: body, buttons: buttons, answer: answer))
            Main.run { done(answer) }
            return
        }
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = body
        for (i, label) in buttons.enumerated() {
            let button = alert.addButton(withTitle: label)
            if i == cancel { button.keyEquivalent = "\u{1b}" }
        }
        let finish: (NSApplication.ModalResponse) -> Void = { r in
            done(r.rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue)
        }
        if let window, window.isVisible {
            alert.beginSheetModal(for: window, completionHandler: finish)
        } else {
            finish(alert.runModal())
        }
    }

    /// prompt(): a question with a text field.
    func type(title: String, value: String, ok: String, cancel: String, done: @escaping (String?) -> Void) {
        if TestMode.on {
            let text = typed.isEmpty ? nil : typed.removeFirst()
            asked.append(Asked(title: title, body: value, buttons: [ok, cancel], answer: text == nil ? 1 : 0))
            Main.run { done(text) }
            return
        }
        let alert = NSAlert()
        alert.messageText = title
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
        field.stringValue = value
        alert.accessoryView = field
        alert.addButton(withTitle: ok)
        alert.addButton(withTitle: cancel).keyEquivalent = "\u{1b}"
        alert.window.initialFirstResponder = field
        let finish: (NSApplication.ModalResponse) -> Void = { r in done(r == .alertFirstButtonReturn ? field.stringValue : nil) }
        if let window, window.isVisible { alert.beginSheetModal(for: window, completionHandler: finish) } else { finish(alert.runModal()) }
    }
}

/// The web view, less what would take the page away behind the app's back: WebKit's own right-click menu offers
/// Reload (and Back, Forward), which would reload at once, a recording included, without the question and the saving
/// that View › Reload does first. In test mode the menu is written down (`lastMenu`: what WebKit offered, what was
/// kept) and never shown.
final class PageView: WKWebView {
    static let away: Set<String> = ["WKMenuItemIdentifierReload", "WKMenuItemIdentifierGoBack", "WKMenuItemIdentifierGoForward"]
    var lastMenu: [String: [String]] = [:]

    /// ⌘= zooms in, as in Safari and Chrome: on a US keyboard ⌘+ (the menu's key) is ⌘⇧=. Taken here, where a key
    /// equivalent arrives first: WebKit hands the page's unhandled keys back to AppKit in a way that a hidden menu
    /// item on "=" never sees.
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        if event.type == .keyDown, event.charactersIgnoringModifiers == "=",
           event.modifierFlags.intersection(.deviceIndependentFlagsMask).subtracting([.capsLock, .numericPad, .function]) == .command {
            return NSApp.sendAction(#selector(MenuActions.zoomIn(_:)), to: nil, from: self)
        }
        return super.performKeyEquivalent(with: event)
    }

    override func willOpenMenu(_ menu: NSMenu, with event: NSEvent) {
        let offered = menu.items.map { $0.identifier?.rawValue ?? $0.title }
        for item in menu.items where Self.away.contains(item.identifier?.rawValue ?? "") { menu.removeItem(item) }
        while let first = menu.items.first, first.isSeparatorItem { menu.removeItem(first) }      // no separator left at an end,
        while let last = menu.items.last, last.isSeparatorItem { menu.removeItem(last) }          // or two in a row
        for (i, item) in menu.items.enumerated().reversed() where i > 0 && item.isSeparatorItem && menu.items[i - 1].isSeparatorItem {
            menu.removeItem(item)
        }
        super.willOpenMenu(menu, with: event)
        if TestMode.on {
            lastMenu = ["offered": offered, "kept": menu.items.map { $0.identifier?.rawValue ?? $0.title }]
            menu.removeAllItems()
        }
    }
}

/// Test mode keeps the window off every screen; AppKit would otherwise pull it back onto one.
final class AppWindow: NSWindow {
    override func constrainFrameRect(_ frameRect: NSRect, to screen: NSScreen?) -> NSRect {
        TestMode.on ? frameRect : super.constrainFrameRect(frameRect, to: screen)
    }
}

/// What the window shows while the page is not there: "starting", or the server that stopped, with what to do.
@MainActor
final class StatusView: NSView {
    private let spinner = NSProgressIndicator()
    private let title = NSTextField(labelWithString: "")
    private let body = NSTextField(wrappingLabelWithString: "")
    private let buttons = NSStackView()
    private var actions: [() -> Void] = []

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.backgroundColor = NSColor(srgbRed: 0xf1 / 255.0, green: 0xf4 / 255.0, blue: 0xf1 / 255.0, alpha: 1).cgColor   // the page's --desk
        spinner.style = .spinning
        spinner.controlSize = .regular
        title.font = .systemFont(ofSize: 17, weight: .semibold)
        title.textColor = NSColor(srgbRed: 0x17 / 255.0, green: 0x21 / 255.0, blue: 0x1e / 255.0, alpha: 1)
        title.alignment = .center
        body.font = .systemFont(ofSize: 13)
        body.textColor = NSColor(srgbRed: 0x58 / 255.0, green: 0x66 / 255.0, blue: 0x61 / 255.0, alpha: 1)
        body.alignment = .center
        body.preferredMaxLayoutWidth = 420
        buttons.orientation = .horizontal
        buttons.spacing = 12
        let stack = NSStackView(views: [spinner, title, body, buttons])
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: centerYAnchor),
            stack.widthAnchor.constraint(lessThanOrEqualToConstant: 460),
        ])
    }

    required init?(coder: NSCoder) { nil }

    func show(title: String, body: String = "", busy: Bool = false, buttons: [(String, () -> Void)] = []) {
        self.title.stringValue = title
        self.body.stringValue = body
        self.body.isHidden = body.isEmpty
        spinner.isHidden = !busy
        if busy { spinner.startAnimation(nil) } else { spinner.stopAnimation(nil) }
        self.buttons.arrangedSubviews.forEach { $0.removeFromSuperview() }
        actions = buttons.map { $0.1 }
        for (i, (label, _)) in buttons.enumerated() {
            let b = NSButton(title: label, target: self, action: #selector(pressed(_:)))
            b.tag = i
            if i == 0 { b.keyEquivalent = "\r" }
            self.buttons.addArrangedSubview(b)
        }
        isHidden = false
    }

    var shownTitle: String { isHidden ? "" : title.stringValue }
    var shownBody: String { isHidden ? "" : body.stringValue }
    var shownButtons: [String] { isHidden ? [] : buttons.arrangedSubviews.compactMap { ($0 as? NSButton)?.title } }

    @objc private func pressed(_ sender: NSButton) {
        if sender.tag < actions.count { actions[sender.tag]() }
    }

    /// Test mode: what a click on the button at `index` does.
    func press(_ index: Int) {
        if index < actions.count { actions[index]() }
    }
}

@MainActor
final class Browser: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate, NSWindowDelegate {
    let window: AppWindow
    let webView: PageView
    let status: StatusView
    let asker = Asker()
    private let texts: Texts
    private(set) var port = 0
    private(set) var loaded = false            // the page finished loading at least once since the last link
    var onFirstLoad: (() -> Void)?
    /// The page goes away (reload, another link, its process ended): what it had started stops with it.
    var onLeave: (() -> Void)?
    /// A reload the page or WebKit started: the app asks and saves first (AppDelegate.reloadPage), as for its menu's.
    var onReloadAsked: (() -> Void)?
    private var reloading = false              // the app's own webView.reload() (the page's process ended)
    // test mode: what would have gone to the user's browser, what was saved, what was printed, files for the next picker
    private(set) var outside: [String] = []
    private(set) var saved: [String] = []
    private(set) var printed: [String] = []
    var nextFiles: [URL]?
    var nextFolder: String?                    // test mode: what the next folder panel answers (nil: Cancel)

    init(texts: Texts, bridge: Bridge, version: String) {
        self.texts = texts
        let config = WKWebViewConfiguration()
        // A test leaves nothing in the WebKit storage of the app itself.
        config.websiteDataStore = TestMode.on ? .nonPersistent() : .default()
        config.mediaTypesRequiringUserActionForPlayback = []        // the recorder's audio graph starts after the click's awaits
        config.applicationNameForUserAgent = "Veuly/\(version)"
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        config.preferences.isFraudulentWebsiteWarningEnabled = false
        config.userContentController.addUserScript(Bridge.userScript(version: version))
        config.userContentController.addScriptMessageHandler(bridge, contentWorld: .page, name: Bridge.name)
        webView = PageView(frame: NSRect(x: 0, y: 0, width: 1240, height: 820), configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.allowsBackForwardNavigationGestures = false
        webView.allowsMagnification = false
        webView.underPageBackgroundColor = NSColor(srgbRed: 0xf1 / 255.0, green: 0xf4 / 255.0, blue: 0xf1 / 255.0, alpha: 1)
        if ProcessInfo.processInfo.environment["VEULY_DEV"] == "1" { webView.isInspectable = true }   // Safari's Develop menu

        window = AppWindow(contentRect: NSRect(x: 0, y: 0, width: 1240, height: 820), styleMask: [.titled, .closable, .miniaturizable, .resizable],
                           backing: .buffered, defer: true)
        if TestMode.on { window.setFrame(Browser.offScreen, display: false) }
        window.title = "Veuly"
        window.minSize = NSSize(width: 760, height: 520)
        window.isRestorable = false
        window.collectionBehavior = [.fullScreenPrimary]
        status = StatusView(frame: NSRect(x: 0, y: 0, width: 1240, height: 820))
        status.autoresizingMask = [.width, .height]
        super.init()

        let content = NSView(frame: NSRect(x: 0, y: 0, width: 1240, height: 820))
        content.addSubview(webView)
        content.addSubview(status)
        window.contentView = content
        window.delegate = self
        webView.navigationDelegate = self
        webView.uiDelegate = self
        asker.window = window
        if !TestMode.on {
            window.center()
            window.setFrameAutosaveName("Veuly")              // the size and place it had last time
            let zoom = UserDefaults.standard.double(forKey: "VeulyZoom")
            if zoom >= 0.5 && zoom <= 3 { webView.pageZoom = zoom }
        }
        webView.isHidden = true
    }

    // MARK: showing

    /// Where test mode keeps the window: far to the left of every screen.
    static let offScreen = NSRect(x: -30000, y: 0, width: 1240, height: 852)

    func show() {
        if TestMode.on {                           // never on the user's screen, never takes the focus
            window.setFrame(Self.offScreen, display: false)
            window.orderFrontRegardless()
            if window.frame.origin != Self.offScreen.origin { window.setFrame(Self.offScreen, display: false) }
            return
        }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate()
    }

    func hide() { window.orderOut(nil) }

    /// Closing the window only hides it: the app keeps running, and a class that records keeps recording.
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        hide()
        return false
    }

    func load(_ link: URL) {
        port = link.port ?? 0
        loaded = false
        webView.load(URLRequest(url: link))
    }

    /// The server is gone or starting again: the page on screen is not a working page any more.
    func forget() {
        loaded = false
    }

    /// The same page goes on with a new server on the same address (it took the new session itself).
    func resumed() {
        loaded = true
        showPage()
    }

    func showStatus(title: String, body: String = "", busy: Bool = false, buttons: [(String, () -> Void)] = []) {
        status.show(title: title, body: body, busy: busy, buttons: buttons)
        webView.isHidden = true
    }

    func showPage() {
        status.isHidden = true
        webView.isHidden = false
        window.makeFirstResponder(webView)
    }

    /// Runs `method` of the page's VeulyMac object (web/native.js) with `arg`, and hands back what it returned.
    func call(_ method: String, _ arg: Any = NSNull(), done: ((Any?) -> Void)? = nil) {
        webView.callAsyncJavaScript("const m = window.VeulyMac; return m && typeof m[name] === 'function' ? await m[name](arg) : null;",
                                    arguments: ["name": method, "arg": arg], in: nil, in: .page) { result in
            switch result {
            case .success(let value): done?(value)
            case .failure(let error):
                Log.write("page: \(method) failed: \(error.localizedDescription)")
                done?(nil)
            }
        }
    }

    // MARK: zoom

    func zoom(_ step: Double?) {
        webView.pageZoom = step.map { min(3, max(0.5, (webView.pageZoom + $0) * 10).rounded() / 10) } ?? 1
        if !TestMode.on { UserDefaults.standard.set(webView.pageZoom, forKey: "VeulyZoom") }
    }

    // MARK: whose address

    func isOwn(_ url: URL) -> Bool {
        url.scheme == "http" && url.host == "127.0.0.1" && url.port == port && port > 0
    }

    func isOwn(_ origin: WKSecurityOrigin) -> Bool {
        origin.protocol == "http" && origin.host == "127.0.0.1" && origin.port == port && port > 0
    }

    /// A link to somewhere else opens where a browser would open it: a web page in the user's own browser, an e-mail
    /// address in their mail app, the class page's "Open in Obsidian" in Obsidian. These are the links the page makes
    /// (web/md.js links only http and https in a note); a new kind of link to another app goes in this list. A test
    /// only writes it down.
    static let outsideSchemes: Set<String> = ["http", "https", "mailto", "obsidian"]

    func openOutside(_ url: URL) {
        guard Self.outsideSchemes.contains((url.scheme ?? "").lowercased()) else {
            Log.write("page: a link to \(url.scheme ?? "?"): is not opened")
            return
        }
        if TestMode.on { outside.append(url.absoluteString); return }
        NSWorkspace.shared.open(url)
    }

    // MARK: navigation

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        if action.shouldPerformDownload { decisionHandler(.download); return }
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        let frameIsMain = action.targetFrame?.isMainFrame ?? true
        if action.navigationType == .reload, frameIsMain, !reloading {     // goes through the app's question and saving
            decisionHandler(.cancel)
            onReloadAsked?()
            return
        }
        if isOwn(url) { decisionHandler(.allow); return }
        if !frameIsMain, url.scheme == "about" || url.scheme == "blob" { decisionHandler(.allow); return }
        if action.navigationType == .linkActivated || action.targetFrame == nil { openOutside(url) }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping @MainActor (WKNavigationResponsePolicy) -> Void) {
        let disposition = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        decisionHandler(disposition.lowercased().hasPrefix("attachment") || !response.canShowMIMEType ? .download : .allow)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        reloading = false
        guard let url = webView.url, isOwn(url) else { return }
        let first = !loaded
        loaded = true
        showPage()
        if first { onFirstLoad?() }
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        onLeave?()
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        reloading = false
        Log.write("page: could not load: \(error.localizedDescription)")
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        Log.write("page: the web content process ended; loading the page again")
        onLeave?()
        reloading = true
        webView.reload()
    }

    // MARK: downloads, with a save panel (the user's choice of folder needs no folder permission)

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping @MainActor (URL?) -> Void) {
        let name = (suggestedFilename as NSString).lastPathComponent.isEmpty ? "Veuly" : (suggestedFilename as NSString).lastPathComponent
        if TestMode.on {
            guard let folder = TestMode.saveFolder else { completionHandler(nil); return }
            var target = folder.appendingPathComponent(name)
            var n = 1
            while FileManager.default.fileExists(atPath: target.path) {
                n += 1
                target = folder.appendingPathComponent("\((name as NSString).deletingPathExtension) \(n).\((name as NSString).pathExtension)")
            }
            saved.append(target.path)
            completionHandler(target)
            return
        }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = name
        panel.canCreateDirectories = true
        if panel.directoryURL == nil { panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first }
        show()
        panel.beginSheetModal(for: window) { result in
            guard result == .OK, let url = panel.url else { completionHandler(nil); return }
            try? FileManager.default.removeItem(at: url)        // the panel has asked already whether to replace it
            completionHandler(url)
        }
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        Log.write("download failed: \(error.localizedDescription)")
        asker.ask(title: texts("mac_download_failed"), body: error.localizedDescription, buttons: [texts("mac_ok")]) { _ in }
    }

    // MARK: what the page asks of a browser

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url, !isOwn(url) { openOutside(url) }
        return nil
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping @MainActor ([URL]?) -> Void) {
        if TestMode.on {
            completionHandler(nextFiles)
            nextFiles = nil
            return
        }
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canChooseFiles = true
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.urls : nil) }
    }

    /// The folder panel of "Keep in step with a folder": folders only, one, its path or nil for Cancel. Nothing reads the
    /// folder until the page has sent that path to the server.
    func chooseFolder(reply: @escaping @MainActor (String?) -> Void) {
        if TestMode.on {
            reply(nextFolder)
            nextFolder = nil
            return
        }
        guard window.attachedSheet == nil else { reply(nil); return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        panel.message = texts("mac_sync_panel")
        panel.prompt = texts("mac_sync_choose")
        panel.beginSheetModal(for: window) { result in reply(result == .OK ? panel.url?.path : nil) }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping @MainActor () -> Void) {
        asker.ask(title: message, body: "", buttons: [texts("mac_ok")]) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping @MainActor (Bool) -> Void) {
        asker.ask(title: message, body: "", buttons: [texts("mac_ok"), texts("cancel")], cancel: 1) { completionHandler($0 == 0) }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor (String?) -> Void) {
        asker.type(title: prompt, value: defaultText ?? "", ok: texts("mac_ok"), cancel: texts("cancel"), done: completionHandler)
    }

    /// The microphone, for the page of this server only (and its main frame). macOS asks the user once, for Veuly.
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin, initiatedByFrame frame: WKFrameInfo,
                 type: WKMediaCaptureType, decisionHandler: @escaping @MainActor (WKPermissionDecision) -> Void) {
        let ok = frame.isMainFrame && isOwn(origin) && type == .microphone
        Log.write("microphone for \(origin.host):\(origin.port): \(ok ? "allowed" : "refused")")
        decisionHandler(ok ? .grant : .deny)
    }

    // MARK: printing (window.print() reaches the app through the bridge)

    func printPage() {
        let info = (NSPrintInfo.shared.copy() as? NSPrintInfo) ?? NSPrintInfo()
        info.horizontalPagination = .fit
        info.verticalPagination = .automatic
        info.isVerticallyCentered = false
        if TestMode.on {
            guard let folder = TestMode.saveFolder else { return }
            let target = folder.appendingPathComponent("printed-\(printed.count + 1).pdf")
            info.jobDisposition = .save
            info.dictionary()[NSPrintInfo.AttributeKey.jobSavingURL] = target
            let op = webView.printOperation(with: info)
            op.showsPrintPanel = false
            op.showsProgressPanel = false
            op.view?.frame = webView.bounds
            op.runModal(for: window, delegate: self, didRun: #selector(printed(_:success:context:)), contextInfo: nil)
            printed.append(target.path)
            return
        }
        let op = webView.printOperation(with: info)
        op.view?.frame = webView.bounds
        op.jobTitle = webView.title ?? "Veuly"
        show()
        op.runModal(for: window, delegate: nil, didRun: nil, contextInfo: nil)
    }

    @objc private func printed(_ op: NSPrintOperation, success: Bool, context: UnsafeMutableRawPointer?) {
        Log.write("printed: \(success)")
    }
}
