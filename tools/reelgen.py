"""Erzeugt ein dynamisches 9:16-Reel (1080x1920, 30 fps, mit Beat) aus einer JSON-Beschreibung.

Aufruf:  python3 reelgen.py spec.json ausgabe.mp4

Szenentypen (jede Szene hat "beats" = Länge in Taktschlägen):
  hook       lines[1-3], sub            – großer Einstieg mit Wackeln + Pinselstrich
  statement  lines[1-3], highlight      – Aussage Zeile für Zeile, Highlight in Akzentfarbe
  bignumber  number, lines[1-2], small  – riesige Zahl auf Akzent-Hintergrund
  list       title, items[2-4]          – Häkchen-Liste, fliegt im Takt rein
  steps      title, items[2-3]          – nummerierte Schritte 1-2-3
  versus     bad_title, bad, good_title, good – ❌ vs ✅ Gegenüberstellung
  ticker     label, words[3-8]          – Wörter wechseln im Halbtakt, letztes bleibt
  cta        lines[1-2], button, sub, phone, foot – Abschluss mit pulsierendem Button
Optionale Felder: bpm (105-128), progression (0-5), seed, theme{accent,dark,dark2,light}, drop_scene (Index).
"""
import json, math, os, shutil, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audio

W, H, FPS = 1080, 1920, 30
FD = "/usr/share/fonts/opentype/inter/"
SYM = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
MAXW = 940
_fc = {}


def F(w, s):
    k = (w, int(s))
    if k not in _fc:
        _fc[k] = ImageFont.truetype(FD + f"Inter-{w}.otf", max(8, int(s)))
    return _fc[k]


def hexc(h): h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
def clamp(x): return max(0.0, min(1.0, x))
def ease_out(x): x = clamp(x); return 1 - (1 - x) ** 3
def back_out(x): x = clamp(x); c = 1.9; return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def fit(text, w, size, maxw=MAXW):
    while size > 30 and F(w, size).getlength(text) > maxw:
        size -= 4
    return F(w, size)


