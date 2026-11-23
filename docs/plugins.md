# Writing a plugin

Everything that draws on the 8×8 pads is a plugin: one folder with two files, and a third if it wants its own part
of the management page. Put yours in `plugins/` inside the
board's home folder (`~/.local/share/padcade/plugins/<name>/` for a new installation), or zip the folder and add it on the Plugins screen (after signing in, if the board has a password). Install plugins only from authors you trust. Their Python runs as the service user and their browser panel has the owner’s page permissions. Updates
never touch that folder. A plugin uses the Python standard library and `padcade.kit` only; it cannot install packages.

To share a set of playable sounds, package the WAVs with their pad layout and import the ZIP from the Soundboard. See [Share a Soundboard bank](sound-banks.md).

## plugin.py

```python
class Plugin:
    def __init__(self, board): ...              # `board` is described below
    def frame(self, now): ...                   # 64 colours (red, green, blue) 0-255, index 8 * y + x, bottom row first
    def press(self, x, y, velocity, now): ...   # a pad pressed: x, y 0-7 from the bottom left, velocity 1-127
    def release(self, x, y, now): ...           # that pad let go
```

`now` is a clock in seconds. The board asks for 50 frames a second, so `frame` must be quick. A plugin that raises,
returns something other than 64 colours, or takes longer than a second is stopped: the Plugins screen shows it as
stopped with the reason and the line, and the rest of the board keeps running. Switch it off and on to try again.

A page may also have any of these:

