// "This computer's sound", recorded by the app because a web view cannot: everything this Mac plays except Veuly
// itself, through a Core Audio process tap (macOS 14.2 and later), as 16 kHz mono for the page's recorder.
//
//   Core Audio's real-time thread  -> SoundRing (copy only: no allocation, no waiting)
//   SoundPump, every 100 ms         -> 16 kHz Int16, numbered by sample (`at`) -> the page (web/native.js)
//
// `at` counts samples of real time since the start: frames that never arrived (a device that stopped, a full ring)
// still move it on, so the page can put silence there and keep this track in step with the microphone's.
// A device that changes its rate under the tap (AirPods switch to a lower one when a microphone opens) is caught by
// Core Audio's notifications, and failing those by the ring itself, which measures the rate its frames really come
// at: either way the tap is built again at the new rate.
// In test mode a stand-in (a tone or a WAV file) writes into the same ring, so everything after it is the real path.
import AVFoundation
import AudioToolbox
import CoreAudio
import Foundation
import os

private let hostTicksPerSecond: Double = {
    var info = mach_timebase_info_data_t()
    mach_timebase_info(&info)
    return 1e9 * Double(info.denom) / Double(info.numer)
}()

/// Frames from the audio thread to the converter: a fixed buffer, one writer and one reader. The lock guards only a
/// few counters: the writer copies its frames into free space (never into frames not read yet: when the buffer is
/// full it drops its newest ones), and the reader copies unread frames out after letting go of the lock. So the lock
/// is held for a few instructions at a time, and the writer, which must never wait, only tries it, a few times.
/// Frames that could not be stored, and host time that jumped (a device that stopped), are recorded as gaps at their
/// place in the stream, for the page to fill with silence.
final class SoundRing: @unchecked Sendable {
    let capacity: Int
    let rate: Double                                // the writer's (nominal) sample rate
    private let samples: UnsafeMutablePointer<Float>
    private let lock: UnsafeMutablePointer<os_unfair_lock>
    // under the lock
    private var written = 0, taken = 0               // frames stored and read since the start
    private var lastHost: UInt64 = 0                 // host time just after the newest frame stored
    private var firstHost: UInt64 = 0
    private let gapAt: UnsafeMutablePointer<Int>     // gaps not read yet: before stored frame number gapAt[i], gapLength[i] frames
    private let gapLength: UnsafeMutablePointer<Int>
    private let gapRoom = 64
    private var gaps = 0
    private var paceFrames = 0, paceTicks: UInt64 = 0   // frames written over host time, for the reader's rate check
    // the writer's own
    private var unrecorded = 0
    private var expectedHost: UInt64 = 0
    private var lastWrite: UInt64 = 0, lastCount = 0    // the previous write: when, and how many frames
    private var newFrames = 0, newTicks: UInt64 = 0     // pace not handed over yet (the lock was busy)

    init(rate: Double, seconds: Double = 10) {
        self.rate = rate
        capacity = Int(rate * seconds)
        samples = .allocate(capacity: capacity)
        samples.initialize(repeating: 0, count: capacity)
        lock = .allocate(capacity: 1)
        lock.initialize(to: os_unfair_lock())
        gapAt = .allocate(capacity: gapRoom)
        gapLength = .allocate(capacity: gapRoom)
    }

    deinit {
        samples.deallocate()
        lock.deallocate()
        gapAt.deallocate()
        gapLength.deallocate()
    }

    /// Under the lock: `length` frames missing before stored frame `position` (merged into the last gap when full).
    private func addGap(_ length: Int, at position: Int) {
        guard length > 0 else { return }
        if gaps > 0, gapAt[gaps - 1] == position || gaps == gapRoom {
            gapLength[gaps - 1] += length
        } else {
            gapAt[gaps] = position
            gapLength[gaps] = length
            gaps += 1
        }
    }

