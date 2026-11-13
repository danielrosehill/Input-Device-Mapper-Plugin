---
name: lookup-device-mapping
description: Look up a connected input device (gamepad, pedal, macro pad, headset buttons) in a device-mappings library and load its existing button map.
---

# Look up a device in a mapping library

Before mapping a device from scratch, check whether a library already has it. A library is a `devices.json` index
(schema `device-mappings-index/1`) listing devices by USB `vendor:product`, each pointing to a repo and a profile file.

Default library: <https://github.com/danielrosehill/Device-Mappings> (`devices.json` on `main`), the plugin author's
own collection. Use another one with `--library <url or local path>` or the `DEVICE_MAPPINGS_LIBRARY` environment
variable; a library can be any repo or folder that follows the same schema.

## Steps

1. Match everything connected:
   ```bash
   python3 -I <plugin>/scripts/idmap.py library
   ```
   `matched` lists connected devices that have entries (repo, profile file, formats, `press_verified`);
   `not_in_library` lists the rest by vendor:product and name.
2. For a match, fetch the profile (`profile_url`, raw file) and read it. Report the device, what it presents
   (driver, nodes) and the button table; say whether the library marks it press-verified.
3. Use it for what the user wants: hand the button table to an app, apply the SDL line or hwdb snippet it carries,
   or set up a remapper. Check one or two buttons live with `idmap.py press <device> --label <button>` before relying
   on an entry that is not press-verified, or when the mode or firmware may differ (product ID matches but layout
   can change with a mode switch).
4. No match: offer the `map-input-device` skill, then add the result to the library: a repo per device with
   `device.json`, a row in the library README and an entry in its `devices.json`:
   ```json
   {"name": "...", "type": "gamepad|foot-pedal|macro-keypad|...", "usb": "vvvv:pppp", "driver": "...",
    "formats": ["device.json", "sdl", "hwdb", "..."], "press_verified": true,
    "repo": "<owner>/<repo>", "profile": "device.json"}
   ```
   Leave per-unit serial numbers out of anything published (`ID_SERIAL`, `iSerial`, evdev `Uniq`, `/dev/input/by-id`
   names): replace them with `<serial>`.

## Notes

- One USB ID can cover several entries (modes, firmware revisions); show all and let the user pick.
- Virtual devices (`0000:0000`, `ydotoold`, `uinput`) never match and can be ignored.
