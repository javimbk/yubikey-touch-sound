# yubikey-touch-sound

Make your Mac **play a sound whenever your YubiKey is blinking and waiting for a touch**.

If you've ever stared at a hanging `git push` for thirty seconds before noticing the key was blinking under a notebook — this is for you. While the key waits for your touch, your Mac pings every 2 seconds. Touch it — or cancel the operation — and the pinging stops.

No dependencies: it uses only tools that ship with macOS (`log`, `afplay`, `python3`).

## How it works

macOS writes a line to its unified log at the exact moment a YubiKey starts waiting for a touch, and another when it stops. A small Python script tails `log stream` for two signatures:

- **FIDO2 / WebAuthn touches** (SSH `sk-` keys, passkeys, 2FA prompts): the kernel's IOHIDFamily driver opens a HID client for the YubiKey and logs `startQueue` while waiting for user presence, then `stopQueue` when you touch it. If you cancel instead (Ctrl+C, timeout), there is no `stopQueue` — the client is torn down with `stop`/`close by` messages, which the script also treats as "stop pinging". The script only reacts to HID clients it saw being opened for a `AppleUserUSBHostHIDDevice`, so your keyboard and trackpad don't trigger false pings.
- **OpenPGP / smart-card touches** (gpg signing, PIV): while the card waits, it sends "time extension" packets every ~2 seconds that `usbsmartcardreaderd` logs under CryptoTokenKit. The waiting state expires unless these keep arriving, so a cancelled operation goes quiet instead of pinging forever.

While either condition is active, the script plays `/System/Library/Sounds/Ping.aiff` every 2 seconds. A LaunchAgent keeps the script running and starts it at login.

### Footprint

Designed to idle at effectively zero cost: the log filtering happens natively inside the `log` client (the predicate is evaluated by the logging subsystem, not in Python), so the script only ever sees the handful of lines that match — one small JSON parse each. Memory is bounded (waiting state self-expires; the table of seen HID clients is pruned), sounds are throttled, and there's a 60-second safety timeout in case macOS ever drops a teardown log line. If `log stream` dies (e.g. on an account that isn't allowed to read it), the script writes a diagnosis to its log and self-throttles instead of crash-looping.

The detection approach comes from [noperator/yknotify](https://github.com/noperator/yknotify) (a Go tool that emits JSON events); this is an independent Python re-implementation that plays sounds directly, so nothing needs to be compiled or installed via brew.

## Install

```sh
git clone https://github.com/javimbk/yubikey-touch-sound
cd yubikey-touch-sound
./install.sh
```

That's it. The installer:

1. Checks that `/usr/bin/python3` actually works (and tells you to install the Command Line Tools if not)
2. Copies `yubikey-touch-sound.py` to `~/.local/bin/`
3. Generates a LaunchAgent at `~/Library/LaunchAgents/com.<you>.yubikey-touch-sound.plist`
4. Loads it, waits 5 seconds, and verifies it's still healthy — a `✓` means it really is running, not just that it started

Re-running `install.sh` is safe — it reloads the agent (and rotates the log), so it's also how you apply changes after editing the script.

### Requirements

- macOS (tested on macOS 15/Darwin 25)
- An admin account (regular accounts may not be allowed to read the system log stream — see Troubleshooting)
- `python3` — ships with the Xcode Command Line Tools. If `python3 --version` pops up an install dialog, accept it (or run `xcode-select --install`)

## Test it

Trigger anything that makes the key blink, e.g. an SSH operation with your YubiKey-backed key:

```sh
ssh -T git@github.com
```

You should hear pings until you touch the key.

## Customize

Edit the two constants at the top of `~/.local/bin/yubikey-touch-sound.py` (or edit the repo copy and re-run `./install.sh`):

```python
SOUND = "/System/Library/Sounds/Ping.aiff"
REPEAT_SECONDS = 2.0
```

Preview the built-in sounds:

```sh
for s in /System/Library/Sounds/*.aiff; do echo "$s"; afplay "$s"; done
```

Then restart the agent:

```sh
launchctl kickstart -k "gui/$(id -u)/com.$(whoami).yubikey-touch-sound"
```

## Pause / uninstall

Stop until next login (or until you bootstrap it again):

```sh
launchctl bootout "gui/$(id -u)/com.$(whoami).yubikey-touch-sound"
```

Remove everything:

```sh
./uninstall.sh
```

## Troubleshooting

**Is it running?**

```sh
launchctl print "gui/$(id -u)/com.$(whoami).yubikey-touch-sound" | grep -m1 "state ="
```

**Errors?** Check `~/Library/Logs/yubikey-touch-sound.log` (the previous session's log is kept at `.log.old`). It records every state transition (`FIDO2 touch wait started/ended`, `OpenPGP touch wait started`), so you can see exactly what the script detected and when.

**"log stream died immediately" in the log?** Your account can't read the system log stream — you need to be an admin or in the `_developer` group (check with `groups`). The agent retries at most once a minute, so it won't burn CPU or disk while broken.

**No sound on touch?** The FIDO2 path is only detected if the script was running when the request started — give it a second after installing. If gpg/PIV touches specifically don't ping, confirm your touch policy actually requires touch (`ykman openpgp info` / `ykman piv info`).

**Testing in a shell:** note that in zsh, `log` is shadowed by a builtin — use `/usr/bin/log` explicitly.

**False pings?** Some non-YubiKey HID devices could in theory match the heuristic. If a specific device causes phantom pings, unplug/replug it and check `~/Library/Logs/yubikey-touch-sound.log` — then open an issue/MR with the offending `log stream` lines.

## Credits

Detection technique reverse-engineered by [noperator](https://github.com/noperator/yknotify) — read their README for the story of finding these log signatures.