    /// The audio thread's side: `count` mono frames, the first captured at host time `host`. Host time that jumps past
    /// what the last write covered (a stopped device, a skipped cycle) is a gap of that many frames.
    func write(_ source: UnsafePointer<Float>, count: Int, host: UInt64) {
        var gap = 0
        if expectedHost > 0, host > expectedHost {
            let seconds = Double(host - expectedHost) / hostTicksPerSecond
            if seconds > 0.05 { gap = Int(seconds * rate) }
        }
        expectedHost = host + UInt64(Double(count) / rate * hostTicksPerSecond)
        // The pace: the previous write's frames over the time to this one. A pause (a stalled device) is no pace.
        if lastWrite > 0, host > lastWrite, Double(host - lastWrite) / hostTicksPerSecond < 0.2 {
            newFrames += lastCount
            newTicks += host - lastWrite
        }
        lastWrite = host
        lastCount = count
        var locked = false
        for _ in 0..<64 where !locked { locked = os_unfair_lock_trylock(lock) }   // held for a few instructions at most
        guard locked else {
            unrecorded += gap + count
            return
        }
        paceFrames += newFrames
        paceTicks += newTicks
        newFrames = 0
        newTicks = 0
        if firstHost == 0 { firstHost = host }
        addGap(unrecorded + gap, at: written)
        unrecorded = 0
        let n = min(count, capacity - (written - taken))    // full: the reader fell behind, the newest frames go
        var at = written % capacity, done = 0
        while done < n {
            let run = min(n - done, capacity - at)
            (samples + at).update(from: source + done, count: run)
            done += run
            at = (at + run) % capacity
        }
        written += n
        addGap(count - n, at: written)
        lastHost = expectedHost
        os_unfair_lock_unlock(lock)
    }

    /// The rate frames have really come at (frames per second of host time) since the last call, once `seconds` of
    /// writes are in; nil before that.
    func takePace(seconds: Double) -> Double? {
        os_unfair_lock_lock(lock)
        defer { os_unfair_lock_unlock(lock) }
        let span = Double(paceTicks) / hostTicksPerSecond
        guard span >= seconds else { return nil }
        let pace = Double(paceFrames) / span
        paceFrames = 0
        paceTicks = 0
        return pace
    }

    /// Host times of the first frame ever stored and just after the newest one (0 before any).
    func hosts() -> (first: UInt64, last: UInt64) {
        os_unfair_lock_lock(lock)
        defer { os_unfair_lock_unlock(lock) }
        return (firstHost, lastHost)
    }

    /// The reader's side, in order: the frames missing before the next unread frame (`gap`), else up to `max` frames
    /// that come before the next gap; and the host time just after the newest frame stored.
    func read(into target: UnsafeMutablePointer<Float>, max: Int) -> (count: Int, gap: Int, host: UInt64, first: UInt64) {
        os_unfair_lock_lock(lock)
        if gaps > 0, gapAt[0] <= taken {
            let gap = gapLength[0]
            gaps -= 1
            for i in 0..<gaps { gapAt[i] = gapAt[i + 1]; gapLength[i] = gapLength[i + 1] }
            let hosts = (lastHost, firstHost)
            os_unfair_lock_unlock(lock)
            return (0, gap, hosts.0, hosts.1)
        }
        let limit = gaps > 0 ? gapAt[0] : written
        let count = min(max, limit - taken), from = taken
        let hosts = (lastHost, firstHost)
        os_unfair_lock_unlock(lock)
        // The writer never writes into frames not read yet, so they are copied without the lock.
        var at = from % capacity, done = 0
        while done < count {
            let run = min(count - done, capacity - at)
            (target + done).update(from: samples + at, count: run)
            done += run
            at = (at + run) % capacity
        }
        os_unfair_lock_lock(lock)
        taken += count
        os_unfair_lock_unlock(lock)
        return (count, 0, hosts.0, hosts.1)
    }
}

protocol SoundSource: AnyObject, Sendable {
    var ring: SoundRing { get }
    /// A new ring after the source started again on another device (another rate): the pump moves on to it.
    var onNewRing: ((SoundRing) -> Void)? { get set }
    func start() throws
    func stop()
    /// Build it again (another device or rate, frames that stopped coming), then hand the new ring over.
    func restart(_ reason: String)
}

