// Test mode only (VEULY_TEST_TOKEN): a small HTTP control port on 127.0.0.1:VEULY_TEST_PORT through which
// tests/e2e_macapp.py drives the app: run JavaScript in the page, take a snapshot, hide and show the window, press
// menu items, answer the app's questions, stall the stand-in sound, quit. Every request must carry the token in
// X-Test-Token. The socket is bound to the loopback address only, so the system firewall never asks about it.
import AppKit
import Foundation

final class TestControl: @unchecked Sendable {
    struct Request {
        let method: String, path: String
        let body: [String: Any]
    }

    struct Response {
        var status = 200
        var type = "application/json"
        var body = Data()

        static func json(_ value: Any, status: Int = 200) -> Response {
            let data = (try? JSONSerialization.data(withJSONObject: value, options: [.fragmentsAllowed])) ?? Data("null".utf8)
            return Response(status: status, body: data)
        }
    }

    typealias Handler = @MainActor (Request, @escaping @MainActor (Response) -> Void) -> Void

    private let queue = DispatchQueue(label: "app.veuly.test-control", attributes: .concurrent)
    private let listener: Int32
    private let token: String
    private let handler: Handler
    private var source: DispatchSourceRead?

    init?(port: UInt16, token: String, handler: @escaping Handler) {
        self.token = token
        self.handler = handler
        listener = socket(AF_INET, SOCK_STREAM, 0)
        guard listener >= 0 else { return nil }
        var yes: Int32 = 1
        setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &yes, socklen_t(MemoryLayout<Int32>.size))
        _ = fcntl(listener, F_SETFD, FD_CLOEXEC)
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = port.bigEndian
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(listener, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        guard bound == 0, listen(listener, 16) == 0 else {
            close(listener)
            return nil
        }
        let s = DispatchSource.makeReadSource(fileDescriptor: listener, queue: queue)
        s.setEventHandler { [weak self] in self?.accept() }
        source = s
        s.resume()
    }

    private func accept() {
        let fd = Darwin.accept(listener, nil, nil)
        guard fd >= 0 else { return }
        var yes: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, socklen_t(MemoryLayout<Int32>.size))
        var timeout = timeval(tv_sec: 30, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        queue.async { self.serve(fd) }
    }

    private func serve(_ fd: Int32) {
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 65536)
        var headerEnd: Range<Data.Index>?
        while headerEnd == nil {
            let n = read(fd, &buffer, buffer.count)
            if n <= 0 { close(fd); return }
            data.append(buffer, count: n)
            headerEnd = data.range(of: Data("\r\n\r\n".utf8))
            if data.count > 1 << 20 { close(fd); return }
        }
        let head = String(decoding: data[data.startIndex..<headerEnd!.lowerBound], as: UTF8.self)
        var lines = head.components(separatedBy: "\r\n")
        let first = lines.removeFirst().split(separator: " ")
        var headers: [String: String] = [:]
        for line in lines {
            if let colon = line.firstIndex(of: ":") {
                headers[line[..<colon].lowercased()] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            }
        }
        let length = Int(headers["content-length"] ?? "0") ?? 0
        var body = Data(data[headerEnd!.upperBound...])
        while body.count < length {
            let n = read(fd, &buffer, min(buffer.count, length - body.count))
            if n <= 0 { break }
            body.append(buffer, count: n)
        }
        guard first.count >= 2 else { reply(fd, .json(["error": "bad request"], status: 400)); return }
        // The token, compared in constant time.
        let given = Array((headers["x-test-token"] ?? "").utf8), wanted = Array(token.utf8)
        var diff = given.count == wanted.count ? 0 : 1
        for i in 0..<min(given.count, wanted.count) { diff |= Int(given[i] ^ wanted[i]) }
        guard diff == 0 else { reply(fd, .json(["error": "token"], status: 403)); return }
        let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] ?? [:]
        let request = Request(method: String(first[0]), path: String(first[1]), body: json)
        let handler = self.handler
        Main.run { handler(request) { response in self.queue.async { self.reply(fd, response) } } }
    }

    private func reply(_ fd: Int32, _ response: Response) {
        let head = "HTTP/1.1 \(response.status) OK\r\nContent-Type: \(response.type)\r\nContent-Length: \(response.body.count)\r\nConnection: close\r\n\r\n"
        var out = Data(head.utf8)
        out.append(response.body)
        out.withUnsafeBytes { raw in
            var sent = 0
            while sent < raw.count {
                let n = write(fd, raw.baseAddress! + sent, raw.count - sent)
                if n <= 0 { break }
                sent += n
            }
        }
        close(fd)
    }
}

/// Test mode: what AppKit hands a view while files are dragged onto it, with the files on a pasteboard of the
/// test's own (the user's clipboard and drag pasteboard are never touched).
final class TestDrag: NSObject, NSDraggingInfo {
    private let pasteboard = NSPasteboard(name: NSPasteboard.Name("app.veuly.test-drag"))
    private let location: NSPoint
    private weak var window: NSWindow?

    init(paths: [String], at location: NSPoint, in window: NSWindow) {
        self.location = location
        self.window = window
        super.init()
        // As Finder puts files on its drag pasteboard: their paths (the type WebKit reads files from) and a file URL.
        let filenames = NSPasteboard.PasteboardType("NSFilenamesPboardType")
        pasteboard.declareTypes([filenames, .fileURL], owner: nil)
        pasteboard.setPropertyList(paths, forType: filenames)
        if let first = paths.first { pasteboard.setString(URL(fileURLWithPath: first).absoluteString, forType: .fileURL) }
    }

    var draggingDestinationWindow: NSWindow? { window }
    var draggingSourceOperationMask: NSDragOperation { [.copy, .generic] }
    var draggingLocation: NSPoint { location }
    var draggedImageLocation: NSPoint { location }
    var draggedImage: NSImage? { nil }
    var draggingPasteboard: NSPasteboard { pasteboard }
    var draggingSource: Any? { nil }
    var draggingSequenceNumber: Int { 4242 }
    func slideDraggedImage(to screenPoint: NSPoint) {}
    var draggingFormation: NSDraggingFormation = .default
    var animatesToDestination = false
    var numberOfValidItemsForDrop = 1
    func enumerateDraggingItems(options enumOpts: NSDraggingItemEnumerationOptions = [], for view: NSView?, classes classArray: [AnyClass],
                                searchOptions: [NSPasteboard.ReadingOptionKey: Any] = [:],
                                using block: (NSDraggingItem, Int, UnsafeMutablePointer<ObjCBool>) -> Void) {}
    var springLoadingHighlight: NSSpringLoadingHighlight { .none }
    func resetSpringLoading() {}
}
