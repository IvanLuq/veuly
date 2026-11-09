'use strict';
// The Mac app (packaging/mac/) shows this page in a window of its own and records this computer's sound itself,
// because a web view cannot: everything this Mac plays except Veuly, through Core Audio. This file is the page's
// half of that. In a browser there is no `window.veulyShell` (the app injects it) and nothing here runs.
//  - This Mac's sound as a source for Recorder (Recorder.native): a track of its own, already at 16 kHz, fed through
//    the same take() and flush() as the microphone's, and kept in step with it when both record. A source that
//    stalls, stops or is started again never holds the microphone's track back.
//  - The texts that read differently in the app (what this computer's sound records, where to allow things).
//  - What the app asks the page (VeulyMac): anything recording or unsaved before quitting, save and send before
//    quitting or reloading, Settings and Info from the menus, and a server that stopped and started again. What the
//    page tells the app: its language, and a recording that starts or stops (the Mac stays awake meanwhile).
(function () {
  const shell = window.veulyShell;
  if (!shell) return;
  const RATE = 16000;
  const STALL = RATE * 2;        // this Mac's sound this far behind the microphone: silence goes in its place, and it is said
  const ROOM = RATE / 4;         // left after such a filling, for blocks still on their way
  const QUIET = 3000;            // ms without a block, when it is the only track: said as lost
  const DRIFT = RATE / 25;       // 40 ms: the two clocks drifting apart by more than this is corrected
  const WINDOW = 10000;          // ms over which that is measured
  const STRETCH = 0.02;          // a correction changes a block's length by at most 2 %
  const AUDIBLE = 1e-5;          // -100 dB: what a recorder's audio graph plays while it runs (see Recorder.prototype.start)

  // ------------------------------------------------------------ texts
  // In the app `key` shows `APP_TEXT[key]` (and its work twin), in every language. The share_* keys are the
  // recording package's names for a shared tab that stops: here it is this Mac's sound. A file the page saves goes
  // where the app's save panel says, not to the downloads, and what is recorded here is in this window, not a tab.
  const APP_TEXT = {
    src_help: 'src_help_app', both_help: 'both_help_app', warn_silent: 'warn_silent_app', share_cancelled: 'share_cancelled_app',
    mic_blocked: 'mic_blocked_app', mic_blocked_mac: 'mic_blocked_app', mic_blocked_os: 'mic_blocked_app',
    share_lost: 'native_lost', share_again: 'share_again_app', share_back: 'native_back',
    pdf_saved: 'pdf_saved_app', closed_body: 'closed_body_app',
  };
  const plainT = window.t;
  window.t = (key, vars) => plainT(APP_TEXT[key] || key, vars);

  // A real folder panel for "Keep in step with a folder" (web/foldersync.js): its answer is the folder's path, or null.
  window.chooseFolder = () => shell.post({ type: 'folder' });

  // ------------------------------------------------------------ the page's language, for the app's menus and questions
  let told = '', work = false;
  const tell = () => {
    const now = `${getLang()}/${work}`;
    if (now === told) return;
    told = now;
    shell.post({ type: 'lang', lang: getLang(), work }).catch(() => {});
  };
  const setLang = window.setLang, setKind = window.setKind;
  window.setLang = (wanted) => { setLang(wanted); tell(); };
  window.setKind = (kind) => { setKind(kind); work = kind === 'project'; tell(); };

  // ------------------------------------------------------------ a recording keeps the Mac awake, and its audio running
  // WebKit runs the page's audio slower than real time while the window is hidden or covered, unless something in the
  // page plays (0.7x measured in the app: a class recorded with the window closed would lose part of the microphone).
  // So while a recorder runs, its audio graph plays a constant of AUDIBLE to the speakers: nothing anyone can hear,
  // and not the microphone (muted speech goes nowhere).
  const running = new Set();
  const proto = Recorder.prototype, start = proto.start, stop = proto.stop, reconnect = proto.reconnect;
  proto.start = async function (...args) {
    await start.apply(this, args);
    if (this.ctx && this.sources && this.sources.length && this.ctx.createConstantSource) {
      this.audible = this.ctx.createConstantSource();
      this.audible.offset.value = AUDIBLE;
      this.audible.connect(this.ctx.destination);
      this.audible.start();
    }
    running.add(this);
    if (running.size === 1) shell.post({ type: 'recording', on: true }).catch(() => {});
  };
  proto.stop = async function (...args) {
    try { if (this.native) this.native.keepUp(true); } catch (e) { /* it stops all the same */ }   // the last chunk holds all of the microphone
    try { return await stop.apply(this, args); } finally {
      if (running.delete(this) && !running.size) shell.post({ type: 'recording', on: false }).catch(() => {});
    }
  };
  // "Share again" of a page that has it (the recording package's Recorder.prototype.reconnect('share')): this Mac's
  // sound is no stream for the audio graph, so the new source takes over the track of the one it replaces, and that
  // one goes without telling the app (the capture is the new one's now). A microphone is reconnected as before.
  if (typeof reconnect === 'function') {
    proto.reconnect = async function (kind, sharing = null) {
      if (kind !== 'share' || !this.native) return reconnect.call(this, kind, sharing);
      const sound = await (sharing || Recorder.share()).catch((e) => { if (e && typeof e === 'object') e.source = 'share'; throw e; });
      const old = this.native, i = (this.streams || []).indexOf(old), lane = this.tracks.indexOf(old.lane);
      if (!sound || !sound.native || i < 0 || lane < 0 || !this.ctx || this.ctx.state === 'closed') {
        if (sound && sound.stop) sound.stop();
        throw new Error('Nothing to reconnect.');
      }
      sound.attach(this, lane);
      this.streams[i] = sound;
      this.native = sound;
      old.stop();
      if (typeof this.watch === 'function') this.watch(sound, kind);
      return sound.track.label;
    };
  }

  // The page's "a source was lost" (rec.dead), set by this track's 'ended': cleared when this Mac's sound comes back,
  // unless the microphone's own track ended (that stays said). The page marks it as true, or as the kind ('share').
  const back = (recorder) => {
    if (typeof rec === 'undefined' || rec.recorder !== recorder || !rec.dead) return;
    const micEnded = (recorder.streams || []).some((s) => !s.native && s.getAudioTracks().some((track) => track.readyState === 'ended'));
    if (rec.dead === 'share' || (rec.dead === true && !micEnded)) {
      rec.dead = rec.dead === true ? false : '';
      if (typeof recChanged === 'function') recChanged();
    }
  };

  // ------------------------------------------------------------ this Mac's sound
  // The app sends it in blocks of about 100 ms: `at` numbers the block's first sample (16 kHz, counted from the
  // start, samples that never arrived included), `t` is when it was heard (ms), `pcm` is base64 little-endian Int16.
  const decode = (b64) => {
    const bin = atob(b64), out = new Float32Array(bin.length >> 1);
    for (let i = 0; i < out.length; i++) {
      const v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8);
      out[i] = (v >= 32768 ? v - 65536 : v) / 32768;
    }
    return out;
  };
  const total = (lane) => lane.flushed + lane.length;      // samples a track of the recorder has taken
  // `samples` made `n` longer (shorter when n < 0) by linear interpolation: an even, tiny change of speed, no cut.
  const stretch = (samples, n) => {
    const out = new Float32Array(samples.length + n), k = (samples.length - 1) / Math.max(1, out.length - 1);
    for (let i = 0; i < out.length; i++) {
      const x = i * k, j = Math.floor(x), next = j + 1 < samples.length ? samples[j + 1] : samples[j];
      out[i] = samples[j] + (next - samples[j]) * (x - j);
    }
    return out;
  };

  // The source the app's capture feeds now: the one that asked last. Another one still attached to a recorder (it is
  // being replaced) gets nothing more, and is stopped without a word to the app, whose capture is not its own.
  let current = null;
  // The two clocks NativeSound reads: ms of wall-clock time, and ms since the page opened. A test replaces them to
  // run two hours of a class in seconds (tests/e2e_macapp.py).
  const clock = { wall: () => Date.now(), now: () => performance.now() };

  // What a MediaStreamTrack is, as far as Recorder and the page look at one: stop() and an 'ended' event.
  class NativeTrack extends EventTarget {
    constructor(sound) { super(); this.sound = sound; this.kind = 'audio'; this.label = t('src_system'); this.readyState = 'live'; }
    stop() { this.sound.stop(); }
  }

  class NativeSound {
    constructor() {
      this.native = true;
      this.track = new NativeTrack(this);
      this.recorder = null;
      this.lane = null;          // its track in the recorder
      this.next = null;          // the `at` of the next sample the lane takes; null until it is lined up with the microphone
      this.lost = false;
      this.stopped = false;
      this.last = clock.now();
      this.drift = { since: clock.now(), sum: 0, count: 0, base: null, fix: 0 };
    }

    getAudioTracks() { return [this.track]; }

    getTracks() { return [this.track]; }

    // Recorder.start calls this once its tracks exist. Alone ('system') it is the only track; with the microphone
    // ('both') the second, the others' voices. `index`: take over that track instead (a source started again).
    attach(recorder, index) {
      this.recorder = recorder;
      if (index !== undefined && recorder.tracks[index]) {
        this.lane = recorder.tracks[index];
        this.lane.ratio = 1;
      } else {
        this.lane = recorder.newTrack(1);
        if (!recorder.sources.length) recorder.tracks = [this.lane];
        else recorder.tracks.push(this.lane);
      }
      this.alone = recorder.tracks.length === 1;
      this.last = clock.now();
    }

    receive({ at, t: heard, pcm }) {
      if (this.stopped || !this.recorder) return;      // before the recorder is ready nothing counts yet
      this.last = clock.now();
      const rec = this.recorder, index = rec.tracks.indexOf(this.lane);
      if (index < 0) return;
      let samples = decode(pcm);
      const end = at + samples.length;
      if (this.next === null) {
        const mic = this.alone ? null : rec.tracks[0];
        if (mic && !total(mic)) return;           // the microphone has not started: nothing to line up with yet
        // With the microphone, this track's next sample must be what this Mac played when the microphone's sample of
        // the same number was heard: total(mic) - total(lane) samples (at 16 per ms) before now. Alone: this block.
        this.next = mic ? Math.round(at + (clock.wall() - (total(mic) - total(this.lane)) / (RATE / 1000) - heard) * (RATE / 1000)) : at;
        Object.assign(this.drift, { since: clock.now(), sum: 0, count: 0 });
      }
      if (at > this.next) rec.take(new Float32Array(Math.min(at - this.next, RATE * 60)), index);    // never arrived: silence
      else if (at < this.next) samples = samples.subarray(Math.min(samples.length, this.next - at));   // that time is filled already
      this.next = Math.max(this.next, end);
      this.place(samples);
    }

    // The Mac slept: whatever came before says nothing about the blocks to come. Lined up with the microphone again
    // from the next block, the drift measured afresh.
    rewind() {
      this.next = null;
      this.last = clock.now();
      Object.assign(this.drift, { since: clock.now(), sum: 0, count: 0, base: null, fix: 0 });
    }

    // Takes a block's samples (stretched a little while a drift is made up), and sends when there is enough.
    place(samples) {
      const rec = this.recorder, index = rec.tracks.indexOf(this.lane), d = this.drift;
      if (d.fix && samples.length > 100) {
        const n = Math.sign(d.fix) * Math.min(Math.abs(d.fix), Math.floor(samples.length * STRETCH));
        samples = stretch(samples, n);
        d.fix -= n;
        if (!d.fix) Object.assign(d, { since: clock.now(), sum: 0, count: 0 });   // measured afresh from here
      }
      if (!samples.length) return;
      rec.take(samples, index);
      if (this.lost) {                             // it reaches the page again
        this.lost = false;
        back(rec);
        if (typeof toast === 'function') toast(t('native_back'));
      }
      if (!this.alone) this.measure(rec.tracks[0]);
      // A hidden page's timers are slowed down: the sound itself sends the chunks.
      if (Math.min(...rec.tracks.map((lane) => lane.length)) >= RATE * 2) rec.flush();
    }

    // How far this track is ahead of the microphone's right after a block, averaged over WINDOW. The first window
    // gives the usual delivery delay; a later one that moved away from it by more than DRIFT means the two clocks
    // run apart, and the next blocks are stretched or shrunk until that is made up.
    measure(mic) {
      const d = this.drift, now = clock.now();
      d.sum += total(this.lane) - total(mic);
      d.count++;
      if (now - d.since < WINDOW || d.fix) return;
      const mean = d.sum / d.count;
      if (d.base === null) d.base = mean;
      else if (Math.abs(mean - d.base) > DRIFT) d.fix = Math.round(d.base - mean);
      Object.assign(d, { since: now, sum: 0, count: 0 });
    }

    // Recorder.flush calls this first, for as long as this is the recorder's source of this Mac's sound, stopped or
    // not: one that stopped while the recorder goes on is a stalled one. With the microphone: when this track is more
    // than STALL behind, silence fills it up to the microphone (less ROOM), so the microphone's track is never held
    // back, and the page is told the way it is told about a lost source. Alone: only told, after QUIET without a block.
    // `ending`: the recording ends here (Finish, quitting, a reload): filled all the way, so the chunk sent last holds
    // all of the microphone; only a block of this Mac's sound still on its way is left out.
    keepUp(ending = false) {
      const rec = this.recorder;
      if (!rec || !this.lane) return;
      const index = rec.tracks.indexOf(this.lane);
      if (index < 0) return;
      if (this.alone) {
        if (!ending && clock.now() - this.last > QUIET) this.lose();
        return;
      }
      const behind = total(rec.tracks[0]) - total(this.lane);
      if (behind <= (ending ? 0 : STALL)) return;
      const fill = ending ? behind : behind - ROOM;
      rec.take(new Float32Array(fill), index);
      if (this.next !== null) this.next += fill;    // anything that arrives late for that time is dropped: it is silence now
      Object.assign(this.drift, { since: clock.now(), sum: 0, count: 0 });
      if (!ending) this.lose();
    }

    lose() {
      if (this.lost || this.stopped) return;             // one the page stopped itself is not lost
      this.lost = true;
      this.track.dispatchEvent(new Event('ended'));      // the page reports a source that stopped
      // A page whose recorder takes no onEnded (before the recording package) only marks it lost: the words come from here.
      if (this.recorder && !this.recorder.onEnded && typeof toast === 'function') toast(t('native_lost'));
    }

    stop() {
      if (this.stopped) return;
      this.stopped = true;
      this.track.readyState = 'ended';
      if (current !== this) return;                      // another source took the capture over: it is that one's to stop
      current = null;
      shell.post({ type: 'sound', op: 'stop' }).catch(() => {});
    }
  }

  Recorder.native = {
    // From the click: the app starts this Mac's sound (macOS asks the user the first time) and the page gets a source.
    // One recording now is left as it is: it goes on (in silence, if the capture moved on) until the new source takes
    // its track over (Recorder.reconnect) or its recorder stops, and gets the capture back if this start fails.
    async open() {
      const before = current;
      const sound = new NativeSound();
      current = sound;
      try {
        await shell.post({ type: 'sound', op: 'start' });
        if (current !== sound) throw new Error('A newer start took over.');
      } catch (e) {
        sound.stopped = true;
        if (current === sound) current = before && !before.stopped ? before : null;
        const err = new Error(String((e && e.message) || e));
        err.name = 'NotAllowedError';      // as when nothing was shared: the page says this Mac's sound did not start
        err.source = 'share';
        throw err;
      }
      return sound;
    },
    Sound: NativeSound,
    clock,
  };

  // ------------------------------------------------------------ what the app asks the page
  const pause = (ms) => new Promise((done) => setTimeout(done, ms));
  const page = () => typeof rec !== 'undefined';
  const audio = (items) => (items || []).filter((item) => item.data).length;
  // Recorded chunks not on the server yet, still queued under the recording's session (rec.queue).
  const unsent = () => (page() ? audio(rec.queue) : 0);
  // What was typed is saved, and the recording's queue goes to the server (`ms` at most).
  const sendAll = async (ms) => {
    try { if (typeof saveLive === 'function') await saveLive(); } catch (e) { /* the page said why */ }
    if (!page()) return;
    if (rec.recorder) {
      try {
        if (rec.recorder.native) rec.recorder.native.keepUp(true);
        rec.recorder.flush();
      } catch (e) { /* nothing left to send */ }
    }
    if (!rec.queue.length) return;
    if (typeof drain === 'function') { await drain(ms); return; }     // the page's own: a retry waiting its turn goes now
    if (typeof pump === 'function') pump();
    const end = Date.now() + ms;
    while (rec.queue.length && Date.now() < end) await pause(100);
  };
  // After a server that stopped by itself: what the recording had not sent goes to the new one (sendAll), then the class
  // is let go, so that this page or another can go on recording it. What the new server refused or did not take in
  // time is lost, and the page says so plainly (recordingLost). A Finish pressed just before the server stopped is done now.
  // Meanwhile the page records nothing new (rec.handing: startRecording waits for it), or the two would mix.
  const letGo = async () => {
    if (!page()) return;
    rec.handing = !rec.recorder && !!rec.session;
    try {
      await sendAll(20000);
      if (rec.recorder || !rec.session || typeof endRecording !== 'function') return;    // the recording the old server had
      const { slug, cid, session } = rec;
      if (unsent()) await recordingLost('not_sent');
      else if (rec.finishing && await finishNow(slug, cid, session)) return;
      else endRecording();
      await api('POST', `/api/classes/${slug}/${cid}/record`, { release: session }).catch(() => {});    // the class is free again
    } finally { rec.handing = false; }
  };
  // The Finish the old server never answered, under the recording's session. Finished already (the answer was lost
  // with the server) counts as finished; a refusal that will not change keeps nothing back; anything else is said, and
  // the class offers Finish again.
  const finishNow = async (slug, cid, session) => {
    try {
      await api('POST', `/api/classes/${slug}/${cid}/finish`, { session });
    } catch (e) {
      if (e.kind !== 'recording_closed') {
        if (typeof LOST !== 'undefined' && LOST.includes(e.kind)) { await recordingLost(e.kind); return true; }
        endRecording();
        if (typeof fail === 'function') fail(e);
        return false;
      }
    }
    endRecording();
    follow(slug, cid);
    return true;
  };

  // ------------------------------------------------------------ the Mac while it records: menu bar, Dock, notifications
  // The app asks `watch` every second or so (the menu bar item, the Dock's badge): what records here, which classes
  // are being written and how far, and which finished or failed since (a notification each). The classes being written
  // are looked up one by one, every few seconds at most; they come from the page's own lists and from a Finish here.
  const watched = new Map();          // `${slug}/${cid}` -> { slug, cid, step, progress, title }
  const settled = new Set();          // reported already: the page's list may still say "processing" until it reloads
  const news = [];
  let looked = -1e9, looking = false;
  const id = (slug, cid) => `${slug}/${cid}`;
  const follow = (slug, cid) => { settled.delete(id(slug, cid)); if (!watched.has(id(slug, cid))) watched.set(id(slug, cid), { slug, cid, step: '', progress: null }); };
  const courseOf = (slug) => (typeof S !== 'undefined' && (S.courses || []).find((c) => c.slug === slug)) || null;
  const titleOf = (slug, cid) => { const c = courseOf(slug), k = c && (c.classes || []).find((x) => x.id === cid); return (k && k.title) || ''; };
  const isProject = (slug) => { const c = courseOf(slug); return !!c && c.kind === 'project'; };
  // A text in the wording of that class's own kind: a meeting's notification says "minutes" whichever side is shown.
  const inKind = (project, fn) => { setKind(project ? 'project' : 'course'); try { return fn(); } finally { setKind(work ? 'project' : 'course'); } };
  // The page's list of classes may be older than what was reported: a class it still says is written is followed
  // again only once the list said otherwise. The class open on its page is fresh: one that starts being written there
  // (Write it again, Try again) is followed at once.
  const seen = (slug, cid, status) => {
    if (status === 'processing') { if (!settled.has(id(slug, cid))) follow(slug, cid); } else settled.delete(id(slug, cid));
  };
  let open = { key: '', status: '' };
  const seenOpen = (slug, cid, status) => {
    if (open.key === id(slug, cid) && open.status !== 'processing' && status === 'processing') follow(slug, cid);
    open = { key: id(slug, cid), status };
  };
  const look = async () => {
    if (looking || performance.now() - looked < 2500 || !page() || typeof S === 'undefined') return;
    looking = true;
    looked = performance.now();
    try {
      for (const c of S.courses || []) for (const k of c.classes || []) seen(c.slug, k.id, k.status);
      if (S.view && S.view.name === 'class' && S.cls) seenOpen(S.view.slug, S.view.cid, S.cls.status);
      for (const [key, w] of [...watched]) {
        let c;
        try { c = await api('GET', `/api/classes/${w.slug}/${w.cid}`); } catch (e) {
          if (e.kind === 'not_found') watched.delete(key);
          continue;
        }
        if (c.status === 'processing') { Object.assign(w, { step: c.step || '', progress: typeof c.progress === 'number' ? c.progress : null, title: c.title || '' }); continue; }
        watched.delete(key);
        if (c.status !== 'done' && c.status !== 'error') continue;      // back to recording (Finish did not go through)
        settled.add(key);
        const ok = c.status === 'done', name = c.title || titleOf(w.slug, w.cid);
        const here = S.view && S.view.name === 'class' && S.view.slug === w.slug && S.view.cid === w.cid;
        news.push(inKind(isProject(w.slug), () => ({ slug: w.slug, cid: w.cid, ok, here,
          title: t(ok ? 'mac_note_ready' : 'mac_note_failed'), body: ok ? name : [name, HELP(c.error_kind) || c.error || ''].filter(Boolean).join(': ') })));
      }
    } finally { looking = false; }
  };
  const activity = () => {
    const r = rec.recorder && rec.session ? { slug: rec.slug, cid: rec.cid, title: titleOf(rec.slug, rec.cid), project: isProject(rec.slug),
      seconds: typeof recSeconds === 'function' ? recSeconds() : 0, muted: !!rec.muted, finishing: !!rec.finishing,
      headsups: typeof window.headsUpWaiting === 'function' ? window.headsUpWaiting() : 0 } : null;
    const writing = [...watched.values()].map((w) => ({ slug: w.slug, cid: w.cid, title: w.title || titleOf(w.slug, w.cid), project: isProject(w.slug),
      step: w.step, progress: w.step === 'transcribing' && typeof w.progress === 'number' ? Math.round(w.progress) : -1 }));
    return { recording: r, writing, news: news.splice(0) };
  };
  // The class the menu bar item is about, on screen: the one recording here, or else the first being written.
  const openClass = async (slug, cid, tab) => {
    if (!slug) return false;
    if (!(S.view && S.view.name === 'class' && S.view.slug === slug && S.view.cid === cid)) await go({ name: 'class', slug, cid, ...(tab ? { tab } : {}) });
    return true;
  };
  // Once app.js and the files after it have run (the page's own start waits for the same moment, and comes after this).
  const later = (fn) => {
    if (typeof document === 'undefined') return;        // not a page (the tests that run this file under node)
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn); else fn();
  };
  later(() => {
    // The first Finish is when the app asks macOS whether it may notify (never at launch); each Finish here is followed.
    if (typeof window.finishClass === 'function') {
      const finish = window.finishClass;
      window.finishClass = function (...args) {
        if (rec.recorder && !rec.finishing) shell.post({ type: 'finishing' }).catch(() => {});
        return finish.apply(this, args);
      };
    }
    if (typeof window.completeFinish === 'function') {
      const complete = window.completeFinish;
      window.completeFinish = async function (...args) {
        const { slug, cid } = rec;
        try { return await complete.apply(this, args); } finally { if (!rec.recorder && slug) follow(slug, cid); }
      };
    }
    // Settings, in the app only: open Veuly when the user logs in (the system's login items, through the app).
    if (typeof VIEWS === 'object' && VIEWS.settings) {
      const plain = VIEWS.settings;
      VIEWS.settings = (...args) => {
        const view = plain(...args);
        try { const lang = view.querySelector('#set-lang'); if (lang) lang.after(...loginPart()); } catch (e) { /* the page shows without it */ }
        return view;
      };
    }
    // The class page says when the Mac slept during this recording, and for how long.
    if (typeof window.alerts === 'function') {
      const plainAlerts = window.alerts;
      window.alerts = (c) => {
        const out = plainAlerts(c);
        try {
          const naps = (rec.slept || []).filter((n) => n.session === rec.session && isLive() && !rec.finishing);
          if (naps.length) {
            out.push(h('div', { class: 'notice', id: 'slept-notice', role: 'status' }, naps.map((n) => h('p', {}, sleptText(n))),
              h('div', { class: 'row gap' }, h('button', { class: 'btn', id: 'slept-ok', onclick: () => { rec.slept = []; recChanged(); } }, t('info_close')))));
          }
        } catch (e) { /* the recording's own notices come first, always */ }
        return out;
      };
    }
  });

  // ------------------------------------------------------------ open at login (Settings)
  let login = null;           // what the app said last: { on, approve }
  const loginPart = () => {
    const box = h('input', { type: 'checkbox', id: 'open-at-login', checked: !!(login && login.on), disabled: !login,
      onchange: async (e) => {
        box.disabled = true;
        try { login = await shell.post({ type: 'login', op: 'set', on: e.target.checked }); } catch (err) { toast(t('mac_login_failed')); }
        show();
      } });
    const help = h('p', { class: 'soft', id: 'login-help' });
    const show = () => {
      box.checked = !!(login && login.on);
      box.disabled = !login;
      help.replaceChildren(t(login && login.approve ? 'mac_login_approve' : 'mac_login_help'), ...(login && login.approve
        ? [' ', h('button', { class: 'btn quiet', id: 'login-settings', onclick: () => shell.post({ type: 'login', op: 'settings' }).catch(() => {}) }, t('mac_login_open'))] : []));
    };
    show();
    shell.post({ type: 'login', op: 'get' }).then((state) => { login = state; show(); }, () => {});
    return [h('label', { class: 'row gap', id: 'login-row' }, box, t('mac_login')), help];
  };

  // ------------------------------------------------------------ the Mac goes to sleep and wakes up while recording
  // Nothing records while it sleeps (the lid closed): the recording simply goes on from where it was when it wakes, and
  // the class keeps where and how long that was (`gaps` in class.json, through the queue like a mute). Whatever
  // stopped meanwhile starts again: the audio graph, a microphone that went away, this Mac's sound (the app rebuilds its
  // capture, and the page lines it up again).
  const span = (s) => (s < 60 ? `${Math.max(1, Math.round(s))} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${Math.floor(s / 3600)} h ${Math.round(s % 3600 / 60)} min`);
  const at = (ms) => new Date(ms).toLocaleTimeString(getLang(), { hour: 'numeric', minute: '2-digit' });
  const sleptText = (n) => t('mac_slept', { time: span(n.seconds), from: at(n.wall - n.seconds * 1000), to: at(n.wall) });
  const woke = async (slept) => {
    const r = rec.recorder, seconds = Math.max(0, Number(slept) || 0);
    if (!r || rec.finishing || !rec.session) return { recording: false };
    // (a resume that never settles, as WebKit may leave one, holds back nothing: the rest goes on after 2 s)
    if (r.ctx && r.ctx.state !== 'running' && r.ctx.state !== 'closed') await Promise.race([r.ctx.resume().catch(() => {}), pause(2000)]);
    if (r.native) r.native.rewind();
    // The gap goes in where the sleep was: after what was recorded before it, ahead of anything a microphone opened again records.
    if (seconds >= 1 && rec.recorder === r) {
      try { r.flush(); } catch (e) { /* nothing to send */ }
      rec.queue.push({ on: !!rec.muted, gap: Math.round(seconds) });
      if (typeof pump === 'function') pump();
      const nap = { session: rec.session, seconds, wall: Date.now() };
      rec.slept = (rec.slept || []).concat([nap]);
      toast(sleptText(nap));
      recChanged();
    }
    const mic = (r.streams || []).find((s) => !s.native), track = mic && mic.getAudioTracks()[0];
    let reopened = false;
    if (rec.recorder === r && track && (track.readyState === 'ended' || track.muted) && (r.kinds || []).includes('mic') && !rec.reconnecting && typeof reconnectMic === 'function') {
      rec.dead = 'mic';
      reopened = await reconnectMic();
    }
    return { recording: true, reopened, state: r.ctx ? r.ctx.state : '' };
  };

  window.VeulyMac = {
    sound: (block) => { if (current) current.receive(block); return null; },
    // What quitting now would lose: whether the page records; holds recorded chunks the server never got (it
    // stopped: `unsent`); has anything else not saved (its own beforeunload); or waits for a note being written.
    quitState: () => {
      const leaving = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(leaving);
      const recording = page() && !!rec.recorder;
      const busy = typeof S !== 'undefined' && (S.courses || []).some((c) => (c.classes || []).some((k) => k.status === 'processing'));
      return { recording, unsent: !recording && unsent() > 0,
               unsaved: leaving.defaultPrevented && !recording, busy };
    },
    // The user chose to quit, or to reload: what was typed is saved, and what was recorded goes to the server (5 s at most).
    beforeQuit: async () => { await sendAll(5000); return true; },
    quitServer: async () => {
      try { await api('POST', '/api/quit'); } catch (e) { /* it may close before answering */ }
      return true;
    },
    // The server stopped by itself: "Veuly stopped working" covers this page. A recording stops here (nothing records
    // out of sight); what it had not sent stays queued under its session (rec.session, which class.json keeps, so a
    // new server takes it), as the typed notes stay in the editor, until the server is back.
    serverGone: async () => {
      if (!page()) return { recording: false, unsent: 0 };
      const recorder = rec.recorder;
      if (recorder) {
        await recorder.stop().catch(() => {});        // its last chunk goes into the queue first
        if (rec.recorder === recorder) rec.recorder = null;
        if (rec.wake) rec.wake.release().catch(() => {});
      }
      return { recording: !!recorder, unsent: unsent() };
    },
    // "Start again": a new server serves the same address. This page takes the new session from its link (the cookie
    // of this data folder, and the token every call carries in X-TA), saves what was typed and sends what the recording
    // had not sent, under the recording's own session (20 s at most). Then it lets go of the class, so that this page or
    // another can go on recording or finish it. What the new server refuses (the class was taken over, finished or
    // deleted meanwhile) or did not take in time is lost, and the page says so plainly (the page's recordingLost).
    // The page goes on where it was: nothing is reloaded.
    resume: async (link) => {
      const url = new URL(link, location.href);
      if (url.origin !== location.origin) return false;
      const r = await fetch(url.href, { cache: 'no-store' });      // the session's cookie
      if (!r.ok) return false;
      if (typeof sessionToken === 'string') {                     // a page that sends the session's token itself
        sessionToken = url.searchParams.get('t') || '';
        try { sessionStorage.setItem('ta-token', sessionToken); } catch (e) { /* optional */ }
      }
      await letGo();
      try { await loadState(); await afterAction(); } catch (e) { if (typeof fail === 'function') fail(e); }
      return { left: unsent() };
    },
    // "Start again" found the address taken and the new server serves another one: this page cannot reach it (another
    // origin), so it hands the app what only it holds (the recording's queue under its session, a Finish pressed just
    // before, the notes typed and not saved, the class on screen), and the app loads the new page and gives it that
    // (takeOver), which sends it as resume would. Chunks go as base64 Int16.
    handOver: () => {
      if (!page()) return null;
      const b64 = (a) => {
        const u = new Uint8Array(a.buffer, a.byteOffset, a.byteLength);
        let s = '';
        for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
        return btoa(s);
      };
      const onClass = typeof S !== 'undefined' && S.view && S.view.name === 'class' && S.cls;
      const typing = onClass && typeof dirty !== 'undefined' && dirty ? { slug: S.view.slug, cid: S.view.cid, live: liveEditor ? liveEditor.getValue() : S.cls.live,
        live_base: notes.rev, typed: (liveEditor ? liveEditor.getTimes() : S.cls.typed) || {} } : null;
      return { slug: rec.slug || '', cid: rec.cid || '', session: rec.recorder ? '' : rec.session || '', finishing: !rec.recorder && !!rec.finishing,
               items: rec.recorder ? [] : rec.queue.map((item) => (item.data ? { seq: item.seq, track: item.track, pcm: b64(item.data) } : { on: !!item.on, gap: item.gap || 0 })),
               // what an earlier refusal lost goes on being said on the new page
               lost: rec.lost || null, typing, view: onClass ? { slug: S.view.slug, cid: S.view.cid } : null };
    },
    takeOver: async (state) => {
      if (!page() || !state || typeof state !== 'object') return false;
      // Nothing new records until the old recording's queue has gone (as in letGo), from the first moment: a start would overwrite it.
      const holding = !!state.session && !rec.recorder && !rec.session;
      if (holding) rec.handing = true;
      for (let i = 0; i < 150 && !(typeof S !== 'undefined' && S.settings && document.querySelector('#main').childElementCount); i++) await pause(100);   // the page's own start first
      try {
        const pcm = (text) => { const bin = atob(text), out = new Int16Array(bin.length >> 1);
          for (let i = 0; i < out.length; i++) { const v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8); out[i] = v >= 32768 ? v - 65536 : v; }
          return out; };
        if (state.lost && !rec.lost) rec.lost = state.lost;
        const typing = state.typing;
        if (typing && typing.slug) {           // what was typed and not saved: a text changed meanwhile is kept in versions/ all the same
          await api('PUT', `/api/classes/${typing.slug}/${typing.cid}`, { live: typing.live, live_base: typing.live_base, typed: typing.typed || {} }).catch(() => {});
        }
        while (state.session && rec.starting) await pause(100);      // a start already under way: its microphone answers first
        if (state.session && !rec.recorder && !rec.session) {
          Object.assign(rec, { slug: state.slug, cid: state.cid, session: state.session, finishing: !!state.finishing, tries: 0,
            queue: (state.items || []).map((item) => (item.pcm !== undefined ? { seq: item.seq, track: item.track, data: pcm(item.pcm) } : { on: item.on, ...(item.gap ? { gap: item.gap } : {}) })) });
          await letGo();
        }
      } finally { if (holding) rec.handing = false; }
      // The class that was on screen comes back, unless the person did anything on this page (S.acted: a page opened, a
      // recording asked for, even if its microphone has not answered). The navigation is the page's own, so it gives way to
      // them also while it loads (go), and the page stays where they are.
      const view = state.view, back = !!(view && view.slug) && !S.acted && !rec.recorder;
      try {
        if (back) await go({ name: 'class', slug: view.slug, cid: view.cid }, true);
        if (!back || S.acted) { await loadState(); await afterAction(); }
      } catch (e) { if (typeof fail === 'function') fail(e); }
      return { left: unsent() };
    },
    // The menu bar item and the Dock (AppDelegate's watch): what goes on now, and what finished since the last call.
    watch: () => { look().catch(() => {}); return activity(); },
    mute: () => { if (rec.recorder && !rec.finishing && typeof toggleMute === 'function') toggleMute(); return activity(); },
    // The menu bar item's Finish: the class's page, and the question the page asks before finishing (as ⌃⇧F does).
    askFinish: async () => {
      if (!rec.recorder || rec.finishing) return false;
      const { slug, cid, session } = rec;
      await openClass(slug, cid, 'live');
      confirmPop({ id: 'finish-pop', title: t('kb_finish_title'), body: t('kb_finish_body'), yes: t('finish'),
        run: () => { if (rec.recorder && rec.session === session && !rec.finishing) finishClass(); } });
      return true;
    },
    // Show Veuly, and a click on a notification: that class on screen.
    open: async (what) => {
      const w = what && typeof what === 'object' ? what : {};
      if (w.slug) return openClass(w.slug, w.cid, w.tab || '');
      if (rec.recorder) return openClass(rec.slug, rec.cid, 'live');
      const first = [...watched.values()][0];
      return first ? openClass(first.slug, first.cid) : false;
    },
    // The Mac goes to sleep: what was recorded so far goes now. It woke up after `slept` seconds.
    sleeping: () => { try { if (rec.recorder && !rec.finishing) rec.recorder.flush(); } catch (e) { /* nothing to send */ } return true; },
    woke,
    // The page's own Quit was pressed here: it shows its "closed" sheet (app.js, reallyQuit).
    closed: () => !!document.querySelector('.sheet.closed'),
    settings: () => { go({ name: 'settings' }).catch(fail); return true; },
    info: () => { if (!document.getElementById('info-pop')) openInfo(); return true; },
  };
})();
