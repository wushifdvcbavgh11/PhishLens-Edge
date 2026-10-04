# T11 — Demo Video Script (≤ 3 min, English)

Contest spec (§ 12.7): the video must show a phishing text message →
the fake login page → the warning overlay firing at the moment the password
box is focused → the local decision pipeline & per-level timings →
the value proposition (USB stick, target customers, custom protection).

Target: **≤ 3:00**, 16:9, 1080p, English narration + English burned-in
subtitles. Upload to YouTube with visibility **"Unlisted — anyone with the
link can view"**.

This document is the shot list + narration the team records against.
Everything below is reproducible on this machine with the real pipeline.

---

## Segment 1 — 0:00–0:20 · The problem (hook)

**Screen:** phone mock-up / split screen begins. A text message in a messaging app:

> 「【统一身份认证】您的账号将于今日到期，请点击 ucas-portal.sso-login.cn 完成认证，逾期将停用。」

**Narration (English):**

> "A message from 'IT': your university account expires today. The link looks
> official. One tap, and your password is gone. Phishing attacks are no longer
> sloppy emails — they are one-click pages that clone the login screen you
> trust. Blacklists can't keep up, and cloud scanners need your screen."

**Visuals:** phone; the suspicious URL highlighted; a quick shot of a legit
portal page morphing into the clone.

*Timecode target: 0:00 → label card "40% of workplace breaches start with a
phishing link" (optional stat, keep honest – use our own 0% FP claim instead
if stat can't be cited).*

---

## Segment 2 — 0:20–1:30 · The demo

**Screen:** desktop. Chrome with the PhishLens Edge extension loaded
(`chrome://extensions` already open, extension "PhishLens Edge Guard"),
and a terminal running `python server/app.py` (FastAPI @ 127.0.0.1:8765).

1. **0:20–0:35** Narrator reads the URL aloud; clicks the fake link
   (`http://127.0.0.1:8000/fake_alipay/` — self-built test page, clearly
   labeled "TEST FAKE PAGE" overlay while recording).
   The page renders — looks identical to the real login page (logo, form,
   input boxes).
2. **0:35–0:50** Narrator: "The page looks right. But look at the domain —
   `fake-alipay.local` vs `alipay.com`." On-screen callout compares the two
   domains.
3. **0:50–1:15** Narrator clicks into the password box. **Instant full-screen
   warning overlay** (Shadow DOM): "This page may be phishing you" —
   spoofed brand: **支付宝 (Alipay)** / current domain:
   `fake-alipay.local` / official domain: `alipay.com` — buttons:
   **« Go back »** and **« I trust this page, continue »**.
4. **1:15–1:30** Narrator clicks "Go back", lands on the real alipay.com
   login → extension does **not** block (official domain → L0 short-circuit,
   `allow`). "The same brand, the real domain — no warning. That's the three
   conditions: impersonation + wrong domain + asking for credentials."

*Make sure the extension's first screenshot (page load) and second screenshot
(password-box focus) both happen on camera — turn on the "screenshot taken"
debug toast in content.js if needed.*

---

## Segment 3 — 1:30–2:20 · Under the hood

**Screen:** terminal / a small dashboard. Show the service event log
(`server/server.log`, JSON Lines) with the just-analyzed request.

Narrator walks the five levels on screen (use the architecture figure from
`docs/architecture.png` or the brief's Figure 1):

| On-screen highlight | Narration (English) |
|---|---|
| Request arrives, `url + DOM signals + screenshot (base64)` | "The extension sends the URL, DOM signals, and the screenshot — to 127.0.0.1. Nothing leaves this computer." |
| **L0** domain whitelist + LRU cache, `< 1 ms` | "Level 0: whitelist and result cache. Real login pages short-circuit here." |
| **L1** Faster R-CNN structure detection (~1.6 s CPU) | "Level 1: find logos and input boxes in the screenshot." |
| **L2** OCR-aided Siamese + MobileNet brand matching (~0.7 s) | "Level 2: match the logo against the 299-brand local library — Alipay, 0.734 similarity, above 0.68." |
| **L3** DOM credential check (~1 ms) | "Level 3: a password field is present — the page is asking for credentials." |
| **L4** VLM review (uncertain → rule-based today, Qwen2-VL-2B on Hailo) | "Level 4: only genuinely uncertain cases reach a language model review." |
| Decision: `block` | "All three conditions hold → block, total 2.1 seconds on CPU." |

**Emphasize privacy:** "The screenshot lives in RAM, is never written to disk,
never uploaded. The log keeps the URL, brand and decision — nothing more."

*Timecode target: ~2:10 card: Local-only diagram, "Screenshots stay in RAM —
never written to disk — never uploaded."*

---

## Segment 4 — 2:20–3:00 · The value

**Screen:** USB stick close-up → connected to a laptop → the same pipeline runs
with an "ASUS UGen300 (Hailo-10H)" label.

**Narration (English):**

> "Now imagine this entire pipeline on a USB stick. ASUS UGen300 with the
> Hailo-10H accelerator: 40 TOPS, 8 GB, 2.5 watts. Three model families share
> one chip — detection, logo embedding, VLM review. No cloud. No GPU server.
> Plug it into any office PC.
>
> Our target customers: universities and mid-size companies that can't afford
> security teams — phishing is their #1 entry point. With PhishLens Edge, an
> admin adds their own brand: their login page, their logo, their official
> domains, protected on every employee machine within minutes.
>
> PhishLens Edge — phishing defense that never leaves your pocket, or your
> machine."

**End card (2:50–3:00):** logo + tagline + "ASUS UGen AI League · 2026".

---

## Recording checklist

- [ ] Chrome/Edge with extension loaded unpacked; service started
- [ ] Self-built fake pages (`tools/make_fake_pages.py`) with visible
      "TEST FAKE PAGE" label → satisfies the "mark test pages" ethics rule
- [ ] Real alipay.com login pass-through shot (L0 short-circuit, no block)
- [ ] `server/server.log` close-up (JSON Lines entries with level timings)
- [ ] `docs/architecture.png` full-screen for the backend segment
- [ ] USB stick / UGen300 prop shots
- [ ] English narration + burned-in subtitles; total runtime ≤ 3:00
- [ ] Export 1080p MP4 (H.264, AAC)

## Upload

1. youtube.com → **Create → Upload videos** → select final MP4
2. Title: `PhishLens Edge — On-Device Visual Phishing Defense (ASUS UGen AI League)`
3. Visibility: **Unlisted** ("Anyone with the link can view")
4. Copy the share link for the submission form