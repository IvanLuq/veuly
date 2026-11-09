// app.py, the server that keeps the library, run by the system's Python as a child of the app.
//
// It gets a process group of its own, and a pipe as stdin that the app never writes to: when the app ends for any
// reason, even killed, the pipe closes and app.py shuts down as if Quit had been pressed (`--quit-with-stdin`). Then it
// stops what it started: whisper-cli, ffmpeg and Claude Code, each in a process group of its own (paths.run_child),
// and the rest of its own group (end_group). Once it is gone the app stops that group too (endGroup).
// A server that crashes or is killed stops nothing itself: it tells the app each group of its own it starts and ends
// ("Child: <pid> started|ended", app.py's announce_child), and the app stops the groups still running (endChildren).
// A second copy of the app finds the first one's server through app.py ("Veuly is already running.") and only
// shows it: that server is not this app's to stop.
import Foundation
import Darwin

@MainActor
final class Server {
    enum Failure: Error { case noPython, cannotStart(String) }

    private(set) var pid: pid_t = 0
    private(set) var link: URL?
    private(set) var owned = false
    private(set) var running = false
    private(set) var lastLines: [String] = []      // what it printed last, for the log when it stops
    /// The process groups of its own the server says it started and has not said ended (whisper-cli, ffmpeg, Claude Code).
    private(set) var children: Set<pid_t> = []
    private var stdinWrite: Int32 = -1
    private var output: FileHandle?                 // kept: once it is released, its end of the pipe closes
    private var exitSource: DispatchSourceProcess?
    private var exited: [() -> Void] = []
    private var stopping = false
    // The exit is judged once everything it printed was read: a second copy prints the first one's link and ends.
    private var exitStatus: Int32?
    private var outputDone = false

    /// The page's link, and whether this app started the server (false: another copy's, shown as it is).
    var onLink: ((URL, Bool) -> Void)?
    /// It stopped by itself: true when it quit cleanly (the page's Quit), false when it failed or was killed.
    var onExit: ((Bool) -> Void)?

    let resources: URL

    init(resources: URL) { self.resources = resources }

    func start() throws {
        guard let python = Python.find() else { throw Failure.noPython }
        let script = resources.appendingPathComponent("app/app.py").path
        // -B: no __pycache__ inside the signed bundle. -s: the user's own site-packages stay out.
        let args = [python, "-B", "-s", script, "--no-open", "--quit-with-stdin"]
        var env = ProcessInfo.processInfo.environment
        for key in env.keys where key.hasPrefix("VEULY_TEST_") || ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"].contains(key) {
            env.removeValue(forKey: key)
        }
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"

        var input: [Int32] = [-1, -1], output: [Int32] = [-1, -1]
        guard pipe(&input) == 0, pipe(&output) == 0 else { throw Failure.cannotStart("pipe") }
        var actions: posix_spawn_file_actions_t?
        posix_spawn_file_actions_init(&actions)
        defer { posix_spawn_file_actions_destroy(&actions) }
        posix_spawn_file_actions_adddup2(&actions, input[0], 0)
        posix_spawn_file_actions_adddup2(&actions, output[1], 1)
        posix_spawn_file_actions_adddup2(&actions, output[1], 2)
        posix_spawn_file_actions_addchdir_np(&actions, resources.appendingPathComponent("app").path)
        var attr: posix_spawnattr_t?
        posix_spawnattr_init(&attr)
        defer { posix_spawnattr_destroy(&attr) }
        // Its own process group; only stdin, stdout and stderr are inherited; signals as a fresh process has them.
        posix_spawnattr_setflags(&attr, Int16(POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_CLOEXEC_DEFAULT | POSIX_SPAWN_SETSIGDEF | POSIX_SPAWN_SETSIGMASK))
        posix_spawnattr_setpgroup(&attr, 0)
        var all = sigset_t(), none = sigset_t()
        sigfillset(&all)
        sigemptyset(&none)
        posix_spawnattr_setsigdefault(&attr, &all)
        posix_spawnattr_setsigmask(&attr, &none)

