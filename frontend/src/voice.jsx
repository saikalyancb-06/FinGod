import { useRef, useState } from "react";
import { fin } from "./api.js";
import { useLang, useT } from "./i18n.jsx";

// Records the microphone as 16 kHz mono WAV (a format every speech-to-text service accepts).
function encodeWav(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  w(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); w(8, "WAVE"); w(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data"); v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) { const s = Math.max(-1, Math.min(1, samples[i])); v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true); }
  return new Blob([buf], { type: "audio/wav" });
}

function downsample(data, from, to) {
  if (from === to) return data;
  const ratio = from / to, out = new Float32Array(Math.floor(data.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const s = Math.floor(i * ratio), e = Math.min(data.length, Math.floor((i + 1) * ratio));
    let sum = 0; for (let j = s; j < e; j++) sum += data[j];
    out[i] = sum / Math.max(1, e - s);
  }
  return out;
}

export function MicButton({ onText, disabled }) {
  const t = useT();
  const lang = useLang();
  const [state, setState] = useState("idle");
  const rec = useRef(null);
  const start = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const src = ctx.createMediaStreamSource(stream);
      const proc = ctx.createScriptProcessor(4096, 1, 1);
      const chunks = [];
      proc.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      src.connect(proc); proc.connect(ctx.destination);
      rec.current = { stream, ctx, proc, chunks };
      setState("rec");
      setTimeout(() => rec.current?.ctx === ctx && stop(), 30000); // max 30 s
    } catch {
      setState("error");
      setTimeout(() => setState("idle"), 2500);
    }
  };
  const stop = async () => {
    const r = rec.current; rec.current = null;
    if (!r) return;
    r.proc.disconnect(); r.stream.getTracks().forEach((x) => x.stop());
    const all = new Float32Array(r.chunks.reduce((n, c) => n + c.length, 0));
    let o = 0; r.chunks.forEach((c) => { all.set(c, o); o += c.length; });
    const wav = encodeWav(downsample(all, r.ctx.sampleRate, 16000), 16000);
    r.ctx.close();
    setState("busy");
    try {
      const fd = new FormData();
      fd.append("file", wav, "voice.wav");
      fd.append("language", lang === "en" ? "auto" : lang);
      const out = await fin.post("/voice/stt", fd);
      if (out.text) onText(out.text);
      setState("idle");
    } catch { setState("error"); setTimeout(() => setState("idle"), 2500); }
  };
  const label = state === "rec" ? t("listening") : state === "busy" ? t("transcribing") : state === "error" ? "🎙️ ✕" : "🎙️";
  return (
    <button type="button" disabled={disabled || state === "busy"} onClick={state === "rec" ? stop : start}
      className={state === "rec" ? "danger mic-live" : "subtle"} title="Speak (Hindi, Kannada or English)">{label}</button>
  );
}

export function SpeakButton({ text }) {
  const t = useT();
  const lang = useLang();
  const [state, setState] = useState("idle");
  const audio = useRef(null);
  const play = async () => {
    if (state === "playing") { audio.current?.pause(); setState("idle"); return; }
    setState("loading");
    try {
      const { audios } = await fin.post("/voice/tts", { text, language: lang });
      let i = 0;
      const next = () => {
        if (i >= audios.length) { setState("idle"); return; }
        audio.current = new Audio(`data:audio/wav;base64,${audios[i++]}`);
        audio.current.onended = next;
        audio.current.play();
      };
      setState("playing");
      next();
    } catch { setState("idle"); }
  };
  return <button type="button" className="small ghost" onClick={play}>{state === "loading" ? "…" : state === "playing" ? "⏸" : "🔊"} {t("speak")}</button>;
}
