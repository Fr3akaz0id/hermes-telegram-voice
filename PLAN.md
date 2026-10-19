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
   (/v1/runs via       Gemini Live STT↔TTS        VAD → Hermes STT → Hermes turn
   ask_hermes)                                    → Hermes TTS → PCM back into call
```

The Hermes plugin layer, the sidecar HTTP contract (loopback + token), `ask_hermes`, the persona briefing, post-call summary, and the `call_owner`/`call_status` tools all carry over unchanged. Only the sidecar internals change, and they grow the mode switch.

## Speech engines

### mode: live (default — closest to the original)
| Role | Engine | Notes |
|---|---|---|
| STT+TTS | Gemini Live (`generativelanguage.googleapis.com`) | Speech-to-speech, native barge-in/interruption, lowest latency. Needs `GOOGLE_API_KEY`; ~$0.005/min in, $0.018/min out. Not local. |

### mode: pipeline — reuse Hermes's own STT/TTS (preferred over bundled models)
| Role | Engine | Notes |
|---|---|---|
| VAD | silero-vad (ONNX) | Tiny, CPU-fine; drives end-of-utterance and barge-in. |
| STT | **whatever Hermes has configured** (`config.yaml` → `stt:`) | This machine: OpenAI-compatible endpoint (`http://172.16.16.88:8895/v1`, model `parakeet`). The sidecar POSTs WAV/OGG to `/v1/audio/transcriptions` like any Hermes client would. Switching providers = edit Hermes config, nothing here. |
| LLM | your Hermes, via `/v1/runs` | The pipeline's "brain" **is** Hermes — same tool as live mode's `ask_hermes`. No separate local model. |
| TTS | **whatever Hermes has configured** (`config.yaml` → `tts:`) | This machine: OpenAI-compatible endpoint, model `qwen3-tts`, voice `vivian`. `/v1/audio/speech`, streamed chunks into the call. |

**Policy: we use what Hermes has configured in `config.yaml` — this is preferred over bundling
whisper/piper.** The sidecar reads `stt:`/`tts:` from Hermes config at startup (same resolution as
Hermes itself: base_url, model, voice, api_key via `get_secret`), so the voice bridge and the rest
of Hermes (voice notes, voice-mode messenger replies) share one speech stack. Bundled
whisper.cpp/piper remain only as an explicit last-resort `stt.source=whisper_local` fallback for
machines with no speech server configured — never the default, and nothing gets installed by
default.

### mode: hybrid (auto)
Live if a Gemini key is present, else fall back to the pipeline, with the fallback stated in the post-call summary. This means the bridge never dies on a missing key or an offline call.

**Latency budget (pipeline, end-of-utterance → first audio out):** VAD tail (~300 ms) + STT round-trip to the configured server (typically sub-second for a ≤3 s utterance) + Hermes turn (variable, dominant) + TTS first chunk (~300 ms on the LAN server). Target: ≤2.5 s for a canned/quick answer. Live mode: sub-1 s. This difference is the honest trade: pipeline = one shared stack with Hermes and no Gemini key, live = natural interruptions.

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

### Phase 4 — mode: pipeline (reuse Hermes-configured STT/TTS)
- [ ] `SpeechEngine` interface: `feed(pcm) -> text?`, `synthesize(text) -> pcm stream`. Providers: `gemini_live` (live mode), `hermes_speech` (reads `stt:`/`tts:` from Hermes config.yaml — base_url/model/voice + api key via `get_secret`).
- [ ] `hermes_speech` implementation: WAV-in → `POST /v1/audio/transcriptions` (STT), `POST /v1/audio/speech` with stream → decode → 48 kHz PCM out; language hint passed when `auto` isn't wanted.
- [ ] Fallback: optional `whisper_local` (whisper.cpp, built from source on demand only when no speech server is configured and the user explicitly opts in) — never installed by default.
- [ ] VAD segmenter (silero): barge-in = cut playback, flush, restart; utterance-complete = STT → Hermes turn → TTS streamed into the call.
- [ ] Config keys:
  ```
  voice.mode: live | pipeline | hybrid          (default hybrid)
  live.model / live.voice                        (unchanged from original)
  stt.source: hermes_config | whisper_local
  stt.language: de | en | auto
  tts.source: hermes_config
  vad.threshold: 0.5
  barge_in: true
  ```
- [ ] Mode + speech-source selector in the dashboard tab + plugin settings; `hermes telegram-voice doctor` reports: which mode, which STT/TTS endpoints were picked up from Hermes config, and a 1 s test round-trip against each.
- [ ] **Exit:** conversation round-trip on a real call using only what Hermes configures — change `stt.openai.model` in config.yaml and the call follows without touching the plugin.

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
2. Voice/persona language: German, English, or auto-detect (the STT language hint; the TTS voice comes from whatever Hermes configures).
3. Default mode: `hybrid` (recommended) or `live`.
