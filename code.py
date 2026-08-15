import time
import random
import board
import busio
import digitalio
import pwmio
import neopixel
import usb_midi
from adafruit_midi import MIDI
from adafruit_midi.note_on import NoteOn
from adafruit_midi.note_off import NoteOff
from adafruit_midi.control_change import ControlChange
from adafruit_ht16k33.matrix import Matrix8x8

# ─────────────────────────────────────────────
# Dev mode
# ─────────────────────────────────────────────
DEV = True
def log(*args):
    if DEV: print(*args)

# ─────────────────────────────────────────────
# Hardware
# ─────────────────────────────────────────────
i2c = busio.I2C(scl=board.GP1, sda=board.GP0, frequency=400000)
mx  = Matrix8x8(i2c, address=0x70)
mx.brightness = 0.2

led_pin = getattr(board, "LED", getattr(board, "GP25", None))
onboard_led = digitalio.DigitalInOut(led_pin)
onboard_led.direction = digitalio.Direction.OUTPUT

pixel = neopixel.NeoPixel(board.GP23, 1, brightness=0.1, auto_write=True)
midi  = MIDI(midi_out=usb_midi.ports[1], out_channel=0)

# ─────────────────────────────────────────────
# Decorative LEDs — GP10–GP13 (PWM)
# ─────────────────────────────────────────────
DECO_PINS   = [board.GP10, board.GP11, board.GP12, board.GP13]
deco_leds   = [pwmio.PWMOut(p, frequency=1000, duty_cycle=0) for p in DECO_PINS]
deco_bright = [0.12, 0.12, 0.12, 0.12]
DECO_DECAY  = 0.97
DECO_PHASE  = 40
DECO_MIN    = 0.04
DECO_MAX    = 0.30

def deco_ambient(led_idx):
    offset = led_idx * DECO_PHASE
    phase  = (energy_ctr + offset) % (ENERGY_HALF * 2)
    e      = phase / ENERGY_HALF if phase < ENERGY_HALF else 2.0 - phase / ENERGY_HALF
    return DECO_MIN + (DECO_MAX - DECO_MIN) * e

def update_deco():
    for i, led in enumerate(deco_leds):
        amb = deco_ambient(i)
        if deco_bright[i] > amb:
            deco_bright[i] = deco_bright[i] * DECO_DECAY + amb * (1 - DECO_DECAY)
            if deco_bright[i] < amb: deco_bright[i] = amb
        else:
            deco_bright[i] = amb
        led.duty_cycle = int(deco_bright[i] * 65535)

def deco_trigger(idx): deco_bright[idx] = 0.85
def deco_all(b):
    for i in range(4): deco_bright[i] = b

# ─────────────────────────────────────────────
# NeoPixel
# ─────────────────────────────────────────────
COL_TEMPO  = (255,  80,   0)
COL_OCTAVE = (  0,  80, 255)
COL_MOD    = (160,   0, 220)
COL_CHAOS  = (255,   0, 120)
COL_RESET  = (255, 200,   0)
COL_SCALE  = (255, 128,   0)
COL_MUTE   = (255,  40,  40)
FLASH_DUR  = 0.25
pixel_expiry = 0.0

def flash(colour, duration=FLASH_DUR):
    global pixel_expiry
    pixel[0]     = colour
    pixel_expiry = time.monotonic() + duration

def idle_colour():
    e = get_energy()
    g = int(15 + 65 * e)
    return (0, g, int(g * 0.5))

def update_pixel():
    if time.monotonic() >= pixel_expiry:
        pixel[0] = idle_colour()

# ─────────────────────────────────────────────
# Button system — non-blocking
# ─────────────────────────────────────────────
# FIX: Previous version used time.sleep() in every button handler.
# While sleeping, note-offs can't fire and LEDs freeze. The fix is a
# timestamp-based debounce: btn() returns True at most once per
# `interval` seconds while held, with no blocking anywhere.
#
# interval guide:
#   0.12  — fast repeat while held (tempo)
#   0.28  — one action per deliberate press (octave, chaos)
#   0.50  — guarded single-fire (reset — prevents accidental double-fire)
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# Physical keycap layout (numbers are printed on the case):
#
#         ┌───┐
#         │ 1 │  MUTATE                 GP26
#         └───┘
#   ┌───┐ ┌───┐ ┌───┐
#   │ 2 │ │ 4 │ │ 7 │  OCT▲ TEMPO▲ MOD▲  GP21 GP20 GP19
#   └───┘ └───┘ └───┘
#   ┌───┐ ┌───┐ ┌───┐
#   │ 3 │ │ 5 │ │ 8 │  OCT▼ TEMPO▼ MOD▼  GP22 GP18 GP16
#   └───┘ └───┘ └───┘
#         ┌───┐
#         │ 6 │  RESET                  GP17
#         └───┘
#
# The keycap→GPIO wiring is soldered and fixed. This dict is the only
# thing deciding which function lands on which key, so the layout can be
# rearranged freely without touching hardware.
#
# FIX: the as-built mapping put the octave pair on buttons 3 and 8 —
# split across opposite sides of the board AND inverted (down on the
# left, up on the right), while every other pair ran up-over-down.
# It was effectively unmemorable. Reset also sat at button 7, in the
# middle of the play zone, where a mis-hit would panic all notes and
# wipe the octave / step / energy state.
#
# Now: three clean vertical up/down columns, with the two disruptive
# singles pushed to the isolated top and bottom keys.
# ─────────────────────────────────────────────
PIN_MAP = {
    "SHF":    "GP26",                    # 1  — mutate  (isolated, top)
    "OCT_UP": "GP21", "OCT_DN": "GP22",  # 2 / 3  — left column
    "T_UP":   "GP20", "T_DN":   "GP18",  # 4 / 5  — middle column
    "MOD_UP": "GP19", "MOD_DN": "GP16",  # 7 / 8  — right column
    "ENT":    "GP17",                    # 6  — reset   (isolated, bottom)
}
btns = {k: digitalio.DigitalInOut(getattr(board, v)) for k, v in PIN_MAP.items()}
for b in btns.values():
    b.direction, b.pull = digitalio.Direction.INPUT, digitalio.Pull.UP