/// Turns the ring's frames into 16 kHz Int16 for the page, every 100 ms, on a queue of its own.
final class SoundPump: @unchecked Sendable {
    private let queue = DispatchQueue(label: "app.veuly.sound.pump", qos: .userInteractive)
    private var timer: DispatchSourceTimer?
    private var rings: [SoundRing]
    private var lostSeen = 0
    private var gapFrom: UInt64 = 0                    // host time the previous device's last frame ended at
    private var converter: AVAudioConverter?
    private var input: AVAudioPCMBuffer?
    private let output = AVAudioPCMBuffer(pcmFormat: AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: false)!,
                                          frameCapacity: 16000)!
    private var at = 0                                 // the 16 kHz sample the next block starts at
    private var lastFrames = DispatchTime.now()        // when frames last came in
    private var quietReported = false
    private var framesIn = 0, ticks = 0                // for the log: what came in, how often it was looked at
    private var pace = 0.0                             // the rate the newest ring's frames really came at, last measured
    private weak var wrongRing: SoundRing?             // the ring whose rate was found wrong (said once)
    private var wrongTimes: [DispatchTime] = []        // when: past three in a minute, a device that says one rate and
                                                       // keeps another is left as it is rather than rebuilt for ever
    private let started = DispatchTime.now()
    /// (at, t, pcm): the first sample's number, its wall-clock time in ms, and the samples as little-endian Int16.
    var onBlock: ((Int, Double, Data) -> Void)?
    /// No frames for a while: the source has to start again.
    var onSilentSource: (() -> Void)?
    /// The frames come at another rate than the ring was made for (the device changed it, and no notification said
    /// so): the source has to start again, at the rate it has now.
    var onWrongRate: ((Double) -> Void)?

    init(ring: SoundRing) { rings = [ring] }

    deinit { timer?.cancel() }

    func start() {
        let t = DispatchSource.makeTimerSource(flags: .strict, queue: queue)
        t.schedule(deadline: .now() + .milliseconds(100), repeating: .milliseconds(100), leeway: .milliseconds(5))
        t.setEventHandler { [weak self] in self?.tick() }
        timer = t
        t.resume()
    }

    func stop() {
        timer?.cancel()
        timer = nil
    }

    /// The source moved to another ring (a new device): what is left in the old one goes first.
    func add(_ ring: SoundRing) { queue.async { self.rings.append(ring) } }

    private func tick() {
        while drain(rings[0]) == 0, rings.count > 1 {
            // the old ring is empty and its device has stopped: the time until the new device's first frame is silence
            gapFrom = rings[0].hosts().last
            rings.removeFirst()
            converter = nil
            lostSeen = 0
        }
        if DispatchTime.now().uptimeNanoseconds - lastFrames.uptimeNanoseconds > 2_000_000_000, !quietReported {
            quietReported = true
            onSilentSource?()
        }
        ticks += 1
        if ticks % 10 == 0, let ring = rings.last, ring !== wrongRing, let measured = ring.takePace(seconds: 2) {
            pace = measured
            if abs(measured / ring.rate - 1) > 0.05 {     // 5 %: far beyond any clock's drift (parts per million)
                wrongRing = ring
                let now = DispatchTime.now()
                wrongTimes = wrongTimes.filter { now.uptimeNanoseconds - $0.uptimeNanoseconds < 60_000_000_000 } + [now]
                Log.write("sound: frames come at \(Int(measured)) Hz, not \(Int(ring.rate))\(wrongTimes.count > 3 ? "; left as it is" : "")")
                if wrongTimes.count <= 3 { onWrongRate?(measured) }
            }
        }
        if ticks % 300 == 0 {                          // every 30 s: what the log needs to tell a device problem apart
            let seconds = Double(DispatchTime.now().uptimeNanoseconds - started.uptimeNanoseconds) / 1e9
            Log.write("sound: \(Int(seconds)) s: \(framesIn) frames at \(Int(rings[0].rate)) Hz (measured \(Int(pace))), \(lostSeen) lost, sent up to \(Double(at) / 16000) s")
        }
    }

    /// Converts everything the ring has; returns how many frames it read.
    private func drain(_ ring: SoundRing) -> Int {
        if converter == nil {
            let from = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: ring.rate, channels: 1, interleaved: false)!
            converter = AVAudioConverter(from: from, to: output.format)
            input = AVAudioPCMBuffer(pcmFormat: from, frameCapacity: AVAudioFrameCount(ring.rate / 4))   // a quarter second per pass
        }
        guard let converter, let input else { return 0 }
        var total = 0
        while true {
            let (count, gap, host, first) = ring.read(into: input.floatChannelData![0], max: Int(input.frameCapacity))
            if gapFrom > 0, first > 0 {
                if first > gapFrom { at += Int(Double(first - gapFrom) / hostTicksPerSecond * 16000) }
                gapFrom = 0
            }
            if gap > 0 {                               // frames that never arrived: the page fills them with silence
                at += Int((Double(gap) * 16000 / ring.rate).rounded())
                lostSeen += gap
                continue
            }
            guard count > 0 else { break }
            total += count
            framesIn += count
            lastFrames = .now()
            quietReported = false
            input.frameLength = AVAudioFrameCount(count)
            var given = false
            output.frameLength = 0
            var error: NSError?
            converter.convert(to: output, error: &error) { _, status in
                if given { status.pointee = .noDataNow; return nil }
                given = true
                status.pointee = .haveData
                return input
            }
            let produced = Int(output.frameLength)
            if produced > 0, let samples = output.int16ChannelData?[0] {
                let data = Data(bytes: samples, count: produced * 2)
                // the newest frame was captured at `host`: the block's first sample, `produced` samples before it
                let ageMs = Double(mach_absolute_time() &- host) / hostTicksPerSecond * 1000
                let t = Date().timeIntervalSince1970 * 1000 - ageMs - Double(produced) / 16
                let start = at
                at += produced
                onBlock?(start, t, data)
            }
        }
        return total
    }
}

