# Share a Soundboard bank

A sound bank is a ZIP containing a pad layout and WAV files. The Soundboard plays it directly. The person importing the bank can move its pads, change how they play, and record them into songs.

## Make a bank

Put a file named `bank.toml` and your WAVs together. For example, a bank with a drum hit and a sustained chord could contain `bank.toml`, `Knock.wav` and `Chord.wav`. For this two-pad layout, write the following in the manifest.

```toml
name = "Table kit"

[pads]
"1,1" = { file = "Knock.wav", dynamics = "velocity" }
"2,1" = { file = "Chord.wav", mode = "hold", colour = "#73c8b2" }
```

- Count columns from the left and rows from the bottom. Each coordinate runs from 1 to 8. Several pads can use the same file.
- `once` plays the clip to the end and is the default. `hold` stops on release. `loop` repeats until the next press. A colour is optional.
- Use `dynamics = "velocity"` for notes or drums that should respond to strike strength, or `"fixed"` for a consistent level. The default `"auto"` uses the clip’s normal response. You can change **Strike response** when editing a pad.
- Use mono or stereo PCM WAV files up to 20 seconds each. Stereo, 16-bit, 44.1 kHz files keep their original audio. Other supported PCM formats need `ffmpeg` on the board for conversion.
- Use up to 40 letters, digits, spaces, underscores or hyphens before the `.wav` extension. Start each filename with a letter, digit, underscore or hyphen.
- Select the manifest and WAVs and compress **the files themselves**. They must appear at the root of the ZIP. The manifest can have up to 64 pads. The unpacked files and the converted audio must each fit in 20 MiB.
- Include redistribution terms for audio you share. The ZIP can also contain `LICENSE`, `LICENSE.txt`, `CREDITS.md` and `README.md`. Keep the ZIP with those documents for future reference.

## Install and play

In the Soundboard, choose **Edit layout → Clips → Import bank** and open the ZIP. The bank appears alongside the existing banks in the browser and the Shift menu. Its clips also appear in the clip library.

Importing the same bank again reuses it. If another bank already has that name and a different layout, choose a different name in the manifest or rename the existing bank. When two clips share a filename, identical audio can be reused and a different recording gets a numbered name. Existing recordings continue using their original files.

Custom bank permissions come from the people who supplied its audio. Padcade’s factory-audio dedication covers the shipped sounds.
