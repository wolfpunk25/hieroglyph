# Wolfpunk Hieroglyph

A hand-painted eight-button generative MIDI sequencer built on an RP2040.

It is not a playable keyboard. Plugged in over USB it appears as a MIDI device and
continuously **composes** ambient, C418-style music across four independent voices,
sending note and CC data to whatever DAW or synth you point it at. The buttons don't
play notes — they steer the composition while it runs.

---

## Hardware

| Part | Detail |
|---|---|
| Board | VCC-GND Studio **YD-RP2040** (`vcc_gnd_yd_rp2040`) |
| Firmware | Adafruit CircuitPython **10.0.3** |
| Display | HT16K33 **8×8 LED matrix**, I²C address `0x70` |
| Status LED | WS2812 NeoPixel on `GP23` (onboard) |
| Decorative LEDs | 4 × PWM on `GP10`–`GP13`, one per voice |
| Buttons | 8 × momentary, `Pull.UP`, active low |

### Pin map

| Peripheral | Pin(s) |
|---|---|
| I²C — SCL / SDA | `GP1` / `GP0` @ 400 kHz |
| NeoPixel | `GP23` |
| Decorative LEDs | `GP10`, `GP11`, `GP12`, `GP13` |
| Onboard LED | `GP25` (beat flash) |
| Buttons | `GP16`–`GP22`, `GP26` |

---

## Library dependencies