btn_last = {k: 0.0 for k in PIN_MAP}

def is_p(name): return not btns[name].value

def btn(name, interval=0.2):
    """Non-blocking, rate-limited button check. No sleep. Safe every loop."""
    if is_p(name):
        t = time.monotonic()
        if t - btn_last[name] >= interval:
            btn_last[name] = t
            return True
    return False

# ─────────────────────────────────────────────
# Note name helper
# ─────────────────────────────────────────────
_NOTE_NAMES = ("C","C#","D","D#","E","F","F#","G","G#","A","A#","B")
def note_name(n):
    return f"{_NOTE_NAMES[n % 12]}{(n // 12) - 1}"

# ─────────────────────────────────────────────
# Scale system
# ─────────────────────────────────────────────
SCALE_SHAPES = {
    "pentatonic": (0, 2, 4, 7, 9),
    "major":      (0, 2, 4, 7, 9, 11),
    "dorian":     (0, 2, 3, 7, 9, 10),
    "lydian":     (0, 2, 4, 6, 7, 9, 11),
    "minblues":   (0, 3, 5, 6, 7, 10),
}
SCALE_NAMES = list(SCALE_SHAPES.keys())
scale_idx   = 0
ROOT        = 48   # C3

# Tonal centres walked by set_root(), on SHF + mod buttons.
# These are the I, IV, V, vi, iii roots of C — all harmonically related,
# so cycling through them produces interesting colour without ever
# sounding wrong. Stored as MIDI note numbers (C3 octave area).
ROOT_CYCLE     = (48, 53, 55, 57, 52)   # C3, F3, G3, A3, E3
root_cycle_idx = 0

def build_scale(root, shape, low=24, high=96):
    notes = []
    for oct in range(-2, 6):
        for interval in shape:
            n = root + oct * 12 + interval
            if low <= n <= high:
                notes.append(n)
    return sorted(set(notes))

SCALE = build_scale(ROOT, SCALE_SHAPES[SCALE_NAMES[scale_idx]])

def nearest_in_scale(note):
    return min(SCALE, key=lambda n: abs(n - note))

def nearest_in_register(note, low, high):
    """
    Snap a MIDI note to the nearest scale note within a register range.
    Used when initialising or re-snapping track buffers after a scale
    change. Ensures no track buffer note escapes its register bounds.
    """
    candidates = [n for n in SCALE if low <= n <= high]
    if not candidates:
        return nearest_in_scale(note)
    return min(candidates, key=lambda n: abs(n - note))

def voice_lead(current, bias=0, low=24, high=96):
    """
    Weighted stepwise voice leading, constrained to [low, high].

    FIX: Previous version had no register bounds — voices could drift
    into each other's octave zones, causing two tracks to hold the same
    MIDI note. When one ended, both cut off audibly.

    This version filters SCALE to the track's register before weighting,
    so a voice physically cannot cross into another voice's range.

    Weights tuned for C418:
      d=0  : linger (probability 6) — melodies repeat pitches often
      d=±1 : step  (probability 8±bias*2) — strongly preferred
      d=±2 : skip  (probability 3) — occasional small skip
      d=±3 : rare  (probability 1)
      d>3  : zero  — no leaps, ever
    """
    reg = [n for n in SCALE if low <= n <= high]
    if not reg:
        return current
    best = min(range(len(reg)), key=lambda i: abs(reg[i] - current))
    weights = []
    for i in range(len(reg)):
        d = i - best
        if   d ==  0: w = 6
        elif d ==  1: w = max(1, 8 + bias * 2)
        elif d == -1: w = max(1, 8 - bias * 2)
        elif d ==  2: w = 3
        elif d == -2: w = 3
        elif d ==  3: w = 1
        elif d == -3: w = 1
        else:         w = 0
        weights.append(w)
    total = sum(weights)
    if total == 0:
        return reg[best]
    r = random.uniform(0, total)
    acc = 0
    for i, w in enumerate(weights):
        acc += w
        if r <= acc:
            return reg[i]
    return reg[best]

