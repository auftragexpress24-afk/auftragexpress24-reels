"""Reel aus echten Stockvideos (Pixabay) mit deutscher Sprecherstimme (Piper), Untertiteln und Abspann.

Aufruf:  python3 realreel.py spec.json ausgabe.mp4
Voraussetzungen: pip install piper-tts; Netzwerkzugriff auf pixabay.com, cdn.pixabay.com, huggingface.co;
Pixabay-Schlüssel in der Umgebungsvariable PIXABAY_KEY (oder Datei ~/.pixabay_key).
Stimmen werden beim ersten Aufruf nach ~/.cache/piper-voices geladen.

Spec (JSON):
{
  "voice": "thorsten-high" | "thorsten_emotional-medium" | "kerstin-low" | "ramona-low" | "eva_k-x_low",
  "speed": 1.0,                    # <1 = schneller, >1 = langsamer (Piper length_scale)
  "theme": {"accent": "#facc15"},
  "music": {"bpm": 112, "progression": 2, "seed": 4, "volume": 0.16},   # leiser Hintergrund-Beat
  "segments": [                    # jede Szene = ein Satz Sprecher + ein Clip
    {"say": "Text, so wie er gesprochen wird (Zahlen ausschreiben!)",
     "show": "Untertitel-Text (optional, sonst = say)",
     "headline": ["Große Zeile", "oben"],          # optional, groß oben; in Szene 1 = Hook ab Bild 1
     "clip": {"q": "painter roller wall"} | {"id": 85347} | {"pexels": 5799415},   # Pixabay-Suche/-ID oder Pexels-ID
     "pick": 0,                                    # welcher Treffer der Suche (0 = erster)
     "start": 1.5,                                 # Sekunde im Clip, ab der gezeigt wird
     "focus": 0.5,                                 # horizontaler Bildausschnitt 0 (links) .. 1 (rechts)
     "layout": "card" | "full"}                    # optional; Standard: card bei Querformat, full bei Hochformat
  ],
  "cta": {"say": "...", "lines": ["Maler gesucht?", "Wir helfen gern."], "button": "auftragexpress24.de",
          "sub": "Bis zu 3 Angebote – kostenlos", "phone": "Tel. 02133 7787688", "foot": "Link in der Bio"}
}
Ausgabe: <out>.mp4, <out>_preview.png (Kontaktbogen) und <out>_clips.json (verwendete Pixabay-Clips).
"""
import json, math, os, random, re, subprocess, sys, urllib.parse, urllib.request
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from scipy.io import wavfile
from scipy.signal import resample_poly

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audio

W, H, FPS, SR = 1080, 1920, 30, 44100
FD = "/usr/share/fonts/opentype/inter/"
CACHE = os.path.expanduser("~/.cache/auftragexpress24")
VOICES = os.path.expanduser("~/.cache/piper-voices")
VOICE_PATHS = {
    "thorsten-high": "de/de_DE/thorsten/high/de_DE-thorsten-high",
    "thorsten_emotional-medium": "de/de_DE/thorsten_emotional/medium/de_DE-thorsten_emotional-medium",
    "kerstin-low": "de/de_DE/kerstin/low/de_DE-kerstin-low",
    "ramona-low": "de/de_DE/ramona/low/de_DE-ramona-low",
    "eva_k-x_low": "de/de_DE/eva_k/x_low/de_DE-eva_k-x_low",
}
_fc = {}


def F(w, s):
    k = (w, int(s))
    if k not in _fc: _fc[k] = ImageFont.truetype(FD + f"Inter-{w}.otf", max(8, int(s)))
    return _fc[k]


def hexc(h): h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def ease_out(x): x = clamp(x); return 1 - (1 - x) ** 3
def back_out(x): x = clamp(x); c = 1.7; return max(0.01, 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2)


UA = {"User-Agent": "AuftragExpress24-Reels/1.0"}


def get(url, timeout=60):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)


def fetch(url, path):
    if os.path.exists(path) and os.path.getsize(path) > 0: return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with get(url, 300) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b: break
            f.write(b)
    os.replace(tmp, path)
    return path


