"""Reel mit deutscher Sprecherstimme (Piper TTS), Karaoke-Untertiteln und Beat.

Aufruf: python3 voicereel.py spec.json out.mp4 --voice /pfad/de-thorsten-low.onnx
Spec: {"bpm":..,"progression":..,"seed":..,"theme":{..},
       "segments":[{"say":"Text zum Sprechen","show":"Untertitel-Text","visual":{...}}]}
visual.type: headline{lines} | number{number,lines} | chips{items} | ticker{label,words} | cta{lines,button,phone,foot}
"""
import argparse, json, math, os, shutil, subprocess, sys, wave
import numpy as np
from PIL import Image, ImageDraw
from scipy.io import wavfile
from scipy.signal import resample_poly

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audio
from reelgen import Reel, F, fit, back_out, ease_out, W, H, FPS, SYM
from PIL import ImageFont


_SYN = {}


def tts(text, voice, out, speed):
    if os.path.isdir(voice):
        # Coqui-VITS-Modell (Ordner mit model_file.pth + config.json) – natürlichere Stimme
        if voice not in _SYN:
            from TTS.utils.synthesizer import Synthesizer
            _SYN[voice] = Synthesizer(tts_checkpoint=os.path.join(voice, "model_file.pth"),
                                      tts_config_path=os.path.join(voice, "config.json"), use_cuda=False)
            try:
                _SYN[voice].tts_model.length_scale = speed
            except Exception:
                pass
        syn = _SYN[voice]
        sr = syn.output_sample_rate
        # Satzweise sprechen und Pausen zwischen Sätzen kurz halten (Standard wäre ~1 s Stille)
        import re
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
        chunks = []
        for p in parts:
            w = np.array(syn.tts(p, split_sentences=False), dtype=np.float32)
            a = np.abs(w); idx = np.where(a > 0.02 * a.max())[0]
            if len(idx): w = w[max(0, idx[0] - int(0.02 * sr)): idx[-1] + int(0.06 * sr)]
            chunks += [w, np.zeros(int(0.22 * sr), np.float32)]
        x = np.concatenate(chunks[:-1]) if chunks else np.zeros(1, np.float32)
    else:
        subprocess.run([sys.executable, "-m", "piper", "-m", voice, "-f", out, "--length-scale", str(speed),
                        "--sentence-silence", "0.0"], input=text.encode(), check=True, capture_output=True)
        sr, x = wavfile.read(out)
        x = x.astype(np.float32) / 32768
    if x.ndim > 1: x = x.mean(1)
    # Stille am Rand kürzen
    idx = np.where(np.abs(x) > 0.02)[0]
    if len(idx): x = x[max(0, idx[0] - int(0.03 * sr)): idx[-1] + int(0.08 * sr)]
    return resample_poly(x, 44100, sr).astype(np.float32)


