// Export on the computer so rendering stems never blocks the board’s audio clock.
const rate = 44100, beats = 8, ppq = 480;
const cutFrames = Math.round(.006 * rate);
function snap(beat,timing) {
  if (timing==='Fine') return Math.floor(beat*8 + .5)/8;
  const shift = Math.floor(beat*4 + .5)/4 - beat;
  return beat + (timing==='Tight' ? shift : timing==='Gentle' ? shift/2 : 0);
}
const bytes = s => new TextEncoder().encode(s);
function join(arrays) {
  const out = new Uint8Array(arrays.reduce((n,a)=>n+a.length,0));
  let pos=0;
  for (const a of arrays) { out.set(a,pos); pos+=a.length; }
  return out;
}
function vlq(value) {
  const out=[value&127];
  while((value>>>=7)>0) out.unshift((value&127)|128);
  return out;
}

// CC64 affects an entire instrument channel. Keep separately recorded pedals independent.
const programs = {0:33, 1:10, 3:4, 4:89, 5:0, 6:118};
const pedalKey = (event, channel) => event.take + ':' + channel;
function pedalChannels(song, sounds) {
  const used = new Set(Object.values(sounds).map(voice => voice.channel));
  const free = Array.from({length:14}, (_, i) => i + 2).filter(ch => ch !== 9 && !used.has(ch));
  const groups = song.tracks.map(track => new Map(track.events.filter(e => e.sustain > 0)
    .map(e => [pedalKey(e, sounds[e.clip].channel), sounds[e.clip].channel])));
  const reserved = new Set();
  song.tracks.forEach((track, i) => track.events.forEach(e => {
    const channel = sounds[e.clip].channel;
    if (!groups[i].has(pedalKey(e, channel))) reserved.add(channel);
  }));
  for (const group of groups) for (const [key, base] of group) {
    group.set(key, reserved.has(base) ? free.shift() : base);
    reserved.add(base);
  }
  return groups;
}

function soundingBeats(event, start) {
  if (!event.sustain) return event.length;
  return Math.max(event.length, event.beat + event.length + event.sustain - start);
}

function midi(song, sounds, voiceChannels, totalBeats = beats, linear = false) {
  const end = Math.round(totalBeats * ppq);
  const nameTag = name => {const n=bytes(name);return [255,3,...vlq(n.length),...n];};
  const tempo = Math.round(60000000/song.bpm);
  function track(events) {
    const data=[]; let last=0;
    for(const [tick,msg] of events.sort((a,b)=>a[0]-b[0])) {
      data.push(...vlq(tick-last),...msg); last=tick;
    }
    data.push(...vlq(Math.max(0,end-last)),255,47,0);
    return join([bytes('MTrk'),[data.length>>>24,(data.length>>>16)&255,(data.length>>>8)&255,data.length&255],data]);
  }
  const chunks=[track([[0,nameTag(song.title)], [0,[255,81,3,tempo>>>16,(tempo>>>8)&255,tempo&255]],
    [0,[255,88,4,4,2,24,8]], ...[...voiceChannels].filter(ch => ch in programs).map(ch => [0,[192+ch,programs[ch]]])])];
  const channels = pedalChannels(song, sounds);
  for(const [i, tr] of song.tracks.entries()) {
    const pedals = new Map(), instruments = new Map();
    const events=[[0,nameTag(tr.name)]];
    for(const e of tr.events) {
      const start = snap(e.beat, tr.timing), voice = sounds[e.clip];
      const at = linear ? Math.round(start * ppq) : Math.round(start * ppq) % end;
      const stop = Math.min(end, e.hardStop == null ? end : Math.round(e.hardStop * ppq));
      if (at >= stop) continue;
      const pedalChannel = channels[i].get(pedalKey(e, voice.channel));
      const channel = pedalChannel ?? voice.channel;
      if (pedalChannel !== undefined && e.sustain > 0) {
        const down = Math.round((e.pedalDown ?? e.beat + e.length) * ppq);
        const up = Math.min(stop, Math.round((e.beat + e.length + e.sustain) * ppq));
        if (!pedals.has(channel)) pedals.set(channel, new Map());
        pedals.get(channel).set(down, Math.max(up, pedals.get(channel).get(down) ?? down));
      }
      if (channel !== voice.channel) {
        e.midiChannel = channel;
        instruments.set(channel, programs[voice.channel]);
      }
      const gated = e.length != null;
      // If all instrument channels are occupied, bake just the overflow take's sustain into its MIDI gate.
      const gate = pedalChannel === undefined ? soundingBeats(e, start) : e.length;
      const seconds = gated ? gate * 60 / song.bpm : voice.duration;
      if (!seconds && !gated) continue;
      const length = Math.min(stop-at, Math.max(1, Math.round(seconds*song.bpm/60*ppq)));
      const velocity = Math.min(127, Math.max(1, Math.round((e.gain??1)*127)));
      for (const note of voice.notes ?? [voice.note]) {
        events.push([at,[144+channel,note,velocity]], [at+length,[128+channel,note,0]]);
      }
    }
    const controls = [...instruments].map(([channel, program]) => [0, [192+channel, program]]);
    for (const [channel, spans] of pedals) {
      let endOfHold = -1;
      for (const [down, up] of [...spans].sort((a,b) => a[0]-b[0])) {
        if (up <= down) continue;
        if (down > endOfHold) {
          if (endOfHold >= 0) controls.push([endOfHold, [176+channel,64,0]]);
          controls.push([down, [176+channel,64,127]]);
        }
        endOfHold = Math.max(endOfHold, up);
      }
      if (endOfHold >= 0) controls.push([endOfHold, [176+channel,64,0]]);
    }
    // At the same tick the pedal must be down before the finger releases the key.
    chunks.push(track([...controls, ...events]));
  }
  return join([bytes('MThd'),[0,0,0,6,0,1,0,chunks.length,ppq>>>8,ppq&255],...chunks]);
}