/// The Core Audio tap: a stereo mix of every process except Veuly, read through a private aggregate device that
/// runs on the default output device's clock (drift-compensated). Built again when the default output changes, and
/// when the rate or format of the device or the tap does.
final class SystemTap: SoundSource, @unchecked Sendable {
    private(set) var ring = SoundRing(rate: 48000)
    var onNewRing: ((SoundRing) -> Void)?
    private let control = DispatchQueue(label: "app.veuly.sound.control")
    private var tap = AudioObjectID(kAudioObjectUnknown)
    private var device = AudioObjectID(kAudioObjectUnknown)
    private var proc: AudioDeviceIOProcID?
    private let io = TapReader()
    private var listener: AudioObjectPropertyListenerBlock?
    private var watched: [(AudioObjectID, AudioObjectPropertyAddress)] = []   // what the listener is on: the system, then this build's objects
    private var stopped = false
    private var restarting = false

    struct Failure: Error, CustomStringConvertible {
        let step: String, status: OSStatus
        var description: String { "\(step) failed (\(status))" }
    }

    func start() throws {
        let listener: AudioObjectPropertyListenerBlock = { [weak self] count, addresses in
            let what = count > 0 ? addresses.pointee.mSelector : 0
            self?.restart(what == kAudioHardwarePropertyDefaultOutputDevice ? "the output device changed"
                          : what == kAudioTapPropertyFormat ? "the tap's format changed" : "the device's rate or streams changed")
        }
        self.listener = listener
        try control.sync {             // listeners call back on `control`: nothing of theirs runs before this is done
            try build()
            watch(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultOutputDevice)
        }
    }

    func stop() {
        control.sync {             // after any restart on its way, never in the middle of one
            stopped = true
            tearDown()
            unwatch(all: true)
        }
    }

    /// Normally stop() took it down already; one dropped without it must not go on recording everything the Mac plays.
    deinit {
        if !stopped {
            tearDown()
            unwatch(all: true)
        }
    }

    /// Another output device (headphones plugged in, AirPods), another rate or format: the same tap, built again on
    /// the device as it is now. Notifications come in bursts: one rebuild, a moment after the first.
    func restart(_ reason: String) {
        control.async {
            guard !self.stopped, !self.restarting else { return }
            self.restarting = true
            self.control.asyncAfter(deadline: .now() + 0.3) {
                self.restarting = false
                guard !self.stopped else { return }
                Log.write("sound: \(reason), starting again")
                self.tearDown()
                do {
                    try self.build()
                    self.onNewRing?(self.ring)
                } catch {
                    Log.write("sound: could not start again: \(error)")
                }
            }
        }
    }

