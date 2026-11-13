"""Sarvam AI client: Indian-language translation, speech-to-text and text-to-speech.

FinLens computes everything in English (so figures stay verifiable); Sarvam translates the
user's words in and the answers out, and handles voice. Key: SARVAM_API_KEY in .env.
"""
from __future__ import annotations

import base64
import logging
import re
from functools import lru_cache

import httpx

from . import config

log = logging.getLogger("shared.sarvam")
BASE = "https://api.sarvam.ai"
LANGS = {"en": "en-IN", "hi": "hi-IN", "kn": "kn-IN"}
SPEAKER = {"hi-IN": "anushka", "kn-IN": "anushka", "en-IN": "anushka"}


class SarvamError(RuntimeError):
    pass


def available() -> bool:
    return bool(config.SARVAM_API_KEY)


def _h():
    return {"api-subscription-key": config.SARVAM_API_KEY}


def code(lang: str | None) -> str:
    return LANGS.get((lang or "en")[:2], "en-IN")


@lru_cache(maxsize=2048)
def _translate_one(text: str, src: str, tgt: str, mode: str = "formal") -> tuple[str, str]:
    body = {"input": text, "source_language_code": src, "target_language_code": tgt, "model": "mayura:v1",
            "mode": mode, "numerals_format": "international"}
    r = httpx.post(f"{BASE}/translate", headers=_h(), json=body, timeout=40)
    if r.status_code == 400:  # some params unsupported for a pair: retry minimal
        body = {"input": text, "source_language_code": src, "target_language_code": tgt}
        r = httpx.post(f"{BASE}/translate", headers=_h(), json=body, timeout=40)
    if r.status_code != 200:
        raise SarvamError(f"translate {r.status_code}: {r.text[:160]}")
    j = r.json()
    return j.get("translated_text", text), j.get("source_language_code", src)


MODES = {"formal": "formal", "colloquial": "modern-colloquial"}


def translate(text: str, target: str, source: str = "auto", style: str = "formal") -> str:
    """Plain text translation (chunks long input on sentence boundaries)."""
    if not text.strip() or not available():
        return text
    tgt, src = code(target), (source if source == "auto" else code(source))
    if tgt == src:
        return text
    chunks, cur = [], ""
    for part in re.split(r"(?<=[.!?।])\s+", text):
        if len(cur) + len(part) > 800 and cur:
            chunks.append(cur)
            cur = part
        else:
            cur = f"{cur} {part}".strip()
    if cur:
        chunks.append(cur)
    try:
        return " ".join(_translate_one(c, src, tgt, MODES.get(style, "formal"))[0] for c in chunks)
    except Exception as exc:
        log.warning("translate failed: %s", exc)
        return text


def to_english(text: str) -> tuple[str, str]:
    """Translate user input to English; returns (english_text, detected_language_code)."""
    if not available() or not text.strip() or re.fullmatch(r"[\x00-\x7F₹]+", text):
        return text, "en-IN"
    try:
        out, src = _translate_one(text, "auto", "en-IN")
        return out, src
    except Exception as exc:
        log.warning("to_english failed: %s", exc)
        return text, "en-IN"


_MD_PREFIX = re.compile(r"^(\s*(?:#{1,4}\s+|[-*•+]\s+|\d+[.)]\s+|>\s*)?)(.*)$")


def translate_markdown(md: str, target: str, style: str = "formal") -> str:
    """Translate line by line, keeping list/heading structure, bold headings and ₹ figures."""
    if code(target) == "en-IN" or not available():
        return md
    out = []
    for line in md.split("\n"):
        if not line.strip() or re.fullmatch(r"\s*\|?[-:| ]+\|?\s*", line):
            out.append(line)
            continue
        if line.strip().startswith("|"):  # table row: translate each cell
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            out.append("| " + " | ".join(translate(c.replace("**", ""), target, "en", style) if re.search(r"[A-Za-z]{3}", c) else c for c in cells) + " |")
            continue
        m = _MD_PREFIX.match(line)
        prefix, body = m.group(1) or "", m.group(2)
        bold_line = bool(re.fullmatch(r"\*\*[^*]+\*\*:?", body.strip()))
        plain = body.replace("**", "")
        tr = translate(plain, target, "en", style) if re.search(r"[A-Za-z]{2}", plain) else plain
        out.append(prefix + (f"**{tr}**" if bold_line else tr))
    return "\n".join(out)


def stt(audio: bytes, filename: str = "audio.wav", language: str | None = None) -> dict:
    if not available():
        raise SarvamError("SARVAM_API_KEY not set")
    data = {"model": "saarika:v2.5", "language_code": code(language) if language and language != "auto" else "unknown"}
    mime = "audio/wav" if filename.endswith(".wav") else "audio/webm"
    r = httpx.post(f"{BASE}/speech-to-text", headers=_h(), files={"file": (filename, audio, mime)}, data=data, timeout=60)
    if r.status_code != 200:
        raise SarvamError(f"speech-to-text {r.status_code}: {r.text[:160]}")
    j = r.json()
    return {"text": j.get("transcript", ""), "language": j.get("language_code")}


def _clean_for_speech(text: str) -> str:
    t = re.sub(r"\*\*|__|`|#+\s|^\s*[-*•]\s+", "", text, flags=re.M)
    t = re.sub(r"\|", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def tts(text: str, language: str) -> list[str]:
    """Returns base64 WAV segments (long text is split so each request stays within limits)."""
    if not available():
        raise SarvamError("SARVAM_API_KEY not set")
    lang = code(language)
    clean = _clean_for_speech(text)[:2400]
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?।])\s+", clean):
        if len(cur) + len(sent) > 450 and cur:
            parts.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        parts.append(cur)
    audios = []
    for p in parts[:6]:
        r = httpx.post(f"{BASE}/text-to-speech", headers=_h(), json={"text": p, "target_language_code": lang, "speaker": SPEAKER[lang]}, timeout=60)
        if r.status_code == 400:
            r = httpx.post(f"{BASE}/text-to-speech", headers=_h(), json={"text": p, "target_language_code": lang}, timeout=60)
        if r.status_code != 200:
            raise SarvamError(f"text-to-speech {r.status_code}: {r.text[:160]}")
        audios.extend(r.json().get("audios", []))
    return audios


def wav_ok(b64: str) -> bool:
    try:
        return base64.b64decode(b64[:64] + "==")[:4] == b"RIFF"
    except Exception:
        return False
