"""Synthetisiert einen lizenzfreien Beat (Tempo/Akkorde variabel) mit Effekten für ein Reel."""
import numpy as np
from scipy.io import wavfile

SR = 44100
BPM = 120
BEAT = 60 / BPM


def env(n, a=0.002, d=0.2):
    t = np.arange(n) / SR
    e = np.exp(-t / d)
    na = max(1, int(a * SR))
    e[:na] *= np.linspace(0, 1, na)
    return e


def kick():
    n = int(0.45 * SR); t = np.arange(n) / SR
    f = 50 + 110 * np.exp(-t * 28)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * env(n, 0.001, 0.16) * 1.0 + np.random.randn(n) * env(n, 0.0005, 0.004) * 0.3


def clap():
    n = int(0.25 * SR)
    noise = np.random.randn(n)
    from scipy.signal import butter, lfilter
    b, a = butter(2, [900 / (SR / 2), 4000 / (SR / 2)], "band")
    x = lfilter(b, a, noise)
    e = env(n, 0.001, 0.07)
    for off in (0.0, 0.012, 0.024):
        k = int(off * SR); e[k:k + 200] += 0.6
    return x * e * 0.9


def hat(open_=False):
    n = int((0.18 if open_ else 0.05) * SR)
    from scipy.signal import butter, lfilter
    b, a = butter(4, 7000 / (SR / 2), "high")
    return lfilter(b, a, np.random.randn(n)) * env(n, 0.0005, 0.06 if open_ else 0.015) * 0.35


def tone(freq, dur, wave="saw", a=0.005, d=0.3, vol=0.2):
    n = int(dur * SR); t = np.arange(n) / SR
    if wave == "saw":
        x = sum(np.sin(2 * np.pi * freq * k * t) / k for k in range(1, 12))
    elif wave == "square":
        x = sum(np.sin(2 * np.pi * freq * k * t) / k for k in range(1, 12, 2))
    else:
        x = np.sin(2 * np.pi * freq * t)
    return x * env(n, a, d) * vol


def whoosh(dur=0.45, up=True):
    n = int(dur * SR)
    from scipy.signal import butter, sosfilt
    x = np.random.randn(n)
    out = np.zeros(n); seg = 512
    for i in range(0, n, seg):
        p = i / n if up else 1 - i / n
        fc = 300 + 6000 * p ** 2
        sos = butter(2, [fc * 0.7 / (SR / 2), min(fc * 1.4, 20000) / (SR / 2)], "band", output="sos")
        out[i:i + seg] = sosfilt(sos, x[i:i + seg])
    e = np.sin(np.linspace(0, np.pi, n)) ** 2
    return out * e * 0.5


def pop():
    n = int(0.09 * SR); t = np.arange(n) / SR
    f = 600 + 900 * np.exp(-t * 60)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, 0.001, 0.03) * 0.35


def riser(dur):
    n = int(dur * SR); t = np.arange(n) / SR
    f = 200 * (8 ** (t / dur))
    return (np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.08 + np.random.randn(n) * 0.04) * (t / dur) ** 2


def note(name):
    names = {"A": 0, "B": 2, "C": 3, "D": 5, "E": 7, "F": 8, "G": 10}
    return 55 * 2 ** (names[name] / 12)


PROGRESSIONS = [
    ["A", "F", "C", "G"], ["C", "G", "A", "F"], ["D", "B", "G", "A"],
    ["E", "C", "G", "D"], ["F", "D", "B", "C"], ["G", "E", "C", "D"],
]


def build(total, events, out, bpm=120, progression=0, seed=0):
    """events: Liste von (zeit_s, art) mit art in whoosh|pop|riser:<dauer>|drop|stop."""
    global BEAT
    BEAT = 60 / bpm
    np.random.seed(seed)
    n = int(total * SR) + SR
    mus = np.zeros(n); sfx = np.zeros(n)

    def add(buf, x, t):
        i = int(t * SR)
        if i < 0:
            x = x[-i:]; i = 0
        j = min(n, i + len(x))
        if j > i:
            buf[i:j] += x[: j - i]

    stops = [t for t, k in events if k == "stop"]
    drop = next((t for t, k in events if k == "drop"), 0.0)
    prog = PROGRESSIONS[progression % len(PROGRESSIONS)]
    beats = int(total / BEAT)
    side = np.ones(n)
    for b in range(beats):
        t = b * BEAT
        if any(s <= t < s + BEAT for s in stops):
            continue
        full = t >= drop
        add(mus, kick() * (1.0 if full else 0.7), t)
        i = int(t * SR); k = int(0.18 * SR)
        side[i:i + k] = np.minimum(side[i:i + k], 0.35 + 0.65 * np.linspace(0, 1, k))
        if full and b % 2 == 1:
            add(mus, clap(), t)
        for h in range(2):
            add(mus, hat(open_=(h == 1 and b % 4 == 3)), t + h * BEAT / 2)
        root = note(prog[(b // 4) % 4])
        for e8 in range(2):
            add(mus, tone(root * (2 if e8 else 1), BEAT / 2, "square", d=0.12, vol=0.16), t + e8 * BEAT / 2)
        if full and b % 4 == 0:
            r = root * 4
            for iv in (1, 1.189, 1.498):
                add(mus, tone(r * iv, BEAT * 4, "saw", a=0.02, d=1.2, vol=0.035), t)
        if full and b % 2 == 0:
            seq = [1, 1.498, 2, 1.782]
            add(mus, tone(root * 8 * seq[(b // 2) % 4], BEAT / 2, "square", d=0.08, vol=0.05), t + BEAT / 2)
    # Sidechain auf Pad/Bass anwenden
    mus *= side
    for t, k in events:
        if k == "whoosh": add(sfx, whoosh(), max(0, t - 0.3))
        elif k == "pop": add(sfx, pop(), t)
        elif k.startswith("riser"):
            d = float(k.split(":")[1]); add(sfx, riser(d), t - d)
    mix = mus * 0.8 + sfx
    mix = mix[: int(total * SR)]
    fade = int(0.4 * SR); mix[-fade:] *= np.linspace(1, 0, fade)
    mix = np.tanh(mix * 1.4)
    mix = mix / np.max(np.abs(mix)) * 0.89
    st = np.stack([mix, mix], 1)
    wavfile.write(out, SR, (st * 32767).astype(np.int16))