    private func build() throws {
        // Veuly's own process object, so that what the app itself plays is left out.
        var pid = getpid()
        var me = AudioObjectID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        var address = Self.address(kAudioHardwarePropertyTranslatePIDToProcessObject)
        AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, UInt32(MemoryLayout<pid_t>.size), &pid, &size, &me)
        let description = CATapDescription(stereoGlobalTapButExcludeProcesses: me == kAudioObjectUnknown ? [] : [me])
        description.uuid = UUID()
        description.name = "Veuly"
        description.isPrivate = true
        description.muteBehavior = .unmuted
        var newTap = AudioObjectID(kAudioObjectUnknown)
        try check(AudioHardwareCreateProcessTap(description, &newTap), "creating the tap")
        tap = newTap

        var format = AudioStreamBasicDescription()
        size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        address = Self.address(kAudioTapPropertyFormat)
        try check(AudioObjectGetPropertyData(tap, &address, 0, nil, &size, &format), "reading the tap's format")

        let (outputID, output) = try Self.defaultOutput()
        let composition: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Veuly",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: output,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: output]],
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true, kAudioSubTapUIDKey: description.uuid.uuidString]],
        ]
        var newDevice = AudioObjectID(kAudioObjectUnknown)
        try check(AudioHardwareCreateAggregateDevice(composition as CFDictionary, &newDevice), "creating the device")
        device = newDevice

        var rate = Float64(0)
        size = UInt32(MemoryLayout<Float64>.size)
        address = Self.address(kAudioDevicePropertyNominalSampleRate)
        if AudioObjectGetPropertyData(device, &address, 0, nil, &size, &rate) != noErr || rate <= 0 { rate = format.mSampleRate }
        Log.write("sound: tap \(format.mChannelsPerFrame) ch at \(format.mSampleRate) Hz, flags \(format.mFormatFlags); device at \(rate) Hz; output \(output)")
        ring = SoundRing(rate: rate)
        io.channels = Int(max(1, format.mChannelsPerFrame))
        let ring = self.ring, io = self.io
        // On Core Audio's real-time thread (no queue): mix down and copy, nothing else.
        try check(AudioDeviceCreateIOProcIDWithBlock(&proc, device, nil) { _, input, time, _, _ in
            io.take(input, time: time, into: ring)
        }, "adding the reader")
        try check(AudioDeviceStart(device, proc), "starting")
        // What makes this build out of date: the rate of the device or of the output device under it (AirPods drop to
        // a lower one for their microphone, as a call in Teams or Zoom starts), the device's streams, the tap's format.
        watch(device, kAudioDevicePropertyNominalSampleRate)
        watch(device, kAudioDevicePropertyStreamConfiguration, scope: kAudioObjectPropertyScopeInput)
        watch(outputID, kAudioDevicePropertyNominalSampleRate)
        watch(tap, kAudioTapPropertyFormat)
    }

    private func tearDown() {
        unwatch(all: false)
        if device != kAudioObjectUnknown {
            if let proc {
                AudioDeviceStop(device, proc)
                AudioDeviceDestroyIOProcID(device, proc)
            }
            AudioHardwareDestroyAggregateDevice(device)
        }
        if tap != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tap) }
        proc = nil
        device = AudioObjectID(kAudioObjectUnknown)
        tap = AudioObjectID(kAudioObjectUnknown)
    }

    private func watch(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector, scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) {
        guard object != kAudioObjectUnknown, let listener else { return }
        var address = AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
        let status = AudioObjectAddPropertyListenerBlock(object, &address, control, listener)
        if status == noErr { watched.append((object, address)) } else { Log.write("sound: cannot follow \(selector) of \(object) (\(status))") }
    }

    /// The listeners on this build's objects; `all`: the system's (the default output) too.
    private func unwatch(all: Bool) {
        guard let listener else { return }
        let system = AudioObjectID(kAudioObjectSystemObject)
        watched.removeAll { entry in
            guard all || entry.0 != system else { return false }
            var address = entry.1
            AudioObjectRemovePropertyListenerBlock(entry.0, &address, control, listener)
            return true
        }
    }

    private func check(_ status: OSStatus, _ step: String) throws {
        if status != noErr {
            tearDown()
            throw Failure(step: step, status: status)
        }
    }

    static func address(_ selector: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    }

    static func defaultOutput() throws -> (AudioObjectID, String) {
        var id = AudioObjectID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        var address = address(kAudioHardwarePropertyDefaultOutputDevice)
        var status = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &id)
        guard status == noErr, id != kAudioObjectUnknown else { throw Failure(step: "finding the output device", status: status) }
        var uid: Unmanaged<CFString>?
        size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        address = Self.address(kAudioDevicePropertyDeviceUID)
        status = AudioObjectGetPropertyData(id, &address, 0, nil, &size, &uid)
        guard status == noErr, let name = uid?.takeRetainedValue() else { throw Failure(step: "reading the output device", status: status) }
        return (id, name as String)
    }
}

