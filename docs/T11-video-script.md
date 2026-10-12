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

> 「【支付宝安全中心】您的账号存在异常登录，请于今日内点击
>   alipay-secure-login.top 完成身份验证，逾期将冻结账户。」

**Narration (English):**

> "A text from 'Alipay Security Center': your account had an abnormal login,
> verify within today or it will be frozen. The link looks almost official —
> `alipay-secure-login.top`, not `alipay.com`. Phishing attacks are no longer
> sloppy emails; they are one-click pages that clone the login screen you
> trust. Blacklists can't keep up, and cloud scanners need your screen."

**Visuals:** phone; the suspicious URL highlighted; a quick shot of a legit
portal page morphing into the clone.

*Timecode target: 0:00 → label card "40% of workplace breaches start with a
phishing link" (optional stat, keep honest – use our own 0% FP claim instead
if stat can't be cited).*

---

## Segment 2 — 0:20–1:30 · The demo

**Screen:** desktop. Edge (note: use Edge 154 — Chrome 147 has no CLI
unpacked-load support) with the PhishLens Edge extension loaded
(`edge://extensions` already open, extension "PhishLens Edge Guard"),
and a terminal running `.venv\Scripts\python.exe -m uvicorn app:app --port 8765`
(FastAPI @ 127.0.0.1:8765). Both services come up via `start_demo.bat`.

1. **0:20–0:35** Narrator reads the URL aloud; clicks the fake link
   (`http://127.0.0.1:8001/fake_alipay.html` — self-built test page, clearly
   labeled "TEST FAKE PAGE" overlay while recording).
   The page renders — looks identical to the real login page (logo, form,
   input boxes).
2. **0:35–0:50** Narrator: "The page looks right. But look at the domain —
   `alipay-secure-login.top` vs `alipay.com`." On-screen callout compares the
   two domains.
3. **0:50–1:15** Narrator clicks into the password box. **Instant full-screen
   warning overlay** (Shadow DOM, Chinese UI): badge `⚠ 疑似钓鱼网站`,
   title `这可能不是 支付宝 官方页面`, domain rows
   `当前域名 127.0.0.1` (red — the address-bar host, i.e. a local IP serving
   the fake page) vs `正规域名 alipay.com` (green), buttons **`返回`** and
   **`我确认安全，继续访问`** (English narration reads these as "Go back" /
   "I trust this page, continue"). Narration: "The address bar says
   `127.0.0.1` — no bank lives at a raw IP — and the form submits to
   `alipay-secure-login.top`, one lookalike domain away from the real one."
4. **1:15–1:30** Narrator clicks "Go back", lands on the real alipay.com
   login → extension does **not** block (official domain → L0 short-circuit,
   `allow`). "The same brand, the real domain — no warning. That's the three
   conditions: impersonation + wrong domain + asking for credentials."

*Make sure the extension's first screenshot (page load) and second screenshot
(password-box focus) both happen on camera — turn on the "screenshot taken"
debug toast in content.js if needed.*

> **Demo-network note:** the "real alipay.com" pass-through shot needs
> alipay.com to be reachable from the demo machine. If the venue network
> blocks it, fall back to recording the pass-through on
> `https://www.baidu.com` (whitelisted, L0 short-circuit) and keep the
> narration identical ("a whitelisted domain — no analysis needed, no
> screenshot of your credentials leaves the page").

---

## Segment 3 — 1:30–2:20 · Under the hood

**Screen:** terminal / a small dashboard. Show the service event log
(`server/event.log`, JSON Lines, one line per analysis: URL, decision, brand,
per-level timings — no screenshot/DOM payload) with the just-analyzed request.

Narrator walks the five levels on screen (use the architecture figure from
`docs/architecture.png` or the brief's Figure 1):

| On-screen highlight | Narration (English) |
|---|---|
| Request arrives, `url + DOM signals + screenshot (base64)` | "The extension sends the URL, DOM signals, and the screenshot — to 127.0.0.1. Nothing leaves this computer." |
| **L0** domain whitelist + LRU cache, `< 1 ms` | "Level 0: whitelist and result cache. Real login pages short-circuit here." |
| **L1** Faster R-CNN structure detection (~1.6 s CPU) | "Level 1: find logos and input boxes in the screenshot." |
| **L2** OCR-aided Siamese + MobileNet brand matching (~0.9 s) | "Level 2: match the logo against the local brand library — Alipay, 0.734 similarity, above 0.68." |
| **L3** DOM credential check (~1 ms) | "Level 3: a password field is present — the page is asking for credentials." |
| **L4** VLM review (uncertain → rule-based today, Qwen2-VL-2B on Hailo) | "Level 4: only genuinely uncertain cases reach a language model review." |
| Decision: `block` | "All three conditions hold → block. About 5 seconds end-to-end on CPU, dominated by the logo detector and feature extraction." |

*The numbers above match a real run on this machine in T15
(2026-10-08: L0 0.016 s, L1 1.56 s, L2 0.87 s, L3 <0.001 s, total 4.9 s incl.
MobileNet embedder init; warm repeats are faster). `server/event.log` shows
the live per-level timings to film — point the camera at it.*

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

- [ ] **Edge 154** (not Chrome) with extension loaded unpacked via
      `edge://extensions` → Load unpacked → `browser-extension/`; service
      started with `start_demo.bat`
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