const crcTable = Uint32Array.from({length:256}, (_, value) => {
  for (let i=0; i<8; i++) value=(value>>>1)^((value&1) ? 0xedb88320 : 0);
  return value;
});
async function crc32(data) {
  let crc = -1;
  const update = chunk => { for (const byte of chunk) crc = (crc >>> 8) ^ crcTable[(crc ^ byte) & 255]; };
  if (data instanceof Blob) {
    const reader = data.stream().getReader();
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      update(chunk.value);
    }
  } else update(data);
  return (crc ^ -1) >>> 0;
}
async function zip(files) {
  const local=[],central=[];let offset=0;
  function header(size,signature) {
    const a=new Uint8Array(size),v=new DataView(a.buffer); v.setUint32(0,signature,true);
    return {a,u16:(p,n)=>v.setUint16(p,n,true),u32:(p,n)=>v.setUint32(p,n,true)};
  }
  for(const [path,data] of files) {
    const size = data.size ?? data.length;
    if (offset + size + 256 > 0xffffffff) throw new Error('This editable project exceeds the ZIP size limit. Download the mix as WAV.');
    const pathBytes=bytes(path),crc=await crc32(data),l=header(30,0x04034b50),c=header(46,0x02014b50);
    l.u16(4,20);l.u16(6,0x800);l.u16(12,33);l.u32(14,crc);l.u32(18,size);l.u32(22,size);l.u16(26,pathBytes.length);
    c.u16(4,20);c.u16(6,20);c.u16(8,0x800);c.u16(14,33);c.u32(16,crc);c.u32(20,size);c.u32(24,size);
    c.u16(28,pathBytes.length);c.u32(42,offset);
    local.push(l.a,pathBytes,data);central.push(c.a,pathBytes);offset+=30+pathBytes.length+size;
  }
  const end=header(22,0x06054b50);
  end.u16(8,files.length);end.u16(10,files.length);end.u32(12,central.reduce((n,a)=>n+a.length,0));end.u32(16,offset);
  return new Blob([...local,...central,end.a],{type:'application/zip'});
}
function safe(name) {return String(name).replace(/[^a-z0-9 _-]/gi,'').trim().slice(0,70)||'Untitled';}