/// What the tap's real-time callback does: picks the tap's stream out of the aggregate device's input (it comes
/// after any input the output device has of its own), mixes it to mono into memory reserved beforehand, and hands
/// it to the ring.
final class TapReader: @unchecked Sendable {
    var channels = 2
    private let capacity = 8192
    private let mono: UnsafeMutablePointer<Float>

    init() {
        mono = .allocate(capacity: capacity)
        mono.initialize(repeating: 0, count: capacity)
    }

    deinit { mono.deallocate() }

    func take(_ list: UnsafePointer<AudioBufferList>, time: UnsafePointer<AudioTimeStamp>, into ring: SoundRing) {
        let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: list))
        guard buffers.count > 0 else { return }
        let host = time.pointee.mFlags.contains(.hostTimeValid) ? time.pointee.mHostTime : mach_absolute_time()
        let last = buffers[buffers.count - 1]
        // Interleaved: the last buffer has every channel. Otherwise: the last `channels` buffers, one channel each.
        let split = last.mNumberChannels == 1 && channels > 1 && buffers.count >= channels
        let width = split ? 1 : Int(max(1, last.mNumberChannels))
        let frames = Int(last.mDataByteSize) / (MemoryLayout<Float>.size * width)
        var done = 0
        while done < frames {
            let n = min(capacity, frames - done)
            if split {
                for i in 0..<n { mono[i] = 0 }
                for b in (buffers.count - channels)..<buffers.count {
                    guard let data = buffers[b].mData?.assumingMemoryBound(to: Float.self) else { continue }
                    for i in 0..<n { mono[i] += data[done + i] }
                }
                let scale = 1 / Float(channels)
                for i in 0..<n { mono[i] *= scale }
            } else {
                guard let data = last.mData?.assumingMemoryBound(to: Float.self) else { return }
                let scale = 1 / Float(width)
                for i in 0..<n {
                    var sum: Float = 0
                    for c in 0..<width { sum += data[(done + i) * width + c] }
                    mono[i] = sum * scale
                }
            }
            let offset = UInt64(Double(done) / ring.rate * hostTicksPerSecond)
            ring.write(mono, count: n, host: host + offset)
            done += n
        }
    }
}

/// Test mode's stand-in for the tap: a 440 Hz tone, or a WAV file, written into a ring as a device would, paced by
/// the host clock (at VEULY_TEST_AUDIO_RATE frames per real second, so a test can make the "device" run fast or slow).
/// `stall` stops it for a while: `gap` drops those frames (a device that stopped), `burst` delivers them late.
/// `changeRate` makes the tone's frames come at another rate under the same ring (a device that switched rates);
/// `restart` makes a new ring at the rate it has now, as the tap's rebuild does. `startDelay`: a start that takes
/// that long, as the tap's first one does while macOS asks the user.
final class StandInSound: SoundSource, @unchecked Sendable {
    private(set) var ring: SoundRing
    var onNewRing: ((SoundRing) -> Void)?
    private let queue = DispatchQueue(label: "app.veuly.sound.standin", qos: .userInteractive)
    private var timer: DispatchSourceTimer?
    private var actual: Double             // frames per second of real time
    private var baseHost: UInt64 = 0       // host time of frame number `baseFrame` (moved on when the rate changes)
    private var baseFrame = 0
    private var frames = 0                 // frames due so far (written or skipped)
    private var phase = 0.0
    private var wav: [Float] = []
    private var wavAt = 0
    private var stallUntil: UInt64 = 0
    private var stallMode = "gap"
    private let startDelay: Double
    private let scratch: UnsafeMutablePointer<Float>
    private let scratchSize = 1 << 18

