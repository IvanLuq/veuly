// Microphone capture in the page: 16 kHz, mono, 16-bit, which is what the transcriber wants.
// It can also record the computer's own sound (an online class) through the browser's screen sharing, or, in the
// Mac app, from the app itself (Recorder.native, web/native.js): a track that arrives already at 16 kHz.
// The same code runs on Mac and Windows, so there is no per-system recording code to maintain.
// While muted, silence is written instead of the microphone: muted speech never leaves this page.
// With 'both', the microphone and the computer's sound are kept as two tracks, so the transcript can
// tell the user's own voice from everyone else's.
class Recorder {
  constructor({ onChunk, onLevel, onEnded }) {
    this.onChunk = onChunk;       // (Int16Array, Int16Array or undefined) about every 2 seconds: the second is the computer's sound when it is a track of its own
    this.onLevel = onLevel;       // (dB) a few times per second
    this.onEnded = onEnded;       // ('mic' or 'share') a source stopped: a microphone unplugged, a tab that stopped sharing
    this.muted = false;
    this.tracks = [];
  }

  // ratio: input samples per output sample (the audio graph's rate / 16 kHz; 1 for a source at 16 kHz already).
  newTrack(ratio = this.ratio) {
    return { pending: [], length: 0, position: 0, carry: new Float32Array(0), ratio, flushed: 0 };   // position: fractional read position for resampling
  }

  // Whether this computer's sound can be recorded here: in a browser by sharing a tab or the screen; in the Mac app,
  // which records it itself (Recorder.native, web/native.js).
  static canShare() {
    return !!(Recorder.native || (navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia));
  }

