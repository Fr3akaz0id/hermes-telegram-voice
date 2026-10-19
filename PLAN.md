# hermes-telegram-voice — Build Plan

Fork of [hermes-whatsapp-voice](https://github.com/ashwanthbalakrishnan5/hermes-whatsapp-voice) re-targeted to Telegram, with the speech stack decoupled into two selectable modes: **live** (Gemini Live speech-to-speech, as in the original) and **pipeline** (local, self-hosted STT → Hermes → TTS). MIT-licensed, so forking is clean.

## Architecture

```
 your phone ──Telegram call──▶ bot account (user acct, MTProto)
        │                          │
        │                   Telethon (signaling) + ntgcalls (WebRTC/SRTP, 48 kHz PCM)
        │                          │
        │                    ┌─────┴────── SpeechBus ──────┐
        │                    │                             │
        ▼                    ▼                             ▼
   Hermes API server   mode: live                  mode: pipeline
   (/v1/runs via       Gemini Live STT↔TTS        VAD → local STT → Hermes turn
   ask_hermes)                                    → local TTS → PCM back into call
```

The Hermes plugin layer, the sidecar HTTP contract (loopback + token), `ask_hermes`, the persona briefing, post-call summary, and the `call_owner`/`call_status` tools all carry over unchanged. Only the sidecar internals change, and they grow the mode switch.

## Speech engines

### mode: live (default — closest to the original)
| Role | Engine | Notes |
|---|---|---|
| STT+TTS | Gemini Live (`generativelanguage.googleapis.com`) | Speech-to-speech, native barge-in/interruption, lowest latency. Needs `GOOGLE_API_KEY`; ~$0.005/min in, $0.018/min out. Not local. |

### mode: pipeline (fully local, no cloud in the audio path)
| Role | Recommended | Alternatives | Notes for this laptop |
|---|---|---|---|
| VAD | silero-vad (ONNX) | webrtcvad | Tiny, CPU-fine; drives end-of-utterance and barge-in. |
| STT | faster-whisper `small` (int8) | Vosk (lower latency, weaker quality); whisper.cpp | ~real-time on this CPU. German + multilingual — whisper small is solid for both. `large-v3` too slow for a call loop here. |
| LLM | your Hermes, via `/v1/runs` | — | The pipeline mode's "brain" **is** Hermes — same tool as live mode's `ask_hermes`. No extra local model needed. |
| TTS | piper | kokoro (better quality, heavier), xtts-v2 (voice cloning — GPU-only, out of scope here) | Piper: real-time on CPU, German voices (`de_DE-thorsten*`, `de_DE-katia*`), streaming output = lower latency. |

Nothing local is installed on the laptop today (no piper, no whisper bindings, no torch) — Phase 4 includes the install. Design target: **CPU-only**; the 940M is Maxwell/EOL and deliberately unused.

### mode: hybrid (auto)
Live if a Gemini key is present, else fall back to the pipeline, with the fallback stated in the post-call summary. This means the bridge never dies on a missing key or an offline call.

**Latency budget (pipeline, end-of-utterance → first audio out):** VAD tail (~300 ms) + whisper-small on a ≤3 s utterance (~0.4–1 s) + Hermes turn (variable, dominant) + piper first chunk (~150 ms). Target: ≤2.5 s for a canned/quick answer. Live mode: sub-1 s. This difference is the honest trade: local = private and free, live = natural interruptions.

## Phases

### Phase 0 — Prereqs (you)
- [ ] Spare Telegram number + account, `api_id`/`api_hash` from my.telegram.org. Not your main account — auto-answering user accounts can get flagged.
- [ ] Voice language (de, en, or both — affects TTS voice and STT language hint).
- **Exit:** creds in the sidecar's env, nothing built yet.

### Phase 1 — Fork & rebrand skeleton
- [ ] Fork + rename `whatsapp_* → telegram_*` across `plugin.yaml`, `tools.py`, `cli.py`, `sidecar_client.py`, dashboard tab.
- [ ] Keep the sidecar's loopback HTTP contract byte-compatible (`/status`, `/pair`, `/call`, token auth) so the plugin layer is untouched thereafter.
- [ ] Stub sidecar returns canned statuses.
- **Exit:** `hermes telegram-voice status` and `call "test"` run against the stub. Plugin tools visible in Hermes.

### Phase 2 — Call transport proof (the risky part)
- [ ] Python sidecar: Telethon login (SMS once, persist session) → `UpdatePhoneCall` → `AcceptCallRequest` → `phone.sendSignalingData` relay.
- [ ] ntgcalls 2.1.0 via pybind, following the proven setup ordering from [p2p-offline-ai-telegram-bridge](https://github.com/TxPKev/p2p-offline-ai-telegram-bridge): `create_p2p_call` → attach **capture** stream *before* DH key exchange → `connect_p2p` → attach **playback** on the microphone slot only when connected → `unmute` → `resume`, with continuous outbound frames (silence when idle). `py-tgcalls` stable cannot do 1-on-1 calls — bypass it.
- [ ] Loopback test: caller's voice comes back to the caller (delay line), then play a local WAV file. No speech AI involved.
- **Exit:** call your bot, hear yourself/your file. This is the go/no-go gate.

### Phase 3 — mode: live (port)
- [ ] 48 kHz ↔ 16 kHz in / 24 kHz out resamplers (the original ships these; different numbers).
- [ ] Gemini Live session with `ask_hermes` + `end_call` tools; ~10-min session resume logic.
- [ ] Persona briefing, post-call summary run, memory write-back — port as-is.
- **Exit:** full original feature parity, minus outbound calls.

### Phase 4 — mode: pipeline (local STT/TTS)
- [ ] Install: `piper-tts` + a German voice model, `faster-whisper` (small, int8), `silero-vad` (pip; onnxruntime CPU).
- [ ] `SpeechEngine` interface: `feed(pcm) -> text?`, `synthesize(text) -> pcm stream`. Providers: `gemini_live`, `local_whisper_piper`.
- [ ] VAD segmenter: barge-in = cut playback, flush, restart; utterance-complete = run STT → Hermes turn → TTS, streamed into the call.
- [ ] Config keys:
  ```
  voice.mode: live | pipeline | hybrid
  live.model / live.voice            (unchanged from original)
  stt.engine: faster_whisper | vosk
  stt.model: small
  stt.language: de | en | auto
  tts.engine: piper | kokoro
  tts.voice: de_DE-thorsten-low
  vad.threshold: 0.5
  barge_in: true
  ```
- [ ] Mode selector surfaces in the dashboard tab + plugin settings; `hermes telegram-voice doctor` reports which engines loaded.
- **Exit:** local conversation round-trip on a real call, ≤2.5 s response, interruptions work.

### Phase 5 — Outbound + escalation
- [ ] `telegram_call_owner(reason, details?)` via `phone.requestCall` (GramJS exposes `initiate_call`; validate early — the proven bridge only did inbound).
- [ ] `call_status`, `no_answer → fallback to Telegram message`, cron-job escalation path.
- **Exit:** `hermes cron create` fires a call; unanswerable case degrades to a message.

### Phase 6 — Hardening & ops
- [ ] systemd user unit for the sidecar; crash loop → message you on Telegram.
- [ ] Session file lives in `plugin-data/hermes-telegram-voice/` (mode 700); note that it **is** the account — treat it like a password.
- [ ] Account-safety defaults: answer only `owner` (or `owner_user_id`), never group calls, never third-party callers.
- [ ] README + setup skill (agent runbook, QR-free: SMS code entry).
- **Exit:** survives reboot, unattended for a week.

## Risks
| Risk | Mitigation |
|---|---|
| Telegram flags the user account for automation | Spare number, owner-only traffic, no mass messaging ever. Same class of risk the original already runs with WhatsApp. |
| Outbound calls under-proven | Phase 5 is last and separately gated; inbound ships without it. |
| Call protocol breakage on Telegram updates | ntgcalls pins version 2.1.0; pin + test suite; the plugin reports `protocol_error` to the owner via message. |
| Pipeline latency feels slow | VAD tail + model tuning knobs; hybrid mode defaults to live when key present. |
| 10-min live session resume failure | Already handled by porting the original's resume logic. |

## Open decisions
1. Fork `hermes-whatsapp-voice` directly (keeps git history + MIT attribution, PR-able upstream for the mode-switch) **vs.** clean repo borrowing its architecture. → Fork recommended.
2. Voice/persona language: German, English, or auto-detect (whisper does it free; TTS voice choice follows).
3. Default mode: `hybrid` (recommended) or `live`.