    init(spec: String, rate: Double, startDelay: Double = 0) {
        var nominal = 48000.0
        if spec != "tone", let (samples, fileRate) = Self.readWav(spec) {
            wav = samples
            nominal = fileRate
        }
        if wav.isEmpty { nominal = 48000 }
        actual = wav.isEmpty ? rate : nominal * rate / 48000
        ring = SoundRing(rate: nominal)
        self.startDelay = startDelay
        scratch = .allocate(capacity: scratchSize)
    }

    deinit {
        timer?.cancel()
        scratch.deallocate()
    }

    func start() throws {
        if startDelay > 0 { Thread.sleep(forTimeInterval: startDelay) }
        baseHost = mach_absolute_time()
        let t = DispatchSource.makeTimerSource(flags: .strict, queue: queue)
        t.schedule(deadline: .now(), repeating: .milliseconds(10), leeway: .milliseconds(1))
        t.setEventHandler { [weak self] in self?.tick() }
        timer = t
        t.resume()
    }

    func stop() {
        timer?.cancel()
        timer = nil
    }

    func stall(seconds: Double, mode: String) {
        queue.async {
            self.stallMode = mode
            self.stallUntil = mach_absolute_time() + UInt64(seconds * hostTicksPerSecond)
        }
    }

    /// The tone's frames come at `rate` from now on, the ring staying as it was made.
    func changeRate(_ rate: Double) {
        queue.async {
            guard self.wav.isEmpty, rate > 0 else { return }
            self.baseHost += UInt64(Double(self.frames - self.baseFrame) / self.actual * hostTicksPerSecond)
            self.baseFrame = self.frames
            self.actual = rate
        }
    }

    func restart(_ reason: String) {
        queue.async {
            Log.write("sound: \(reason), starting the stand-in again at \(Int(self.actual)) Hz")
            self.ring = SoundRing(rate: self.wav.isEmpty ? self.actual : self.ring.rate)
            self.onNewRing?(self.ring)
        }
    }

    private func tick() {
        let now = mach_absolute_time()
        let due = Int(Double(now - baseHost) / hostTicksPerSecond * actual) - (frames - baseFrame)
        guard due > 0 else { return }
        if now < stallUntil {
            if stallMode == "gap" { frames += due }    // never delivered: the ring sees the jump in host time
            return
        }
        var left = due
        while left > 0 {
            let n = min(left, scratchSize)
            for i in 0..<n {
                if wav.isEmpty {
                    scratch[i] = Float(0.25 * sin(phase))
                    phase += 2 * Double.pi * 440 / actual
                    if phase > 2 * Double.pi { phase -= 2 * Double.pi }
                } else {
                    scratch[i] = wav[wavAt]
                    wavAt = (wavAt + 1) % wav.count
                }
            }
            let host = baseHost + UInt64(Double(frames - baseFrame) / actual * hostTicksPerSecond)
            ring.write(scratch, count: n, host: host)
            frames += n
            left -= n
        }
    }

    /// 16-bit PCM WAV, any rate, mixed to mono.
    static func readWav(_ path: String) -> ([Float], Double)? {
        guard let data = FileManager.default.contents(atPath: path), data.count > 44,
              String(decoding: data[0..<4], as: UTF8.self) == "RIFF" else { return nil }
        var channels = 1, rate = 16000.0, bits = 16, offset = 12
        while offset + 8 <= data.count {
            let id = String(decoding: data[offset..<offset + 4], as: UTF8.self)
            let size = Int(data[offset + 4]) | Int(data[offset + 5]) << 8 | Int(data[offset + 6]) << 16 | Int(data[offset + 7]) << 24
            let body = offset + 8
            if id == "fmt " {
                channels = Int(data[body + 2]) | Int(data[body + 3]) << 8
                rate = Double(Int(data[body + 4]) | Int(data[body + 5]) << 8 | Int(data[body + 6]) << 16 | Int(data[body + 7]) << 24)
                bits = Int(data[body + 14]) | Int(data[body + 15]) << 8
            } else if id == "data", bits == 16, channels > 0 {
                let end = min(data.count, body + size)
                let frames = (end - body) / (2 * channels)
                var out = [Float](repeating: 0, count: frames)
                data.withUnsafeBytes { raw in
                    for f in 0..<frames {
                        var sum: Float = 0
                        for c in 0..<channels {
                            sum += Float(raw.loadUnaligned(fromByteOffset: body + (f * channels + c) * 2, as: Int16.self)) / 32768
                        }
                        out[f] = sum / Float(channels)
                    }
                }
                return (out, rate)
            }
            offset = body + size + (size & 1)
        }
        return nil
    }
}

