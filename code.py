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
PIN_MAP = {
    "MOD_UP": "GP20", "MOD_DN": "GP18",
    "OCT_UP": "GP16", "OCT_DN": "GP22",
    "SHF":    "GP21", "ENT":    "GP19",
    "T_UP":   "GP26", "T_DN":   "GP17",
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

# Tonal centres used by root_shift() on extended mutation hold.
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

# Mutation button hold-state
# shf_held_since : time.monotonic() when SHF was first pressed (-1 = not held)
# shf_fired      : True once the initial press action has run this hold
# shf_root_fired : time of last root shift during this hold (avoids repeat)
shf_held_since = -1.0
shf_fired      = False
shf_root_fired = -1.0

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
    t["buf"] = [nearest_in_register(n, t["low"], t["high"]) for n in t["buf"]]

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


def root_shift():
    """
    Advance to the next tonal centre in ROOT_CYCLE and re-snap all buffers.
    Called when SHF is held for 1.5 s — a second, deeper layer of change
    on top of the phrase mutation that already happened on press.

    ROOT_CYCLE = C3, F3, G3, A3, E3 (I IV V vi iii of C major).
    All are harmonically related, so the shift always sounds intentional.
    """
    global ROOT, root_cycle_idx
    root_cycle_idx = (root_cycle_idx + 1) % len(ROOT_CYCLE)
    ROOT = ROOT_CYCLE[root_cycle_idx]
    SCALE[:] = build_scale(ROOT, SCALE_SHAPES[SCALE_NAMES[scale_idx]])
    for t in tracks:
        t["buf"] = [nearest_in_register(n, t["low"], t["high"]) for n in t["buf"]]
    log(f"ROOT SHIFT → {note_name(ROOT)}  (cycle pos {root_cycle_idx})")


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

    # ── 1. Mod wheel (latching, time-based ramp, non-blocking) ────────────
    # Hold MOD_UP / MOD_DN to sweep. Release and the value STAYS put.
    # Press BOTH mod buttons together to snap back to centre (64).
    if now - mod_last_move >= MOD_RATE:
        mod_last_move = now
        up, dn = is_p("MOD_UP"), is_p("MOD_DN")
        if up and dn:
            mod_val = 64                                  # both = re-centre
        elif up:
            mod_val = min(127, mod_val + MOD_STEP)
        elif dn:
            mod_val = max(0,   mod_val - MOD_STEP)
    if mod_val != last_mod:
        midi.send(ControlChange(74, mod_val))
        flash(COL_MOD)
        log(f"MOD    CC74={mod_val}")
        last_mod = mod_val

    # ── 2. Octave shift ───────────────────────────────────────────────────
    # FIX: was time.sleep(0.25). Now rate-limited via btn() — no blocking.
    if btn("OCT_UP", 0.28):
        octave = min(2, octave + 1)
        flash(COL_OCTAVE)
        log(f"OCTAVE {octave:+d}  ({octave*12:+d} semitones)")
    if btn("OCT_DN", 0.28):
        octave = max(-2, octave - 1)
        flash(COL_OCTAVE)
        log(f"OCTAVE {octave:+d}  ({octave*12:+d} semitones)")

    # ── 3. Reset ──────────────────────────────────────────────────────────
    # FIX: was time.sleep(0.3) + partial note cleanup.
    # Now uses midi_panic() and a 0.5s guard to prevent accidental double-fire.
    if btn("ENT", 0.5):
        midi_panic()
        octave = 0; master_step = 0; energy_ctr = 0
        for t in tracks:
            t["next_play_step"] = 0
            t["momentum"]       = 0
            t["last_played"]    = -1
        deco_all(0.85)
        flash(COL_RESET, duration=0.5)
        log("RESET  Panic sent | state cleared")

    # ── 4. Mutation (two-tier hold behaviour) ────────────────────────────
    #
    #   PRESS  (fires once on first detection):
    #     → Full phrase mutation: cuts all notes, rebuilds all 4 buffers
    #       from random seeds using bold voice leading, shakes up density.
    #       Effect is immediate and audible on the very next beat.
    #
    #   HOLD 1.5 s (fires once per 2 s while still held):
    #     → Root shift: advances tonal centre through ROOT_CYCLE.
    #       A second layer of change on top of the phrase mutation.
    #       Pixel flashes gold to signal the shift.
    #
    if is_p("SHF"):
        if shf_held_since < 0:
            shf_held_since = now
            shf_fired      = False
        held = now - shf_held_since

        if not shf_fired:
            # First detection — fire the full mutation immediately
            shf_fired = True
            full_mutation()
            deco_all(0.9)
            flash(COL_CHAOS, duration=0.4)
            log(f"MUTATE press  held={held:.2f}s")

        elif held > 1.5 and now - shf_root_fired > 2.0:
            # Extended hold — shift tonal centre every 2 s
            shf_root_fired = now
            root_shift()
            flash(COL_SCALE, duration=0.4)
            log(f"MUTATE root-shift  held={held:.2f}s  root={note_name(ROOT)}")
    else:
        shf_held_since = -1.0
        shf_fired      = False

    # ── 5. Tempo — rate-limited repeat while held ─────────────────────────
    # FIX: was time.sleep(0.1). Now repeats every 0.12s via btn() — same
    # feel, no blocking.
    if btn("T_UP", 0.12):
        BPM = min(120, BPM + 1)
        flash(COL_TEMPO)
        log(f"TEMPO  {BPM} BPM  (step={60/BPM*1000:.0f}ms)")
    if btn("T_DN", 0.12):
        BPM = max(50, BPM - 1)
        flash(COL_TEMPO)
        log(f"TEMPO  {BPM} BPM  (step={60/BPM*1000:.0f}ms)")

    # ── 6. Sequencer tick ─────────────────────────────────────────────────
    if now >= next_tick:
        next_tick = now + step_time
        master_step += 1
        energy_ctr  += 1
        onboard_led.value = True
        mx.fill(0)

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
                if random.random() < t["prob"]:

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

            # ── Matrix: buf_pos dot + note duration countdown bar ─────────
            r = i * 2
            mx[t["buf_pos"] % 8, r] = 1
            if t["note"] != -1:
                cols = min(max(0, t["note_off_step"] - master_step), 8)
                for c in range(cols):
                    mx[c, r + 1] = 1

        mx.show()

        if master_step % 4 == 0:
            g = int(15 + 65 * energy)
            flash((0, g, int(g * 0.5)), duration=0.12)

    # ── Per-loop updates ──────────────────────────────────────────────────
    update_deco()
    update_pixel()

    if now >= next_tick - step_time * 0.5:
        onboard_led.value = False

    time.sleep(0.01)