| Method | When |
|---|---|
| `action(name, now)` | a round button with one of its actions was pressed |
| `released(name, now)` | that round button was let go (at once, when the action was started on the management page) |
| `background(now)`, `power_off(now)` | advance an inactive page while the board is awake; finish a performance when the board goes dark |
| `lights(now)` | the colours of its action buttons: `{action: (r, g, b)}` |
| `stay_lit(now)` | true while its buttons should stay lit instead of fading after a press (always: `buttons_stay_lit = true` in plugin.toml) |
| `actions()` | actions that only exist at run time: `{id: label}` |
| `open()`, `close()` | the page comes up, the page is left or the pads go dark |
| `stop()` | the page is switched off or the plugins are read again: end what it keeps going (a thread, a socket) |
| `things()` | what can sit on a pad, for the Edit screen: `[{id, name, colour}]`; with `kind = "Lights"` the heading it is listed under and the kind the list can be narrowed to, with `place = "Kitchen"` the room it can be narrowed to (a list of more than 16 things, or of things with kinds, also has a box to find one by name). Things with `taps` (`[[tap, words], …]`: what a tap can do with it; a third entry names a pad with that tap; a fourth, `{type = "range", min, max, step, default, unit}` or `{type = "colour", default}`, makes it a tap that sets a value the owner chooses, kept as `tap:value`) give every pad two slots on the Edit screen, kept in the pad's table: `tap` (one of them, `"none"`, or `"page"`: a tap opens the pad's subpage, which stays until the pad is pressed again), `hold` (`"controls"`, `"page"`, or one of the taps: done once when the pad is held; nothing without it), `of` (the thing a hold acts on or whose controls it shows, if not its own; `controls = "words"` says what those are, `None` that it has none) `sub` (the pad's own subpage: a table of pads like a bank's; a pad that only opens it needs no `thing`), `name` (the owner's word for the pad on the management page) and `stays` (a subpage shown on a hold stays when the pad is let go, until it is pressed again) |
| `state()` | anything the management page should know, as a dictionary; a page with banks says which one is up: `{bank: 0}`; with `labels` (`{"column,row": "words"}`) it says what each pad is just now, which the page writes on the pads in View (without it: the names of the things placed); with `setup` (a sentence, or `None`) it says what is missing before it can do anything, and until then the management page shows only that sentence and the page's typed settings |
| `hosted()` | for a page that hosts others: the id of the plugin whose round buttons are in force just now |

A page gets a new `Plugin` once, when the board starts or the page is switched on. A scene gets a new one every time it
comes up in Pixel Art, and a game every time it is started or restarted on the Games page, so neither needs a reset.
Pixel Art also starts a scene that is switched on for a moment, apart from the one showing, to take the small picture
of it for the page: its `frame` is asked a few times and it is put away again.

A game may have `action`, `released`, `lights` and `close` too (`close` when the Games page is left or the pads go
dark): its `[actions]` sit on the round buttons its `[defaults.buttons]` name (until the owner moves them) while it is
being played, lit in the colours `lights` gives.

## plugin.toml

```toml
name = "Metronome"
kind = "page"            # page, scene (plays in Pixel Art) or game (sits on the Games menu)
api = 2                  # the plugin interface it was written for; a newer one than the board's is refused
version = "0.1.0"
colour = "#ffd23f"
about = "One sentence for the Board screen."
needs = ["Speaker"]      # parts it wants: Speaker, Motion sensor, Battery board
button = "top5"          # pages: the round button it gets until the owner moves it
thing = "Sound"          # pages: what can sit on a pad (leave out if the page draws all 64 pads itself)
things = "Sounds"        # the name of that tab on the Edit screen

[actions]                # what a round button can trigger on this page
faster = "Faster"

[settings.tempo]         # a setting: type is range, choice, switch, text or secret
label = "Beats a minute"
type = "range"
min = 40
max = 200
default = 100
# also: note = "a sentence under the field"; link = "{address}/some/page" and link_text = "Open that page" add a
# A secret setting can use bound_to = "address" to clear the old credential when its destination changes.
# link after the note, made from the page's own settings

[defaults.buttons]       # where its actions sit until the owner moves them
bottom1 = "faster"
```

A page whose pads hold things may also have:

```toml
banks = true             # several boards of 64 pads: the Edit screen gets bank tabs, and the page's table in
                         # config.toml has `banks` ([{name, pads}]) in place of `pads`. The page offers the actions
                         # "bank:0", "bank:1", … from actions() and goes there in action().
holds = true             # its pads or buttons are held for more: in the browser a long press with the mouse stays down
                         # until it is clicked again (a mouse has one finger)

[pad.mode]               # an option the owner sets for each pad; kept in that pad's table beside `thing`
label = "When pressed"
type = "choice"          # range, choice, switch or text as for settings
default = "once"
choices = [["once", "Play once"], ["hold", "Play while held"]]
```

## panel.js: its own part of the management page

Without it a plugin has the standard Edit column, built from what `plugin.toml` and `things()` say. A `panel.js` in
the plugin's folder is a JavaScript module the page loads; its default export takes the page's toolkit and gives
back what the plugin adds or puts there instead. It runs inside the page with all its rights, like the plugin's
Python does on the board. Place frontend modules and styles beside `panel.js` as ordinary top-level `.js` and `.css` files. The page loads `panel.js` while the plugin is switched on, and anew when it changes, also into a page left open; ask for its own parts under the same stamp, so that such a page does not go on using the ones it loaded before: `const stamp = new URL(import.meta.url).search`, then `import('./helpers.js' + stamp)` and `new URL('./panel.css' + stamp, import.meta.url)`. The shipped plugins are the examples: `soundboard/panel.js` (uploads, a drop target),
`pixel_art/panel.js` (a painter that takes the board over), `games/panel.js` (one more button).

```js
export default kit => ({
  main: standard => [...standard, kit.el('button', {className: 'quiet', textContent: 'Reload', onclick: () => kit.ask('reload', {})})],
});
```

| It may give back | |
|---|---|
| `deckTools()` | optional buttons beneath the grid in View, beside the bank selector |
| `main(standard)` | the nodes of the column with nothing clicked on the board (given the standard ones) |
| `padButtons(key, pad)` | more buttons for a pad that is clicked (`pad` is its table, or nothing) |
| `tile(tile, thing)` | each tile of a thing to place, to add to |
| `placing(thing)` | more buttons while a thing waits to be put on a pad |
| `view()` | controls shown beside the live board in View mode, returned as DOM nodes |
| `dropped(key, files)` | files dropped on a pad |

| The toolkit | |
|---|---|
| `kit.el(tag, properties, ...children)`, `field`, `chooser`, `switchButton`, `swatches`, `head`, `thumb`, `toast`, `PALETTE` | what the page itself is built from, so a panel looks like the rest |
| `kit.ask(what, data)`, `kit.url(what)` | the plugin's own `request(what, params, body)` on the board, and its address. The toolkit calls `ask` with POST. For a read-only URL used directly with GET, list its operation name in the plugin’s `read_requests` tuple. Requests named in the plugin instance’s `board_requests` run on the board loop; keep them short |
| `kit.act(name)` | one of the open page's own actions (`[actions]` in `plugin.toml`), as its round button does it |
| `kit.id`, `kit.clone(value)` | the plugin ID and a deep copy of a JSON value |
| `kit.plugin()`, `kit.table(config)`, `kit.state()`, `kit.bank()`, `kit.pads()`, `kit.item(id)` | what the board says of the plugin, its table in `config.toml`, the whole state, the bank and pads that are open |
| `kit.change(config => …)`, `kit.refresh()`, `kit.redraw()`, `kit.repaint()` | save a change to `config.toml` (Undo takes it back), fetch the state again, draw the page or only the board again |
| `kit.forget()` | empty Undo, after something that cannot be taken back (a file deleted for good) |
| `kit.tile(thing)`, `kit.list(things, tile)`, `kit.bankGroup()` | the standard pieces |
| `kit.placing({thing: id})` | puts a thing on the next pad clicked; `null` stops |
| `kit.files({accept, multiple})`, `kit.play(url, key)` | the browser's file chooser (a promise of files), a sound in the browser. The optional key identifies the decoded audio in the browser cache and defaults to the URL. Pass `null` to preview a changing file without caching it. Call `kit.wakeAudio()` on a user gesture that starts simulator audio |
| `kit.canvas({frame, stroke, undo, undoable, column, title, hint, dirty, close})` | the board is the panel's to draw on until `kit.canvas(null)`: `frame()` gives the 64 colours shown, `stroke(pad, first)` is called for each pad pressed or dragged over (0-63 from the bottom left), `column()` gives the column's nodes. An optional `dirty()` protects unsaved work when leaving the canvas |
| `kit.enable(id, on)`, `kit.plugins(kind)` | switch another plugin on or off, open the Plugins screen |

## What `board` gives a plugin

| | |
|---|---|
| `board.settings` | its settings: what the owner chose, else the defaults |
| `board.config` | its table in `config.toml`: `pads` (`"column,row"` → `{thing = …, colour = …}`; with `banks = true`, `banks`: `[{name, pads}]`), `buttons`, `settings` |
| `board.data` | a folder of its own for files; survives updates |
| `board.folder` | the plugin's own folder |
| `board.play(wav, gain=1.)` | plays a stereo 44.1 kHz, 16-bit WAV at the given fraction of full volume (0–1). The voice has `poll()` and `terminate()`. A recorded performance keeps the gain. |
| `board.prepare_sounds(paths)` | asks the physical speaker to preload the current bank’s WAV files in the background. Pass an empty iterable when leaving the bank. |
| `board.volume`, `board.set_volume(1-5)` | the speaker's volume |
| `board.gallery` | the owner's drawings (`drawings`, `save(drawing)`, `delete(drawing)`). Pass the saved drawing’s `id` and `version` when editing or deleting it. A stale edit raises `DrawingConflict`. API 1 also supports `put(drawing, index=None)`, which returns the saved index, and `delete(index)` |
| `board.motion(now)` | the motion sensor's latest reading, or `None` |
| `board.tabletop_gravity` | only if `tabletop_gravity = [x, y, z]` is in `config.toml`: what the sensor reads with the board flat on a table |
| `board.plugins(kind, include_disabled=False)` | working scenes or games, each with `name` and `start()`. Pass `include_disabled=True` to preview one outside the rotation. |
| `board.changed()` | tells the management page to fetch its things and state again |

`padcade.kit` has drawing helpers (`mix`, `noise`, `smooth`, `pictures`, `sprite`, `shifted`, `Stepped`). The scenes in
`padcade/plugins/` are short examples: `lava` is a function of time, `snake` a small simulation.

Gallery plugins using API 1 should switch from list positions to the saved drawing’s ID and version, and declare `api = 2`. Existing drawings keep their order and identifiers across restarts. The first gallery edit writes the new format. Other API 1 plugins can keep their declarations.
