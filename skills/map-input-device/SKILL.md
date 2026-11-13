---
name: map-input-device
description: Identify a USB/Bluetooth input device on Linux (gamepad, macro pad, foot pedal, remote) and map each physical button to its evdev code via guided presses.
---

# Map an input device

Find out what a newly connected device is (vendor:product, driver, stable path) and what each physical button
really emits, by asking the user to press one button at a time and reading each press as it happens. Output is a device
profile (JSON + a short table) the user can hand to an app, a remapper or a shortcut tool.

Script: `scripts/idmap.py` in this plugin (resolve it relative to the plugin root). Run it as `python3 -I <path>/idmap.py`.
Requires `python3-evdev` and read access to `/dev/input/event*` (user in the `input` group, or a uaccess ACL).

## 1. Find the device

```bash
lsusb                                  # vendor:product and product string, as the USB bus reports it
python3 -I idmap.py list               # every evdev node: vendor:product, bus, driver, class, by-id path
```

Pick the node by name or by the device that appeared since the last list. A composite device (keyboard + consumer
keys + mouse) exposes several nodes with the same vendor:product; `capture`/`info` given `vvvv:pppp` choose the one
with the most buttons, but pass an explicit `/dev/input/by-id/...` path when the buttons live on another node.

Note virtual devices (`ydotoold`, `espanso`, input-remapper outputs, `uinput`) so they are not mistaken for hardware.

## 2. Read what it declares

```bash
python3 -I idmap.py info <vvvv:pppp | /dev/input/...>
```

Gives the udev properties (`ID_SERIAL`, `ID_USB_DRIVER`, `ID_INPUT_JOYSTICK`…), declared keys, axes with ranges and
force feedback. Declared is not the same as present: many devices declare buttons they do not have.

## 3. Guided press session: one button at a time (the ground truth)

Never hand the user a list of buttons to press in sequence: on an unfamiliar device they cannot hold the order in
their head. Go one button per round:

1. Work out the physical buttons from `info`, the device itself or the user (labels, photo, product page), and keep
   the list yourself.
2. Each round, tell the user the **one** button to press (with where it is, e.g. "the blue X, left face button"),
   then immediately run in the foreground:
   ```bash
   python3 -I idmap.py press <device> --label "<physical label>" --seconds 60
   ```
   It waits for one input and prints it (key code, hat direction, or axis past half travel such as `ABS_Z+`).
3. Report the result in one line ("X → BTN_X, code 307, browser index 2") and move to the next button. On a
   timeout, ask whether they pressed it; a button that emits nothing is a finding, not a retry loop.
4. Ask the user for buttons you did not know about at the end ("anything left you haven't pressed?") and run
   `press` once more per extra button.
5. For open exploration ("what does this odd button do"), use `watch <device> --seconds 30`, which prints every
   press. `capture --count N` (N inputs in order) exists for scripted checks, not for people.

## 4. Gamepads used in a browser or Electron app

The `browser_standard_index` field is a **prediction** of the W3C standard-gamepad index (0 bottom face, 1 right,
2 left, 3 top, 4/5 bumpers, 6/7 triggers, 8 back, 9 start, 10/11 stick clicks, 12-15 D-pad, 16 guide).
Two traps:

- Kernel names lie for Xbox-layout pads: on `xpad`, code 307 is the physical **X** (left) and 308 the physical **Y**
  (top), though the kernel's primary names for those codes are `BTN_NORTH`/`BTN_WEST`. The script prints `BTN_X` /
  `BTN_Y` for xpad devices.
- A page sees no gamepad until a button is pressed **after** the page loaded (Gamepad API privacy rule), and
  `navigator.getGamepads()` may list virtual devices or a second pad first.

To confirm the browser view, open a gamepad tester page in the same browser (or run in the app's devtools console):
```js
[...navigator.getGamepads()].filter(Boolean).map(g => ({id: g.id, mapping: g.mapping, pressed: g.buttons.flatMap((b, i) => b.pressed ? [i] : [])}))
```
Hold one button and run it; `mapping: "standard"` means the indexes above apply, an empty string means raw order.
The `id` string carries the vendor and product (`... Vendor: 046d Product: c21d` in Chromium, `046d-c21d-...` in
Firefox), which is the stable key for per-device profiles.

## 5. Record the profile

Write, where the user keeps device notes (a repo for the device, or the reply if one-off):

```json
{
  "captured": "<YYYY-MM-DD>", "host": "<hostname>", "product": "<name>",
  "usb": {"vendor": "vvvv", "product": "pppp"}, "driver": "<ID_USB_DRIVER>", "by_id": "<stable path>",
  "buttons": {"<physical label>": {"evdev": "<name>", "code": 0, "browser_index": 0}},
  "verified_by_press": true
}
```

Mark anything not pressed as unverified. Mode switches (e.g. XInput/DirectInput, Android/Switch modes on 8BitDo
pads) change the product ID and the layout: record the mode and re-map per mode.

## Remapping afterwards (pointers, not part of this skill)

- App-level: if the app reads the device (Gamepad API, HID), give it the profile; it is the most portable route.
- OS-level: `input-remapper` (GUI and presets per device name), `keyd` (keyboards), udev hwdb `KEYBOARD_KEY_*` for
  scancode-level keyboard remaps, or an evdev→uinput script for anything else.