        let argv = args.map { strdup($0) } + [nil]
        let envp = env.map { strdup("\($0.key)=\($0.value)") } + [nil]
        defer { argv.forEach { free($0) }; envp.forEach { free($0) } }
        var child: pid_t = 0
        let status = posix_spawn(&child, python, &actions, &attr, argv, envp)
        close(input[0])
        close(output[1])
        guard status == 0 else {
            close(input[1]); close(output[0])
            throw Failure.cannotStart(String(cString: strerror(status)))
        }
        _ = fcntl(input[1], F_SETFD, FD_CLOEXEC)
        _ = fcntl(output[0], F_SETFD, FD_CLOEXEC)
        pid = child
        stdinWrite = input[1]
        running = true
        stopping = false
        owned = true
        link = nil
        children = []
        exitStatus = nil
        outputDone = false
        Log.write("server: started \(python) app.py, pid \(child)")
        read(output[0])
        watch(child)
    }

    /// What app.py prints, line by line, read on FileHandle's own queue.
    private final class Lines: @unchecked Sendable {
        var pending = Data()
        var already = false      // "Veuly is already running.": the link that follows is another copy's
    }

    /// Lines from app.py: "Open: <link>" is the page; "Veuly is already running." means the link is another copy's.
    private func read(_ fd: Int32) {
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        output = handle
        let lines = Lines()
        handle.readabilityHandler = { [weak self] h in
            let chunk = h.availableData
            if chunk.isEmpty {
                h.readabilityHandler = nil
                Main.run { self?.outputEnded() }
                return
            }
            lines.pending.append(chunk)
            while let end = lines.pending.firstIndex(of: 10) {
                let line = String(decoding: lines.pending[lines.pending.startIndex..<end], as: UTF8.self)
                lines.pending.removeSubrange(lines.pending.startIndex...end)
                if line.hasPrefix("Veuly is already running") { lines.already = true }
                if let (child, running) = Server.childLine(line) {
                    Main.run { self?.child(child, running: running) }
                } else if line.hasPrefix("Open: "), let url = URL(string: String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces)) {
                    let owned = !lines.already
                    Main.run { self?.found(url, owned: owned) }
                } else {
                    Log.write("server: \(line)")
                    Main.run { self?.remember(line) }
                }
            }
        }
    }

    /// "Child: 123 started" / "Child: 123 ended", as app.py's announce_child prints them.
    nonisolated static func childLine(_ line: String) -> (pid_t, Bool)? {
        let parts = line.split(separator: " ")
        guard parts.count == 3, parts[0] == "Child:", let pid = pid_t(parts[1]), pid > 1,
              parts[2] == "started" || parts[2] == "ended" else { return nil }
        return (pid, parts[2] == "started")
    }

    private func child(_ pid: pid_t, running: Bool) {
        if running { children.insert(pid) } else { children.remove(pid) }
    }

    private func remember(_ line: String) {
        lastLines.append(line)
        if lastLines.count > 20 { lastLines.removeFirst(lastLines.count - 20) }
    }

    private func found(_ url: URL, owned: Bool) {
        guard url.host == "127.0.0.1", url.scheme == "http" else { return }
        link = url
        self.owned = owned
        Log.write("server: \(owned ? "serving" : "another copy serves") on port \(url.port ?? 0)")
        onLink?(url, owned)
    }

    private func watch(_ child: pid_t) {
        let source = DispatchSource.makeProcessSource(identifier: child, eventMask: .exit, queue: .global(qos: .userInitiated))
        source.setEventHandler { [weak self] in
            var status: Int32 = 0
            waitpid(child, &status, 0)
            Main.run {
                guard let self, self.pid == child else { return }
                self.exitSource?.cancel()
                self.exitSource = nil
                self.exitStatus = status
                // what it printed last may still be on its way (or held by a program it started): a second at most
                Main.after(1) { self.outputEnded(force: true) }
                if self.outputDone { self.ended(status) }
            }
        }
        exitSource = source
        source.resume()
    }

    private func outputEnded(force: Bool = false) {
        if !force { outputDone = true }
        if let status = exitStatus, running, outputDone || force { ended(status) }
    }

    /// The server is about to stop because this app asked: its exit is not news.
    func expectExit() { stopping = true }

    private func ended(_ status: Int32) {
        guard running else { return }
        running = false
        if stdinWrite >= 0 { close(stdinWrite); stdinWrite = -1 }
        let signaled = status & 0x7f != 0, code = (status >> 8) & 0xff
        Log.write("server: ended, \(signaled ? "signal \(status & 0x7f)" : "exit \(code)")")
        let waiting = exited
        exited = []
        waiting.forEach { $0() }
        if stopping || !owned { return }      // stopped by us, or a second copy that only printed the first one's link
        // What it left in its own group (the PDF's browser, say) has no one to report to now. whisper-cli and Claude
        // Code have groups of their own (paths.run_child): only app.py's own stop_all, at quit, reaches them.
        let group = pid, left = children
        children = []
        if !left.isEmpty { Log.write("server: it left \(left.count) program(s) of its own running (a transcription, a note): stopping them") }
        DispatchQueue.global(qos: .utility).async {
            Server.endChildren(left, of: group)
            Server.endGroup(group)
        }
        onExit?(!signaled && code == 0)
    }

    /// Stops a server this app started: it was asked to quit already (the page's POST /api/quit); closing its stdin
    /// asks again, then a deadline, then SIGTERM (app.py quits cleanly on it), then SIGKILL. What it started goes too.
    func stop(then done: @escaping () -> Void) {
        guard owned, pid > 0 else { done(); return }
        stopping = true
        let group = pid
        let finish = { [weak self] in
            let left = self?.children ?? []
            self?.children = []
            DispatchQueue.global(qos: .userInitiated).async {
                Server.endChildren(left, of: group)     // a server that quit stopped them itself: normally none are left
                Server.endGroup(group)
                Main.run(done)
            }
        }
        guard running else { finish(); return }
        if stdinWrite >= 0 { close(stdinWrite); stdinWrite = -1 }
        exited.append(finish)
        Main.after(5) { [weak self] in
            guard let self, self.running, self.pid == group else { return }
            Log.write("server: still running after 5 s, SIGTERM")
            kill(group, SIGTERM)
            Main.after(3) { [weak self] in
                guard let self, self.running, self.pid == group else { return }
                Log.write("server: still running, SIGKILL")
                killpg(group, SIGKILL)
            }
        }
    }

    /// The groups of its own a server that is gone left running: each one still there gets SIGTERM, then SIGKILL. Only a
    /// group whose leader ended, or is an orphan now (or still the server's child), is taken: never a process that got
    /// the same number since.
    nonisolated static func endChildren(_ groups: Set<pid_t>, of server: pid_t) {
        let ours = groups.filter { group in
            guard group > 1, killpg(group, 0) == 0 else { return false }
            guard let parent = parentOf(group) else { return true }        // the leader ended; its group is still the one it made
            return parent == 1 || parent == server
        }
        guard !ours.isEmpty else { return }
        ours.forEach { killpg($0, SIGTERM) }
        for _ in 0..<20 {
            usleep(50_000)
            if ours.allSatisfy({ killpg($0, 0) != 0 }) { return }
        }
        ours.forEach { killpg($0, SIGKILL) }
    }

    /// The parent of a running process, or nil when there is no such process.
    nonisolated static func parentOf(_ pid: pid_t) -> pid_t? {
        var info = kinfo_proc()
        var size = MemoryLayout<kinfo_proc>.stride
        var name: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, pid]
        guard sysctl(&name, 4, &info, &size, nil, 0) == 0, size > 0 else { return nil }
        return info.kp_eproc.e_ppid
    }

    /// After the server is gone, what it started may still run (a transcription, a note being written): the group
    /// gets SIGTERM, and SIGKILL a moment later for anything still there.
    nonisolated static func endGroup(_ group: pid_t) {
        guard group > 0, killpg(group, 0) == 0 else { return }
        killpg(group, SIGTERM)
        for _ in 0..<20 {
            usleep(50_000)
            if killpg(group, 0) != 0 { return }
        }
        killpg(group, SIGKILL)
    }
}