Firmware first — flash **CircuitPython 10.0.3** for the YD-RP2040 from
[circuitpython.org/board/vcc_gnd_yd_rp2040](https://circuitpython.org/board/vcc_gnd_yd_rp2040/).

Then copy these from the **Adafruit CircuitPython Bundle for 10.x** into `/lib` on the
`CIRCUITPY` drive. Grab the bundle from
[circuitpython.org/libraries](https://circuitpython.org/libraries) — this build was made
against `adafruit-circuitpython-bundle-10.x-mpy-20260630`.

### Directly imported

| Library | Provides | Used for |
|---|---|---|
| `adafruit_midi` | `MIDI`, `NoteOn`, `NoteOff`, `ControlChange` | All MIDI output |
| `adafruit_ht16k33` | `matrix.Matrix8x8` | The 8×8 LED matrix |
| `neopixel` | `NeoPixel` | Status pixel |

### Required transitively

| Library | Pulled in by |
|---|---|
| `adafruit_bus_device` | `adafruit_ht16k33` (I²C device wrapper) |
| `adafruit_pixelbuf` | `neopixel` (pixel buffer backing) |

### Resulting `/lib` layout

```
lib/
├── adafruit_bus_device/
├── adafruit_ht16k33/
├── adafruit_midi/
├── adafruit_pixelbuf.mpy
└── neopixel.mpy
```

These are third-party Adafruit libraries (MIT) and are deliberately **not** vendored into
this repo — install them from the bundle above.

Everything else `code.py` imports (`time`, `random`, `board`, `busio`, `digitalio`,
`pwmio`, `usb_midi`) is built into CircuitPython.

### Install

1. Flash CircuitPython 10.0.3 — the board mounts as `CIRCUITPY`.
2. Copy the five libraries above into `CIRCUITPY/lib/`.
3. Copy `code.py` to the root of `CIRCUITPY`.

CircuitPython runs the file the moment it lands, so it starts playing immediately.

---

## Controls

```
           ┌─────┐
           │  1  │           MUTATE
           └─────┘
   ┌─────┐ ┌─────┐ ┌─────┐
   │  2  │ │  4  │ │  7  │   ▲  OCT   TEMPO   MOD
   └─────┘ └─────┘ └─────┘
   ┌─────┐ ┌─────┐ ┌─────┐
   │  3  │ │  5  │ │  8  │   ▼  OCT   TEMPO   MOD
   └─────┘ └─────┘ └─────┘
           ┌─────┐
           │  6  │           RESET
           └─────┘
```

| Button | GPIO | Function |
|---|---|---|
| **1** | `GP26` | **Mutate** — tap = rebuild all four phrases; hold 1.5 s = shift key |
| **2** | `GP21` | Octave up |
| **3** | `GP22` | Octave down |
| **4** | `GP20` | Tempo up (50–120 BPM, repeats while held) |
| **5** | `GP18` | Tempo down |
| **6** | `GP17` | **Reset** — MIDI panic, clears octave / step / energy |
| **7** | `GP19` | Mod up — CC74 |
| **8** | `GP16` | Mod down — CC74 |

Three vertical up/down columns, with the two disruptive controls — mutate and reset — on
the isolated top and bottom keys, out of the play zone.

Two chords:

- **7 + 8 together** — snap the mod wheel back to centre (64)
- **4 + 5 together** — sweep the full status display on the matrix (BPM → octave → scale)

> The keycap-to-GPIO wiring is soldered and is the ground truth. Which *function* sits on
> which pin is just the `PIN_MAP` dict near the top of `code.py` — remap freely without
> touching hardware.

### Reading the lights

The NeoPixel colour-codes the last action, which is the quickest way to identify a button:

| Colour | Meaning |
|---|---|
| 🟠 Orange | Tempo |
| 🔵 Blue | Octave |
| 🟣 Purple | Mod wheel |
| 🩷 Hot pink | Mutate |
| 🟡 Gold | Reset / scale change |
| 🟢 Green pulse | Idle — brightness tracks the energy cycle |

The **four PWM LEDs** flash on note-on, then decay to a slow phase-offset breathe.

### The matrix

The 8×8 matrix has two modes.

**Sequencer view** (default) — two rows per voice: a dot showing position within the
phrase buffer, and a bar counting down the current note's remaining beats.

**Status view** (transient) — whenever you change a parameter, the matrix shows its value
for about 1.5 s, then hands the display straight back. There's no gesture to remember;
it appears exactly when you'd want it.

Row 0 carries a marker showing which page you're looking at — **left** = tempo,
**centre** = octave, **right** = scale:

| Page | Shows | Example |
|---|---|---|
| Tempo | Two digits | `70` |
| | BPM ≥ 100 adds a full-width underline on the bottom row | `20` + underline = 120 |
| Octave | Sign and digit, blank sign at zero | `+2`, `0`, `-2` |
| Scale | Scale letter, then root letter | `P C` = pentatonic in C |
| Mod | Horizontal bar across the CC74 range | centre marks show where 64 is |

Scale letters are `P`entatonic, `M`ajor, `D`orian, `L`ydian, `B`lues — `B` rather than `M`
for minblues so it doesn't collide with major.

Press **4 + 5 together** to sweep all three pages on demand. That combination was
previously a no-op, since tempo up and down cancelled each other out.

---

## How it works

Four voices, each locked to a **non-overlapping register** so they can never collide:

| Track | Range | Role | Note lengths |
|---|---|---|---|
| `BASS` | E1–B2 | Root movement, slowest | 4–8 beats |
| `INNR` | C3–B3 | Harmonic fill | 3–6 beats |
| `MID` | C4–B4 | Countermelody | 2–4 beats |
| `HIGH` | C5–D6 | Wandering melody | 1–3 beats |

Each holds a six-note phrase buffer and steps through it. Every step it rolls against its
own `prob` — fail and it rests instead, which is what stops the texture sounding like a loop.

**Voice leading.** Notes move by weighted stepwise motion. Repeats and single steps are
heavily favoured, skips are rare, and leaps beyond three scale degrees are impossible.

**Momentum.** After three consecutive steps in one direction a voice flips its bias, so
melodies form arches instead of drifting to the top of their range and sticking.

**Energy.** A slow LFO rises and falls over roughly five minutes, driving note density,
velocity and the idle pixel colour.

**Drift.** Every 300 steps there's a 35% chance the scale silently changes — pentatonic →
major → dorian → lydian → minor blues.

**Mutate** (button 1) cuts all playing notes, rebuilds every phrase buffer from fresh random
seeds using bolder voice leading, and reshuffles density. Held, it walks the tonal centre
through C → F → G → A → E (I-IV-V-vi-iii), so a key change always sounds intentional.

---

## Development

`DEV = True` at the top of `code.py` logs every note, rest and parameter change to the USB
serial console. Useful for tinkering, but it's a lot of per-note string formatting — set it
to `False` for performance use.

To watch the log on macOS:

```bash
screen /dev/cu.usbmodem2101 115200
```

Reading the port is passive and won't interrupt the running code, so it's safe to attach
while playing. Note that macOS has no `timeout` command if you're scripting a capture.

---

## Licence

MIT — see [LICENSE](LICENSE). The Adafruit libraries it depends on are separately MIT
licensed and are not included here.
