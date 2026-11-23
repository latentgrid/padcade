// A clip recorded with this device's microphone, in a dialog of the board's page. A browser lends its microphone
// only to a page opened securely: panel.js sends people to the board's secure address before this is opened.
const LONGEST = 20, RATE = 44100;

// `onto`, if given, is told the saved clip's name (to put it on a pad); the recorder then closes.
export function openRecorder(kit, onto) {
  const {el} = kit;
  let audio, stream, recorder, timer, limit, clip, peaks, from = 0, to = 0, mark = null, playing, again = false, starting = false, replacing = null;
  const clock = seconds => Math.floor(seconds / 60) + ':' + String(Math.floor(seconds % 60)).padStart(2, '0');
  const button = (text, onclick, className = 'quiet') => el('button', {type:'button', className, textContent:text, onclick});
  const taken = () => new Set((kit.plugin().items || []).map(item => String(item.id)));

  const record = el('button', {type:'button', className:'sb-mic'}, el('i'));
  const time = el('output', {className:'sb-mic-time', textContent:'0:00'});
  const level = el('div', {className:'sb-level', hidden:true}, el('i'));
  const said = el('p', {className:'sb-notice', hidden:true});
  said.setAttribute('role', 'status');
  // The play button sits beside the picture it plays: a triangle to listen, two bars to pause.
  const listen = el('button', {type:'button', className:'sb-listen', onclick:() => playing ? pause() : play()});
  const face = sounding => {
    listen.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + (sounding ? '<rect x="6.5" y="5.5" width="4" height="13" rx="1"/><rect x="13.5" y="5.5" width="4" height="13" rx="1"/>' : '<path d="M8.5 5.5v13l11-6.5z"/>') + '</svg>';
    listen.setAttribute('aria-label', sounding ? 'Pause' : 'Listen');
    listen.title = sounding ? 'Pause' : 'Listen';
  };
  face(false);
  const length = el('span', {className:'muted'});
  // The cut: what is kept of the recording lies between two handles on its picture. `mark` is where the playback is,
  // or where it was paused or put by a click: it stays there, to cut by.
  const wave = el('canvas', {className:'sb-wave'});
  wave.setAttribute('aria-hidden', 'true');
  const place = event => {
    const box = wave.getBoundingClientRect();
    return Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)) * clip.duration;
  };
  const handle = (id, label) => {
    const h = el('button', {type:'button', className:'sb-handle', id:'recorder-' + id});
    h.setAttribute('role', 'slider');
    h.setAttribute('aria-label', label);
    h.setAttribute('aria-valuemin', '0');
    h.onpointerdown = event => {
      if (!event.isPrimary || event.button) return;
      event.preventDefault();
      h.focus();
      h.setPointerCapture(event.pointerId);
    };
    h.onpointermove = event => { if (h.hasPointerCapture(event.pointerId)) cut(id, place(event)); };
    h.onpointerup = h.onpointercancel = () => settled();
    h.onkeydown = event => {   // (the arrow keys: a hundredth of a second, with Shift a tenth)
      const way = ['ArrowLeft', 'ArrowDown'].includes(event.key) ? -1 : ['ArrowRight', 'ArrowUp'].includes(event.key) ? 1 : 0;
      if (!way) return;
      event.preventDefault();
      cut(id, (id === 'start' ? from : to) + way * (event.shiftKey ? .1 : .01));
      settled();
    };
    return h;
  };
  const cutStart = handle('start', 'Start'), cutEnd = handle('end', 'End');
  const name = el('input', {type:'text', id:'recorder-name', maxLength:40, required:true, autocomplete:'off'});
  const save = button('Save clip', () => keep(), 'act');
  const review = el('section', {hidden:true},
    el('div', {className:'sb-player'}, listen, el('div', {className:'sb-crop'}, wave, cutStart, cutEnd), length),
    kit.field('Name of the clip', name), el('div', {className:'pair'}, save, button('Record again', () => start())));
  const dialog = el('dialog', {className:'sb-dialog sb-recorder'}, el('h2', {textContent:onto ? 'Record a clip for this pad' : 'Record a clip'}),
    el('p', {textContent:'With this device’s microphone, up to 20 seconds. Afterwards you drag its two ends to where the clip should start and stop.'}),
    el('div', {className:'sb-mic-stage'}, record, el('div', {}, time, el('small', {textContent:' of 0:20'})), level),
    said, review, el('div', {className:'pair'}, button('Close', () => dialog.close())));

  function say(text, kind = '') {
    said.hidden = !text;
    said.textContent = text;
    said.className = 'sb-notice ' + kind;
  }
  function show(recording) {
    record.setAttribute('aria-pressed', String(recording));
    record.setAttribute('aria-label', recording ? 'Stop recording' : 'Start recording');
    level.hidden = !recording;
  }

  // ---- recording: what the microphone hears, at most twenty seconds of it
  async function start() {
    if (starting) return;   // (the browser is still asking for the microphone)
    starting = true;
    record.disabled = true;
    try { await begin(); } finally { starting = false; record.disabled = false; }
  }
  async function begin() {
    stopListening();
    review.hidden = true;
    say('');
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) return say('This browser cannot record sound.', 'bad');
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false, noiseSuppression:false, autoGainControl:false}});
    } catch (error) {
      return say(error.name === 'NotFoundError' ? 'No microphone was found on this device.'
        : 'The browser did not let this page use the microphone. Allow it for this page, then try again.', 'bad');
    }
    if (!dialog.open) return stream.getTracks().forEach(track => track.stop());   // (closed while the browser was asking)
    audio ||= new AudioContext();
    await audio.resume();
    const pieces = [], began = performance.now(), mine = recorder = new MediaRecorder(stream);
    mine.ondataavailable = event => pieces.push(event.data);
    mine.onstop = () => { if (dialog.open) finish(new Blob(pieces, {type:mine.mimeType})); };
    mine.start();
    limit = setTimeout(stop, LONGEST * 1000);   // (also with the page in the background, where nothing is drawn)
    show(true);
    const hearing = audio.createMediaStreamSource(stream), analyser = audio.createAnalyser(), samples = new Float32Array(1024);
    analyser.fftSize = 1024;
    hearing.connect(analyser);
    const meter = () => {
      analyser.getFloatTimeDomainData(samples);
      const peak = samples.reduce((most, x) => Math.max(most, Math.abs(x)), 0);
      level.firstChild.style.width = Math.min(100, Math.round(Math.sqrt(peak) * 100)) + '%';
      level.classList.toggle('loud', peak > .95);
      time.textContent = clock((performance.now() - began) / 1000);
      timer = requestAnimationFrame(meter);
    };
    meter();
  }
  function stop() {
    cancelAnimationFrame(timer);
    clearTimeout(limit);
    show(false);
    if (recorder?.state === 'recording') recorder.stop();
    stream?.getTracks().forEach(track => track.stop());
  }

  // ---- what was recorded, as the board plays it: stereo, 44.1 kHz. The cut starts out around what was heard.
  async function finish(blob) {
    try {
      const heard = await audio.decodeAudioData(await blob.arrayBuffer());
      const render = new OfflineAudioContext(2, Math.ceil(heard.duration * RATE), RATE), source = render.createBufferSource();
      source.buffer = heard;
      source.connect(render.destination);
      source.start();
      clip = await render.startRendering();
    } catch { return say('That recording could not be read. Try again.', 'bad'); }
    const left = clip.getChannelData(0), right = clip.getChannelData(1);
    const loud = i => Math.abs(left[i]) > .012 || Math.abs(right[i]) > .012;
    let first = 0, last = left.length - 1;
    while (first < last && !loud(first)) first++;
    while (last > first && !loud(last)) last--;
    if (first >= last) return say('Nothing was heard. Check that the microphone is on, then record again.', 'bad');
    peaks = Array.from({length:240}, (_, column) => {   // (how loud each sliver of it is, for the picture)
      let most = 0;
      for (let i = Math.floor(column * left.length / 240), stop = Math.floor((column + 1) * left.length / 240); i < stop; i += 8) {
        most = Math.max(most, Math.abs(left[i]), Math.abs(right[i]));
      }
      return most;
    });
    from = Math.max(0, first / RATE - .03);              // (a breath of room before the first sound)
    to = Math.min(clip.duration, last / RATE + .15);     // (and its tail after the last)
    mark = null;
    if (!name.value.trim()) {
      const names = taken();
      for (let n = 1; ; n++) if (!names.has('Recording ' + n)) { name.value = 'Recording ' + n; break; }
    }
    replacing = null;
    save.textContent = 'Save clip';
    review.hidden = false;
    cut();
    name.focus();
    name.select();
  }
  function cut(moved, value) {
    if (moved) {
      // Near the mark, an edge goes exactly to it. The two edges never cross: each stops a twentieth of a second short.
      if (mark !== null && !playing && Math.abs(value - mark) < clip.duration * .012) value = mark;
      if (moved === 'start') from = Math.max(0, Math.min(value, to - .05));
      else to = Math.min(clip.duration, Math.max(value, from + .05));
      again ||= !!playing;
      if (playing) pause();
    }
    for (const [h, value] of [[cutStart, from], [cutEnd, to]]) {
      h.style.left = value / clip.duration * 100 + '%';
      h.setAttribute('aria-valuemax', clip.duration.toFixed(2));
      h.setAttribute('aria-valuenow', value.toFixed(2));
      h.setAttribute('aria-valuetext', value.toFixed(2) + ' seconds');
    }
    time.textContent = clock(to - from);
    tell();
  }
  // An edge moved while the cut was playing: once it is let go, the new cut plays from its start.
  function settled() {
    if (again && !playing) {
      mark = null;
      play();
    }
    again = false;
  }
  // How long the cut is, in words; and where the mark is, while there is one.
  const span = () => (to - from).toFixed(1).replace('.0', '') + ((to - from).toFixed(1) === '1.0' ? ' second' : ' seconds');
  function tell() {
    length.textContent = (mark === null ? '' : 'at ' + mark.toFixed(1) + ' s · ') + span() + ' kept';
    const ratio = window.devicePixelRatio || 1, width = wave.width = Math.round(wave.clientWidth * ratio), height = wave.height = Math.round(wave.clientHeight * ratio);
    const paint = wave.getContext('2d'), colours = getComputedStyle(dialog), bar = width / peaks.length;
    peaks.forEach((peak, column) => {
      const at = (column + .5) / peaks.length * clip.duration, tall = Math.max(2 * ratio, Math.sqrt(peak) * height);
      paint.fillStyle = colours.getPropertyValue(at >= from && at <= to ? '--accent' : '--line');
      paint.fillRect(column * bar, (height - tall) / 2, Math.max(1, bar - ratio), tall);
    });
    if (mark !== null) {
      paint.fillStyle = colours.getPropertyValue('--bad');
      paint.fillRect(Math.round(mark / clip.duration * width) - ratio, 0, 2 * ratio, height);
    }
  }
  // (a click or a drag on the picture puts the mark there: listening goes on from it)
  wave.onpointerdown = wave.onpointermove = event => {
    if (event.type === 'pointerdown') wave.setPointerCapture(event.pointerId);
    else if (!wave.hasPointerCapture(event.pointerId)) return;
    if (playing) pause();
    mark = place(event);
    tell();
  };
  function wav(buffer) {
    const first = Math.round(from * RATE), frames = Math.max(1, Math.min(buffer.length, Math.round(to * RATE)) - first), ease = Math.min(132, frames >> 1);
    const view = new DataView(new ArrayBuffer(44 + frames * 4)), channels = [buffer.getChannelData(0), buffer.getChannelData(1)];
    const text = (at, word) => [...word].forEach((letter, i) => view.setUint8(at + i, letter.charCodeAt(0)));
    text(0, 'RIFF'); view.setUint32(4, 36 + frames * 4, true); text(8, 'WAVEfmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 2, true);
    view.setUint32(24, RATE, true); view.setUint32(28, RATE * 4, true); view.setUint16(32, 4, true); view.setUint16(34, 16, true);
    text(36, 'data'); view.setUint32(40, frames * 4, true);
    // Levelled here, where the samples already are, so that the board can take the clip as it is: brought to the
    // loudness of the other clips (about -18 dB on average), never above -2 dB at its loudest, never raised more
    // than tenfold (a recording of near silence stays one).
    let peak = 0, power = 0;
    for (let c = 0; c < 2; c++) for (let i = first; i < first + frames; i++) { const x = channels[c][i]; power += x * x; if (x > peak) peak = x; else if (-x > peak) peak = -x; }
    const gain = peak ? Math.min(.126 / Math.sqrt(power / (frames * 2)), .794 / peak, 10) : 1;
    for (let i = 0; i < frames; i++) for (let c = 0; c < 2; c++) {
      const eased = channels[c][first + i] * gain * Math.min(1, (i + 1) / ease, (frames - i) / ease);   // (three milliseconds in and out)
      view.setInt16(44 + i * 4 + c * 2, Math.round(Math.max(-1, Math.min(1, eased)) * 32767), true);
    }
    return new Blob([view], {type:'audio/wav'});
  }
  function pause() {   // (the mark stays where the playback was)
    playing?.stop();
    playing = null;
    face(false);
    if (clip && dialog.open) tell();
  }
  function stopListening() {   // (and the mark goes: a new recording, a saved clip, the dialog closed)
    mark = null;
    pause();
  }
  function play() {
    // From the mark, if there is one before the end of the cut (one put before its start lets that part be heard
    // too); otherwise from the start of the cut. Always to the end of the cut.
    const begin = mark !== null && mark < to - .05 ? mark : from;
    const source = playing = audio.createBufferSource(), began = audio.currentTime;
    source.buffer = clip;
    source.connect(audio.destination);
    source.onended = () => {   // (played to the end: the mark is gone)
      if (playing !== source) return;
      playing = mark = null;
      face(false);
      if (dialog.open) tell();
    };
    source.start(0, begin, to - begin);
    face(true);
    const follow = () => {
      if (playing !== source) return;
      mark = Math.min(to, begin + audio.currentTime - began);
      tell();
      requestAnimationFrame(follow);
    };
    follow();
  }

  // ---- saving: the board's own way of taking a clip, the same one Upload clips uses
  async function keep() {
    const wanted = name.value.replace(/[^\p{L}\p{N}_ \-]/gu, '').trim().slice(0, 40);
    name.value = wanted;
    if (!wanted) return name.reportValidity();
    save.disabled = true;
    try {
      await kit.refresh();   // (the names taken now: a clip is never replaced unasked)
      if (taken().has(wanted) && replacing !== wanted) {
        replacing = wanted;
        save.textContent = 'Replace ' + wanted;
        return say('There is already a clip called ' + wanted + '. Save again to replace it, or give this one another name.');
      }
      const answer = await kit.ask('upload?levelled=1' + (replacing === wanted ? '&replace=1' : '') + '&name=' + encodeURIComponent(wanted + '.wav'), wav(clip));
      if (!answer?.id) return say('The board did not take the clip. Try again.', 'bad');
      await kit.refresh();
      stopListening();
      if (onto) {
        await onto(answer.id);
        return dialog.close();
      }
      review.hidden = true;
      name.value = '';
      say('Saved as ' + answer.id + '. It is in the clip library: choose it there to put it on a pad.', 'good');
    } finally { save.disabled = false; }
  }
  name.oninput = () => { replacing = null; save.textContent = 'Save clip'; };
  name.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); keep(); } };
  record.onclick = () => recorder?.state === 'recording' ? stop() : start();
  dialog.onclose = () => {   // (closed: the microphone is given back and nothing goes on sounding)
    stop();
    stopListening();
    audio?.close();
    dialog.remove();
  };
  show(false);
  document.body.append(dialog);
  dialog.showModal();
}
