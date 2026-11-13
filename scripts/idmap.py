#!/usr/bin/env python3
"""Identify a Linux input device and map its physical buttons to evdev codes.

Subcommands (all print JSON):
  list                          every evdev device: name, vendor:product, bus, driver, by-id path, class
  info  <device>                one device in full: udev properties, declared keys and axes, force feedback
  capture <device> --count N    wait for N distinct inputs (buttons, hat directions, triggers/axes pushed past
                                half travel) and print them in the order they were first pressed
  press <device> [--label L]    wait for ONE input and print it (the one-button-at-a-time session; drains anything
                                pressed before it started, so a stray earlier press is not reported)
  watch <device> --seconds S    print every press for S seconds (JSON lines), for free exploration

<device> is /dev/input/eventN, a /dev/input/by-id/... path, or a vendor:product like 046d:c21d.
Needs python3-evdev (apt install python3-evdev) and read access to /dev/input (the `input` group or a udev uaccess ACL).
"""
import argparse
import json
import os
import subprocess
import sys
import time

try:
    import evdev
    from evdev import ecodes
except ImportError:
    sys.exit(json.dumps({"error": "python3-evdev missing: sudo apt install python3-evdev"}))

BUS = {0x03: "usb", 0x05: "bluetooth", 0x11: "i8042", 0x19: "host", 0x06: "virtual"}
# Linux xpad / hid-generic BTN_* -> W3C "standard gamepad" index, for devices Chromium/Firefox map as standard.
STANDARD_PAD = {
    "BTN_SOUTH": 0, "BTN_EAST": 1, "BTN_WEST": 2, "BTN_NORTH": 3, "BTN_X": 2, "BTN_Y": 3, "BTN_TL": 4, "BTN_TR": 5,
    "BTN_TL2": 6, "BTN_TR2": 7, "BTN_SELECT": 8, "BTN_START": 9, "BTN_THUMBL": 10, "BTN_THUMBR": 11,
    "HAT_UP": 12, "HAT_DOWN": 13, "HAT_LEFT": 14, "HAT_RIGHT": 15, "BTN_MODE": 16,
    "ABS_Z+": 6, "ABS_RZ+": 7,
}
# xpad reports the physical X as code 307 and Y as 308. The kernel's primary names for those codes are BTN_NORTH /
# BTN_WEST (aliases BTN_X / BTN_Y), so the "compass" name is wrong for Xbox-layout pads. Browsers follow position.
XPAD_FIX = {307: ("BTN_X", 2), 308: ("BTN_Y", 3)}


def by_id_paths():
    out = {}
    for base in ("/dev/input/by-id", "/dev/input/by-path"):
        if os.path.isdir(base):
            for n in sorted(os.listdir(base)):
                real = os.path.realpath(os.path.join(base, n))
                out.setdefault(real, []).append(os.path.join(base, n))
    return out


def udev_props(path):
    try:
        r = subprocess.run(["udevadm", "info", "--query=property", path], capture_output=True, text=True, timeout=5)
        return dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    except Exception:
        return {}


def name_of(code, table):
    n = table.get(code, str(code))
    return n[0] if isinstance(n, (list, tuple)) else n


def classify(dev):
    caps = dev.capabilities()
    keys = set(caps.get(ecodes.EV_KEY, []))
    if keys & set(range(0x130, 0x140)):
        return "gamepad"
    if keys & set(range(0x120, 0x130)):
        return "joystick"
    if ecodes.EV_REL in caps and ecodes.BTN_LEFT in keys:
        return "pointer"
    if ecodes.KEY_A in keys and ecodes.KEY_Z in keys:
        return "keyboard"
    if keys:
        return "buttons"
    return "other"


def summary(dev, ids):
    i = dev.info
    p = udev_props(dev.path)
    return {
        "path": dev.path, "name": dev.name, "vendor_product": f"{i.vendor:04x}:{i.product:04x}",
        "bus": BUS.get(i.bustype, hex(i.bustype)), "phys": dev.phys, "uniq": dev.uniq or None,
        "driver": p.get("ID_USB_DRIVER") or None, "serial": p.get("ID_SERIAL") or None,
        "class": classify(dev), "stable_paths": ids.get(dev.path, []),
    }


def resolve(spec):
    if spec.startswith("/dev/"):
        return evdev.InputDevice(os.path.realpath(spec))
    if ":" in spec:
        v, p = (int(x, 16) for x in spec.split(":"))
        hits = []
        for path in evdev.list_devices():
            d = evdev.InputDevice(path)
            if d.info.vendor == v and d.info.product == p:
                hits.append(d)
        if hits:  # prefer the node with the most buttons (composite devices expose several)
            return max(hits, key=lambda d: len(d.capabilities().get(ecodes.EV_KEY, [])))
    sys.exit(json.dumps({"error": f"no input device matches {spec}"}))


def cmd_list(_):
    ids = by_id_paths()
    devs = []
    for path in evdev.list_devices():
        try:
            devs.append(summary(evdev.InputDevice(path), ids))
        except PermissionError:
            devs.append({"path": path, "error": "permission denied (join the input group)"})
    devs.sort(key=lambda d: int(d["path"].rsplit("event", 1)[-1]))
    print(json.dumps(devs, indent=1))


