#!/bin/sh
# Installs the board on a Raspberry Pi with Raspberry Pi OS Lite, from an unpacked release; run again, it updates.
#   sudo ./install.sh              asks which parts are wired (a terminal), or keeps what an earlier run set up
#   sudo ./install.sh --speaker --motion     says so instead: an I2S amplifier (MAX98357A), a BNO085 on the UART
#   sudo ./install.sh --no-speaker --no-motion
#   sudo ./install.sh --update     fetches the latest release (REPO in padcade/__init__.py) and installs that
#   sudo ./install.sh --user=NAME  the user the board runs as (a reinstall keeps the one it has; a first one takes yours)
#   --battery or --no-battery selects whether the Pi monitors a PiSugar 3; the choice survives updates.
# It installs packages, the program in /opt, a service that starts it, and only the boot options for those parts.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=${INSTALL_ROOT:-}                      # (the tests give a folder that stands in for /)
package=padcade
say() { printf '%s\n' "$*"; }
ask() { printf '%s [y/N] ' "$1"; read -r answer; case $answer in y*|Y*) return 0 ;; esac; return 1; }
# (-B: run as root, it must leave no cache files in the folder the release was unpacked into)
value() { python3 -B -c "import sys; sys.path.insert(0, '$here'); import $package; print($package.$1)"; }

speaker= motion= battery= update= user=
for option in "$@"; do
  case $option in
    --speaker) speaker=yes ;; --no-speaker) speaker=no ;;
    --motion) motion=yes ;; --no-motion) motion=no ;;
    --battery) battery=yes ;; --no-battery) battery=no ;;
    --update) update=yes ;; --download-release) update=download ;;
    --user=*) user=${option#--user=} ;;
    *) sed -n '2,9p' "$0"; exit 2 ;;
  esac
