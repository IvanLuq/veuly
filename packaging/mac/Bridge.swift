// What the page and the app say to each other. The app injects `window.veulyShell` (a frozen object with `post`)
// at the start of every page of its own address; web/native.js uses it. Messages are accepted only from the main
// frame of the app's own origin, and each one gets an answer (a promise in the page).
//
//   page -> app   {type: 'lang', lang, work}       the interface language and wording, for the menus and alerts
//                 {type: 'recording', on}          a recorder started or stopped: keep the Mac awake meanwhile
//                 {type: 'sound', op: 'start'|'stop'}  this Mac's sound (SystemSound); start only right after a click
//                 {type: 'print'}                  window.print()
//                 {type: 'finishing'}              Finish was pressed: the first time, macOS asks whether Veuly may notify
//                 {type: 'login', op: 'get'|'set'|'settings', on}   Settings' "Open Veuly at login" (LoginItem)
//                 {type: 'folder'}                 "Keep in step with a folder": the folder panel; answers its path, or null
//   app -> page   VeulyMac.sound(at, t, pcm) and the calls in Browser.call (web/native.js)
import AppKit
import WebKit

@MainActor
protocol BridgeOwner: AnyObject {
    func pageLanguage(_ lang: String, work: Bool)
    func pageRecording(_ on: Bool)
    func pageWantsSound(_ on: Bool, reply: @escaping (Any?, String?) -> Void)
    func pageWantsPrint()
    func pageFinishing()
    func pageLogin(_ op: String, on: Bool, reply: @escaping (Any?, String?) -> Void)
    func pageChoosesFolder(reply: @escaping (Any?, String?) -> Void)
    func isOwn(_ origin: WKSecurityOrigin) -> Bool
}

@MainActor
final class Bridge: NSObject, WKScriptMessageHandlerWithReply {
    static let name = "veuly"
    weak var owner: BridgeOwner?

    static func userScript(version: String) -> WKUserScript {
        let source = """
        (() => {
          const handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.\(name);
          if (!handler) return;
          const post = (message) => handler.postMessage(message);
          Object.defineProperty(window, 'veulyShell', { value: Object.freeze({ post, version: '\(version)' }) });
          // A web view prints nothing on window.print(): the app shows the print panel.
          window.print = () => { post({ type: 'print' }).catch(() => {}); };
        })();
        """
        return WKUserScript(source: source, injectionTime: .atDocumentStart, forMainFrameOnly: true, in: .page)
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping @MainActor @Sendable (Any?, String?) -> Void) {
        guard let owner, message.frameInfo.isMainFrame, owner.isOwn(message.frameInfo.securityOrigin) else {
            Log.write("bridge: refused a message from \(message.frameInfo.securityOrigin.host):\(message.frameInfo.securityOrigin.port)")
            replyHandler(nil, "refused")
            return
        }
        guard let body = message.body as? [String: Any], let type = body["type"] as? String else {
            replyHandler(nil, "bad message")
            return
        }
        switch type {
        case "lang":
            owner.pageLanguage(body["lang"] as? String ?? "en", work: body["work"] as? Bool ?? false)
            replyHandler(true, nil)
        case "recording":
            owner.pageRecording(body["on"] as? Bool ?? false)
            replyHandler(true, nil)
        case "sound":
            owner.pageWantsSound(body["op"] as? String == "start", reply: replyHandler)
        case "print":
            owner.pageWantsPrint()
            replyHandler(true, nil)
        case "finishing":
            owner.pageFinishing()
            replyHandler(true, nil)
        case "login":
            owner.pageLogin(body["op"] as? String ?? "get", on: body["on"] as? Bool ?? false, reply: replyHandler)
        case "folder":
            owner.pageChoosesFolder(reply: replyHandler)
        default:
            replyHandler(nil, "unknown message")
        }
    }
}
