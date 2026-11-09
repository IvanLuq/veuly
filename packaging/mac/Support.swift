// Small things the rest of the Mac app leans on: its log, the data folder, the Python that runs the server,
// and test mode. Nothing here touches the window.
import Foundation

/// Work for the main thread, through its run loop (in the common modes) rather than the main dispatch queue.
/// AppKit's "may I quit?" wait and modal alerts run a nested loop; when that loop was entered from a block of the
/// main queue, no other block of that queue runs until it ends, so the app would wait forever for a server's exit,
/// a timer or an answer delivered that way.
enum Main {
    static func run(_ block: @escaping @MainActor () -> Void) {
        RunLoop.main.perform(inModes: [.common]) { MainActor.assumeIsolated(block) }
        CFRunLoopWakeUp(CFRunLoopGetMain())
    }

    @discardableResult
    static func after(_ seconds: TimeInterval, repeats: Bool = false, _ block: @escaping @MainActor () -> Void) -> Timer {
        let timer = Timer(timeInterval: seconds, repeats: repeats) { _ in MainActor.assumeIsolated(block) }
        RunLoop.main.add(timer, forMode: .common)
        return timer
    }
}

/// What the Mac app itself did, for when something has to be looked at afterwards. It goes to veuly-app.log, next
/// to the server's veuly.log when VEULY_HOME names the data folder, otherwise to ~/Library/Logs/Veuly (the app never
/// creates the data folder itself: the server renames an old TranscApp folder on its first start, and an empty
/// Veuly folder made here first would stop that). Readable only by this account; past 1 MB it starts again.
/// The session's token never goes in.
enum Log {
    private static let queue = DispatchQueue(label: "app.veuly.log")
    nonisolated(unsafe) private static var file: FileHandle?
    nonisolated(unsafe) private static var path = ""

    static func open() {
        let folder = DataFolder.override ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/Veuly", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let url = folder.appendingPathComponent("veuly-app.log")
        if let size = (try? FileManager.default.attributesOfItem(atPath: url.path))?[.size] as? Int, size > 1 << 20 {
            try? FileManager.default.removeItem(at: url.appendingPathExtension("1"))
            try? FileManager.default.moveItem(at: url, to: url.appendingPathExtension("1"))
        }
        let fd = Darwin.open(url.path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0o600)
        if fd >= 0 { file = FileHandle(fileDescriptor: fd, closeOnDealloc: true) }
        path = url.path
    }

    static var location: String { path }

    static func write(_ text: String) {
        let line = "\(ISO8601DateFormatter().string(from: Date())) \(text)\n"
        queue.async {
            if let data = line.data(using: .utf8) {
                file?.write(data)
                FileHandle.standardError.write(data)
            }
        }
    }
}

/// The folder app.py keeps the library in (transcapp/paths.py, data_dir): VEULY_HOME or the older TRANSCAPP_HOME,
/// otherwise Application Support/Veuly, or TranscApp for a library from before the rename.
enum DataFolder {
    static var override: URL? {
        let env = ProcessInfo.processInfo.environment
        guard let path = [env["VEULY_HOME"], env["TRANSCAPP_HOME"]].compactMap({ $0 }).first(where: { !$0.isEmpty }) else { return nil }
        return URL(fileURLWithPath: (path as NSString).expandingTildeInPath, isDirectory: true)
    }

    static var url: URL {
        if let given = override { return given }
        let support = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support", isDirectory: true)
        let veuly = support.appendingPathComponent("Veuly", isDirectory: true)
        let old = support.appendingPathComponent("TranscApp", isDirectory: true)
        return !FileManager.default.fileExists(atPath: veuly.path) && FileManager.default.fileExists(atPath: old.path) ? old : veuly
    }

    /// The library the user really has: test mode refuses to run on it.
    static var real: [URL] {
        let support = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support", isDirectory: true)
        return ["Veuly", "TranscApp"].map { support.appendingPathComponent($0, isDirectory: true) }
    }

    /// The interface language the user chose in Settings ("" means the system's), read before the page is there.
    static func chosenLanguage() -> String {
        guard let data = try? Data(contentsOf: url.appendingPathComponent("settings.json")),
              let settings = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return "" }
        return settings["ui_lang"] as? String ?? ""
    }
}

/// The Python that runs app.py. A copy built for other people (`build_mac.py --standalone`) carries its own and needs nothing
/// else. /usr/bin/python3 is the one a developer's build uses, but on a Mac without Apple's Command Line Tools it is only a
/// stub that offers to install them; it is used only when they are there.
enum Python {
    static func find() -> String? {
        let fm = FileManager.default
        if let given = ProcessInfo.processInfo.environment["VEULY_PYTHON"], fm.isExecutableFile(atPath: given) { return given }
        if let own = Bundle.main.resourcePath.map({ $0 + "/python/bin/python3" }), fm.isExecutableFile(atPath: own) { return own }
        if let developer = developerFolder(), fm.isExecutableFile(atPath: developer + "/usr/bin/python3") { return "/usr/bin/python3" }
        let others = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/Library/Frameworks/Python.framework/Versions/Current/bin/python3"]
        return others.first { fm.isExecutableFile(atPath: $0) }
    }

    /// Where the Command Line Tools (or Xcode) are, without running anything that could ask to install them.
    private static func developerFolder() -> String? {
        let selected = "/var/db/xcode_select_link"
        if let target = try? FileManager.default.destinationOfSymbolicLink(atPath: selected) { return target }
        let candidates = ["/Library/Developer/CommandLineTools", "/Applications/Xcode.app/Contents/Developer"]
        return candidates.first { FileManager.default.fileExists(atPath: $0 + "/usr/bin/python3") }
    }
}

/// Test mode, for automated tests only: on when VEULY_TEST_TOKEN holds a long random token. Then the app shows no
/// Dock icon, never takes the focus, keeps its window off screen, answers a control port on 127.0.0.1, skips the
/// save and open panels for paths the test gives, and replaces this Mac's sound with a stand-in, so a test never
/// meets a permission prompt. Off, none of this exists.
enum TestMode {
    static let token: String? = {
        guard let t = ProcessInfo.processInfo.environment["VEULY_TEST_TOKEN"], t.count >= 24 else { return nil }
        return t
    }()
    static var on: Bool { token != nil }
    static var port: UInt16 { UInt16(ProcessInfo.processInfo.environment["VEULY_TEST_PORT"] ?? "") ?? 0 }
    /// "tone" (440 Hz) or the path of a WAV file played as this Mac's sound.
    static var audio: String { ProcessInfo.processInfo.environment["VEULY_TEST_AUDIO"] ?? "tone" }
    /// The stand-in's sample rate, to test how the page copes with a device clock that runs fast or slow.
    static var audioRate: Double { Double(ProcessInfo.processInfo.environment["VEULY_TEST_AUDIO_RATE"] ?? "") ?? 48000 }
    static var saveFolder: URL? {
        guard let p = ProcessInfo.processInfo.environment["VEULY_TEST_SAVE_DIR"], !p.isEmpty else { return nil }
        return URL(fileURLWithPath: p, isDirectory: true)
    }

    /// A test must name its own data folder: never the user's library.
    static func refuseRealLibrary() {
        guard on else { return }
        guard let given = DataFolder.override else {
            Log.write("test mode needs VEULY_HOME")
            exit(2)
        }
        let resolved = given.resolvingSymlinksInPath().standardizedFileURL.path
        if DataFolder.real.contains(where: { $0.resolvingSymlinksInPath().standardizedFileURL.path == resolved }) {
            Log.write("test mode refuses the real data folder")
            exit(2)
        }
    }
}