done
[ -n "$root" ] || [ "$(id -u)" = 0 ] || { say 'Run it with sudo: it installs packages and a service.'; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { say 'It needs Python 3.11 or newer (Raspberry Pi OS 12 or newer).'; exit 1; }
slug=$(value SLUG)
# A board that is installed already keeps the user it runs as: that user's folder holds its songs and its password.
[ -n "$user" ] || user=$(sed -n 's/^User=//p' "$root/etc/systemd/system/$slug.service" 2>/dev/null | head -n 1)
user=${user:-${SUDO_USER:-$(id -un)}}
[ "$user" != root ] || { say 'The board should not run as root: run this with sudo as its user, or say --user=NAME.'; exit 1; }

if [ -n "$update" ]; then                   # the latest release, unpacked, and its own install.sh run
  repo=$(value REPO)
  [ -n "$repo" ] || { say 'This copy does not say where its releases are (REPO in padcade/__init__.py).'; exit 1; }
  if [ "$update" = yes ]; then
    exec systemd-run --unit="$slug-update" --collect --wait --pipe --property=PrivateTmp=yes -- \
      "$here/install.sh" --download-release "--user=$user"
  fi
  tag=$(curl -fsSL "https://api.github.com/repos/$repo/releases/latest" | python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])' 2>/dev/null) ||
    { say "No release of $repo could be found: is the Pi online?"; exit 1; }
  installed=$(value VERSION)
  python3 -I "$here/$package/release.py" "$installed" "$tag" || { say "No newer release than $installed (published tag: $tag)."; exit 0; }
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  curl -fsSL "https://github.com/$repo/archive/refs/tags/$tag.tar.gz" -o "$work/release.tar.gz"
  tar -xzf "$work/release.tar.gz" -C "$work"
  "$work"/*/install.sh "--user=$user"
  exit
fi
target=$root/opt/$slug
mkdir -p "$root/run/lock"
exec 9>"$root/run/lock/$slug-install"
flock -n 9 || { say "Another $slug installation is running."; exit 1; }
boot=$root/boot/firmware/config.txt
[ -f "$boot" ] || boot=$root/boot/config.txt
changed=
keep() { [ ! -f "$1" ] || [ -f "$1.before-$slug" ] || cp -p "$1" "$1.before-$slug"; }   # (as it was before the first change)

# ---- 1. packages (a failure here stops everything: the old version keeps running)
wanted='ffmpeg alsa-utils i2c-tools python3-paho-mqtt gcc libc6-dev openssl'
missing=
for name in $wanted; do dpkg -s "$name" >/dev/null 2>&1 || missing="$missing $name"; done
if [ -n "$missing" ]; then
  say "Installing packages:$missing"
  apt-get update -q
  # shellcheck disable=SC2086
  apt-get install -y -q --no-install-recommends $missing
fi

# ---- 2. the program, put in place whole
say "Installing $(value NAME) $(value VERSION) in /opt/$slug"
saved=$target.rollback
managed="etc/systemd/system/$slug.service etc/systemd/system/multi-user.target.wants/$slug.service
etc/sudoers.d/$slug etc/$slug.parts etc/asound.conf
boot/firmware/cmdline.txt.$slug-uart boot/cmdline.txt.$slug-uart
etc/systemd/system/serial-getty@ttyAMA0.service.d/$slug.conf etc/systemd/system/serial-getty@ttyS0.service.d/$slug.conf
etc/modules-load.d/$slug.conf boot/config.txt boot/cmdline.txt boot/firmware/config.txt boot/firmware/cmdline.txt"

restore() {
  say 'Restoring the previous installation.'
  if [ -d "$saved/program" ]; then
    rm -rf "$target"
    mv "$saved/program" "$target"
  elif [ -f "$saved/fresh" ]; then
    rm -rf "$target"
  fi
  for f in $managed; do
    rm -f "$root/$f"
    if [ -e "$saved/files/$f" ] || [ -L "$saved/files/$f" ]; then cp -a "$saved/files/$f" "$root/$f"; fi
  done
  systemctl daemon-reload
  if [ -f "$saved/serial-gettys" ]; then
    while read -r tty; do systemctl start "$tty"; done < "$saved/serial-gettys"
  fi
  if [ -f "$root/etc/systemd/system/$slug.service" ]; then
    systemctl restart "$slug"
  fi
  rm -rf "$saved"
}
[ ! -d "$saved" ] || restore
rm -rf "$target.new" "$saved.new"
mkdir -p "$target.new"
cp -r "$here/$package" "$here/docs" "$here/case" "$here/install.sh" "$here/README.md" "$here/LICENSE" "$here/LICENSE-assets" "$here/LICENSE-sounds" "$target.new/"
find "$target.new" -name __pycache__ -prune -exec rm -rf {} +
gcc -O3 -fPIC -shared "$target.new/$package/drivers/pcm.c" -o "$target.new/$package/drivers/pcm.so" -lm
mkdir -p "$saved.new/files"
(cd "${root:-/}" && for f in $managed; do
  if [ -e "$f" ] || [ -L "$f" ]; then cp -a --parents "$f" "$saved.new/files"; fi
done)
[ -d "$target" ] || touch "$saved.new/fresh"
mv "$saved.new" "$saved"
trap 'restore' EXIT
trap 'exit 1' HUP INT TERM
[ ! -d "$target" ] || mv "$target" "$saved/program"
mv "$target.new" "$target"

# ---- 3. the parts: which are there, and the boot options only they need
parts=$root/etc/$slug.parts
previous_part() {
  if [ -f "$parts" ]; then
    grep -qxF "$1=yes" "$parts" && echo yes || echo no
  elif [ -f "$boot" ] && grep -q "^# >>> $slug" "$boot"; then
    sed -n "/^# >>> $slug/,/^# <<< $slug/p" "$boot" | grep -qxF "$2" && echo yes || echo no
  fi
}
if [ -z "$speaker$motion$battery" ] && [ -f "$parts" ] && grep -qx uart_managed=yes "$parts" && grep -q "^battery=" "$parts"; then
  say 'Keeping the boot options of the earlier installation (say --speaker, --motion or their --no- to change them).'
else
  speaker=${speaker:-$(previous_part speaker dtoverlay=max98357a,no-sdmode)}
  motion=${motion:-$(previous_part motion dtoverlay=miniuart-bt)}
  if [ -z "$speaker" ] && [ -t 0 ]; then ask 'Is an I2S amplifier (MAX98357A) wired to the Pi?' && speaker=yes; fi
  if [ -z "$motion" ] && [ -t 0 ]; then ask 'Is a BNO085 motion sensor wired to the UART pins?' && motion=yes; fi
  if [ -z "$battery" ] && [ -f "$parts" ]; then
    battery=$(sed -n 's/^battery=\(yes\|no\)$/\1/p' "$parts")
  fi
  if [ -z "$battery" ]; then
    if [ ! -e "$root/dev/i2c-1" ]; then dtparam i2c_arm=on 2>/dev/null || true; modprobe i2c-dev 2>/dev/null || true; fi
    version=$(i2cget -y 1 0x57 0x00 2>/dev/null) || version=
    if [ "$version" = 0x03 ] && [ "$(i2cget -y 1 0x57 0x01 2>/dev/null)" = 0x0f ]; then
      battery=yes
      say 'Found the battery board (PiSugar) on I2C.'
    elif [ -z "$version" ] && [ -f "$root/etc/modules-load.d/$slug.conf" ]; then
      battery=yes  # Older installs recorded a fitted battery here. The driver verifies its identity before use.
    elif [ -t 0 ] && ask 'No PiSugar responded. Is a PiSugar 3 or 3 Plus fitted?'; then
      battery=yes
    else
      battery=no
    fi
  fi
  if [ "$battery" = yes ]; then say 'After reboot, check Settings for a battery percentage to verify monitoring.'; fi

  lines=
  [ "$battery" != yes ] || lines="dtparam=i2c_arm=on"
  [ "$speaker" != yes ] || lines="$lines
dtoverlay=max98357a,no-sdmode"
  [ "$motion" != yes ] || lines="$lines
enable_uart=1
core_freq=250
dtoverlay=miniuart-bt"
  if [ -f "$boot" ]; then
    keep "$boot"
    before=$(cat "$boot")
    kept=$(sed "/^# >>> $slug/,/^# <<< $slug/d" "$boot")     # (without the block an earlier run wrote)
    common=$(printf '%s\n' "$kept" | awk '/^[[:space:]]*\[/ {filtered = ($1 != "[all]")} !filtered')
    {
      printf '%s\n' "$kept"
      block=$(printf '%s\n' "$lines" | while read -r line; do      # (only what is not there already)
        [ -z "$line" ] || printf '%s\n' "$common" | grep -qxF "$line" || printf '%s\n' "$line"; done)
      [ -z "$block" ] || printf '# >>> %s (written by install.sh)\n[all]\n%s\n# <<< %s\n' "$slug" "$block" "$slug"
    } > "$boot.new"
    mv "$boot.new" "$boot"
    [ "$before" = "$(cat "$boot")" ] || changed=yes
  fi
  if [ "$battery" = yes ]; then
    mkdir -p "$root/etc/modules-load.d"
    echo i2c-dev > "$root/etc/modules-load.d/$slug.conf"
  else
    rm -f "$root/etc/modules-load.d/$slug.conf"
  fi
  for file in "$root/boot/firmware/cmdline.txt" "$root/boot/cmdline.txt"; do
    [ -f "$file" ] || continue
    before=$(cat "$file")
    saved_cmdline=$file.$slug-uart
    # Old installations kept the original cmdline, but had no per-sensor restoration record.
    if [ -f "$parts" ] && grep -qx 'motion=yes' "$parts" && ! grep -qx 'uart_managed=yes' "$parts" &&
       [ -f "$file.before-$slug" ] && [ ! -f "$saved_cmdline" ]; then
      cp -p "$file.before-$slug" "$saved_cmdline"
    fi
    python3 - "$motion" "$file" "$saved_cmdline" "$file.before-$slug" <<'PYTHON'
from pathlib import Path
import shutil
import sys

motion = sys.argv[1] == 'yes'
cmdline, saved, backup = map(Path, sys.argv[2:])
current = cmdline.read_text().split()

def serial(token):
    return token.split(',', 1)[0] == 'console=serial0'

if motion and any(map(serial, current)):
    if not backup.exists():
        shutil.copyfile(cmdline, backup)
    shutil.copyfile(cmdline, saved)
    cmdline.write_text(' '.join(t for t in current if not serial(t)) + '\n')
elif not motion and saved.exists():
    original = saved.read_text().split()
    if not any(map(serial, current)) and any(map(serial, original)):
        for i, token in enumerate(original):
            if serial(token):
                # Console order determines where the kernel sends messages. Keep unrelated boot edits.
                at = next((current.index(t) for t in original[i + 1:] if t in current), len(current))
                current.insert(at, token)
        cmdline.write_text(' '.join(current) + '\n')
    saved.unlink()
PYTHON
    [ "$before" = "$(cat "$file")" ] || changed=yes
  done
  for tty in serial-getty@ttyAMA0.service serial-getty@ttyS0.service; do
    dropin=$root/etc/systemd/system/$tty.d/$slug.conf
    if [ "$motion" = yes ]; then
      mkdir -p "$(dirname "$dropin")"
      printf '[Unit]\nConditionPathExists=!/etc/%s.parts\n' "$slug" > "$dropin"
      if systemctl is-active --quiet "$tty"; then
        echo "$tty" >> "$saved/serial-gettys"
        systemctl stop "$tty"
      fi
    elif [ -f "$dropin" ]; then
      rm -f "$dropin"
      changed=yes
    fi
  done
  sound=$root/etc/asound.conf
  owned_sound() { [ -f "$sound" ] && head -n 1 "$sound" | grep -qF "# Written by $slug's install.sh."; }
  if [ "$speaker" = yes ]; then
    if [ -f "$sound" ] && ! owned_sound; then
      say '/etc/asound.conf is your own: left as it is.'
    else
      mkdir -p "$root/etc"
      cat > "$sound" <<EOF
# Written by $slug's install.sh. The amplifier (MAX98357A on I2S) is the default output: mixed, so that several
# clips can sound at once, and with a volume control named PCM (the amplifier has none).
pcm.!default { type plug; slave.pcm "amp_vol" }
pcm.amp_vol {
    type softvol
    slave.pcm "amp_mix"
    control { name "PCM"; card MAX98357A }
    min_dB -51.0
    max_dB 0.0
}
pcm.amp_mix {
    type dmix
    ipc_key 1024
    ipc_perm 0666
    hw_ptr_alignment roundup
    slave { pcm "hw:MAX98357A,0"; rate 44100; channels 2; format S16_LE; period_size 256; buffer_size 8192 }
}
ctl.!default { type hw; card MAX98357A }
EOF
    fi
  elif owned_sound; then
    rm -f "$sound"
  fi
  mkdir -p "$root/etc"
  printf '%s\n' "speaker=${speaker:-no}" "motion=${motion:-no}" "battery=$battery" "uart_managed=yes" > "$parts.new"
  chmod 644 "$parts.new"
  mv "$parts.new" "$parts"
fi

# ---- 4. who runs it, and what that user may do as root: switch the Pi off (a flat battery), restart and update it
for group in audio i2c dialout gpio; do
  ! getent group "$group" >/dev/null 2>&1 || usermod -aG "$group" "$user"
done
mkdir -p "$root/etc/sudoers.d" "$root/etc/systemd/system"
rules=$(mktemp)
printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff, /usr/bin/systemctl restart %s, /opt/%s/install.sh --update\n' \
  "$user" "$slug" "$slug" > "$rules"
if command -v visudo >/dev/null 2>&1 && ! visudo -cf "$rules" >/dev/null; then    # (a wrong rule here could lock sudo)
  say 'The rule for sudo did not pass its check: nothing changed there.'; rm -f "$rules"; exit 1
fi
rm -f "$root/etc/sudoers.d/$slug"
install -m 440 "$rules" "$root/etc/sudoers.d/$slug"
rm -f "$rules"

# ---- 5. the service
cat > "$root/etc/systemd/system/$slug.service" <<EOF
[Unit]
Description=$(value NAME) (the Launchpad's pads and its management page)
After=network.target sound.target
StartLimitIntervalSec=0

[Service]
Type=notify
TimeoutStartSec=45
User=$user
WorkingDirectory=/opt/$slug
ExecStart=/usr/bin/python3 -m $package --port 80
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
sync
systemctl daemon-reload
systemctl enable -q "$slug"
systemctl restart "$slug"  # Type=notify waits for a working first frame.
trap - EXIT HUP INT TERM
rm -rf "$saved"

say "Done. The management page: http://$(hostname).local/."
[ -z "$changed" ] || say 'The boot options changed: restart the Pi (sudo reboot) for the parts to work.'
