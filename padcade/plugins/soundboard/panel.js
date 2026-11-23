export default kit => {
  const {el, toast} = kit;
  const style = document.getElementById('soundboard-style') || el('link', {id:'soundboard-style', rel:'stylesheet'});
  // This file is loaded anew when it changes, also into a page left open. Its own parts are asked for under the same
  // stamp, or that page would go on using the ones it loaded before an update.
  const stamp = new URL(import.meta.url).search;
  style.href = new URL('./panel.css' + stamp, import.meta.url);
  if (!style.isConnected) document.head.append(style);
  // What is open in the panel and is not the board's business: a track's settings, a menu, a name being typed.
  let introOpen = false, optionsFor = null, renaming = false, renamingTake = null, openMenu = null, exporting = false, importingSong = false;
  const button = (text, onclick, disabled = false, className = 'quiet') =>
    el('button', {type: 'button', className, textContent: text, onclick, disabled});
  const row = (...children) => el('div', {className: 'pair'}, ...children);
  const note = text => el('p', {className: 'muted', textContent: text});
  const group = (title, ...children) => el('section', {className: 'slot'}, el('h3', {textContent: title}), ...children);

  const ICONS = {   // drawn for this panel, 24 by 24, in the colour of the text
    back: '<path d="M19 12H5"/><path d="M11 6l-6 6 6 6"/>',
    more: '<circle class="solid" cx="5.5" cy="12" r="1.7"/><circle class="solid" cx="12" cy="12" r="1.7"/><circle class="solid" cx="18.5" cy="12" r="1.7"/>',
    grip: '<circle class="solid" cx="9" cy="6.5" r="1.5"/><circle class="solid" cx="15" cy="6.5" r="1.5"/><circle class="solid" cx="9" cy="12" r="1.5"/><circle class="solid" cx="15" cy="12" r="1.5"/><circle class="solid" cx="9" cy="17.5" r="1.5"/><circle class="solid" cx="15" cy="17.5" r="1.5"/>',
    play: '<path class="solid" d="M8 5.5v13l11-6.5z"/>',
    stop: '<rect class="solid" x="6.5" y="6.5" width="11" height="11" rx="1.5"/>',
    record: '<circle class="solid" cx="12" cy="12" r="5.2"/>',
    undo: '<path d="M8.5 5L4 9.5 8.5 14"/><path d="M4 9.5h10a5 5 0 0 1 0 10H10"/>',
    bin: '<path d="M4 7h16"/><path d="M9.5 7V4h5v3"/><path d="M6 7l1 13h10l1-13"/><path d="M10 11v5.5M14 11v5.5"/>',
    download: '<path d="M12 4v11"/><path d="M7 10.5l5 5 5-5"/><path d="M5 20h14"/>',
    upload: '<path d="M12 16V5"/><path d="M7 9.5l5-5 5 5"/><path d="M5 20h14"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
    pencil: '<path d="M4 20l1-4L16 5l3 3L8 19z"/><path d="M14 7l3 3"/>',
  };
  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    svg.innerHTML = ICONS[name];
    return svg;
  }
  // A button that is only its icon says what it does to a screen reader and when pointed at.
  function tool(name, label, onclick, {id, className = '', disabled = false, title = label} = {}) {
    const b = el('button', {type:'button', className:('sb-icon ' + className).trim(), onclick, disabled, title}, icon(name));
    b.setAttribute('aria-label', label);
    if (id) b.id = id;
    return b;
  }
  const worded = (name, text, onclick, disabled = false, className = 'quiet') =>
    el('button', {type:'button', className:className + ' sb-with-icon', onclick, disabled}, icon(name), text);

  async function send(action, extra = {}) {
    kit.wakeAudio();
    const answer = await kit.ask('studio', {action, ...extra});
    await kit.refresh();
    return answer;
  }
  function menu(id, name, label, items, disabled = false) {
    const open = openMenu === id && !disabled;
    const toggle = tool(name, label, () => { openMenu = open ? null : id; kit.redraw(); }, {id:'studio-menu-' + id, disabled});
    toggle.setAttribute('aria-haspopup', 'menu');
    toggle.setAttribute('aria-expanded', String(open));
    const list = el('div', {hidden:!open}, ...items);
    list.setAttribute('role', 'menu');
    return el('div', {className:'sb-menu'}, toggle, list);
  }
  function item(name, text, run, {disabled = false, danger = false} = {}) {
    const b = el('button', {type:'button', className:danger ? 'danger' : '', disabled, onclick: () => { openMenu = null; kit.redraw(); run(); }}, icon(name), text);
    b.setAttribute('role', 'menuitem');
    return b;
  }
  // One pair of listeners for the whole page, replaced when the panel is loaded again: a press elsewhere closes the
  // open menu, and Escape closes it or goes back to the tracks.
  window.removeEventListener('pointerdown', window.padcadeStudioAway, true);
  window.removeEventListener('keydown', window.padcadeStudioEscape);
  window.padcadeStudioAway = event => {
    if (openMenu && !event.target.closest?.('.sb-menu')) { openMenu = null; kit.redraw(); }
  };
  window.padcadeStudioEscape = event => {
    if (event.key !== 'Escape' || event.target.matches?.('input, select, textarea')) return;
    if (openMenu) { openMenu = null; return kit.redraw(); }
    document.querySelector('.sb-studio .sb-back:enabled')?.click();
  };
  window.addEventListener('pointerdown', window.padcadeStudioAway, true);
  window.addEventListener('keydown', window.padcadeStudioEscape);

  // What to press on the pads for each thing, to look up at a glance.
  const SHEET = [
    ['Record', [['A part', 'Hold a track arrow, play after four ticks'], ['Another layer', 'Hold the same arrow again'],
      ['End it early', 'Tap that arrow'], ['A whole performance', 'Tap the round Record, tap again to save']]],
    ['Play', [['Start or stop the loop', 'Stop Clip'], ['Mute a track', 'Tap its arrow'], ['Another bank', 'Shift, then a bank'],
      ['Tempo', 'Shift, then ▲ or ▼'], ['Sustain', 'Hold Sends'], ['Metronome', 'Click'], ['Volume', 'Volume, then a level']]],
    ['Tidy up', [['Timing of a track', 'Hold Quantise, tap its arrow'], ['Remove the newest layer', 'Hold Undo, tap its arrow'],
      ['Clear a track or song', 'Delete, tap it, Delete again'], ['Bring it back', 'Undo']]],
    ['Songs and takes', [['Songs', 'Track Select'], ['Copy a song', 'Duplicate, the song, an empty slot'], ['Takes', 'Hold the round Record']]],
  ];
  function intro() {
    const help = el('details', {className:'sb-guide', open:introOpen}, el('summary', {textContent:'What to press on the pads'}),
      ...SHEET.flatMap(([title, rows]) => [el('h4', {textContent:title}),
        el('dl', {}, ...rows.flatMap(([what, how]) => [el('dt', {textContent:what}), el('dd', {textContent:how})]))]));
    help.firstChild.onclick = () => { introOpen = !help.open; };
    return help;
  }

  function beatStrip(s) {
    const strip = el('div', {className:'sb-beats'});
    strip.setAttribute('aria-hidden', 'true');
    for (let i = 0; i < 8; i++) {
      const mark = el('span');
      if (s.playing) {
        const position = ((s.beat - i) % 8 + 8) % 8;
        mark.style.animationDuration = (8 * 60 / s.bpm) + 's';
        mark.style.animationDelay = (-position * 60 / s.bpm) + 's';
      }
      strip.append(mark);
    }
    return strip;
  }

  // ---- moving a track: its grip is dragged to a gap between the rows, or moved with the arrow keys
  let drag = null;   // {slot, from, to}: the row being dragged and the gap it is over (0 above the first row, 8 below the last)
  function showDrag() {
    document.querySelectorAll('.sb-studio .sb-track-row').forEach((line, k, lines) => {
      line.classList.toggle('dragging', !!drag && k === drag.from);
      line.classList.toggle('drop-before', !!drag && k === drag.to && drag.to !== drag.from && drag.to !== drag.from + 1);
      line.classList.toggle('drop-after', !!drag && drag.to === lines.length && k === lines.length - 1 && drag.from !== k);
    });
  }
  let moving = false;   // One move at a time: a second one asked for meanwhile would name places that have changed.
  async function moveTrack(slot, from, to, focus) {
    if (moving) return;
    moving = true;
    try {
      const answer = await kit.ask('studio', {action:'move_track', slot, mode:'play', from, to});
      if (!answer?.error && optionsFor?.slot === slot) {   // its settings stay open where the track went
        const k = optionsFor.index;
        optionsFor.index = k === from ? to : k - (from < k && k <= to) + (to <= k && k < from);
      }
      await kit.refresh();
      if (focus && !answer?.error) document.getElementById('studio-grip-' + to)?.focus();
    } finally { moving = false; }
  }
  function grip(s, track, index, movable) {
    const g = tool('grip', 'Move ' + track.name + ': drag, or use the arrow keys', null,
      {id:'studio-grip-' + index, className:'sb-grip', disabled:!movable, title:'Drag to move'});
    g.onpointerdown = event => {
      if (!event.isPrimary || event.button !== 0) return;
      event.preventDefault();
      g.focus();
      drag = {slot:s.slot, from:index, to:index};
      const mine = event.pointerId;   // (another finger moving or lifting elsewhere is not this drag's)
      const move = e => {
        if (e.pointerId !== mine) return;
        const lines = [...document.querySelectorAll('.sb-studio .sb-track-row')];
        const over = lines.findIndex(line => { const box = line.getBoundingClientRect(); return e.clientY < box.top + box.height / 2; });
        drag.to = over < 0 ? lines.length : over;
        showDrag();
      };
      const end = e => {
        if (e.pointerId !== mine) return;
        for (const type of ['pointermove', 'pointerup', 'pointercancel']) window.removeEventListener(type, type === 'pointermove' ? move : end);
        const {slot, from, to} = drag, place = to > from ? to - 1 : to;
        drag = null;
        showDrag();
        if (e.type === 'pointerup' && place !== from) moveTrack(slot, from, place, true);
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', end);
      window.addEventListener('pointercancel', end);
      showDrag();
    };
    g.onkeydown = event => {
      const step = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
      if (!step || index + step < 0 || index + step > 7) return;
      event.preventDefault();
      moveTrack(s.slot, index, index + step, true);
    };
    return g;
  }

  const recordingPhases = {'count-in':'Count-in', waiting:'Waiting for loop', recording:'Recording'};
  let hold = null;   // The track being held: {index, timer, recorded}. It outlives the row, which is redrawn while a song plays.
  function trackButton(s, track, index, available, clearing) {
    // (as a tap on the track's arrow on the pads: it mutes or unmutes what is recorded there, and nothing on an empty track)
    const tap = (clearing ? 'track:' : 'tap_track:') + index, record = 'record_track:' + index;
    const guard = {slot:s.slot, mode:s.mode};
    const current = s.recording_track === index;
    const preview = clearing && s.target === index;
    const editing = !clearing && optionsFor?.slot === s.slot && optionsFor.index === index;
    const recording = current && recordingPhases[s.recording] && recordingPhases[s.recording] + (s.recording === 'count-in' ? ' · ' + s.count_in : '');
    const audible = !track.muted;
    const state = recording || (!track.hits ? 'Empty' : track.muted ? 'Muted' : 'On');
    const caption = [state, preview && 'Preview',
      track.hits > 0 && track.hits + (track.hits === 1 ? ' hit' : ' hits'),
      track.layers > 0 && track.layers + (track.layers === 1 ? ' layer' : ' layers'),
      track.hits > 0 && track.timing !== 'As played' && track.timing].filter(Boolean).join(' · ');
    const pattern = el('span', {className:'sb-pattern'});
    pattern.setAttribute('aria-hidden', 'true');
    for (let step = 0; step < 16; step++) pattern.append(el('span', {className: track.steps & (1 << step) ? 'hit' : ''}));

    const b = el('button', {type:'button', className:'sb-track', id:'studio-track-' + index, disabled:!available.has(tap) && !available.has(record)},
      el('span', {className:'sb-number', textContent:index + 1}),
      el('span', {className:'sb-track-label'}, el('strong', {textContent:track.name}), el('small', {textContent:caption})), pattern);
    b.dataset.track = index;
    b.style.setProperty('--track-colour', kit.PALETTE[index]);
    if (preview || editing || recording) b.classList.add('sb-active');
    if (!track.hits && !recording) b.classList.add('sb-empty');
    if (track.hits && !recording) b.setAttribute('aria-pressed', String(clearing ? preview : audible));
    b.setAttribute('aria-label', [index + 1, track.name, caption].join(' · '));
    if (!audible) b.classList.add('sb-muted');
    b.onclick = () => {
      const wasHeld = hold?.index === index && hold.recorded;
      hold = null;
      if (!wasHeld && available.has(tap)) send(tap, guard);
    };
    if (available.has(record)) {
      b.onpointerdown = e => {
        if (!e.isPrimary || e.button !== 0) return;
        clearTimeout(hold?.timer);
        const mine = hold = {index, recorded: false};
        mine.timer = setTimeout(() => {
          if (hold !== mine || document.hidden || !document.hasFocus()) return;
          mine.recorded = true;
          send(record, guard);
        }, 450);
      };
      // (Not on blur: a redraw takes the focus from the row under the finger. The timer asks about the window instead.)
      b.onpointerup = b.onpointercancel = b.onpointerleave = () => {
        clearTimeout(hold?.timer);
        const over = hold;
        setTimeout(() => { if (hold === over) hold = null; });   // (after the click that follows a release)
      };
      b.setAttribute('aria-keyshortcuts', 'R');
      b.onkeydown = e => {
        if (e.key.toLowerCase() === 'r' && !e.altKey && !e.ctrlKey && !e.metaKey) {
          e.preventDefault();
          if (!e.repeat) send(record, guard);
        }
      };
    }
    return b;
  }
  function trackRow(s, track, index, available, busy) {
    const guard = {slot:s.slot, mode:s.mode};
    const phase = s.recording_track === index && s.recording in recordingPhases ? s.recording : null;
    const open = optionsFor?.slot === s.slot && optionsFor.index === index;
    // The red dot starts a layer on this track; while this track counts in or records, the same button ends it.
    const recordLabel = phase === 'count-in' ? 'Cancel count-in' : phase === 'waiting' ? 'Cancel recording' : phase === 'recording' ? 'Finish layer'
      : track.hits ? 'Add a layer to ' + track.name : 'Record ' + track.name;
    const recordAction = phase ? 'record' : 'record_track:' + index;
    const record = tool(phase ? 'stop' : 'record', recordLabel, () => send(recordAction, phase ? {...guard, track:index} : guard),
      {id:'studio-record-' + index, className:'sb-rec', disabled:!available.has(recordAction), title:phase ? recordLabel : track.hits ? 'Add a layer' : 'Record'});
    record.setAttribute('aria-pressed', String(!!phase));
    const more = tool('more', (open ? 'Close ' : 'Open ') + track.name + ' settings', () => { optionsFor = open ? null : {slot:s.slot, index}; kit.redraw(); },
      {id:'studio-more-' + index, disabled:busy, title:'Name, timing, undo, clear'});
    more.setAttribute('aria-expanded', String(open));
    const line = el('div', {className:'sb-track-row'}, grip(s, track, index, available.has('move_track')), trackButton(s, track, index, available, false), record, more);
    if (open) {
      const target = {slot:s.slot, index};
      const name = el('input', {type:'text', id:'studio-rename_track-' + s.slot + '-' + index, value:track.name, maxLength:32, required:true, disabled:busy,
        onchange: event => { if (event.target.reportValidity()) send('rename_track', {value:event.target.value.trim(), ...target}); }});
      const timing = el('div', {className:'seg'}, ...['As played', 'Gentle', 'Fine', 'Tight'].map(choice => {
        const b = button(choice, () => send('timing', {value:choice, ...target}), busy || !available.has('quantise_track:' + index), '');
        b.setAttribute('aria-pressed', String(track.timing === choice));
        return b;
      }));
      timing.setAttribute('role', 'group');
      timing.setAttribute('aria-label', 'Timing');
      const undo = button('Undo last layer', () => send('undo_track:' + index, guard), !available.has('undo_track:' + index));
      undo.id = 'studio-undo-' + index;
      const tools = el('div', {className:'sb-tools'}, undo, el('span', {className:'gap'}),
        tool('bin', 'Clear ' + track.name, () => send('clear_track:' + index, guard),
          {id:'studio-clear-' + index, className:'framed danger', disabled:!available.has('clear_track:' + index), title:'Clear track'}));
      const fields = el('div', {className:'sb-option-fields', id:'studio-options-' + index},
        kit.field('Name', name), el('div', {className:'field'}, el('span', {textContent:'Timing'}), timing), tools);
      fields.setAttribute('role', 'group');
      fields.setAttribute('aria-label', track.name + ' settings');
      more.setAttribute('aria-controls', fields.id);
      line.append(fields);
    }
    return line;
  }
  function clock(seconds) {
    const n = Math.floor(seconds);
    return Math.floor(n / 60) + ':' + String(n % 60).padStart(2, '0');
  }
  function recordingClock(elapsed) {
    const start = kit.state().receivedAt - elapsed * 1000;
    const label = el('output', {className:'sb-capturing'});
    label.setAttribute('aria-live', 'off');
    function tick() {
      const ms = performance.now() - start;
      label.textContent = 'Recording performance · ' + clock(ms / 1000);
      setTimeout(() => { if (label.isConnected) tick(); }, Math.ceil(1000 - ms % 1000));
    }
    tick();
    return label;
  }
  // The one way back from Songs and from Takes: the same button in the same place.
  function back(action, available, guard) {
    const b = worded('back', 'Back to tracks', () => send(action, guard), !available.has(action), 'quiet sb-back');
    b.id = 'studio-back';
    return b;
  }
  // What was last cleared or deleted, with the way to get it back.
  // It is told for a while and then goes by itself; its × puts it away at once. The way back stays in the song's
  // menu, or under the takes, for as long as the board can still bring the thing back.
  const TOLD = 12000, told = new Map();   // each thing's own name -> when its notice first showed here (−Infinity: put away)
  function notice(id, text, onclick) {
    if (!told.has(id)) {
      told.set(id, performance.now());
      setTimeout(() => kit.redraw(), TOLD + 60);
    }
    if (performance.now() - told.get(id) > TOLD) return null;
    const undo = button('Undo', onclick, false, '');
    undo.id = 'studio-restore';
    const away = button('×', () => { told.set(id, -Infinity); kit.redraw(); }, false, 'sb-away');
    away.id = 'studio-dismiss';
    away.title = 'Dismiss';
    away.setAttribute('aria-label', 'Dismiss');
    const line = el('p', {className:'sb-notice'}, text, el('span', {}, undo, away));
    line.setAttribute('role', 'status');
    return line;
  }
  function takeLibrary(s, command, available) {
    const p = s.performance, deleting = s.mode === 'delete-take', target = s.target, slot = s.slot;
    const out = [el('div', {className:'sb-sectionhead'}, back('takes', available, {slot, mode:s.mode}), el('h3', {textContent:'Takes'}))];
    const bringBack = () => send('undo', {slot, mode:'takes'});
    const gone = p.deleted && !deleting && notice(p.deleted.id, p.deleted.title + ' deleted.', bringBack);
    if (gone) out.push(gone);
    if (s.playing) out.push(note('Your loop is playing. Playing a take pauses the loop.'));
    const rows = p.items.map(t => {
      const context = {slot, take:t.id};
      const playing = p.playing && t.id === p.selected;
      const listen = tool(playing ? 'stop' : 'play', (playing ? 'Stop ' : 'Play ') + t.title, () => send('play_take', context), {id:'studio-listen-' + t.id, disabled:deleting});
      let name;
      if (deleting) {   // (Delete was pressed on the pads: the take to remove is chosen here or there)
        name = button(t.title, () => send('take:' + t.id, {slot, mode:s.mode}), false, 'sb-take-name');
        name.setAttribute('aria-pressed', String(target === t.id));
      } else if (renamingTake === t.id) {
        name = el('input', {type:'text', id:'studio-take-name-' + t.id, value:t.title, maxLength:48, required:true,
          onchange: e => { if (e.target.reportValidity()) { renamingTake = null; send('rename_take', {...context, value:e.target.value.trim()}); } },
          onblur: () => { if (renamingTake === t.id) { renamingTake = null; kit.redraw(); } }});
        name.setAttribute('aria-label', 'Take name');
        queueMicrotask(() => { if (name.isConnected && document.activeElement !== name) name.focus(); });
      } else {
        name = button(t.title, () => { renamingTake = t.id; kit.redraw(); }, false, 'sb-take-name');
        name.title = 'Rename';
        name.setAttribute('aria-label', 'Rename ' + t.title);
      }
      name.id ||= 'studio-take-' + t.id;
      const save = format => async () => {
        exporting = format; kit.redraw();
        try {
          const {exportTake} = await import('./export.js' + stamp);
          await exportTake(kit, slot, t.id, format);
        } catch (error) { toast(error.message || 'Could not export the take.'); }
        finally { exporting = false; kit.redraw(); }
      };
      const download = menu('take-' + t.id, 'download', 'Download ' + t.title, [
        item('download', 'WAV, ready to listen to', save('wav')), item('download', 'Editable project (ZIP)', save('zip'))], deleting || !!exporting);
      const remove = tool('bin', 'Delete ' + t.title, () => send('delete_take', {...context, mode:'takes'}),
        {id:'studio-delete-' + t.id, className:'danger', disabled:deleting || !available.has('delete')});
      return el('div', {className:'sb-take-row'}, listen, name, el('small', {textContent:clock(t.duration)}), download, remove);
    });
    out.push(rows.length ? el('div', {className:'sb-takes'}, ...rows)
      : el('div', {}, note('No takes yet.'), note('Go back to tracks and choose Record performance to save your first take.')));
    if (exporting) out.push(note('Preparing download…'));
    if (p.deleted && !deleting && !gone) out.push(button('Bring back ' + p.deleted.title, bringBack, !available.has('undo'), 'sb-link'));
    if (deleting) {
      const confirm = command('delete','Confirm deletion');
      confirm.disabled = !target || confirm.disabled;
      out.push(note(target ? 'Press Confirm deletion to remove the selected take.' : 'Choose the take to delete.'),
        row(command('undo','Cancel'), confirm));
    }
    return out;
  }
  async function importSong() {
    const [file] = await kit.files({accept:'.zip'});
    if (!file || importingSong) return;
    importingSong = true;
    kit.redraw();
    try {
      const result = await kit.ask('import-song', file);
      if (result.error) return;
      await kit.refresh();
      toast('Imported ' + result.title + ' as song ' + (result.slot + 1) + '.');
    } finally {
      importingSong = false;
      kit.redraw();
    }
  }
  async function exportSong() {
    exporting = 'song'; kit.redraw();
    try {
      const {exportSong} = await import('./export.js' + stamp);
      await exportSong(kit);
    } catch (error) { toast(error.message || 'Could not export the song.'); }
    finally { exporting = false; kit.redraw(); }
  }
  function studioView() {
    const {studio: s, picker} = kit.plugin().state;
    if (!s) return [];
    const available = new Set(s.available);
    const guard = {slot:s.slot, mode:s.mode};
    const command = (action, label, primary = false) => {
      const confirming = action === 'delete' && s.mode.startsWith('delete') && s.target !== null;
      const context = confirming ? {...guard, target:s.target} : action === 'performance' ? {...guard, wasRecording:s.performance.recording} : guard;
      const b = button(label, () => send(action, context),
        !available.has(action), primary ? 'act' : 'quiet');
      b.id = 'studio-action-' + action;
      return b;
    };
    const takesOpen = ['takes', 'delete-take'].includes(s.mode);
    const library = ['songs','duplicate','delete-song'].includes(s.mode);
    const busy = s.recording !== 'off';
    const canSetTempo = s.slot !== null && !busy && ['play','songs'].includes(s.mode);
    const canEdit = canSetTempo && !picker;
    if (optionsFor && (optionsFor.slot !== s.slot || s.mode !== 'play')) optionsFor = null;
    if (!canEdit) renaming = false;

    // ---- the song: its name, Songs, and one menu for what is done with it
    let title = el('h2', {textContent:s.title || 'Choose a song'});
    if (renaming) {
      title = el('input', {type:'text', id:'studio-rename_song-' + s.slot, value:s.title, maxLength:48, required:true,
        onchange: event => { if (event.target.reportValidity()) { renaming = false; send('rename_song', {value:event.target.value.trim(), slot:s.slot}); } },
        onblur: () => { if (renaming) { renaming = false; kit.redraw(); } }});
      title.setAttribute('aria-label', 'Song name');
      queueMicrotask(() => { if (title.isConnected && document.activeElement !== title) title.focus(); });
    }
    const songs = command('songs', 'Songs');
    songs.disabled = songs.disabled || library;
    const songMenu = menu('song', 'more', 'Song menu', [
      s.restorable && item('undo', 'Bring back ' + s.restorable.name, () => send('restore', guard), {disabled:!available.has('restore')}),
      s.restorable && el('hr'),
      item('pencil', 'Rename', () => { renaming = true; kit.redraw(); }, {disabled:!canEdit}),
      item('copy', 'Duplicate', async () => {
        const answer = await send('duplicate_song', {slot:s.slot});
        if (Number.isInteger(answer?.slot)) toast('Copied to song ' + (answer.slot + 1) + '.');
      }, {disabled:!available.has('duplicate_song')}),
      el('hr'),
      item('download', exporting === 'song' ? 'Exporting…' : 'Export song', exportSong, {disabled:!canEdit || !!exporting}),
      item('upload', importingSong ? 'Importing…' : 'Import song', importSong, {disabled:importingSong || busy || takesOpen || s.performance.recording || !s.slots.includes(null)}),
      el('hr'),
      item('bin', 'Delete song', () => send('delete_song', {slot:s.slot}), {disabled:!available.has('delete_song'), danger:true}),
    ]);
    const header = el('div', {className:'sb-header'},
      el('div', {className:'sb-title'}, el('small', {textContent:s.slot !== null ? 'Song ' + (s.slot + 1) + ' of 16' : 'No song open'}), title),
      songs, songMenu);
    const studio = el('section', {className:'sb-studio'}, header);
    if (s.performance?.error) studio.append(el('p', {role:'status', textContent:s.performance.error}));
    if (s.storage_notice) studio.append(el('p', {role:'status', textContent:s.storage_notice}));

    // ---- playing: the loop, its tempo and click; below, a whole performance and the takes it made
    const tracksOpen = !takesOpen && !library && !picker;
    if (s.slot !== null && !takesOpen && !library) {
      const play = worded(s.playing ? 'stop' : 'play', s.playing ? 'Stop' : 'Play', () => send('play', guard), !available.has('play'), 'quiet sb-play');
      play.id = 'studio-action-play';
      const click = command('click', 'Click');
      click.classList.add('sb-toggle');
      click.setAttribute('aria-pressed', String(s.click));
      click.title = 'Keep time with the metronome, even when the song is stopped.';
      const bpm = el('input', {type:'number', id:'studio-tempo-' + s.slot, value:s.bpm, min:45, max:200, required:true, disabled:!canSetTempo || (picker && picker !== 'banks'),
        onchange: event => { if (event.target.reportValidity()) send('tempo', {value:event.target.valueAsNumber, slot:s.slot}); }});
      bpm.setAttribute('aria-label', 'Tempo (BPM)');
      studio.append(el('div', {className:'sb-transport'}, play, click, el('label', {className:'sb-tempo'}, 'Tempo', bpm, 'BPM')), beatStrip(s));
      if (s.performance && (s.performance.recording || tracksOpen)) {
        const recording = s.performance.recording;
        const capture = worded(recording ? 'stop' : 'record', recording ? 'Save take' : 'Record performance',
          () => send('performance', {...guard, wasRecording:recording}), !available.has('performance'), 'quiet sb-record');
        capture.id = 'studio-action-performance';
        capture.setAttribute('aria-pressed', String(recording));
        const takes = command('takes', 'Takes (' + s.performance.items.length + ')');
        takes.className = 'sb-link';
        studio.append(el('div', {className:'sb-performance'}, capture, recording ? recordingClock(s.performance.elapsed) : takes));
      }
    }
    // (said only when the board waits for the loop to end before the next song)
    if (s.queued !== null) studio.append(el('p', {className:'sb-notice'}, 'Song ' + (s.queued + 1) + ' starts when this loop ends.', command('undo', 'Cancel')));

    const previousFocus = document.activeElement;
    if (previousFocus?.id && previousFocus.closest('.sb-studio') && previousFocus.matches('button, summary')) {
      queueMicrotask(() => {
        if (document.activeElement === document.body) document.getElementById(previousFocus.id)?.focus({preventScroll:true});
      });
    }
    const out = [];
    if (takesOpen) {
      out.push(...takeLibrary(s, command, available));
    } else if (picker === 'volume') {
      const current = kit.state().volume;
      const steps = ['Quietest', 'Quiet', 'Medium', 'Loud', 'Loudest'].map((name, i) => {
        const level = i + 1;
        const b = button(level + ' · ' + name, () => send('level:' + level), false, level === current ? 'act' : 'quiet');
        b.setAttribute('aria-pressed', String(level === current));
        return b;
      });
      const levels = group('Master volume', note('Current level ' + current + ' of 5. Higher rows are louder. Tap a level to apply it, or press Volume again to go back.'),
        ...steps.reverse());
      levels.setAttribute('role', 'group');
      levels.setAttribute('aria-label', 'Master volume');
      out.push(levels);
    } else if (picker === 'banks') {
      out.push(note('Choose either pad in a bank’s pair. ' + s.bpm + ' BPM. ' + (busy
        ? 'Finish recording to change tempo.' : 'Tap ▲ or ▼ for 1 BPM. Hold to keep changing tempo, then release.')));
    } else if (library) {
      // (Duplicate and Delete pressed on the pads choose their song here too; the page's own are in the song's menu.)
      const copying = s.mode === 'duplicate', deleting = s.mode === 'delete-song';
      const hint = copying ? (s.target === null ? 'Choose the song to copy.' : 'Choose an empty tile for the copy.')
        : deleting ? (s.target === null ? 'Choose a song to hear it.' : 'Press Delete again to clear the song you heard.')
        : s.playing ? 'Choose a saved song for the next loop, or an empty tile to start a song now.' : 'Choose a song to open, or an empty tile for a new song.';
      const tiles = s.slots.map((name, i) => {
        const label = [i + 1, name || 'Empty', s.queued === i ? 'Queued' : s.target === i ? (copying ? 'Source' : 'Preview') : s.slot === i ? 'Current' : null].filter(Boolean).join(' · ');
        const tile = command('slot:' + i, label, s.slot === i || s.target === i);
        if (name) tile.classList.add('filled');
        return tile;
      });
      out.push(el('div', {className:'sb-sectionhead'}, s.slot !== null && back('songs', available, guard), el('h3', {textContent:'Songs'})));
      if (s.restorable?.kind === 'song') out.push(notice(s.restorable.id, s.restorable.name + ' deleted.', () => send('restore', guard)));
      out.push(note(hint), el('div', {className:'sb-songs'}, ...tiles));
      if (copying || deleting) out.push(row(command('undo', 'Cancel'), deleting && command('delete', 'Confirm song deletion')));
    } else {
      const clearing = s.mode === 'delete-track';   // (Delete was pressed on the pads: the track is chosen here or there)
      out.push(el('div', {className:'sb-sectionhead'}, el('h3', {textContent:clearing ? 'Choose a track to clear' : 'Tracks'})));
      if (s.restorable?.kind === 'track' && !clearing) out.push(notice(s.restorable.id, s.restorable.name + ' cleared.', () => send('restore', guard)));
      out.push(note(clearing ? (s.target === null ? 'Choose a recorded track to hear it alone.' : 'Press Delete again to clear the track you heard.')
          : 'Tap a track to mute it. The red dot records on it after four ticks. Drag the grip to move it.'),
        el('div', {className:'sb-tracks'}, ...s.tracks.map((track, i) => clearing
          ? el('div', {className:'sb-track-row'}, trackButton(s, track, i, available, true)) : trackRow(s, track, i, available, busy))));
      if (clearing) out.push(row(command('undo', 'Cancel'), command('delete', s.target !== null ? 'Confirm track deletion' : 'Delete track')));
      if (drag) queueMicrotask(showDrag);
    }
    studio.append(...out.filter(Boolean), intro());
    return [studio];
  }
  const play = id => kit.play(kit.url('clip?id=' + encodeURIComponent(id)), null).catch(() => toast('This browser cannot play that clip.'));
  let uploadLabel = '';
  async function add(files, to) {
    if (!files.length) return;
    if (uploadLabel) return toast('Another upload is in progress. Try again when it finishes.');
    const at = kit.bank(), added = [], failed = [];
    try {
      for (const [i, file] of files.entries()) {
        uploadLabel = files.length === 1 ? 'Uploading…' : 'Uploading ' + (i + 1) + ' of ' + files.length;
        kit.redraw();
        const answer = await kit.ask('upload?name=' + encodeURIComponent(file.name), file);
        if (answer.id) added.push(answer.id);
        else failed.push(file.name + (typeof answer.error === 'string' ? ' — ' + answer.error : ''));
      }
      await kit.refresh();
      if (to && files.length === 1 && added.length) {
        await kit.change(config => {
          const b = kit.table(config).banks[at];
          if (b) (b.pads = b.pads || {})[to] = {thing: added[0]};
        });
      }
    } finally {
      uploadLabel = '';
      kit.redraw();
    }
    const done = added.length === 1 ? 'Added ' + added[0] + '.' : added.length ? 'Added ' + added.length + ' clips.' : '';
    const hint = added.length && !to && !failed.length ? ' Choose a clip in Edit layout to place it on a pad.' : '';
    toast([done + hint, ...failed].filter(Boolean).join(' '));
  }
  async function remove(id) {
    const answer = await kit.ask('delete?id=' + encodeURIComponent(id), {});
    if (answer.error) return;
    kit.placing(null); await kit.refresh();
    kit.forget?.();   // (the sound is gone for good: Undo must not put a pad back that would be silent)
  }
  let importing = false, bankSettingsOpen = false;
  async function importBank() {
    const [file] = await kit.files({accept:'.zip'});
    if (!file || importing) return;
    importing = true;
    kit.redraw();
    try {
      const result = await kit.ask('import-bank', file);
      if (result.error) return;
      await kit.refresh();
      if (Number.isInteger(result.bank) && kit.plugin().state.studio.available.includes('bank:' + result.bank)) {
        await send('bank:' + result.bank);
      }
      toast('Imported ' + result.name + '.');
    } finally {
      importing = false;
      kit.redraw();
    }
  }
  const choose = to => kit.files({accept: 'audio/*,.wav,.mp3,.ogg', multiple: !to}).then(files => add(files, to));
  // Recording a clip with this device's microphone: a dialog of this page. A browser lends its microphone only to a
  // page opened securely, so from the board's plain address the way is explained first, and the same page then opens
  // again on the secure address with the recorder in it.
  // With a pad ({bank, key}), what is recorded goes straight onto it.
  async function recordClip(pad) {
    if (window.isSecureContext) {
      const onto = pad && (async id => {
        await kit.change(config => {
          const b = kit.table(config).banks[pad.bank];
          if (b) (b.pads = b.pads || {})[pad.key] = {thing: id};
        });
        toast('Recorded ' + id + ' onto the pad.');
      });
      return (await import('./recorder.js' + stamp)).openRecorder(kit, onto);
    }
    const port = kit.state().secure_port;
    const words = port ? [
      'Your browser only lends its microphone to a page opened securely. The board has a secure address for that, with a certificate it made itself.',
      'Because nobody vouches for a device in your home, the browser will first say that the connection is not private. Here that is expected: choose Advanced, then continue to ' + location.hostname + '. This page opens again there, with the recorder. What you record goes to the board and nowhere else.',
    ] : ['Your browser only lends its microphone to a page opened securely, and this board has no secure address: it could not make its certificate.',
      'Record on this device with its own recorder instead, then add the file with Upload clips.'];
    const dialog = el('dialog', {className:'sb-dialog'}, el('h2', {textContent:'Recording needs the board’s secure address'}),
      ...words.map(text => el('p', {textContent:text})));
    const close = () => dialog.close();
    const go = port && el('a', {className:'act', textContent:'Continue on the secure address',
      href:'https://' + location.hostname + (port === 443 ? '' : ':' + port) + '/#record-clip' + (pad ? '/' + pad.bank + '/' + pad.key : '')});
    dialog.append(row(go, button(port ? 'Not now' : 'Close', close)));
    dialog.onclose = () => dialog.remove();
    document.body.append(dialog);
    dialog.showModal();
  }
  const arrived = location.hash.match(/^#record-clip(?:\/(\d+)\/([1-8],[1-8]))?$/);
  if (arrived) {   // (arrived on the secure address for exactly this: a clip for the library, or for one pad)
    window.history.replaceState(null, '', location.pathname);   // (the page keeps its own list under the bare name)
    setTimeout(() => recordClip(arrived[2] && {bank:Number(arrived[1]), key:arrived[2]}));
  }
  const recordControl = () => button('Record a clip', () => recordClip());
  // A pad's own buttons, short: what each does in full is its name for a screen reader and its tooltip.
  function padButton(text, full, onclick, className = 'quiet', disabled = false) {
    const b = el('button', {type:'button', className, textContent:text, title:full, onclick, disabled});
    b.setAttribute('aria-label', full);
    return b;
  }
  function uploadControl() {
    const upload = button(uploadLabel || 'Upload clips', () => choose(null), !!uploadLabel, 'act');
    upload.addEventListener('dragover', event => { event.preventDefault(); upload.classList.add('over'); });
    upload.addEventListener('dragleave', () => upload.classList.remove('over'));
    upload.addEventListener('drop', event => { event.preventDefault(); upload.classList.remove('over'); add([...event.dataTransfer.files], null); });
    return upload;
  }
  return {
    view: studioView,
    main() {
      const settings = el('details', {className:'sb-bank-settings', open:bankSettingsOpen}, el('summary', {textContent:'Bank settings'}), kit.bankGroup());
      settings.ontoggle = () => { if (settings.isConnected) bankSettingsOpen = settings.open; };
      return [el('section', {className:'group sb-library'},
        el('header', {}, el('h2', {textContent:'Clip library'}),
          row(button(importing ? 'Importing…' : 'Import bank', importBank, importing), recordControl(), uploadControl())),
        note('Choose sound files (WAV, MP3, M4A, OGG, FLAC and others), drop a file on a pad, or record a clip with this device’s microphone.'),
        kit.list(kit.plugin().items, clip => kit.tile(clip))), settings];
    },
    tile: (tile, clip) => {   // (each clip in the library: listen to it; one of the owner's own can be deleted there)
      if (clip.kind === 'My clips') tile.append(el('button', {className: 'go sb-bin', title: 'Delete the clip ' + clip.name, ariaLabel: 'Delete the clip ' + clip.name,
        onclick: event => { event.stopPropagation(); if (confirm(`Delete ${clip.name}? It is taken off every pad and cannot be brought back.`)) remove(clip.id); }}, icon('bin')));
      tile.append(el('button', {className: 'go', textContent: '▶', title: 'Preview ' + clip.name, ariaLabel:'Preview ' + clip.name, onclick: event => { event.stopPropagation(); play(clip.id); }}));
    },
    padButtons: (key, to) => [
      padButton(uploadLabel || 'Upload', 'Upload a clip to this pad', () => choose(key), 'act', !!uploadLabel),
      padButton('Record', 'Record a clip to this pad', () => recordClip({bank:kit.bank(), key})),
      to && padButton('Play', 'Play this pad’s clip', () => play(to.thing)),
      ],
    dropped: (key, files) => add(files, key),
  };
};
