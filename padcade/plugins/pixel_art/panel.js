// Pixel Art's own part of the management page: the scenes you made, the installed ones, and drawing a scene on the
// pads themselves. A plugin's panel.js gives the page a function that takes the page's toolkit (docs/plugins.md).
export default kit => {
  const {el, head, field, chooser, switchButton, thumb, toast, clone, PALETTE} = kit;
  const BLANK = () => Array(64).fill('#000000');
  let paint = null, playing = null;   // (the scene being drawn; the timer that plays its frames)
  const stop = () => { clearInterval(playing); playing = null; };
  const remember = () => { stop(); paint.history.push(JSON.stringify([paint.frames, paint.at])); if (paint.history.length > 60) paint.history.shift(); };
  const thumbs = () => document.querySelectorAll('#frames button').forEach((b, i) => b.replaceChildren(thumb(paint.frames[i])));
  const content = d => ({name: d.name, frames: d.frames, ms: d.ms, plays: d.plays});
  async function save(what, copy = false) {
    const draft = paint;
    if (draft.saving) return;
    draft.saving = true;
    const fields = content(draft), sent = JSON.stringify(fields);
    const target = {id: copy ? null : draft.id, version: draft.version};
    const answer = await kit.ask(what, what === 'save' ? {...fields, ...target} : target);
    draft.saving = false;
    if (paint !== draft) return;
    if (answer.error) {
      if (answer.conflict) { draft.conflict = true; kit.redraw(); }
      return;
    }
    if (what === 'save') {
      draft.id = answer.id; draft.version = answer.version; draft.before = sent; draft.conflict = false;
      toast('Saved ' + answer.name);
    }
    if (what === 'delete' || JSON.stringify(content(draft)) === sent) kit.canvas(null);
    await kit.refresh();
  }

  function start(drawing) {   // (the board becomes the canvas until the scene is saved or given up)
    paint = drawing ? {id: drawing.id, version: drawing.version, name: drawing.name, frames: clone(drawing.frames), ms: drawing.ms, plays: drawing.plays !== false, at: 0, color: PALETTE[0], history: []}
                    : {id: null, name: 'My scene', frames: [BLANK()], ms: 500, plays: true, at: 0, color: PALETTE[0], history: []};
    paint.before = JSON.stringify(content(paint));
    kit.canvas({dirty: () => JSON.stringify(content(paint)) !== paint.before, title: 'Drawing', hint: 'Click, drag or press Enter on a pad to paint.', column,
      frame: () => paint.frames[paint.at],
      stroke: (pad, first) => { if (first) remember(); paint.frames[paint.at][pad] = paint.color; kit.repaint(); thumbs(); },
      undoable: () => paint.history.length > 0,
      undo: () => { if (paint.history.length) { stop(); [paint.frames, paint.at] = JSON.parse(paint.history.pop()); } },
      close: () => { stop(); paint = null; }});
  }

  async function picture_file() {   // (a picture file, shrunk to the 64 pads, into the frame that is open)
    const [file] = await kit.files({accept: 'image/*'});
    if (!file) return;
    try {
      const bitmap = await createImageBitmap(file), small = el('canvas', {width: 8, height: 8}), c = small.getContext('2d');
      c.drawImage(bitmap, 0, 0, 8, 8);
      const data = c.getImageData(0, 0, 8, 8).data, pixels = BLANK();
      for (let i = 0; i < 64; i++) { const x = i % 8, y = 7 - Math.floor(i / 8), at = (y * 8 + x) * 4; pixels[i] = data[at + 3] < 40 ? '#000000' : '#' + [0, 1, 2].map(k => data[at + k].toString(16).padStart(2, '0')).join(''); }
      if (!paint) start(null);
      remember(); paint.frames[paint.at] = pixels; if (paint.id === null) paint.name = file.name.replace(/\.[^.]+$/, '').slice(0, 18); kit.redraw();
    } catch { toast('That file is not a picture this browser can read.'); }
  }

  function column() {   // (what stands beside the board while a scene is drawn)
    const name = el('input', {type: 'text', id: 'scene-name', value: paint.name}); name.addEventListener('input', () => { paint.name = name.value; });
    const colours = [...PALETTE, '#000000'].map(c => { const b = el('button', {style: `background:${c}`, title: c === '#000000' ? 'Erase' : c, onclick: () => { paint.color = c; kit.redraw(); }}); b.setAttribute('aria-pressed', c === paint.color); return b; });
    const any = el('input', {type: 'color', id: 'paint-colour', value: paint.color, title: 'Any colour'}); any.addEventListener('input', () => { paint.color = any.value; });
    const frames = el('div', {className: 'frames', id: 'frames'}, ...paint.frames.map((f, i) => { const b = el('button', {title: `Frame ${i + 1}`, onclick: () => { stop(); paint.at = i; kit.redraw(); }}, thumb(f)); b.setAttribute('aria-pressed', i === paint.at); return b; }));
    const insert = frame => { remember(); paint.frames.splice(paint.at + 1, 0, frame); paint.at++; kit.redraw(); };
    const play = () => { if (playing) stop(); else playing = setInterval(() => { paint.at = (paint.at + 1) % paint.frames.length; kit.repaint();
      document.querySelectorAll('#frames button').forEach((b, i) => b.setAttribute('aria-pressed', i === paint.at)); }, paint.ms); kit.redraw(); };
    return [head(paint.id === null ? 'New scene' : 'Change scene'),
      el('p', {className: 'muted', textContent: 'Click or drag across the pads, or focus a pad and press Enter or Space.'}),
      field('Name', name), field('Colour', el('div', {className: 'swatches'}, ...colours, any)),
      el('div', {className: 'group'}, el('h3', {textContent: 'Frames: more than one makes it move'}), frames, el('div', {className: 'pair'},
        el('button', {className: 'quiet', textContent: 'Add a frame', onclick: () => insert(BLANK())}),
        el('button', {className: 'quiet', textContent: 'Copy this frame', onclick: () => insert([...paint.frames[paint.at]])}),
        el('button', {className: 'quiet', textContent: 'Remove this frame', disabled: paint.frames.length < 2, onclick: () => { remember(); paint.frames.splice(paint.at, 1); paint.at = Math.max(0, paint.at - 1); kit.redraw(); }}),
        el('button', {className: 'quiet', textContent: 'Picture into this frame', onclick: picture_file}),
        el('button', {className: 'quiet', textContent: playing ? '■ Stop' : '▶ Play', disabled: paint.frames.length < 2, onclick: play}))),
      field('Each frame shows', chooser([['', [[200, '0.2 seconds'], [500, '0.5 seconds'], [1000, '1 second']]]], paint.ms, v => { paint.ms = +v; if (playing) { stop(); play(); } })),
      field('Plays in Pixel Art', switchButton(paint.plays, 'Plays in Pixel Art', v => { paint.plays = v; kit.redraw(); })),
      paint.plays ? null : el('p', {className: 'muted', textContent: 'Kept in the gallery only: open it here or in Paint on the pads.'}),
      el('div', {className: 'pair'}, el('button', {className: 'act', textContent: paint.conflict ? 'Save as a new scene' : 'Save the scene', onclick: () => save('save', paint.conflict)}),
        el('button', {className: 'quiet', textContent: 'Discard', onclick: () => kit.canvas(null)}),
        paint.id !== null && !paint.conflict && el('button', {className: 'quiet', textContent: 'Delete the scene', onclick: () => { if (confirm('Delete this saved scene?')) save('delete'); }}))];
  }

  // ---- the scenes as tiles, the same for the installed ones and the owner's: a small picture, a name, what can be done
  const stills = new Map();   // an installed scene's picture, asked of the board once (null: it has none)
  let asking = false;
  async function fetchStills(ids) {
    if (asking) return;
    asking = true;
    let got = false;
    for (const id of ids) if (!stills.has(id)) { stills.set(id, (await kit.ask('picture', {key: id})).pixels || null); got = true; }
    asking = false;
    if (got) kit.redraw();
  }
  function all() {   // every scene there is: {key, name, pixels, colour, on, error, drawing}
    const installed = Object.entries(kit.state().plugins).filter(([, s]) => s.kind === 'scene');
    const wanted = installed.filter(([id, s]) => s.enabled && !s.error && !stills.has(id)).map(([id]) => id);   // (one switched off is not run for its picture)
    if (wanted.length) fetchStills(wanted);
    return [...kit.plugin().items.map(d => ({key: 'drawing ' + d.id, name: d.name, pixels: d.frames[0], on: d.plays !== false, drawing: d})),
      ...installed.map(([id, s]) => ({key: id, id, name: s.name, pixels: stills.get(id), colour: s.colour, on: s.enabled && !s.error, error: s.error}))];
  }
  const picture = (scene, dim = false) => { const p = scene.pixels ? thumb(scene.pixels) : el('i', {style: `background:${scene.error ? 'var(--bad)' : scene.colour || 'var(--soft)'}`}); if (dim) p.style.opacity = .4; return p; };
  function tile(scene) {   // (in Edit layout: the tile itself says whether the scene takes turns; ▶ shows it on the pads for a moment)
    const previewing = kit.plugin().state.preview === scene.key, id = 'preview-' + scene.key;
    const label = previewing ? `Stop preview of ${scene.name}` : `Preview ${scene.name} on the pads`;
    const turns = el('button', {type: 'button', className: 'thing', style: 'flex:1;border:0;padding:6px 4px 6px 8px', title: scene.error || (scene.on ? 'Takes turns in Pixel Art. Click to leave it out.' : 'Left out. Click to let it take turns.'),
      ariaLabel: `${scene.name} takes turns`, disabled: !!scene.error,
      onclick: async () => {
        if (!scene.drawing) return kit.enable(scene.id, !scene.on);
        const d = scene.drawing;
        await kit.ask('save', {name: d.name, frames: d.frames, ms: d.ms, plays: !scene.on, id: d.id, version: d.version});
        await kit.refresh();
      }}, picture(scene, !scene.on), el('div', {className: 'label'}, el('div', {textContent: scene.name}), scene.error ? el('small', {textContent: 'Stopped'}) : null));
    turns.setAttribute('aria-pressed', scene.on);
    const look = el('button', {id, type: 'button', className: 'go', textContent: previewing ? '■' : '▶', title: label, ariaLabel: label, disabled: !!scene.error || !kit.state().power,
      onclick: async () => {
        await kit.ask('preview', {key: previewing ? null : scene.key});
        await kit.refresh();
        document.getElementById(id)?.focus();
      }});
    return el('div', {className: 'thing', style: 'gap:6px;padding:2px 8px 2px 2px'}, turns, look,
      scene.drawing && el('button', {type: 'button', className: 'link', style: 'font-size:13px', textContent: 'Edit', ariaLabel: `Edit ${scene.name}`, onclick: () => start(scene.drawing)}));
  }
  const GRID = 'grid-template-columns:repeat(auto-fill,minmax(168px,1fr))';   // (wide enough for a name beside its buttons)

  return {
    view() {   // (beside the live board: the scenes that take turns; a click shows one now)
      const {scenes = [], key, paused} = kit.plugin().state, known = new Map(all().map(scene => [scene.key, scene]));
      const cards = scenes.map(([id, name]) => {
        const b = el('button', {type: 'button', className: 'thing', title: `Show ${name}`, ariaLabel: `Show ${name}`,
          onclick: async () => { await kit.ask('show', {key: id}); await kit.refresh(); }},
          picture(known.get(id) || {}), el('div', {className: 'label'}, el('div', {textContent: name})));
        if (id === key) b.setAttribute('aria-current', 'true');
        return b;
      });
      return [el('div', {className: 'group'}, el('h3', {textContent: 'Scenes'}),
        el('p', {className: 'muted', textContent: scenes.length ? 'These take turns on the pads. Choose one to show it now.' : 'No scene takes turns yet. Switch some on in Edit layout, or draw one there.'}),
        el('div', {className: 'things'}, ...cards),
        el('div', {className: 'pair'}, el('button', {className: 'quiet', textContent: 'Previous', onclick: () => kit.act('previous')}),
          el('button', {className: 'quiet', textContent: paused ? 'Go on' : 'Pause', onclick: () => kit.act('pause')}),
          el('button', {className: 'quiet', textContent: 'Next', onclick: () => kit.act('next')})))];
    },
    main() {   // (with nothing chosen on the board: every scene, to look at, switch on or off, and change)
      return [el('div', {className: 'group'}, el('h3', {textContent: 'Scenes'}),
        el('p', {className: 'muted', textContent: 'Click a scene to let it take turns in Pixel Art or to leave it out. ▶ shows it on the pads for a moment.'}),
        el('div', {className: 'things', style: GRID}, ...all().map(tile)),
        el('div', {className: 'pair'}, el('button', {className: 'act', textContent: 'Draw a scene', onclick: () => start(null)}),
          el('button', {className: 'quiet', textContent: 'Upload a picture', onclick: picture_file}),
          el('button', {className: 'link', textContent: 'Add a scene written as a plugin', onclick: () => kit.plugins('scene')})))];
    },
  };
};