def mutate_voice_lead(current, bias=0, low=24, high=96):
    """
    Bolder voice leading used exclusively during full_mutation().

    Compared to voice_lead():
      - Unison weight reduced  (1 vs 6) — pushes melody to actually move
      - Step weights reduced   (5 vs 8) — steps no longer dominate
      - Skip weights increased (5 vs 3) — skips of 2–3 are equally welcome
      - Leap weights added     (d=4: 2, d=5: 1) — occasional jumps allowed

    The result sounds like a purposeful key change or new phrase, not
    just a slightly nudged version of what was playing.
    """
    reg = [n for n in SCALE if low <= n <= high]
    if not reg:
        return current
    best = min(range(len(reg)), key=lambda i: abs(reg[i] - current))
    weights = []
    for i in range(len(reg)):
        d = i - best
        if   d ==  0: w = 1
        elif d ==  1: w = max(1, 5 + bias * 2)
        elif d == -1: w = max(1, 5 - bias * 2)
        elif d ==  2: w = 5
        elif d == -2: w = 5
        elif d ==  3: w = 4
        elif d == -3: w = 4
        elif d ==  4: w = 2
        elif d == -4: w = 2
        elif d ==  5: w = 1
        elif d == -5: w = 1
        else:         w = 0
        weights.append(w)
    total = sum(weights)
    if total == 0:
        return reg[best]
    r = random.uniform(0, total)
    acc = 0
    for i, w in enumerate(weights):
        acc += w
        if r <= acc:
            return reg[i]
    return reg[best]

# ─────────────────────────────────────────────
# Global state
# ─────────────────────────────────────────────
octave      = 0
BPM         = 70
mod_val     = 64
last_mod    = 64

# Mod wheel ramp timing.
# FIX: previous version stepped mod_val by 4 on every main-loop pass
# (~10 ms), so a held press swept 64→127 in about 160 ms and sprang
# straight back to 64 on release. In practice CC74 was a switch, not a
# wheel — it could never rest at a usable intermediate value.
#
# Now the ramp is time-based, so travel speed is predictable no matter
# how fast the loop runs, and the value LATCHES where you leave it.
#   MOD_RATE 0.02 + MOD_STEP 1  →  ~1.3 s from centre to full, 2.5 s
#   end to end. Slow enough to land on a specific value by ear.
MOD_RATE      = 0.02   # seconds between ramp steps
MOD_STEP      = 1      # CC units per ramp step
mod_last_move = 0.0

master_step = 0
energy_ctr  = 0
ENERGY_HALF = 180   # ~2.6 min per half-cycle at 70 BPM

# FIX: pre-defined tuple — previous version allocated [1, 1, 2] on every
# "didn't fire" event (100+ times per session). Tuple is faster and free.
SKIP_REST = (1, 1, 2)

# Modifier hold-state.
# SHF (button 1) and ENT (button 6) are the two isolated corner keys and
# each does double duty: held, it is a modifier; tapped alone, it fires
# its original action on RELEASE.
#   *_held_since : time.monotonic() when first pressed (-1 = not held)
#   *_consumed   : True once this hold has done something, which
#                  suppresses the tap action on release
shf_held_since = -1.0
shf_consumed   = False
ent_held_since = -1.0
ent_consumed   = False
HOLD_PAGE      = 30.0   # status page duration while a modifier is held

def get_energy():
    phase = energy_ctr % (ENERGY_HALF * 2)
    return phase / ENERGY_HALF if phase < ENERGY_HALF else 2.0 - phase / ENERGY_HALF

# ─────────────────────────────────────────────
# Track definitions
# ─────────────────────────────────────────────
# Each track is confined to a non-overlapping register via low/high.
# This prevents voice crossings entirely without any runtime collision
# detection — the constraint is structural.
#
# Register layout (no gaps, no overlaps):
#   BASS : E1–B2  (28–47)   root movement, slowest
#   INNR : C3–B3  (48–59)   harmonic fill
#   MID  : C4–B4  (60–71)   countermelody
#   HIGH : C5–D6  (72–86)   wandering melody, most space
#
# FIX: dur_options and rest_options changed to tuples (were lists).
# random.choice() works with both; tuples are lighter on memory and
# slightly faster to iterate since they're immutable.
#
# FIX: BASS initial buffer had note 48 (C3) which is INNER's floor.
# Changed to A2 (45) which sits correctly within BASS's register.
#
# momentum / last_played: used by update_momentum() to create natural
# melodic arcs — see that function for details.
# ─────────────────────────────────────────────
tracks = [
    {
        "buf": [36, 43, 40, 45, 43, 36],      # C2 G2 E2 A2 G2 C2
        "buf_pos": 0, "note": -1,
        "note_off_step": 0, "next_play_step": 0,
        "last_played": -1, "momentum": 0,
        "prob": 0.72, "prob_ceil": 0.78,
        "vel_base": 68,
        "dur_options":  (4, 4, 6, 8, 8),
        "rest_options": (2, 3, 4, 4),
        "bias": -1, "low": 28, "high": 47, "label": "BASS",
    },
    {
        "buf": [52, 55, 57, 55, 52, 48],      # E3 G3 A3 G3 E3 C3
        "buf_pos": 0, "note": -1,
        "note_off_step": 0, "next_play_step": 5,
        "last_played": -1, "momentum": 0,
        "prob": 0.65, "prob_ceil": 0.72,
        "vel_base": 52,
        "dur_options":  (3, 4, 4, 6),
        "rest_options": (2, 3, 4, 4),
        "bias": 1, "low": 48, "high": 59, "label": "INNR",
    },
    {
        "buf": [60, 64, 62, 67, 64, 60],      # C4 E4 D4 G4 E4 C4
        "buf_pos": 0, "note": -1,
        "note_off_step": 0, "next_play_step": 1,
        "last_played": -1, "momentum": 0,
        "prob": 0.58, "prob_ceil": 0.68,
        "vel_base": 58,
        "dur_options":  (2, 2, 3, 4),
        "rest_options": (2, 3, 3, 4),
        "bias": -1, "low": 60, "high": 71, "label": "MID ",
    },
    {
        "buf": [72, 74, 76, 79, 81, 76],      # C5 D5 E5 G5 A5 E5
        "buf_pos": 0, "note": -1,
        "note_off_step": 0, "next_play_step": 3,
        "last_played": -1, "momentum": 0,
        "prob": 0.55, "prob_ceil": 0.68,
        "vel_base": 62,
        "dur_options":  (1, 2, 2, 3),
        "rest_options": (2, 2, 3, 4, 5),
        "bias": 1, "low": 72, "high": 86, "label": "HIGH",
    },
]