function instrumentVoice(clip, chords) {
  if (chords && Object.hasOwn(chords, clip)) return {channel:4, notes:chords[clip]};
  const pitched = /^(Bass|Pluck|Jam bass|Jam pluck|Round bass|Plucked bass|Finger bass|Studio bass|Electric kick|Bright pluck|Soft keys|Warm pad|Piano) ([A-G])([b#]?)(\d)$/i.exec(clip);
  if (pitched) {
    const [, family, key, accidental, octave] = pitched;
    const note = 12 * (Number(octave)+1) + {C:0,D:2,E:4,F:5,G:7,A:9,B:11}[key.toUpperCase()]
      + (accidental === 'b' ? -1 : accidental === '#' ? 1 : 0);
    if (note <= 127) {
      const voice = family.toLowerCase();
      if (voice === 'electric kick') return {channel:6, note};
      if (voice === 'piano') return {channel:5, note};
      if (voice === 'warm pad') return {channel:4, note};
      return {channel: voice.endsWith('bass') ? 0 : voice === 'soft keys' ? 3 : 1, note};
    }
  }
  const drums = {Kick:36, Snare:38, Hat:42, 'Closed hat':42, 'Open hat':46, Clap:39, Shaker:70,
    Tom:45, 'Low tom':45, 'High tom':50, 'Deep kick':35, 'Rim click':37, Tambourine:54, Ride:51,
    'Pedal hat':44, Crash:49, Splash:55, 'Ride bell':53, 'Floor tom':41, 'Mid tom':47,
    'Electric kick':36, 'Electric snare':40, Cowbell:56, Claves:75, 'High block':76, 'Low block':77,
    'Low bongo':61, 'High bongo':60, 'Low conga':64, 'Open conga':63, 'Mute conga':62, 'Short guiro':73,
    'Mute triangle':80, 'Open triangle':81, 'Punch kick':36, 'Snap snare':40, 'Electric closed hat':42,
    'Electric open hat':46, 'Sub kick':35, 'Electric clap':39, 'Electric rim':37, 'Electric shaker':70,
    'Click kick':36, 'Noise snare':40, 'Low zap tom':45, 'High zap tom':50, 'Pulse bell':56,
    'Snare flam':38, 'Hat double':42, 'Kick double':36, 'Classic kick':36, 'Jam snare':38, 'Jam clap':39,
    'Jam closed hat':42, 'Jam open hat':46, 'Jam rimshot':37, 'Jam tom':45, 'Jam crash':49};
  if (Object.hasOwn(drums, clip)) return {channel:9, note:drums[clip]};
}

function sampleVoice(clip, samples, sampleChannels) {
  const index = samples.length;
  samples.push(clip);
  const channel = sampleChannels[Math.floor(index / 128)];
  if (channel === undefined) throw new Error('Too many distinct samples to assign MIDI notes.');
  return {channel, note:index % 128};
}

function wavHeader(frames) {
  if (frames*4 + 36 > 0xffffffff) throw new Error('This performance exceeds the WAV size limit.');
  const header = new Uint8Array(44), view = new DataView(header.buffer);
  const word = (i,s) => header.set(bytes(s),i);
  word(0,'RIFF'); view.setUint32(4,36+frames*4,true); word(8,'WAVE'); word(12,'fmt ');
  view.setUint32(16,16,true); view.setUint16(20,1,true); view.setUint16(22,2,true);
  view.setUint32(24,rate,true); view.setUint32(28,rate*4,true); view.setUint16(32,4,true); view.setUint16(34,16,true);
  word(36,'data'); view.setUint32(40,frames*4,true);
  return header;
}
function stereoPCM(left, right, scale) {
  const pcm = new Uint8Array(left.length * 4), view = new DataView(pcm.buffer);
  for (let i=0; i<left.length; i++) {
    view.setInt16(i*4, Math.round(left[i] * scale), true);
    view.setInt16(i*4+2, Math.round(right[i] * scale), true);
  }
  return pcm;
}

async function loadSamples(kit, song) {
  const sounds = Object.create(null), audio = new Map(), sampleNotes = [], files = [];
  const decoder = new OfflineAudioContext(2,1,rate);
  const clips = new Set(song.tracks.flatMap(t => t.events.map(e => e.clip)));
  const voices = new Map([...clips].map(id => [id, instrumentVoice(id, song.chordNotes)]));
  const voiceChannels = new Set([...voices.values()].filter(Boolean).map(voice => voice.channel));
  const sampleChannels = [2,3,4,5,6,7,8,10,11,12,13,14,15].filter(ch => !voiceChannels.has(ch));
  const taken = new Set();   // (two kicks of different kits would share the standard kick note: the second gets a note of its own)
  for (const id of clips) {
    const response = await fetch(kit.url('clip?id=' + encodeURIComponent(id)));
    if (!response.ok || !response.headers.get('content-type')?.startsWith('audio/wav')) throw new Error('Missing sample: ' + id);
    const raw = await response.arrayBuffer();
    const buffer = await decoder.decodeAudioData(raw.slice(0));
    const file = 'samples/' + (audio.size+1) + ' ' + safe(id) + '.wav';
    let voice = voices.get(id);
    const spot = voice && voice.channel + ':' + (voice.notes ?? [voice.note]).join('+');
    if (taken.has(spot)) voice = undefined; else if (voice) taken.add(spot);
    voice ??= sampleVoice(id, sampleNotes, sampleChannels);
    sounds[id] = {...voice, duration:buffer.duration, file};
    audio.set(id,buffer); files.push([file,new Uint8Array(raw)]);
  }
  return {sounds, audio, files, voiceChannels};
}

// One instrument per track in SFZ, the plain-text sampler format: which sample each note of that track's MIDI plays.
// With it in an SFZ player the imported notes sound as they did on the board and stay notes that can be changed.
// (Run after midi(), which settles the channel of every note.)
function instruments(song, sounds) {
  const files = [], chords = new Set();
  song.tracks.forEach((track, i) => {
    const channels = new Map(), held = new Set();
    for (const e of track.events) {
      if (sounds[e.clip].notes) { chords.add(e.clip); continue; }   // (a chord pad is one sample but several notes: no player maps that)
      if (!channels.has(e.clip)) channels.set(e.clip, new Set());
      channels.get(e.clip).add(e.midiChannel ?? sounds[e.clip].channel);
      // Played while a key is held, or cut short on the board: such a sound ends with its note. Any other rings
      // out whatever the note's length, as a drum sampler does, so that notes drawn in later need no length.
      if (e.length != null || e.hardStop != null) held.add(e.clip);
    }
    if (!channels.size) return;
    const shared = new Map();
    for (const id of channels.keys()) shared.set(sounds[id].note, (shared.get(sounds[id].note) || 0) + 1);
    const lines = [`// ${safe(song.title)}, track ${i + 1}: ${safe(track.name)}. Made by Padcade for the notes of this track in the MIDI file.`,
      '<control> default_path=../',
      '<global> amp_veltrack=100 amp_velcurve_1=0.008 amp_velcurve_127=1',   // (hit half as hard, half as loud: as on the board)
      ...[...channels].flatMap(([id, used]) => {
        const voice = sounds[id], lasts = held.has(id) ? `ampeg_release=${Math.max(.005, song.releaseSeconds?.[id] ?? 0).toFixed(3)}` : 'loop_mode=one_shot';
        // Only where two sounds of this track have the same note number does the channel tell them apart.
        const apart = shared.get(voice.note) > 1 ? [...used].map(ch => ` lochan=${ch + 1} hichan=${ch + 1}`) : [''];
        return apart.map(channel => `<region> key=${voice.note}${channel} ${lasts} sample=${voice.file}`);
      })];
    files.push([`instruments/${i + 1} ${safe(track.name)}.sfz`, bytes(lines.join('\n') + '\n')]);
  });
  return {files, chords:[...chords]};
}
function readme(song, {midiFile, mix, chords, length}) {
  const tempo = `${song.bpm} BPM`;
  const muted = mix ? [] : song.tracks.flatMap((track, i) => track.muted ? [`${i+1} ${track.name}`] : []);
  return bytes([`${song.title}: exported from Padcade`, '',
    `Tempo ${tempo}, four beats to the bar. ${length}`, '',
    ...(mix ? [`${mix}`.padEnd(15) + 'the finished mix.'] : []),
    'tracks/'.padEnd(15) + `one sound file per track (44.1 kHz, 16 bits, stereo). They start together${muted.length ? '.' : ' and add up to the mix.'}`,
    ''.padEnd(15) + `Set your music program to ${tempo} and drag them in.`,
    ...(muted.length ? [`To hear the saved mix, mute ${muted.join(', ')} in your music program.`] : []),
    midiFile.padEnd(15) + 'the notes: one MIDI track per track, with the tempo, the track names, how hard each pad was hit',
    ''.padEnd(15) + 'and the sustain pedal. Drums use the standard drum notes (General MIDI) where there is one.',
    ''.padEnd(15) + 'No two sounds share a note: where they would, the later one has a note of its own at the low end.',
    'instruments/'.padEnd(15) + 'the board\u2019s own sounds for those notes, one SFZ file per track. Put an SFZ player on the track',
    ''.padEnd(15) + 'that holds the MIDI track of the same number and load the file: the notes then sound as they did',
    ''.padEnd(15) + 'on the board, and you can move, add and delete them.',
    'samples/'.padEnd(15) + 'the sounds themselves. The instruments point at them: keep the folders together.',
    'project.json'.padEnd(15) + 'Padcade\u2019s own description of this. Import song on the board reads a song\u2019s ZIP back in.', '',
    ...(chords.length ? [`Chord pads (${chords.join(', ')}) are written as the notes of the chord, for an instrument of your choice;`,
      'their own sound is in the track\u2019s sound file.', ''] : [])].join('\r\n'));
}

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = name;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

function strike(song, event, startBeat, audio) {
  const framesPerBeat = rate * 60 / song.bpm;
  const at = Math.round(startBeat * framesPerBeat);
  const buffer = audio.get(event.clip), left = buffer.getChannelData(0);
  const right = buffer.numberOfChannels > 1 ? buffer.getChannelData(1) : left;
  const gate = event.length == null ? buffer.length : Math.round(soundingBeats(event, startBeat) * framesPerBeat);
  const release = event.length == null ? 0 : Math.round((song.releaseSeconds?.[event.clip] ?? 0) * rate);
  const cut = event.hardStop == null ? Infinity : Math.round(event.hardStop * framesPerBeat);
  const end = Math.min(at + buffer.length, at + gate + release, cut);
  return {at, end, gate, release, fadeFrom:end === cut ? cut - cutFrames : Infinity, left, right, gain:event.gain ?? 1};
}
function addStrike(s, left, right, from, to, offset) {
  for (let frame = from; frame < to; frame++) {
    const n = frame - s.at, i = frame - offset;
    let level = n < s.gate ? s.gain : s.gain * (1 - (n - s.gate) / s.release) ** 3;
    if (frame >= s.fadeFrom) level *= (s.end - frame) / cutFrames;
    left[i] += s.left[n] * level;
    right[i] += s.right[n] * level;
  }
}

export async function exportSong(kit) {
  const result = await kit.ask('project');
  if (!result?.tracks) throw new Error(result?.error || 'Choose a song to export.');
  const {importLimits, ...song} = result;
  const frames = Math.round(rate * beats * 60 / song.bpm);
  const {sounds, audio, files, voiceChannels} = await loadSamples(kit, song);
  const sampleFiles = files.slice();
  const stems = song.tracks.map(track => track.events.map(event => {
    const hit = strike(song, event, snap(event.beat, track.timing), audio);
    if (hit.at >= frames) {  // A snapped downbeat belongs at frame zero.
      hit.at -= frames; hit.end -= frames; hit.fadeFrom -= frames;
    }
    return hit;
  }));
  const recorded = stems.filter(hits => hits.length);
  const audible = stems.filter((hits, i) => hits.length && !song.tracks[i].muted);
  const mixes = [...recorded];
  if (recorded.length > 1) mixes.push(recorded.flat());
  if (audible.length > 1 && audible.length < recorded.length) mixes.push(audible.flat());
  // Each WAV rounds independently. Allow half a PCM step per stem when the files are summed.
  const scale = await pcmScale(mixes, frames, 32767 - Math.floor(recorded.length / 2));
  for (const [i, hits] of stems.entries()) {
    files.push([`tracks/${i+1} ${safe(song.tracks[i].name)}${song.tracks[i].muted ? ' (muted)' : ''} ${song.bpm}bpm.wav`, await takeWav(hits, frames, scale)]);
  }
  const sequence = midi(song,sounds,voiceChannels);
  const project = {format:'Padcade song 1', loopBeats:beats, sampleRate:rate, midiChannelNumbering:'0–15', ...song, sounds};
  const manifest = ['project.json', bytes(JSON.stringify(project))];
  const played = instruments(song, sounds);
  files.push(manifest, ['song.mid',sequence], ...played.files,
    ['README.txt', readme(song, {midiFile:'song.mid', chords:played.chords, length:`The song is a loop of ${beats} beats (two bars).`})]);
  let archive = await zip(files);
  let notice;
  if (importLimits) {
    if (sampleFiles.length > importLimits.clips || manifest[1].length > importLimits.project
        || sampleFiles.reduce((n, [, raw]) => n + raw.length, 0) > importLimits.samples
        || Object.values(sounds).some(voice => voice.duration > 20)) {
      notice = 'Saved for editing on your computer. This song exceeds the board’s import limits.';
    } else if (archive.size > importLimits.archive) {
      archive = await zip([...sampleFiles, manifest]);
      notice = 'Saved a compact song backup with its samples. Rendered tracks and MIDI were left out to fit the board.';
    }
  }
  download(archive, safe(song.title) + '.zip');
  if (notice) kit.toast(notice);
}

// Both passes use one second of floating-point audio, even for a long performance.
async function mixBlocks(hits, frames, consume) {
  hits.sort((a,b) => a.at - b.at);
  let next = 0, ringing = [];
  for (let offset = 0; offset < frames; offset += rate) {
    const count = Math.min(rate, frames-offset), end = offset + count;
    const left = new Float32Array(count), right = new Float32Array(count);
    while (next < hits.length && hits[next].at < end) ringing.push(hits[next++]);
    for (const hit of ringing) addStrike(hit, left, right, Math.max(offset, hit.at), Math.min(end, hit.end), offset);
    ringing = ringing.filter(hit => hit.end > end);
    consume(left, right);
    if ((offset / rate + 1) % 16 === 0) await new Promise(done => setTimeout(done, 0));
  }
}

async function pcmScale(streams, frames, ceiling = 32767) {
  let peak = ceiling / 32767;  // Preserve the original gain when the mix already fits.
  for (const hits of streams) if (hits.length) {
    await mixBlocks(hits, frames, (left, right) => {
      for (let i=0; i<left.length; i++) peak = Math.max(peak, Math.abs(left[i]), Math.abs(right[i]));
    });
  }
  return ceiling / peak;
}

async function takeWav(hits, frames, scale) {
  const parts = [wavHeader(frames)];
  await mixBlocks(hits, frames, (left, right) => parts.push(new Blob([stereoPCM(left, right, scale)])));
  return new Blob(parts, {type:'audio/wav'});
}

export async function exportTake(kit, slot, id, format) {
  const song = await kit.ask('take-project?id=' + encodeURIComponent(id) + '&slot=' + slot);
  if (!song?.tracks) throw new Error(song?.error || 'Choose a saved take to export.');
  const frames = Math.round(song.duration * rate);
  wavHeader(frames);  // Check the file limit before measuring a long performance.
  const {sounds, audio, files, voiceChannels} = await loadSamples(kit, song);
  const stems = song.tracks.flatMap((track, i) => track.events.length ? [{
    file:`tracks/${i+1} ${safe(track.name)} ${song.bpm}bpm.wav`,
    hits:track.events.map(event => strike(song, event, event.beat, audio))
  }] : []);
  const all = stems.flatMap(stem => stem.hits);
  const streams = stems.length > 1 ? [all, ...stems.map(stem => stem.hits)] : [all];
  const scale = await pcmScale(streams, frames);
  const mix = await takeWav(all, frames, scale);
  if (format === 'wav') return download(mix, safe(song.title) + '.wav');
  files.push(['performance.wav',mix]);
  for (const stem of stems) files.push([stem.file, stems.length === 1 ? mix : await takeWav(stem.hits, frames, scale)]);
  const sequence = midi(song, sounds, voiceChannels, song.duration * song.bpm / 60, true);
  const project = {format:'Padcade performance 1', sampleRate:rate, midiChannelNumbering:'0–15', ...song, sounds};
  const played = instruments(song, sounds), seconds = Math.round(song.duration);
  files.push(['project.json',bytes(JSON.stringify(project,null,2))], ['performance.mid',sequence], ...played.files,
    ['README.txt', readme(song, {midiFile:'performance.mid', mix:'performance.wav', chords:played.chords,
      length:`The performance lasts ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}.`})]);
  download(await zip(files), safe(song.title) + '.zip');
}