class Reel:
    def __init__(self, spec):
        self.spec = spec
        th = spec.get("theme", {})
        self.ACC = hexc(th.get("accent", "#f59e0b"))
        self.D1 = hexc(th.get("dark", "#0f172a"))
        self.D2 = hexc(th.get("dark2", "#1e293b"))
        self.LIGHT = hexc(th.get("light", "#ffffff"))
        self.MUTED = (203, 213, 225)
        self.bpm = spec.get("bpm", 120)
        self.B = 60 / self.bpm
        self.scenes = []
        beat = 0
        for i, s in enumerate(spec["scenes"]):
            bg = {"bignumber": self.ACC, "cta": self.LIGHT}.get(s["type"], self.D1 if i % 2 == 0 else self.D2)
            self.scenes.append((beat, beat + s["beats"], bg, s))
            beat += s["beats"]
        self.total_beats = beat
        self.TOTAL = beat * self.B
        di = spec.get("drop_scene", 2)
        self.DROP = self.scenes[min(di, len(self.scenes) - 1)][0] * self.B
        self.events = []

    # ---------- Bausteine ----------
    def pop(self, c, text, font, color, cx, cy, t, t0, fs=0.4, dur=0.28):
        if t < t0: return
        p = (t - t0) / dur
        s = fs + (1 - fs) * back_out(p)
        a = ease_out(p * 1.6)
        l, tp, r, b = font.getbbox(text)
        lay = Image.new("RGBA", (r - l + 40, b - tp + 40), (0, 0, 0, 0))
        ImageDraw.Draw(lay).text((20 - l, 20 - tp), text, font=font, fill=color)
        if s != 1:
            lay = lay.resize((max(1, int(lay.width * s)), max(1, int(lay.height * s))), Image.LANCZOS)
        if a < 1:
            lay.putalpha(lay.getchannel("A").point(lambda v: int(v * a)))
        c.alpha_composite(lay, (int(cx - lay.width / 2), int(cy - lay.height / 2)))

    def brush(self, d, y0, y1, p, color, seed=0):
        xe = -60 + (W + 120) * ease_out(p)
        pts = [(-60, y0)]
        for i in range(41):
            x = -60 + (xe + 60) * i / 40
            pts.append((x, y0 + 10 * math.sin(i * 1.7 + seed) + 6 * math.sin(i * 3.1)))
        for i in range(12):
            pts.append((xe + 25 * math.sin(i * 2.3 + seed), y0 + (y1 - y0) * i / 11))
        for i in range(40, -1, -1):
            x = -60 + (xe + 60) * i / 40
            pts.append((x, y1 + 10 * math.sin(i * 1.3 + seed) + 6 * math.cos(i * 2.7)))
        d.polygon(pts, fill=color)

    def lines(self, c, t, lines, w, size, color, y0, gap, start=0.0, step=None, fs=0.4):
        step = self.B if step is None else step
        for i, ln in enumerate(lines):
            f = fit(ln, w, size)
            self.pop(c, ln, f, color, W / 2, y0 + i * gap, t, start + i * step, fs)
        return y0 + len(lines) * gap

    # ---------- Szenen ----------
    def s_hook(self, c, t, s):
        B = self.B; ls = s["lines"]
        size = 210 if len(ls) <= 2 else 170
        gap = int(size * 0.98)
        y0 = 820 - gap * (len(ls) - 1) / 2
        end = self.lines(c, t, ls, "Black", size, self.LIGHT, y0, gap, fs=2.2)
        if t >= len(ls) * B:
            self.brush(ImageDraw.Draw(c), end - gap / 2 + 40, end - gap / 2 + 80, (t - len(ls) * B) / 0.3, self.ACC, 1)
        if s.get("sub"):
            self.pop(c, s["sub"], fit(s["sub"], "Bold", 62), self.ACC, W / 2, end + 120, t, (len(ls) + 0.5) * B)

    def s_statement(self, c, t, s):
        B = self.B; ls = s["lines"]
        end = self.lines(c, t, ls, "ExtraBold", 110, self.MUTED, 760 - 70 * (len(ls) - 1), 140)
        if s.get("highlight"):
            self.pop(c, s["highlight"], fit(s["highlight"], "Black", 124), self.ACC, W / 2, end + 110, t, len(ls) * B, 1.8)

    def s_bignumber(self, c, t, s):
        B = self.B
        self.pop(c, s["number"], fit(s["number"], "Black", 700), self.D1, W / 2, 760, t, 0.0, 3.5, 0.35)
        end = self.lines(c, t, s.get("lines", []), "Black", 130, self.LIGHT if self.ACC != self.LIGHT else self.D1, 1200, 140, 2 * B)
        if s.get("small"):
            self.pop(c, s["small"], fit(s["small"], "Bold", 54), self.D1, W / 2, end + 30, t, (2 + len(s.get("lines", []))) * B)

    def _items(self, c, t, items, symbol_fn, y0=820, gap=170):
        B = self.B; d = ImageDraw.Draw(c)
        for i, it in enumerate(items):
            t0 = (1 + i) * B
            if t < t0: continue
            y = y0 + i * gap
            x = -400 + (110 + 400) * back_out((t - t0) / 0.28)
            symbol_fn(d, x, y, i)
            d.text((x + 125, y), it, font=fit(it, "Bold", 68, MAXW - 140), fill=self.LIGHT, anchor="lm")

    def s_list(self, c, t, s):
        self.pop(c, s["title"], fit(s["title"], "Black", 120), self.LIGHT, W / 2, 560, t, 0.0)
        def sym(d, x, y, i):
            d.ellipse([x, y - 45, x + 90, y + 45], fill=self.ACC)
            d.text((x + 45, y), "✓", font=ImageFont.truetype(SYM, 60), fill=self.D1, anchor="mm")
        self._items(c, t, s["items"], sym)

    def s_steps(self, c, t, s):
        self.pop(c, s["title"], fit(s["title"], "Black", 120), self.LIGHT, W / 2, 560, t, 0.0)
        def sym(d, x, y, i):
            d.rounded_rectangle([x, y - 45, x + 90, y + 45], 22, fill=self.ACC)
            d.text((x + 45, y), str(i + 1), font=F("Black", 62), fill=self.D1, anchor="mm")
        self._items(c, t, s["items"], sym, 820, 190)

    def s_versus(self, c, t, s):
        B = self.B; d = ImageDraw.Draw(c)
        if t >= 0:
            self.pop(c, "✗  " + s["bad_title"], fit("✗  " + s["bad_title"], "Black", 90), (248, 113, 113), W / 2, 560, t, 0.0)
            self.pop(c, s["bad"], fit(s["bad"], "Bold", 64), self.MUTED, W / 2, 680, t, 0.5 * B)
        if t >= 1.5 * B:
            p = ease_out((t - 1.5 * B) / 0.25)
            d.line([(W / 2 - 300 * p, 860), (W / 2 + 300 * p, 860)], fill=(71, 85, 105), width=6)
        self.pop(c, "✓  " + s["good_title"], fit("✓  " + s["good_title"], "Black", 90), self.ACC, W / 2, 1020, t, 2 * B, 1.8)
        self.pop(c, s["good"], fit(s["good"], "Bold", 64), self.LIGHT, W / 2, 1140, t, 2.5 * B)

    def s_ticker(self, c, t, s):
        self.pop(c, s["label"], fit(s["label"], "Bold", 72), self.MUTED, W / 2, 760, t, 0.0)
        words = s["words"]; step = self.B / 2
        i = min(len(words) - 1, int(t / step))
        last = i == len(words) - 1
        self.pop(c, words[i], fit(words[i], "Black", 170 if last else 150), self.ACC if last else self.LIGHT,
                 W / 2, 950, t, i * step, 1.5, 0.15)

    def s_cta(self, c, t, s):
        B = self.B; d = ImageDraw.Draw(c); ink = self.D1
        ls = s.get("lines", ["Jetzt anfragen!"])
        self.lines(c, t, ls, "Black", 115, ink, 560, 130, 0.0, B / 2)
        if t >= B:
            pulse = max(0.2, 1 + 0.05 * math.sin((t - B) * 2 * math.pi / B) if t > B + 0.3 else back_out((t - B) / 0.3))
            bw, bh = 880 * pulse, 170 * pulse
            d.rounded_rectangle([W / 2 - bw / 2, 900 - bh / 2, W / 2 + bw / 2, 900 + bh / 2], int(bh / 2), fill=self.ACC)
            btn = s.get("button", "auftragexpress24.de")
            d.text((W / 2, 900), btn, font=fit(btn, "Black", int(72 * pulse), int(bw - 80)), fill=ink, anchor="mm")
        if s.get("sub"): self.pop(c, s["sub"], fit(s["sub"], "Bold", 58), self.D2, W / 2, 1100, t, 2 * B)
        if s.get("phone"): self.pop(c, s["phone"], fit(s["phone"], "ExtraBold", 72), ink, W / 2, 1220, t, 3 * B)
        if s.get("foot"): self.pop(c, s["foot"], fit(s["foot"], "Bold", 54), self.ACC, W / 2, 1420, t, 4 * B)

    # ---------- Ablauf ----------
    def render(self, t):
        B = self.B
        idx = max(i for i, s in enumerate(self.scenes) if s[0] * B <= t)
        sb, se, bg, s = self.scenes[idx]
        c = Image.new("RGBA", (W, H), bg + (255,))
        getattr(self, "s_" + s["type"])(c, t - sb * B, s)
        if idx + 1 < len(self.scenes):
            nb = self.scenes[idx + 1][0] * B
            if t >= nb - 0.25:
                self.brush(ImageDraw.Draw(c), -80, H + 80, (t - (nb - 0.25)) / 0.25, self.scenes[idx + 1][2], idx)
        zoom, dx, dy = 1.0, 0, 0
        if t >= self.DROP:
            zoom = 1 + 0.03 * math.exp(-(t % B) / 0.07)
        if t < 2 * B:
            k = math.exp(-(t % B) / 0.08)
            dx, dy = int(14 * k * math.sin(t * 90)), int(10 * k * math.cos(t * 70))
        if zoom != 1 or dx or dy:
            cw, ch = W / zoom, H / zoom
            c = c.crop((int((W - cw) / 2 - dx), int((H - ch) / 2 - dy), int((W + cw) / 2 - dx), int((H + ch) / 2 - dy))).resize((W, H), Image.BILINEAR)
        d = ImageDraw.Draw(c)
        light = bg in (self.LIGHT, self.ACC)
        d.text((W / 2, 200), "AuftragExpress24", font=F("Bold", 46), fill=self.D1 if light else (255, 255, 255), anchor="mm")
        track = ((226, 232, 240) if bg == self.LIGHT else tuple(max(0, v - 40) for v in self.ACC)) if light else (51, 65, 85)
        d.rounded_rectangle([80, 120, W - 80, 130], 5, fill=track)
        d.rounded_rectangle([80, 120, 80 + int((W - 160) * t / self.TOTAL), 130], 5, fill=self.D1 if bg == self.ACC else self.ACC)
        return c.convert("RGB")

    def build_events(self):
        B = self.B
        ev = [(self.DROP, "drop"), (self.DROP, "riser:%.2f" % min(2.0, self.DROP))]
        for sb, se, bg, s in self.scenes[1:]:
            ev.append((sb * B, "whoosh"))
        for sb, se, bg, s in self.scenes:
            st = sb * B; ty = s["type"]
            if ty in ("hook", "statement"):
                n = len(s["lines"]) + (1 if s.get("sub") or s.get("highlight") else 0)
                ev += [(st + i * B, "pop") for i in range(n)]
            elif ty == "bignumber":
                ev += [(st + i * B, "pop") for i in range(2, 3 + len(s.get("lines", [])))]
            elif ty in ("list", "steps"):
                ev += [(st + i * B, "pop") for i in range(len(s["items"]) + 1)]
            elif ty == "versus":
                ev += [(st, "pop"), (st + 2 * B, "pop")]
            elif ty == "ticker":
                ev += [(st + i * B / 2, "pop") for i in range(len(s["words"]))]
            elif ty == "cta":
                ev += [(st + B, "pop"), (st + 3 * B, "pop"), (st + 4 * B, "pop")]
        return [e for e in ev if e[0] < self.TOTAL]

    def make(self, out):
        tmp = out + ".frames"
        shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
        for f in range(int(self.TOTAL * FPS)):
            self.render(f / FPS).save(f"{tmp}/{f:05d}.png", compress_level=1)
        wav = os.path.join(tmp, "a.wav")
        audio.build(self.TOTAL, self.build_events(), wav, self.bpm, self.spec.get("progression", 0), self.spec.get("seed", 0))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", f"{tmp}/%05d.png", "-i", wav,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out], check=True)
        shutil.rmtree(tmp, ignore_errors=True)
        # Vorschau-Bilder (Kontaktbogen) für die Sichtprüfung
        times = [((se - 0.25) * self.B - 0.3) for sb, se, _, _ in self.scenes]
        prev = out.rsplit(".", 1)[0] + "_preview.png"
        ims = []
        for tt in times:
            ims.append(self.render(min(tt, self.TOTAL - 0.05)).resize((270, 480)))
        sheet = Image.new("RGB", (270 * len(ims), 480))
        for i, im in enumerate(ims): sheet.paste(im, (270 * i, 0))
        sheet.save(prev)
        print(f"ok {out} ({self.TOTAL:.1f}s) preview: {prev}")


if __name__ == "__main__":
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    Reel(spec).make(sys.argv[2])
