#!/usr/bin/env python3
"""Play a sound while a YubiKey is waiting for touch.

Detection approach based on https://github.com/noperator/yknotify,
re-implemented in Python with sound output instead of JSON events.
Watches the macOS unified log:

- FIDO2 touch: the kernel IOHIDFamily driver opens a HID user client for the
  YubiKey and logs `startQueue` while waiting for user presence. On touch it
  logs `stopQueue`; on cancel (Ctrl+C, timeout) there is no `stopQueue` — the
  client is torn down instead (`stop` / `close by`), so both are handled.
- OpenPGP/smart-card touch: the card sends "time extension" packets every few
  seconds while it waits, logged by usbsmartcardreaderd under CryptoTokenKit.
  The waiting state expires unless extensions keep arriving, so a cancelled
  operation goes quiet instead of pinging forever.

Runs as a LaunchAgent; see install.sh in this repo.
"""

import json
import subprocess
import threading
import time

SOUND = "/System/Library/Sounds/Ping.aiff"
REPEAT_SECONDS = 2.0  # keep pinging at this interval while touch is needed
OPENPGP_STALE_SECONDS = 6.0  # extensions arrive every ~2s while waiting
FIDO2_MAX_WAIT_SECONDS = 60.0  # safety net if teardown log lines are missed
CLIENT_MAX_AGE_SECONDS = 3600.0  # prune HID clients whose close we never saw

# `log stream` filters natively before anything crosses the pipe, so keep the
# predicate as narrow as possible.
PREDICATE = (
    '(processImagePath == "/kernel" AND senderImagePath ENDSWITH "IOHIDFamily") '
    'OR (processImagePath ENDSWITH "/usbsmartcardreaderd" '
    'AND subsystem CONTAINS "CryptoTokenKit")'
)


def log_event(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


class TouchState:
    def __init__(self):
        self.waiting_clients = set()
        self.fido2_since = 0.0
        self.openpgp_last_extension = 0.0
        self.last_sound = 0.0
        self.last_player = None
        self.lock = threading.Lock()

    def fido2_wait_started(self, client_id):
        with self.lock:
            if client_id in self.waiting_clients:
                return
            if not self.waiting_clients:
                self.fido2_since = time.monotonic()
            self.waiting_clients.add(client_id)
        log_event(f"FIDO2 touch wait started ({client_id})")

    def fido2_wait_ended(self, client_id, reason):
        with self.lock:
            if client_id not in self.waiting_clients:
                return
            self.waiting_clients.discard(client_id)
        log_event(f"FIDO2 touch wait ended: {reason} ({client_id})")

    def openpgp_extension_received(self):
        with self.lock:
            now = time.monotonic()
            is_new_episode = (
                now - self.openpgp_last_extension > OPENPGP_STALE_SECONDS
            )
            self.openpgp_last_extension = now
        if is_new_episode:
            log_event("OpenPGP touch wait started")

    def openpgp_wait_ended(self):
        with self.lock:
            self.openpgp_last_extension = 0.0

    def reap_player(self):
        player = self.last_player
        if player is not None and player.poll() is not None:
            self.last_player = None

    def check_and_notify(self):
        with self.lock:
            now = time.monotonic()
            if (
                self.waiting_clients
                and now - self.fido2_since > FIDO2_MAX_WAIT_SECONDS
            ):
                self.waiting_clients.clear()
                log_event("FIDO2 touch wait ended: safety timeout")
            needed = bool(self.waiting_clients) or (
                now - self.openpgp_last_extension < OPENPGP_STALE_SECONDS
            )
            if not needed or now - self.last_sound < REPEAT_SECONDS:
                return
            self.last_sound = now
        self.last_player = subprocess.Popen(
            ["/usr/bin/afplay", SOUND],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def ticker(state):
    while True:
        time.sleep(1)
        state.check_and_notify()
        state.reap_player()


def main():
    log_event("yubikey-touch-sound started")
    state = TouchState()
    threading.Thread(target=ticker, args=(state,), daemon=True).start()

    started_at = time.monotonic()
    process = subprocess.Popen(
        [
            "/usr/bin/log",
            "stream",
            "--level",
            "debug",
            "--style",
            "ndjson",
            "--predicate",
            PREDICATE,
        ],
        stdout=subprocess.PIPE,
        text=True,
        errors="replace",
    )

    # HID user clients opened for the YubiKey (id -> when we saw the open);
    # other HID devices (keyboards, trackpads) also log startQueue/stopQueue
    # and must not trigger sounds.
    yubikey_clients = {}

    assert process.stdout is not None
    for line in process.stdout:
        try:
            entry = json.loads(line)
        except ValueError:
            continue

        process_image = entry.get("processImagePath", "") or ""
        sender_image = entry.get("senderImagePath", "") or ""
        subsystem = entry.get("subsystem", "") or ""
        message = entry.get("eventMessage", "") or ""

        if process_image == "/kernel" and sender_image.endswith("IOHIDFamily"):
            if "AppleUserUSBHostHIDDevice:" in message:
                # e.g. "AppleUserUSBHostHIDDevice:0x... open by IOHIDLibUserClient:0x... (0x1)"
                if " open by IOHIDLibUserClient:" in message:
                    now = time.monotonic()
                    client_id = message.split(" open by ")[1].split(" ")[0]
                    yubikey_clients[client_id] = now
                    if len(yubikey_clients) > 32:
                        cutoff = now - CLIENT_MAX_AGE_SECONDS
                        stale = [
                            cid
                            for cid, seen in yubikey_clients.items()
                            if seen < cutoff
                        ]
                        for cid in stale:
                            del yubikey_clients[cid]
                elif " close by IOHIDLibUserClient:" in message:
                    client_id = message.split(" close by ")[1].split(" ")[0]
                    yubikey_clients.pop(client_id, None)
                    state.fido2_wait_ended(client_id, "client closed/cancelled")
            elif message.endswith("startQueue"):
                # e.g. "IOHIDLibUserClient:0x... startQueue"
                client_id = message.split(" ")[0]
                if client_id in yubikey_clients:
                    state.fido2_wait_started(client_id)
            elif message.endswith("stopQueue"):
                state.fido2_wait_ended(message.split(" ")[0], "touched")
            elif message.endswith(" stop"):
                state.fido2_wait_ended(message.split(" ")[0], "client stopped")
        elif process_image.endswith("/usbsmartcardreaderd") and subsystem.endswith(
            "CryptoTokenKit"
        ):
            if message == "Time extension received":
                state.openpgp_extension_received()
            else:
                state.openpgp_wait_ended()

        state.check_and_notify()

    # `log stream` exiting is abnormal. Dying instantly here would make the
    # KeepAlive LaunchAgent restart us in a tight loop (and grow the log file
    # forever), so diagnose and self-throttle instead.
    returncode = process.wait()
    log_event(f"log stream exited with code {returncode}")
    if time.monotonic() - started_at < 10:
        log_event(
            "log stream died immediately — your account may not be allowed "
            "to read the log stream (admin or _developer group required); "
            "retrying in 60s"
        )
        time.sleep(60)
    raise SystemExit(returncode)


if __name__ == "__main__":
    main()