class VoiceReel(Reel):
    def __init__(self, spec, voice, speed):
        base = {"bpm": spec.get("bpm", 112), "theme": spec.get("theme", {}),
                "scenes": [{"type": "hook", "beats": 1, "lines": ["x"]}]}
        super().__init__(base)
        self.vspec = spec
        tmp = "/tmp/claude-0/voicereel"; os.makedirs(tmp, exist_ok=True)
        self.clips, self.segs = [], []
        t = 0.35
        gap = 0.18
        for i, s in enumerate(spec["segments"]):
            v = tts(s["say"], voice, f"{tmp}/s{i}.wav", speed)
            d = len(v) / 44100
            self.clips.append((t, v))
            self.segs.append({"start": t, "end": t + d, "seg": s})
            t += d + gap
        self.TOTAL = t + 0.6
        self.DROP = 0.0
        # Szenengrenzen = Segmentstart (erste Szene ab 0)
        self.segs[0]["start_vis"] = 0.0
        for i, sg in enumerate(self.segs):
            sg["vis_start"] = 0.0 if i == 0 else sg["start"] - 0.12
            sg["vis_end"] = self.segs[i + 1]["start"] - 0.12 if i + 1 < len(self.segs) else self.TOTAL
            bg_cycle = [self.D1, self.D2]
            ty = sg["seg"]["visual"]["type"]
            sg["bg"] = self.ACC if ty == "number" else self.LIGHT if ty == "cta" else bg_cycle[i % 2]
            words = sg["seg"].get("show", sg["seg"]["say"]).split()
            lens = np.array([len(w) + 2 for w in words], float)
            cum = np.concatenate([[0], np.cumsum(lens)]) / lens.sum()
            dur = sg["end"] - sg["start"]
            sg["words"] = [(w, sg["start"] + cum[k] * dur) for k, w in enumerate(words)]

    # ---- Visuals ----
    def v_headline(self, c, t, v, bg):
        col = self.LIGHT if bg != self.LIGHT else self.D1
        ls = v["lines"]; gap = 150; y0 = 760 - gap * (len(ls) - 1) / 2
        for i, ln in enumerate(ls):
            self.pop(c, ln, fit(ln, "Black", 150), self.ACC if i == len(ls) - 1 and len(ls) > 1 else col, W / 2, y0 + i * gap, t, i * 0.25, 2.0 if i == 0 else 0.4)

    def v_number(self, c, t, v, bg):
        self.pop(c, v["number"], fit(v["number"], "Black", 560), self.D1, W / 2, 640, t, 0.0, 3.0, 0.35)
        for i, ln in enumerate(v.get("lines", [])):
            self.pop(c, ln, fit(ln, "Black", 110), self.LIGHT, W / 2, 1000 + i * 125, t, 0.4 + i * 0.25)

    def v_chips(self, c, t, v, bg, words_t):
        d = ImageDraw.Draw(c)
        items = v["items"]
        for i, it in enumerate(items):
            key = it.split()[0].lower().strip(",.&")
            t0 = next((wt for w, wt in words_t if w.lower().strip(",.!?").startswith(key[:5])), i * 0.6)
            if self.cur_t < t0: continue
            p = back_out((self.cur_t - t0) / 0.3)
            y = 520 + i * 230
            x = -900 + (90 + 900) * p
            d.rounded_rectangle([x, y - 85, x + 900, y + 85], 40, fill=self.ACC)
            d.text((x + 450, y), it, font=fit(it, "Black", 92, 820), fill=self.D1, anchor="mm")

    def v_ticker(self, c, t, v, bg, words_t):
        self.pop(c, v["label"], fit(v["label"], "Bold", 72), self.MUTED, W / 2, 620, t, 0.0)
        shown = [w for w in v["words"] if any(ww.strip(",.").startswith(w[:4]) and wt <= self.cur_t for ww, wt in words_t)]
        for i, w in enumerate(shown):
            self.pop(c, w, fit(w, "Black", 110), self.ACC if i == len(shown) - 1 else self.LIGHT, W / 2, 760 + i * 120, t, 0, 1.0, 0.01)

    def v_cta(self, c, t, v, bg):
        d = ImageDraw.Draw(c); ink = self.D1
        for i, ln in enumerate(v.get("lines", [])):
            self.pop(c, ln, fit(ln, "Black", 115), ink, W / 2, 520 + i * 130, t, i * 0.2)
        if t >= 0.5:
            pulse = max(0.2, 1 + 0.05 * math.sin((t - 0.5) * 2 * math.pi / self.B) if t > 0.8 else back_out((t - 0.5) / 0.3))
            bw, bh = 900 * pulse, 170 * pulse
            d.rounded_rectangle([W / 2 - bw / 2, 860 - bh / 2, W / 2 + bw / 2, 860 + bh / 2], int(bh / 2), fill=self.ACC)
            d.text((W / 2, 860), v["button"], font=fit(v["button"], "Black", int(72 * pulse), int(bw - 80)), fill=ink, anchor="mm")
        self.pop(c, v["phone"], fit(v["phone"], "ExtraBold", 70), ink, W / 2, 1060, t, 0.9)
        self.pop(c, v["foot"], fit(v["foot"], "Bold", 56), self.ACC, W / 2, 1170, t, 1.2)

    def subtitles(self, c, t, sg, bg):
        """Karaoke-Untertitel: Gruppen à 3-4 Wörter, aktuelles Wort in Akzentfarbe."""
        ws = sg["words"]
        if t < sg["start"] - 0.05 or t > sg["end"] + 0.15: return
        cur = max(0, max((k for k, (w, wt) in enumerate(ws) if wt <= t), default=0))
        groups, g = [], []
        for k in range(len(ws)):
            if g and (len(g) >= 4 or sum(len(ws[j][0]) + 1 for j in g) + len(ws[k][0]) > 24):
                groups.append(g); g = []
            g.append(k)
        groups.append(g)
        gi = next(gg for gg in groups if cur in gg)
        g0 = gi[0]; grp = [ws[j] for j in gi]
        txt = " ".join(w for w, _ in grp)
        f = fit(txt, "Black", 74, 960)
        total = f.getlength(txt)
        x = W / 2 - total / 2; y = 1560
        d = ImageDraw.Draw(c)
        pad = 26
        light = bg == self.LIGHT
        d.rounded_rectangle([x - pad, y - 62, x + total + pad, y + 62], 26, fill=(0, 0, 0) if not light else self.D1)
        for k, (w, wt) in enumerate(grp):
            active = g0 + k == cur
            col = self.ACC if active else (255, 255, 255)
            d.text((x, y), w, font=f, fill=col, anchor="lm")
            x += f.getlength(w + " ")

    def render(self, t):
        self.cur_t = t
        idx = max(i for i, s in enumerate(self.segs) if s["vis_start"] <= t)
        sg = self.segs[idx]; v = sg["seg"]["visual"]; bg = sg["bg"]
        c = Image.new("RGBA", (W, H), bg + (255,))
        lt = t - sg["vis_start"]
        ty = v["type"]
        if ty in ("chips", "ticker"):
            getattr(self, "v_" + ty)(c, lt, v, bg, sg["words"])
        else:
            getattr(self, "v_" + ty)(c, lt, v, bg)
        if idx + 1 < len(self.segs):
            nb = self.segs[idx + 1]["vis_start"]
            if t >= nb - 0.22:
                self.brush(ImageDraw.Draw(c), -80, H + 80, (t - (nb - 0.22)) / 0.22, self.segs[idx + 1]["bg"], idx)
        self.subtitles(c, t, sg, bg)
        zoom = 1 + 0.018 * math.exp(-(t % self.B) / 0.07)
        cw, ch = W / zoom, H / zoom
        c = c.crop((int((W - cw) / 2), int((H - ch) / 2), int((W + cw) / 2), int((H + ch) / 2))).resize((W, H), Image.BILINEAR)
        d = ImageDraw.Draw(c)
        light = bg in (self.LIGHT, self.ACC)
        d.text((W / 2, 200), "AuftragExpress24", font=F("Bold", 46), fill=self.D1 if light else (255, 255, 255), anchor="mm")
        track = ((226, 232, 240) if bg == self.LIGHT else tuple(max(0, x - 40) for x in self.ACC)) if light else (51, 65, 85)
        d.rounded_rectangle([80, 120, W - 80, 130], 5, fill=track)
        d.rounded_rectangle([80, 120, 80 + int((W - 160) * t / self.TOTAL), 130], 5, fill=self.D1 if bg == self.ACC else self.ACC)
        return c.convert("RGB")

    def make(self, out):
        tmp = out + ".frames"; shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
        n = int(self.TOTAL * FPS)
        for f in range(n):
            self.render(f / FPS).save(f"{tmp}/{f:05d}.png", compress_level=1)
        # Musikbett (leise, nur Whoosh bei Szenenwechsel)
        ev = [(sg["vis_start"], "whoosh") for sg in self.segs[1:]]
        mus_wav = f"{tmp}/m.wav"
        audio.build(self.TOTAL, ev + [(0.0, "drop")], mus_wav, self.bpm, self.vspec.get("progression", 0), self.vspec.get("seed", 0))
        sr, m = wavfile.read(mus_wav); m = m.astype(np.float32).mean(1) / 32768
        voice = np.zeros_like(m)
        for st, v in self.clips:
            i = int(st * 44100); j = min(len(voice), i + len(v)); voice[i:j] += v[: j - i]
        voice /= max(1e-6, np.abs(voice).max())
        # Ducking: Musik leiser, wenn gesprochen wird
        envv = np.convolve(np.abs(voice), np.ones(4410) / 4410, mode="same")
        duck = 1 - 0.7 * np.clip(envv / (envv.max() + 1e-6) * 4, 0, 1)
        mix = voice * 0.95 + m * 0.22 * duck
        mix /= max(1e-6, np.abs(mix).max()) / 0.95
        wavfile.write(f"{tmp}/a.wav", 44100, (np.stack([mix, mix], 1) * 32767).astype(np.int16))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", f"{tmp}/%05d.png", "-i", f"{tmp}/a.wav",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out], check=True)
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"ok {out} {self.TOTAL:.1f}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("spec"); ap.add_argument("out")
    ap.add_argument("--voice", required=True); ap.add_argument("--speed", type=float, default=0.92)
    a = ap.parse_args()
    VoiceReel(json.load(open(a.spec, encoding="utf-8")), a.voice, a.speed).make(a.out)