# Snap all initial buffer notes into their register-bounded scale
# FIX: was nearest_in_scale() (ignores register) — now register-aware
for t in tracks:
    t["mute"] = False
    t["buf"]  = [nearest_in_register(n, t["low"], t["high"]) for n in t["buf"]]

# ─────────────────────────────────────────────
# Matrix: sequencer view + status overlay
# ─────────────────────────────────────────────
# The instrument previously had no way to show its own state — BPM,
# octave, scale and root were all invisible, so they could only be
# steered by ear with no idea where you were in the range.
#
# The matrix now has two modes:
#
#   SEQUENCER (default)  two rows per voice — a dot for position in the
#                        phrase buffer, a bar counting down the current
#                        note's remaining beats.
#
#   STATUS (transient)   a big readable value for whichever parameter
#                        you just changed, then it falls back.
#
# Status appears automatically on change, so there is no gesture to
# remember. Pressing BOTH tempo buttons (4+5) sweeps all three pages on
# demand — that combination was previously a no-op, since up and down
# cancelled out.
# ─────────────────────────────────────────────

# 3x5 pixel font. Two glyphs fit side by side on an 8x8 with a 1px gap.
FONT = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "+": ("000", "010", "111", "010", "000"),
    "-": ("000", "000", "111", "000", "000"),
    "P": ("111", "101", "111", "100", "100"),
    "M": ("101", "111", "111", "101", "101"),
    "D": ("110", "101", "101", "101", "110"),
    "L": ("100", "100", "100", "100", "111"),
    "B": ("110", "101", "110", "101", "110"),
    "C": ("111", "100", "100", "100", "111"),
    "F": ("111", "100", "111", "100", "100"),
    "G": ("111", "100", "101", "101", "111"),
    "A": ("111", "101", "111", "101", "101"),
    "E": ("111", "100", "111", "100", "111"),
}

# major and minblues both start with M, so the letter is explicit.
SCALE_GLYPH = {
    "pentatonic": "P", "major": "M", "dorian": "D",
    "lydian":     "L", "minblues": "B",
}

STATUS_DUR    = 1.5    # seconds a status page stays up
STATUS_MOD    = 0.8    # shorter for the mod bar — it moves constantly
status_page   = None   # None | "bpm" | "oct" | "scale" | "mod"
status_expiry = 0.0
status_queue  = None   # remaining pages when sweeping all three
status_sig    = None   # last-rendered value, so we only redraw on change
chord_last    = 0.0

# Display rotation, in degrees counter-clockwise: 0, 90, 180 or 270.
# The matrix is mounted turned relative to the drawing code's idea of
# "up", so every pixel write goes through px() and is transformed once
# here. Change this constant alone to re-orient the whole display —
# sequencer view and status pages together.
ROTATE = 90

def px(x, y, v=1):
    """Write one pixel in logical coordinates, applying ROTATE."""
    if   ROTATE == 90:  mx[y,     7 - x] = v
    elif ROTATE == 180: mx[7 - x, 7 - y] = v
    elif ROTATE == 270: mx[7 - y, x    ] = v
    else:               mx[x,     y    ] = v

def glyph(ch, x0, y0):
    """Blit a 3x5 font glyph with its top-left corner at (x0, y0)."""
    g = FONT.get(ch)
    if not g:
        return
    for ry in range(5):
        row = g[ry]
        for cx in range(3):
            if row[cx] == "1":
                px(x0 + cx, y0 + ry)

def draw_sequencer():
    mx.fill(0)
    for i, t in enumerate(tracks):
        r = i * 2
        px(t["buf_pos"] % 8, r)
        if t["note"] != -1:
            cols = min(max(0, t["note_off_step"] - master_step), 8)
            for c in range(cols):
                px(c, r + 1)
    mx.show()

