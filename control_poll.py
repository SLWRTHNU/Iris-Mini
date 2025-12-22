# control_poll.py
import utime as time
import network
import machine

import bootloader

# How often to check control.json while the app is running
CONTROL_POLL_MS = 30_000  # 60 seconds (adjust)

# If True, allows remote "force_update_ids" to trigger an update immediately from the running app
# If False, it will just reboot, and your bootloader can do updates on boot.
ENABLE_FORCE_UPDATE_FROM_APP = True

_last_poll_ms = 0


def _wifi_connected():
    try:
        sta = network.WLAN(network.STA_IF)
        return sta.active() and sta.isconnected()
    except:
        return False


def tick(lcd=None):
    """
    Call this frequently (every loop / every refresh).
    It will only do network work once per CONTROL_POLL_MS.

    Behavior:
    - If control.json says reboot for this device -> bootloader.apply_control_if_needed() will reset.
    - If control.json says force update for this device:
        - If ENABLE_FORCE_UPDATE_FROM_APP is True: fetch versions + run perform_update(force=True).
        - Else: just reset to let the bootloader handle it on boot (if your bootloader is set up for that).
    """
    global _last_poll_ms

    now = time.ticks_ms()
    if _last_poll_ms and time.ticks_diff(now, _last_poll_ms) < CONTROL_POLL_MS:
        return

    _last_poll_ms = now

    if not _wifi_connected():
        # Do not spam or crash if Wi-Fi drops
        return

    try:
        # This will reboot immediately if this device ID is in reboot_ids.
        # It returns True if this device ID is in force_update_ids.
        force_update = bootloader.apply_control_if_needed(lcd)

        if force_update:
            if ENABLE_FORCE_UPDATE_FROM_APP:
                vers_data = bootloader.fetch_versions_json(lcd)
                if vers_data:
                    # perform_update() already resets at the end
                    bootloader.perform_update(vers_data, lcd, force=True)
                else:
                    # If we can't fetch versions, reboot and try again later
                    machine.reset()
            else:
                machine.reset()

    except Exception as e:
        # Never let control polling break the app
        print("APP: control poll failed:", repr(e))