  // source: 'mic' (a classroom), 'system' (an online class playing on this computer) or 'both'.
  // The screen-share picker must be opened in the click itself (Safari refuses it after any await),
  // so the caller opens it with Recorder.share() before awaiting anything and passes the promise in.
  static share() {
    if (Recorder.native) return Recorder.native.open();      // the Mac app: everything the Mac plays, no picker
    return navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: 1 },
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      systemAudio: 'include',
    });
  }

  // A failed request says which one it was (`source` 'mic' or 'share'), so the page names the right thing.
  static tagged(source) {
    return (e) => { if (e && typeof e === 'object') e.source = source; throw e; };
  }

  async start(source = 'mic', sharing = null) {
    this.streams = [];
    this.kinds = { mic: ['mic'], system: ['share'] }[source] || ['mic', 'share'];   // what each stream is, in order
    this.micOptions = { channelCount: 1, echoCancellation: source === 'both', noiseSuppression: false, autoGainControl: true };
    let shared = null;
    if (source !== 'mic') {
      // The browser shows its own picker. Chrome and Edge can share a tab's sound on any system,
      // and the whole computer's sound on Windows; a video track always comes along and is ignored.
      shared = await (sharing || Recorder.share()).catch(Recorder.tagged('share'));
      this.streams.push(shared);
      if (!shared.getAudioTracks().length) {
        const err = new Error('The shared tab or screen has no audio.');
        err.name = 'NoSharedAudio';
        err.source = 'share';
        throw err;
      }
    }
    if (source !== 'system') {
      this.streams.push(await navigator.mediaDevices.getUserMedia({ audio: this.micOptions }).catch(Recorder.tagged('mic')));
      this.streams.reverse();   // microphone first, the computer's sound second
    }
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const media = this.streams.filter((s) => !s.native);     // the Mac app's own sound arrives as samples, not as a stream
    const connect = (options) => {
      this.ctx = new Ctx(options);
      this.sources = media.map((s) => this.ctx.createMediaStreamSource(new MediaStream(s.getAudioTracks())));
    };
    try {
      connect({ sampleRate: 16000 });   // ask the browser to resample for us
    } catch (e) {                       // Firefox refuses a rate different from the device's
      if (this.ctx) await this.ctx.close();
      connect(undefined);
    }
    this.ratio = this.ctx.sampleRate / 16000;
    await this.ctx.audioWorklet.addModule('/recorder-worklet.js');
    // A node has one mono input: stereo is folded down. 'both' gets a node per source (microphone first),
    // so they stay apart; they run on the same clock, which keeps the two tracks in step.
    const tap = () => new AudioWorkletNode(this.ctx, 'tap', { numberOfOutputs: 0, channelCount: 1, channelCountMode: 'explicit', channelInterpretation: 'speakers' });
    this.nodes = source === 'both' ? this.sources.map(tap) : [tap()];
    this.tracks = this.nodes.map(() => this.newTrack());
    this.nodes.forEach((node, i) => { node.port.onmessage = (e) => this.take(e.data, i); });
    this.native = this.streams.find((s) => s.native);
    if (this.native) this.native.attach(this);        // a track of its own at 16 kHz: the only one, or the second after the microphone
    this.sources.forEach((src, i) => src.connect(this.nodes[Math.min(i, this.nodes.length - 1)]));
    await this.ctx.resume();
    this.timer = setInterval(() => this.flush(), 2000);
    this.streams.forEach((stream, i) => this.watch(stream, this.kinds[i]));
  }

  // An unplugged microphone or a tab that stops sharing in the middle of a class must not go unnoticed.
  watch(stream, kind) {
    stream.getAudioTracks().forEach((track) => track.addEventListener('ended', () => {
      if (this.streams.includes(stream) && this.onEnded) this.onEnded(kind);
    }));
  }

  // Put a new source where one stopped, on the same node: the recording goes on in the same track, in step.
  // A microphone needs no click (the system's default one is taken); sharing again does, so `sharing` comes from one.
  async reconnect(kind, sharing = null) {
    const i = this.kinds.indexOf(kind);
    if (i < 0 || !this.ctx || this.ctx.state === 'closed') throw new Error('Nothing to reconnect.');
    const stream = kind === 'mic' ? await navigator.mediaDevices.getUserMedia({ audio: this.micOptions }).catch(Recorder.tagged('mic'))
      : await (sharing || Recorder.share()).catch(Recorder.tagged('share'));
    if (!stream.getAudioTracks().length) {
      stream.getTracks().forEach((track) => track.stop());
      const err = new Error('The shared tab or screen has no audio.');
      err.name = 'NoSharedAudio';
      err.source = 'share';
      throw err;
    }
    const source = this.ctx.createMediaStreamSource(new MediaStream(stream.getAudioTracks()));
    source.connect(this.nodes[Math.min(i, this.nodes.length - 1)]);
    this.sources[i].disconnect();
    this.streams[i].getTracks().forEach((track) => track.stop());
    this.streams[i] = stream;
    this.sources[i] = source;
    this.watch(stream, kind);
    return stream.getAudioTracks()[0].label;
  }

  take(block, index = 0) {
    const track = this.tracks[index];
    let samples = block;
    if (track.ratio !== 1) samples = this.downsample(block, track);
    const out = new Int16Array(samples.length);
    let sum = 0;
    for (let i = 0; i < samples.length; i++) {
      const v = Math.max(-1, Math.min(1, samples[i]));
      sum += v * v;
      out[i] = this.muted ? 0 : Math.round(v * 32767);
    }
    track.pending.push(out);
    track.length += out.length;
    // A hidden tab's timers can be slowed down a lot; the sound itself keeps the chunks coming, at most 4 s each.
    if (track.length >= 64000 && Math.min(...this.tracks.map((t) => t.length)) >= 64000) this.flush();
    this.levelSum = (this.levelSum || 0) + sum;
    this.levelCount = (this.levelCount || 0) + samples.length;
    if (this.levelCount >= 3200 * this.tracks.length) {            // five times per second
      const rms = Math.sqrt(this.levelSum / this.levelCount);
      this.onLevel(rms > 0 ? 20 * Math.log10(rms) : -Infinity, false);
      this.levelSum = this.levelCount = 0;
    }
  }

  // Average the input samples that fall inside each output sample (a box filter):
  // exact for 48 kHz, close enough for speech at 44.1 kHz.
  downsample(block, track) {
    const input = new Float32Array(track.carry.length + block.length);
    input.set(track.carry);
    input.set(block, track.carry.length);
    const out = [];
    let pos = track.position;
    while (pos + track.ratio <= input.length) {
      const from = Math.floor(pos), to = Math.floor(pos + track.ratio);
      let sum = 0;
      for (let i = from; i < to; i++) sum += input[i];
      out.push(sum / (to - from));
      pos += track.ratio;
    }
    const keepFrom = Math.floor(pos);
    track.carry = input.slice(keepFrom);
    track.position = pos - keepFrom;
    return Float32Array.from(out);
  }

  flush() {
    if (this.native) this.native.keepUp();     // this Mac's sound running late never holds the microphone's track back
    // Both tracks give the same number of samples per chunk; what one has ahead of the other waits for the next.
    const size = Math.min(...this.tracks.map((track) => track.length));
    if (!this.tracks.length || !size) return;
    const chunks = this.tracks.map((track) => {
      const all = new Int16Array(track.length);
      let offset = 0;
      for (const part of track.pending) { all.set(part, offset); offset += part.length; }
      const rest = all.slice(size);
      track.pending = rest.length ? [rest] : [];
      track.length = rest.length;
      track.flushed += size;
      return all.slice(0, size);
    });
    this.onChunk(chunks[0], chunks[1]);
  }

  async stop() {
    clearInterval(this.timer);
    this.flush();
    if (this.nodes) { this.nodes.forEach((node) => { node.port.onmessage = null; }); this.sources.forEach((src) => src.disconnect()); }
    (this.streams || []).forEach((s) => s.getTracks().forEach((track) => track.stop()));
    if (this.ctx && this.ctx.state !== 'closed') await this.ctx.close();
  }
}