def draw_status(page):
    """
    Row 0 carries a position marker saying which page you're looking at:
    left = tempo, centre = octave, right = scale. Rows 2-6 hold the value.
    """
    mx.fill(0)
    if page == "bpm":
        px(0, 0); px(1, 0)
        glyph(str((BPM // 10) % 10), 0, 2)
        glyph(str(BPM % 10),         4, 2)
        # BPM tops out at 120, so the hundreds digit is always 1 — an
        # underline means "add 100" rather than trying to fit 3 digits.
        if BPM >= 100:
            for c in range(8):
                px(c, 7)
    elif page == "oct":
        px(3, 0); px(4, 0)
        if   octave > 0: glyph("+", 0, 2)
        elif octave < 0: glyph("-", 0, 2)
        glyph(str(abs(octave)), 4, 2)
    elif page == "scale":
        px(6, 0); px(7, 0)
        glyph(SCALE_GLYPH[SCALE_NAMES[scale_idx]], 0, 2)
        glyph(note_name(ROOT)[0], 4, 2)
    elif page == "mod":
        # Horizontal bar, 8 columns across the CC74 range, with centre
        # reference marks above and below so 64 is easy to find.
        n = int(mod_val / 127.0 * 8 + 0.5)
        for c in range(n):
            for r in (3, 4, 5):
                px(c, r)
        px(4, 1)
        px(4, 7)
    elif page == "mutes":
        # One row per voice in logical space: BASS INNR MID HIGH.
        # ROTATE turns these into four vertical bars on the physical
        # display. Full bar = sounding, single stub = muted.
        for i, t in enumerate(tracks):
            r = i * 2
            if t["mute"]:
                px(0, r)
            else:
                for c in range(8):
                    px(c, r)
    mx.show()

def status_signature(page):
    """Value identity for the current page — redraw only when this changes."""
    if page == "bpm":   return (page, BPM)
    if page == "oct":   return (page, octave)
    if page == "scale": return (page, scale_idx, ROOT)
    if page == "mod":   return (page, int(mod_val / 127.0 * 8 + 0.5))
    if page == "mutes": return (page, tuple(t["mute"] for t in tracks))
    return None

def status_show(page, dur=STATUS_DUR):
    global status_page, status_expiry, status_queue
    status_page   = page
    status_expiry = time.monotonic() + dur
    status_queue  = None          # a direct change cancels any sweep

def status_sweep():
    """Show all three pages in turn — the 4+5 chord."""
    global status_queue
    status_show("bpm")
    status_queue = ["oct", "scale"]

next_tick = time.monotonic()

# ─────────────────────────────────────────────
# MIDI panic
# ─────────────────────────────────────────────
# FIX: previous reset only sent individual NoteOffs for tracked notes.
# midi_panic() additionally sends CC123 (All Notes Off) to catch any
# notes the sequencer lost track of, and CC64=0 to release sustain.
# Called at startup and on reset.
def midi_panic():
    for t in tracks:
        if t["note"] != -1:
            midi.send(NoteOff(t["note"], 0))
            t["note"] = -1
    midi.send(ControlChange(123, 0))   # All Notes Off
    midi.send(ControlChange(64,  0))   # Sustain pedal off
    log("PANIC  All Notes Off sent")

# ─────────────────────────────────────────────
# Phrase evolution
# ─────────────────────────────────────────────
def evolve_phrase(idx, source="CYCLE"):
    """
    Replace one note in the phrase buffer using voice-led motion from
    its neighbour, constrained to the track's register.
    FIX: now passes low/high to voice_lead() — was using unbounded SCALE.
    """
    t      = tracks[idx]
    pos    = random.randint(0, len(t["buf"]) - 1)
    anchor = t["buf"][(pos - 1) % len(t["buf"])]
    new    = voice_lead(anchor, t["bias"], t["low"], t["high"])
    old    = t["buf"][pos]
    t["buf"][pos] = new
    log(f"  PHRASE [{source}] {t['label']} buf[{pos}]: {note_name(old)} → {note_name(new)}")

def full_mutation():
    """
    Complete replacement of all four phrase buffers. Called on SHF press.

    Why the old approach was inaudible:
      - evolve_phrase() changes 1 note out of 6 per track = 4/24 total
      - voice_lead weight 6 for unison means the "new" note is often
        identical to the old one
      - Currently playing notes are never cut, so buffer changes are
        only heard after the track naturally cycles — up to 8 beats later

    This function:
      1. Cuts all currently playing notes immediately (audible right now)
      2. Builds entirely new phrase buffers from a fresh random seed
         per track, using mutate_voice_lead() which actually moves
      3. Staggers next_play_step so voices re-enter offset, not all at once
      4. Randomises each track's prob to create a new density feel
    """
    log("MUTATE ── Full phrase replacement ──")
    for i, t in enumerate(tracks):
        # Immediate note cut
        if t["note"] != -1:
            midi.send(NoteOff(t["note"], 0))
            t["note"] = -1

        # Pick a random seed note anywhere in the track's register
        candidates = [n for n in SCALE if t["low"] <= n <= t["high"]]
        if not candidates:
            continue
        current = random.choice(candidates)

        # Walk through the buffer length using bolder voice leading
        new_buf = []
        for _ in range(len(t["buf"])):
            new_buf.append(current)
            current = mutate_voice_lead(current, t["bias"], t["low"], t["high"])

        t["buf"]         = new_buf
        t["buf_pos"]     = 0
        t["momentum"]    = 0
        t["last_played"] = -1
        t["next_play_step"] = master_step + i   # staggered re-entry

        # Shake up density — could go sparse or active
        t["prob"] = t["prob_ceil"] * random.uniform(0.35, 0.95)

        buf_str = " ".join(note_name(n) for n in t["buf"])
        log(f"  [{i}] {t['label']}  prob={t['prob']:.2f}  [{buf_str}]")


# ───────────────────────────────────────────
# Second-function targets
# ───────────────────────────────────────────
# Reached by holding a modifier key. Each is a direct jump to a value,
# as opposed to the base layer's nudge-by-one — the point of the layer
# is to get somewhere in one gesture while playing.
# ───────────────────────────────────────────
BPM_PRESETS = (50, 60, 70, 85, 100, 120)

def resnap_buffers():
    for t in tracks:
        t["buf"] = [nearest_in_register(n, t["low"], t["high"]) for n in t["buf"]]

def set_scale(step):
    """Choose a scale outright instead of waiting on the random drift."""
    global scale_idx
    scale_idx = (scale_idx + step) % len(SCALE_NAMES)
    SCALE[:]  = build_scale(ROOT, SCALE_SHAPES[SCALE_NAMES[scale_idx]])
    resnap_buffers()
    status_show("scale")
    log(f"SCALE  → {SCALE_NAMES[scale_idx]}  root={note_name(ROOT)}")

def set_root(step):
    """
    Walk the tonal centre through ROOT_CYCLE — C F G A E, the I IV V vi
    iii of C. All harmonically related, so a shift always sounds
    intentional rather than like a mistake.
    """
    global ROOT, root_cycle_idx
    root_cycle_idx = (root_cycle_idx + step) % len(ROOT_CYCLE)
    ROOT = ROOT_CYCLE[root_cycle_idx]
    SCALE[:] = build_scale(ROOT, SCALE_SHAPES[SCALE_NAMES[scale_idx]])
    resnap_buffers()
    status_show("scale")
    log(f"ROOT   → {note_name(ROOT)}  (cycle pos {root_cycle_idx})")

def bpm_preset(step):
    """
    Jump between musically useful tempos. Nudging by 1 BPM is still on
    the base layer; this is for getting from 70 to 100 mid-performance.
    """
    global BPM
    i = min(range(len(BPM_PRESETS)), key=lambda k: abs(BPM_PRESETS[k] - BPM))
    # If snapping to the nearest preset already moves the way you asked,
    # that is the jump — otherwise step to the next one along.
    if BPM_PRESETS[i] != BPM and (
            (step > 0 and BPM_PRESETS[i] > BPM) or
            (step < 0 and BPM_PRESETS[i] < BPM)):
        BPM = BPM_PRESETS[i]
    else:
        BPM = BPM_PRESETS[max(0, min(len(BPM_PRESETS) - 1, i + step))]
    status_show("bpm")
    log(f"TEMPO  preset → {BPM} BPM")

def set_mute(i, state):
    t = tracks[i]
    if t["mute"] == state:
        return
    t["mute"] = state
    if state and t["note"] != -1:
        midi.send(NoteOff(t["note"], 0))   # drop out now, not at end of note
        t["note"] = -1
    status_show("mutes")
    log(f"MUTE   {t['label']} {'muted' if state else 'live'}")

def toggle_mute(i):
    set_mute(i, not tracks[i]["mute"])

def mute_all(state):
    for i in range(len(tracks)):
        set_mute(i, state)
    status_show("mutes")

def layer_hit(name, fn, arg, colour):
    """One second-function slot. Returns True if it fired."""
    if btn(name, 0.30):
        fn(arg)
        flash(colour)
        return True
    return False


def drift_prob(idx):
    t      = tracks[idx]
    e      = get_energy()
    target = t["prob_ceil"] * (0.25 + 0.75 * e)
    t["prob"] += (target - t["prob"]) * 0.06
    t["prob"]  = max(0.08, min(t["prob_ceil"], t["prob"]))
    log(f"  DRIFT  {t['label']} prob → {t['prob']:.2f}  energy={e:.2f}")

# ─────────────────────────────────────────────
# Melodic momentum
# ─────────────────────────────────────────────
# FIX: previous code had a fixed bias per track. A voice with bias=+1
# always trended upward — over a long session it would reach the top
# of its register and get stuck repeating the highest available notes.
#
# update_momentum() tracks how many consecutive steps a voice has moved
# in one direction. After 3 steps the same way, it flips the bias to
# bring the melody back — creating natural arch-shaped phrases.
#
# This mirrors how real melodic writing works: ascending runs resolve
# downward, descending lines turn and rise again.
# ─────────────────────────────────────────────
def update_momentum(idx, note):
    t = tracks[idx]
    if t["last_played"] == -1:
        t["last_played"] = note
        return
    if note > t["last_played"]:
        t["momentum"] = min(3, t["momentum"] + 1)
    elif note < t["last_played"]:
        t["momentum"] = max(-3, t["momentum"] - 1)
    if t["momentum"] >= 3:
        t["bias"] = -1
        t["momentum"] = 0
        log(f"  CONTOUR {t['label']} ascending peak → bias now DOWN")
    elif t["momentum"] <= -3:
        t["bias"] = 1
        t["momentum"] = 0
        log(f"  CONTOUR {t['label']} descending trough → bias now UP")
    t["last_played"] = note

# ─────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────
midi_panic()   # Clear any hanging notes from a previous session

log("\n╔══════════════════════════════════╗")
log("║  C418-STYLE SEQUENCER  (DEV)     ║")
log("╚══════════════════════════════════╝")
log(f"Scale : {SCALE_NAMES[scale_idx]}  root={note_name(ROOT)}")
log(f"BPM   : {BPM}   energy half-cycle: {ENERGY_HALF} steps")
log("Tracks:")
for i, t in enumerate(tracks):
    reg = f"{note_name(t['low'])}–{note_name(t['high'])}"
    buf_str = " ".join(note_name(n) for n in t["buf"])
    log(f"  [{i}] {t['label']} [{reg}]  prob={t['prob']:.2f}  buf=[{buf_str}]")
    log(f"       dur={t['dur_options']}  rest={t['rest_options']}")
log("─" * 38)

# ─────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────
while True:
    now = time.monotonic()
    step_time = 60.0 / BPM

    # ── Modifier state ────────────────────────────────────────────────────
    # SHF (button 1) and ENT (button 6) are the two isolated corner keys,
    # and each now does double duty:
    #
    #   tap alone           → its original action, fired on RELEASE
    #   hold + another key  → second function; the tap action is suppressed
    #
    # SHF carries the global layer (scale / tempo preset / root), ENT the
    # mute layer. Firing on release is what makes a layer possible at all:
    # a press can no longer commit to an action before you have said which
    # one you meant.
    #
    # This retires the old "hold SHF 1.5 s for a root shift" gesture, which
    # would otherwise fire by accident every time you paused to decide
    # which second function you wanted. Root now lives on SHF + 7/8.
    shf_down = is_p("SHF")
    ent_down = is_p("ENT") and not shf_down      # SHF wins if both are held
    mod_held = shf_down or ent_down

    # ── 1. Mod wheel (latching, time-based ramp, non-blocking) ────────────
    # Hold MOD_UP / MOD_DN to sweep. Release and the value STAYS put.
    # Press BOTH mod buttons together to snap back to centre (64).
    if now - mod_last_move >= MOD_RATE:
        mod_last_move = now
        if not mod_held:
            up, dn = is_p("MOD_UP"), is_p("MOD_DN")
            if up and dn:
                mod_val = 64                              # both = re-centre
            elif up:
                mod_val = min(127, mod_val + MOD_STEP)
            elif dn:
                mod_val = max(0,   mod_val - MOD_STEP)
    if mod_val != last_mod:
        midi.send(ControlChange(74, mod_val))
        flash(COL_MOD)
        status_show("mod", STATUS_MOD)
        log(f"MOD    CC74={mod_val}")
        last_mod = mod_val

    # ── 2. Octave shift (base layer) ──────────────────────────────────────
    if not mod_held:
        if btn("OCT_UP", 0.28):
            octave = min(2, octave + 1)
            flash(COL_OCTAVE)
            status_show("oct")
            log(f"OCTAVE {octave:+d}  ({octave*12:+d} semitones)")
        if btn("OCT_DN", 0.28):
            octave = max(-2, octave - 1)
            flash(COL_OCTAVE)
            status_show("oct")
            log(f"OCTAVE {octave:+d}  ({octave*12:+d} semitones)")

    # ── 3. SHF layer — scale / tempo preset / root, or mutate on a tap ────
    if shf_down:
        if shf_held_since < 0:
            shf_held_since = now
            shf_consumed   = False
            status_show("scale", HOLD_PAGE)    # show what this layer edits
        if layer_hit("OCT_UP", set_scale,    1, COL_SCALE): shf_consumed = True
        if layer_hit("OCT_DN", set_scale,   -1, COL_SCALE): shf_consumed = True
        if layer_hit("T_UP",   bpm_preset,   1, COL_TEMPO): shf_consumed = True
        if layer_hit("T_DN",   bpm_preset,  -1, COL_TEMPO): shf_consumed = True
        if layer_hit("MOD_UP", set_root,     1, COL_SCALE): shf_consumed = True
        if layer_hit("MOD_DN", set_root,    -1, COL_SCALE): shf_consumed = True
    elif shf_held_since >= 0:
        if not shf_consumed:
            full_mutation()
            deco_all(0.9)
            flash(COL_CHAOS, duration=0.4)
            log("MUTATE tap")
        shf_held_since = -1.0
        if status_expiry > now + 2.0:
            status_expiry = now + 0.5          # retire the held page

    # ── 4. ENT layer — per-voice mutes, or reset on a tap ─────────────────
    if ent_down:
        if ent_held_since < 0:
            ent_held_since = now
            ent_consumed   = False
            status_show("mutes", HOLD_PAGE)
        if layer_hit("OCT_UP", toggle_mute,    0, COL_MUTE): ent_consumed = True
        if layer_hit("OCT_DN", toggle_mute,    1, COL_MUTE): ent_consumed = True
        if layer_hit("T_UP",   toggle_mute,    2, COL_MUTE): ent_consumed = True
        if layer_hit("T_DN",   toggle_mute,    3, COL_MUTE): ent_consumed = True
        if layer_hit("MOD_UP", mute_all,   False, COL_MUTE): ent_consumed = True
        if layer_hit("MOD_DN", mute_all,    True, COL_MUTE): ent_consumed = True
    elif ent_held_since >= 0:
        if not ent_consumed:
            midi_panic()
            octave = 0; master_step = 0; energy_ctr = 0
            for t in tracks:
                t["next_play_step"] = 0
                t["momentum"]       = 0
                t["last_played"]    = -1
                t["mute"]           = False
            deco_all(0.85)
            flash(COL_RESET, duration=0.5)
            log("RESET  Panic sent | state cleared")
        ent_held_since = -1.0
        if status_expiry > now + 2.0:
            status_expiry = now + 0.5

    # ── 5. Tempo — rate-limited repeat while held (base layer) ────────────
    # Both tempo buttons together sweeps the full status display. That
    # combination is otherwise a no-op, since up and down cancel out.
    if not mod_held:
        if is_p("T_UP") and is_p("T_DN"):
            if now - chord_last >= 0.6:
                chord_last = now
                status_sweep()
                log("STATUS sweep (tempo chord)")
        else:
            if btn("T_UP", 0.12):
                BPM = min(120, BPM + 1)
                flash(COL_TEMPO)
                status_show("bpm")
                log(f"TEMPO  {BPM} BPM  (step={60/BPM*1000:.0f}ms)")
            if btn("T_DN", 0.12):
                BPM = max(50, BPM - 1)
                flash(COL_TEMPO)
                status_show("bpm")
                log(f"TEMPO  {BPM} BPM  (step={60/BPM*1000:.0f}ms)")

    # ── 6. Sequencer tick ─────────────────────────────────────────────────
    if now >= next_tick:
        next_tick = now + step_time
        master_step += 1
        energy_ctr  += 1
        onboard_led.value = True

        energy = get_energy()

        if master_step % 16 == 0:
            bar = "█" * int(energy * 8) + "░" * (8 - int(energy * 8))
            log(f"ENERGY [{bar}] {energy:.2f}  step={master_step}")

        # Slow scale drift
        # FIX: buffer re-snap now uses nearest_in_register() not nearest_in_scale()
        if master_step % 300 == 0 and random.random() < 0.35:
            old_name  = SCALE_NAMES[scale_idx]
            scale_idx = (scale_idx + 1) % len(SCALE_NAMES)
            SCALE[:]  = build_scale(ROOT, SCALE_SHAPES[SCALE_NAMES[scale_idx]])
            for t in tracks:
                t["buf"] = [nearest_in_register(n, t["low"], t["high"]) for n in t["buf"]]
            flash(COL_SCALE, duration=0.6)
            status_show("scale")
            log(f"SCALE  {old_name} → {SCALE_NAMES[scale_idx]}")

        for i, t in enumerate(tracks):

            # ── Note off ──────────────────────────────────────────────────
            if t["note"] != -1 and master_step >= t["note_off_step"]:
                midi.send(NoteOff(t["note"], 0))
                log(f"  OFF    [{i}] {t['label']} {note_name(t['note'])}")
                t["note"] = -1
                rest = random.choice(t["rest_options"])
                t["next_play_step"] = master_step + rest
                log(f"  REST   [{i}] {t['label']} {rest}b → step {t['next_play_step']}")

            # ── Note on ───────────────────────────────────────────────────
            elif t["note"] == -1 and master_step >= t["next_play_step"]:
                if t["mute"]:
                    t["next_play_step"] = master_step + 2
                elif random.random() < t["prob"]:

                    raw          = t["buf"][t["buf_pos"]]
                    t["buf_pos"] = (t["buf_pos"] + 1) % len(t["buf"])

                    if t["buf_pos"] == 0:
                        drift_prob(i)
                        if random.random() < 0.60:
                            evolve_phrase(i, source="CYCLE")

                    note = max(0, min(127, raw + octave * 12))
                    dur  = random.choice(t["dur_options"])
                    vel  = int(t["vel_base"] * (0.88 + 0.12 * energy))
                    vel  = max(1, min(127, vel + random.randint(-5, 5)))

                    midi.send(NoteOn(note, vel))
                    t["note"]          = note
                    t["note_off_step"] = master_step + dur

                    update_momentum(i, note)   # NEW: track direction for arch phrases
                    deco_trigger(i)
                    log(f"  ON     [{i}] {t['label']} {note_name(note):5s}  vel={vel}  dur={dur}b  bias={t['bias']:+d}")

                else:
                    # FIX: SKIP_REST is now a pre-defined tuple, not a per-call list
                    t["next_play_step"] = master_step + random.choice(SKIP_REST)

        # Matrix drawing moved into draw_sequencer() so the status overlay
        # can take the display over and hand it straight back.
        if status_page is None:
            draw_sequencer()

        if master_step % 4 == 0:
            g = int(15 + 65 * energy)
            flash((0, g, int(g * 0.5)), duration=0.12)

    # ── Status overlay ────────────────────────────────────────────────────
    # Redraws only when the displayed value actually changes, so a page
    # that sits there for 1.5 s costs one I2C write, not 150.
    if status_page is not None:
        if now >= status_expiry:
            if status_queue:
                status_page   = status_queue.pop(0)
                status_expiry = now + STATUS_DUR
            else:
                status_page = None
                status_sig  = None
                draw_sequencer()      # hand the display straight back
        if status_page is not None:
            sig = status_signature(status_page)
            if sig != status_sig:
                status_sig = sig
                draw_status(status_page)

    # ── Per-loop updates ──────────────────────────────────────────────────
    update_deco()
    update_pixel()

    if now >= next_tick - step_time * 0.5:
        onboard_led.value = False

    time.sleep(0.01)

