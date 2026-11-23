# Maintaining Padcade

## Update the Pi

When a newer release is out, **Settings** on the board shows an **Update to …** button. On the Pi itself, run `sudo /opt/padcade/install.sh --update`. Hardware choices persist in `/etc/padcade.parts`, and the board keeps running as the user it was first installed for, whoever runs the installer later; `--user=NAME` changes that. Reinstall with `--battery` to monitor a fitted PiSugar 3 or `--no-battery` to stop monitoring it. A failed service start rolls back the update. Reboot if boot options changed. To return the UART to the serial console, rerun the installer with `--no-motion` and reboot.

## Settings and recovery

Back up the data folder printed at startup. It holds settings, plugins, drawings, songs and samples. The default is `~/.local/share/padcade`. Keep backups private because `config.toml` can contain service credentials and the `https` folder holds the key of the board’s own certificate.

The board also answers on a secure address, `https://` with the same name, so that a browser lends its microphone for **Record a clip**. Its certificate is made on the first start, kept in the `https` folder and renewed by the board itself. `--https-port 0` on the command line switches the secure address off.

A fresh or factory-reset board is open on your local network: anyone there can play it and manage it. Managing includes installing plugins, and a plugin is code that runs on the Pi, so set a password if people you do not trust use your network.

**Settings → Password for this page** sets one. It locks the whole page at first. **Open the page for playing** then keeps the password only for managing the board (plugins, connections, the password itself, restart, update and reset), while the pads, songs, clips and layout stay open to everyone; they find **Sign in to manage the board** in Settings. **Remove the password** opens everything again.

A forgotten password is replaced on the Pi, as the user who installed Padcade. `--set-password` locks the whole page, `--set-admin-password` only the managing:

```sh
cd /opt/padcade
python3 -m padcade --set-password
```

For remote access, use an SSH tunnel to loopback or an HTTPS reverse proxy. For a proxy, bind Padcade to loopback with `--host 127.0.0.1 --require-owner`, have the proxy replace `X-Forwarded-Proto` with `https`, and add the proxy’s hostname to the board’s `names` if needed. A proxied connection always needs the owner and HTTPS. On your own network you can sign in over plain HTTP; the board’s `https://` address keeps the password from others on that network.

## Home Assistant connections

To put lights and other Home Assistant devices on the pads, switch the **Home Assistant** plugin on under **Plugins** and open it on the **Board** screen. Until it is connected it asks for the address and an access token beside the board; later both are under **Edit layout → Settings**, which also shows whether they work. A rejected token stays saved so you can see the error and replace it.

The **MQTT** section is for publishing the board as a device in Home Assistant. Choose **Save MQTT settings**, then **Restart the board** to apply changes. Return to Settings to see whether the broker accepted the connection. Each new board gets its own MQTT device ID, so several boards can share a broker. An upgrade keeps the original ID for a board that already used MQTT. If you clone a configured SD card, give the copy a different top-level `mqtt_id` in `config.toml` before connecting it to the broker. Use lowercase letters, digits or underscores.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Pads are dark or show the stock layout | Read `journalctl -u padcade -b -n 30`. Reconnect the USB cable. |
| Browser cannot reach the board | Try the Pi’s IP address. Add custom hostnames to the top-level `names` list in `config.toml` and restart Padcade. |
| No speaker output | If MAX98357A is missing from `aplay -l`, reinstall with `--speaker` and reboot. For identical channels, check the [amplifier wiring](build.md#wiring). |
| Motion missing | On a 3A+, motion needs UART wiring and `/dev/serial0` pointing to `ttyAMA0` after installing with `--motion` and rebooting. |
| Battery missing | With a PiSugar 3 or 3 Plus fitted, rerun the installer with `--battery` and reboot. Check **Settings** for a percentage. If it is still missing, check the contacts. A PiSugar 3 in application mode reports `0x03` from `i2cget -y 1 0x57 0x00` and `0x0f` from `i2cget -y 1 0x57 0x01`. |
| **Record a clip** says the board has no secure address | The board could not make its certificate or port 443 is taken. Install `openssl`, free the port, and restart Padcade; `journalctl -u padcade -b` says so at the start. |
| Clip upload fails or a plugin stops | Reinstall missing `ffmpeg`: without it the board only takes WAV files at 44.1 kHz, 16 bits, stereo. **Plugins** shows the error and line number for a stopped plugin. |
| Settings reverted or songs disappeared | Recover malformed files from `config.broken` or `data/soundboard/songs.broken*`. Keep song samples with the index. If saving fails, leave the studio open and free disk space for its retry. |

## Hardware and timing

The board still draws power with the pads dark. It checks for pickup at the motion sensor’s 20 Hz rate, and a button press wakes the board immediately. The battery build shuts down at 8% charge. Runtime depends on brightness, volume and use.

The case amplifier can click when it first wakes. Padcade keeps its clock running for 12 hours after a clip to avoid repeated wake clicks. Opening a sound bank also prepares the output for its first note. Song tracks, Takes and the metronome follow their own playback deadlines. A pad assigned **Loop until pressed again** restarts its WAV on the next display frame, which can leave a gap of up to 20 ms.

Home Assistant polls the visible bank every two seconds by default. While the page is closed, it checks the connection once a minute and validates changed credentials on the next poll.