def cmd_info(a):
    dev = resolve(a.device)
    caps = dev.capabilities()
    xpad = udev_props(dev.path).get("ID_USB_DRIVER") == "xpad"
    keys = []
    for c in caps.get(ecodes.EV_KEY, []):
        n = name_of(c, ecodes.BTN) if c >= 0x100 else name_of(c, ecodes.KEY)
        if xpad and c in XPAD_FIX:
            n = XPAD_FIX[c][0]
        keys.append({"code": c, "name": n})
    axes = [{"code": c, "name": name_of(c, ecodes.ABS), "min": ai.min, "max": ai.max}
            for c, ai in caps.get(ecodes.EV_ABS, [])]
    ff = [name_of(c, ecodes.FF) for c in caps.get(ecodes.EV_FF, [])]
    out = summary(dev, by_id_paths())
    out.update(keys=keys, axes=axes, force_feedback=ff, udev=udev_props(dev.path))
    print(json.dumps(out, indent=1))


def events(dev, deadline):
    """Yield (id, label, detail) for each new press: key down, hat direction, axis past half travel."""
    absinfo = dict(dev.capabilities().get(ecodes.EV_ABS, []))
    xpad = udev_props(dev.path).get("ID_USB_DRIVER") == "xpad"
    held = set()
    while time.time() < deadline:
        ev = dev.read_one()
        if ev is None:
            time.sleep(0.005)
            continue
        if ev.type == ecodes.EV_KEY and ev.value == 1:
            n = XPAD_FIX[ev.code][0] if xpad and ev.code in XPAD_FIX else (
                name_of(ev.code, ecodes.BTN) if ev.code >= 0x100 else name_of(ev.code, ecodes.KEY))
            yield f"key:{ev.code}", n, {"type": "EV_KEY", "code": ev.code}
        elif ev.type == ecodes.EV_ABS:
            n = name_of(ev.code, ecodes.ABS)
            if n.startswith("ABS_HAT"):
                if ev.value == 0:
                    continue
                d = {("ABS_HAT0X", -1): "HAT_LEFT", ("ABS_HAT0X", 1): "HAT_RIGHT",
                     ("ABS_HAT0Y", -1): "HAT_UP", ("ABS_HAT0Y", 1): "HAT_DOWN"}.get((n, ev.value), f"{n}{ev.value:+d}")
                yield f"hat:{ev.code}:{ev.value}", d, {"type": "EV_ABS", "code": ev.code, "value": ev.value}
                continue
            ai = absinfo.get(ev.code)
            if not ai:
                continue
            mid = (ai.min + ai.max) / 2
            span = (ai.max - ai.min) / 2 or 1
            if ai.min >= 0 and ai.max <= 1023 and ai.min == 0:  # one-way trigger (0..255 etc.)
                frac, sign = ev.value / ai.max, "+"
            else:
                frac, sign = abs(ev.value - mid) / span, ("+" if ev.value > mid else "-")
            key = f"abs:{ev.code}{sign}"
            if frac > 0.5 and key not in held:
                held.add(key)
                yield key, f"{n}{sign}", {"type": "EV_ABS", "code": ev.code, "direction": sign}
            elif frac < 0.2:
                held.discard(f"abs:{ev.code}+")
                held.discard(f"abs:{ev.code}-")


def browser_index(label):
    """Predicted W3C standard-gamepad index. A prediction: confirm in the browser (see the skill)."""
    return STANDARD_PAD.get({"BTN_A": "BTN_SOUTH", "BTN_B": "BTN_EAST"}.get(label, label))


def cmd_capture(a):
    dev = resolve(a.device)
    seen, out = set(), []
    for k, label, detail in events(dev, time.time() + a.seconds):
        if k in seen:
            continue
        seen.add(k)
        out.append({"order": len(out) + 1, "evdev": label, **detail, "browser_standard_index": browser_index(label)})
        if len(out) >= a.count:
            break
    print(json.dumps({"device": summary(dev, by_id_paths()), "presses": out,
                      "complete": len(out) >= a.count, "expected": a.count}, indent=1))


def cmd_press(a):
    dev = resolve(a.device)
    while dev.read_one() is not None:  # drop queued events from before the prompt
        pass
    for _, label, detail in events(dev, time.time() + a.seconds):
        print(json.dumps({"label": a.label, "evdev": label, **detail,
                          "browser_standard_index": browser_index(label)}))
        return
    print(json.dumps({"label": a.label, "timeout": a.seconds}))


def cmd_watch(a):
    dev = resolve(a.device)
    for _, label, detail in events(dev, time.time() + a.seconds):
        print(json.dumps({"t": round(time.time(), 3), "evdev": label, **detail}), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("list").set_defaults(f=cmd_list)
    p = sp.add_parser("info"); p.add_argument("device"); p.set_defaults(f=cmd_info)
    p = sp.add_parser("capture"); p.add_argument("device"); p.add_argument("--count", type=int, required=True)
    p.add_argument("--seconds", type=float, default=90); p.set_defaults(f=cmd_capture)
    p = sp.add_parser("press"); p.add_argument("device"); p.add_argument("--label")
    p.add_argument("--seconds", type=float, default=60); p.set_defaults(f=cmd_press)
    p = sp.add_parser("watch"); p.add_argument("device"); p.add_argument("--seconds", type=float, default=30)
    p.set_defaults(f=cmd_watch)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