# ------------------------------------------------------------ Stimme
def voice_model(name):
    rel = VOICE_PATHS.get(name, name)
    base = os.path.join(VOICES, os.path.basename(rel))
    for ext in (".onnx", ".onnx.json"):
        fetch(f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{rel}{ext}", base + ext)
    return base + ".onnx"


_V = {}


def tts(text, voice, speed):
    from piper import PiperVoice
    try:
        from piper import SynthesisConfig
    except ImportError:
        SynthesisConfig = None
    if voice not in _V: _V[voice] = PiperVoice.load(voice_model(voice))
    v = _V[voice]
    chunks = []
    if SynthesisConfig is not None:
        cfg = SynthesisConfig(length_scale=speed)
        for ch in v.synthesize(text, syn_config=cfg):
            chunks.append(ch.audio_float_array.astype(np.float32))
            chunks.append(np.zeros(int(0.12 * v.config.sample_rate), np.float32))
        x = np.concatenate(chunks[:-1]) if chunks else np.zeros(1, np.float32)
    else:  # ältere piper-Version
        import io, wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf: v.synthesize(text, wf, length_scale=speed)
        buf.seek(0); sr_, x = wavfile.read(buf); x = x.astype(np.float32) / 32768
    sr = v.config.sample_rate
    idx = np.where(np.abs(x) > 0.02 * max(1e-6, np.abs(x).max()))[0]
    if len(idx): x = x[max(0, idx[0] - int(0.02 * sr)): idx[-1] + int(0.08 * sr)]
    y = resample_poly(x, SR, sr).astype(np.float32)
    return y / max(1e-6, np.abs(y).max()) * 0.92


# ------------------------------------------------------------ Clips (Pixabay)
def pixabay_key():
    k = os.environ.get("PIXABAY_KEY")
    if not k and os.path.exists(os.path.expanduser("~/.pixabay_key")):
        k = open(os.path.expanduser("~/.pixabay_key")).read().strip()
    if not k: raise SystemExit("PIXABAY_KEY fehlt")
    return k


def search(q, per_page=30):
    path = os.path.join(CACHE, "search", re.sub(r"\W+", "_", q) + ".json")
    if not os.path.exists(path):
        url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode(
            dict(key=pixabay_key(), q=q, per_page=per_page, safesearch="true", video_type="film"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = get(url).read()
        open(path, "wb").write(data)
    return json.load(open(path))["hits"]


def by_id(i):
    path = os.path.join(CACHE, "search", f"id_{i}.json")
    if not os.path.exists(path):
        url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode(dict(key=pixabay_key(), id=i))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").write(get(url).read())
    hits = json.load(open(path))["hits"]
    if not hits: raise SystemExit(f"Pixabay-ID {i} nicht gefunden")
    return hits[0]


def probe_wh(path):
    o = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip().split(",")
    return int(o[0]), int(o[1])


def resolve_clip(c, pick=0, need=3.0):
    if "pexels" in c:   # Pexels-Video über den normalen Download-Link (Pexels-Lizenz: frei, auch gewerblich)
        pid = int(c["pexels"])
        path = os.path.join(CACHE, "clips", f"pexels_{pid}.mp4")
        fetch(f"https://www.pexels.com/download/video/{pid}/", path)
        w, h = probe_wh(path)
        hit = {"id": f"pexels-{pid}", "pageURL": f"https://www.pexels.com/video/{pid}/", "user": "", "tags": "",
               "videos": {"medium": {"width": w, "height": h}}}
        return path, hit
    if "pexels_photo" in c:   # Pexels-Foto als ruhige Kamerafahrt (langsamer Zoom), 8 Sekunden
        pid = int(c["pexels_photo"])
        img = os.path.join(CACHE, "photos", f"pexels_{pid}.jpg")
        fetch(f"https://images.pexels.com/photos/{pid}/pexels-photo-{pid}.jpeg?auto=compress&w=3000", img)
        path = os.path.join(CACHE, "clips", f"pexels_photo_{pid}.mp4")
        if not os.path.exists(path):
            from PIL import Image as _I
            w0, h0 = _I.open(img).size
            ow, oh = (1920, int(1920 * h0 / w0) // 2 * 2) if w0 >= h0 else (int(1920 * w0 / h0) // 2 * 2, 1920)
            vf = (f"scale={ow * 2}:-2,zoompan=z='min(1+0.0009*on,1.2)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                  f":d={8 * FPS}:s={ow}x{oh}:fps={FPS}")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", img, "-vf", vf, "-frames:v", str(8 * FPS),
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", path], check=True)
        w, h = probe_wh(path)
        hit = {"id": f"pexels-photo-{pid}", "pageURL": f"https://www.pexels.com/photo/{pid}/", "user": "", "tags": "",
               "videos": {"medium": {"width": w, "height": h}}}
        return path, hit
    if "id" in c: hit = by_id(c["id"])
    else:
        hits = [h for h in search(c["q"]) if h.get("duration", 0) >= min(need, 6)] or search(c["q"])
        if not hits: raise SystemExit(f"Keine Clips für '{c['q']}'")
        hit = hits[pick % len(hits)]
    v = hit["videos"]
    best = next((v[k] for k in ("large", "medium", "small") if v.get(k, {}).get("url")), None)
    path = os.path.join(CACHE, "clips", f"{hit['id']}_{best['width']}x{best['height']}.mp4")
    fetch(best["url"], path)
    return path, hit


FG_Y, FG_H = 470, 1000   # Bereich des scharfen Clips im Hochformat (Layout "card")


def clip_frames(path, start, n, motion=1, focus=0.5, layout=None, portrait=False):
    """Liefert n Bilder 1080x1920.
    layout "card" (Standard bei Querformat): Clip groß in der Mitte, dahinter unscharf als Hintergrund.
    layout "full" (Standard bei Hochformat-Clips): bildfüllend zugeschnitten. focus = horizontale Bildmitte (0..1)."""
    layout = layout or ("full" if portrait else "card")
    m = motion * 18
    if layout == "full":
        hh = int(H * 1.08) // 2 * 2
        fc = (f"[0:v]fps={FPS},scale=-2:{hh}:flags=lanczos,"
              f"crop={W}:{H}:'max(0,min(in_w-{W},(in_w-{W})*{focus}+{m}*(t-1)))':'(in_h-{H})/2'[v]")
    else:
        fc = (f"[0:v]fps={FPS},split[a][b];"
              f"[a]scale=-2:{H},crop={W}:{H},boxblur=28:2,eq=brightness=-0.12:saturation=1.1[bg];"
              f"[b]scale=-2:{FG_H}:flags=lanczos,crop={W}:{FG_H}:'max(0,min(in_w-{W},(in_w-{W})*{focus}+{m}*(t-1)))':0[fg];"
              f"[bg][fg]overlay=0:{FG_Y}[v]")
    cmd = ["ffmpeg", "-loglevel", "error", "-stream_loop", "-1", "-ss", str(start), "-i", path,
           "-filter_complex", fc, "-map", "[v]", "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)
    if len(fr) < n: fr = np.concatenate([fr, np.repeat(fr[-1:], n - len(fr), 0)])
    return fr


# ------------------------------------------------------------ Text
def draw_text(d, xy, text, font, fill, stroke=8, sfill=(0, 0, 0), anchor="mm"):
    d.text(xy, text, font=font, fill=fill, stroke_width=stroke, stroke_fill=sfill, anchor=anchor)


def fit(text, w, size, maxw=980):
    while size > 30 and F(w, size).getlength(text) > maxw: size -= 4
    return F(w, size)


def chunk_words(text, maxc=22):
    words = text.split(); out = []; cur = []
    for w in words:
        if cur and (len(" ".join(cur + [w])) > maxc or re.search(r"[.!?,;:]$", cur[-1])):
            out.append(cur); cur = []
        cur.append(w)
    if cur: out.append(cur)
    return out


class Reel:
    def __init__(self, spec):
        self.spec = spec
        self.acc = hexc(spec.get("theme", {}).get("accent", "#facc15"))
        self.voice = spec.get("voice", "thorsten-high"); self.speed = spec.get("speed", 1.0)
        self.parts = []   # (art, seg, audio, dauer)
        for s in spec["segments"]:
            a = tts(s["say"], self.voice, self.speed)
            self.parts.append(("seg", s, a, max(1.3, len(a) / SR + 0.22)))
        c = spec["cta"]
        a = tts(c["say"], self.voice, self.speed)
        self.parts.append(("cta", c, a, max(3.2, len(a) / SR + 0.9)))
        self.T = sum(p[3] for p in self.parts)
        self.credits = []

    def overlay_seg(self, im, s, t, dur, first):
        # Verlauf oben/unten für Lesbarkeit (einmal berechnet)
        if not hasattr(self, "_grad"):
            g = Image.new("L", (1, H))
            for y in range(H):
                k = max(0, 1 - y / FG_Y) * 110 + max(0, (y - FG_Y - FG_H) / (H - FG_Y - FG_H)) * 120
                g.putpixel((0, y), int(min(200, k)))
            self._grad = g.resize((W, H))
        im.paste((0, 0, 0), (0, 0, W, H), self._grad)
        d = ImageDraw.Draw(im, "RGBA")
        hl = s.get("headline")
        if hl:
            for i, ln in enumerate(hl):
                t0 = 0 if first else i * 0.18
                if t < t0: continue
                k = 1.0 if first else back_out((t - t0) / 0.3)
                f = fit(ln, "Black", int(118 * (0.6 + 0.4 * k)))
                draw_text(d, (W / 2, 250 + i * 118), ln, f, (255, 255, 255) if i else self.acc, 10)
        # Untertitel (Karaoke): Wörter proportional zur Sprechzeit
        text = s.get("show", s["say"])
        chunks = chunk_words(text)
        words = [w for ch in chunks for w in ch]
        speak = max(0.4, dur - 0.22)
        lens = np.array([len(w) + 2 for w in words], float); cum = np.cumsum(lens) / lens.sum() * speak
        wi = int(np.searchsorted(cum, t)); wi = min(wi, len(words) - 1)
        k = 0; ci = 0
        for j, ch in enumerate(chunks):
            if wi < k + len(ch): ci = j; break
            k += len(ch)
        ch = chunks[ci]; start_w = k
        f = F("Black", 76)
        widths = [f.getlength(w + " ") for w in ch]; tot = sum(widths)
        x = W / 2 - tot / 2; y = 1610
        for j, w in enumerate(ch):
            on = start_w + j <= wi
            col = self.acc if start_w + j == wi else ((255, 255, 255) if on else (230, 230, 230))
            sc = 1.0
            draw_text(d, (x, y), w, f, col, 9, anchor="lm")
            x += widths[j]

    def overlay_cta(self, im, c, t):
        d = ImageDraw.Draw(im, "RGBA")
        ink = (15, 23, 42)
        for i, ln in enumerate(c.get("lines", [])):
            t0 = i * 0.25
            if t < t0: continue
            k = back_out((t - t0) / 0.3)
            f = fit(ln, "Black", int(112 * (0.5 + 0.5 * k)))
            draw_text(d, (W / 2, 520 + i * 135), ln, f, (255, 255, 255) if i else self.acc, 10)
        if t >= 0.5:
            k = back_out((t - 0.5) / 0.3); pulse = k * (1 + 0.03 * math.sin((t - 0.5) * 6))
            bw, bh = 900 * pulse, 170 * pulse; cy = 900
            d.rounded_rectangle([W / 2 - bw / 2, cy - bh / 2, W / 2 + bw / 2, cy + bh / 2], int(bh / 2), fill=self.acc)
            draw_text(d, (W / 2, cy), c.get("button", "auftragexpress24.de"), fit(c.get("button", ""), "Black", int(76 * pulse), bw - 90), ink, 0)
        rows = [(c.get("sub"), "ExtraBold", 60, (255, 255, 255), 1080), (c.get("phone"), "Black", 74, (255, 255, 255), 1190),
                (c.get("foot"), "Bold", 56, self.acc, 1300)]
        for i, (txt, w, sz, col, y) in enumerate(rows):
            t0 = 0.9 + i * 0.3
            if not txt or t < t0: continue
            draw_text(d, (W / 2, y), txt, fit(txt, w, int(sz * back_out((t - t0) / 0.3))), col, 7)

    def header(self, im, t):
        d = ImageDraw.Draw(im, "RGBA")
        draw_text(d, (W / 2, 118), "AuftragExpress24", F("Bold", 46), (255, 255, 255), 5)
        d.rounded_rectangle([80, 52, W - 80, 62], 5, fill=(255, 255, 255, 70))
        d.rounded_rectangle([80, 52, 80 + int((W - 160) * t / self.T), 62], 5, fill=self.acc)

    def make(self, out):
        frames_total = int(self.T * FPS)
        # Audio: Stimme + leiser Beat
        voice = np.zeros(int(self.T * SR) + SR, np.float32)
        tpos = 0.0; cuts = []
        for kind, s, a, dur in self.parts:
            i = int((tpos + (0.12 if kind == "seg" else 0.35)) * SR); voice[i:i + len(a)] += a[:len(voice) - i]
            cuts.append(tpos); tpos += dur
        m = self.spec.get("music", {})
        wav = out + ".music.wav"
        audio.build(self.T, [(c, "whoosh") for c in cuts[1:]], wav, m.get("bpm", 110), m.get("progression", 0), m.get("seed", 0))
        sr_, mus = wavfile.read(wav); os.remove(wav)
        mus = mus.astype(np.float32); mus = mus / max(1e-6, np.abs(mus).max())
        if mus.ndim > 1: mus = mus.mean(1)
        mus = np.pad(mus, (0, max(0, len(voice) - len(mus))))[:len(voice)]
        env = np.convolve(np.abs(voice), np.ones(4410) / 4410, mode="same")
        duck = 1 - 0.6 * np.clip(env / 0.05, 0, 1)
        mix = voice + mus * m.get("volume", 0.16) * duck
        mix = np.tanh(mix * 1.1) * 0.95
        mix = mix[:int(self.T * SR)]
        wfile = out + ".wav"; wavfile.write(wfile, SR, (mix * 32767).astype(np.int16))
        # Video
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
               "-i", "-", "-i", wfile, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out]
        pr = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        self.preview_imgs = []
        done = 0; tpos = 0.0; last_clip = None
        for pi, (kind, s, a, dur) in enumerate(self.parts):
            n = int(round((tpos + dur) * FPS)) - done
            n = min(n, frames_total - done)
            if kind == "seg":
                path, hit = resolve_clip(s["clip"], s.get("pick", 0), dur)
                self.credits.append(dict(id=hit["id"], url=hit["pageURL"], user=hit["user"], tags=hit["tags"]))
                portrait = hit["videos"]["medium"].get("height", 0) > hit["videos"]["medium"].get("width", 1)
                fr = clip_frames(path, s.get("start", 0.5), n, 1 if pi % 2 == 0 else -1, s.get("focus", 0.5),
                                 s.get("layout"), portrait)
                last_clip = fr[-1]
            else:
                bg = Image.fromarray(last_clip if last_clip is not None else np.zeros((H, W, 3), np.uint8))
                bg = bg.filter(ImageFilter.GaussianBlur(28))
                bg = Image.blend(bg, Image.new("RGB", (W, H), (10, 14, 30)), 0.55)
                fr = None
            for f in range(n):
                tl = f / FPS; tg = (done + f) / FPS
                im = Image.fromarray(fr[f]) if fr is not None else bg.copy()
                if kind == "seg": self.overlay_seg(im, s, tl, dur, pi == 0)
                else: self.overlay_cta(im, s, tl)
                # kurzer Blitz beim Schnitt
                if pi > 0 and tl < 0.08:
                    im = Image.blend(im, Image.new("RGB", (W, H), (255, 255, 255)), 0.35 * (1 - tl / 0.08))
                self.header(im, tg)
                if f == n - 1 or (pi == 0 and f == 0):
                    self.preview_imgs.append(im.resize((270, 480)))
                pr.stdin.write(im.tobytes())
            done += n; tpos += dur
        pr.stdin.close(); pr.wait(); os.remove(wfile)
        if pr.returncode: raise SystemExit("ffmpeg fehlgeschlagen")
        sheet = Image.new("RGB", (270 * len(self.preview_imgs), 480))
        for i, p in enumerate(self.preview_imgs): sheet.paste(p, (270 * i, 0))
        sheet.save(out.rsplit(".", 1)[0] + "_preview.png")
        json.dump(self.credits, open(out.rsplit(".", 1)[0] + "_clips.json", "w"), ensure_ascii=False, indent=1)
        print(f"ok {out} ({self.T:.1f}s)")


if __name__ == "__main__":
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    Reel(spec).make(sys.argv[2])