/// The page's "this computer's sound": one source at a time, its pump, and what goes to the page.
@MainActor
final class SystemSound {
    private var source: SoundSource?
    private var pump: SoundPump?
    /// Counts starts and stops. A start finishes in the background (the tap's first one waits for macOS to ask the
    /// user): when a stop or a newer start came meanwhile, what it started is stopped at once, never left running.
    private var generation = 0
    /// (at, t, base64 Int16): a block for the page.
    var onBlock: ((Int, Double, String) -> Void)?
    var running: Bool { source != nil }
    /// Test mode: how long the next stand-in takes to start.
    var startDelay = 0.0

    /// Starts the tap (or the test stand-in), off the main thread: creating a tap can wait for the system's permission
    /// question the first time.
    func start(done: @escaping (String?) -> Void) {
        stop()
        generation += 1
        let mine = generation, delay = startDelay
        let make: () -> SoundSource = {
            TestMode.on ? StandInSound(spec: TestMode.audio, rate: TestMode.audioRate, startDelay: delay) : SystemTap()
        }
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let source = make()
                try source.start()
                Main.run {
                    guard mine == self.generation else {
                        Log.write("sound: a start that a stop or a newer start overtook, stopped")
                        DispatchQueue.global(qos: .userInitiated).async { source.stop() }
                        done("stopped")
                        return
                    }
                    self.started(source)
                    done(nil)
                }
            } catch {
                Log.write("sound: \(error)")
                Main.run { done("failed") }
            }
        }
    }

    private func started(_ source: SoundSource) {
        self.source = source
        let pump = SoundPump(ring: source.ring)
        source.onNewRing = { [weak pump] ring in pump?.add(ring) }
        pump.onBlock = { [weak self] at, t, data in
            let text = data.base64EncodedString()
            Main.run { self?.onBlock?(at, t, text) }
        }
        // Nothing for two seconds (a tap whose device went away), or frames at another rate than the ring's: starting
        // it again is what can be done.
        pump.onSilentSource = { [weak source] in source?.restart("no frames for 2 s") }
        pump.onWrongRate = { [weak source] pace in source?.restart("frames came at \(Int(pace)) Hz") }
        self.pump = pump
        pump.start()
        Log.write("sound: started (\(TestMode.on ? "stand-in \(TestMode.audio)" : "Core Audio tap"))")
    }

    func stop() {
        generation += 1                 // a start still on its way is stopped when it arrives
        guard let source else { return }
        pump?.stop()
        pump = nil
        let old = source
        self.source = nil
        DispatchQueue.global(qos: .userInitiated).async { old.stop() }
        Log.write("sound: stopped")
    }

    /// The Mac woke up: its devices may have changed or gone while it slept, so the capture is built again.
    func wake() {
        source?.restart("the Mac woke up")
    }

    /// Test mode: make the stand-in stop for a while.
    func stall(seconds: Double, mode: String) {
        (source as? StandInSound)?.stall(seconds: seconds, mode: mode)
    }

    /// Test mode: the stand-in's frames come at another rate; `notify`: as Core Audio would say, it is started again
    /// (otherwise the pump has to find out).
    func changeRate(_ rate: Double, notify: Bool) {
        guard let standIn = source as? StandInSound else { return }
        standIn.changeRate(rate)
        if notify { Main.after(0.3) { standIn.restart("the device's rate changed") } }
    }
}
