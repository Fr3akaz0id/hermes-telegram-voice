# hermes-telegram-voice

> **Port in progress.** This is a Telegram port of
> [hermes-whatsapp-voice](https://github.com/ashwanthbalakrishnan5/hermes-whatsapp-voice). The
> plugin layer (tools, CLI, dashboard tab) works and is tested against a stub sidecar; the real
> call transport (Telethon + ntgcalls, user-account SMS login) lands in Phase 2 — see
> [PLAN.md](PLAN.md). Until then: `hermes telegram-voice serve --stub`.

Live voice calls with [Hermes](https://github.com/NousResearch/hermes-agent) over Telegram.

- **Call your bot.** Call your Hermes bot's Telegram number and talk to it. The voice agent can hand
  real work to Hermes mid-call ("move my 3pm to tomorrow", "what did Sam say about the invoice?").
- **Let Hermes call you.** For things that matter, Hermes can ring you instead of sending a message:
  a top-priority reminder, an urgent alert, a cron job that needs a decision. If you don't pick up,
  the tool says so and Hermes falls back to another channel.
- **Summary afterwards.** When a call ends, Hermes digests the transcript and sends you a short
  summary in Telegram.
- **Shared speech stack.** Besides Gemini Live (cloud speech-to-speech), the sidecar can run a
  pipeline mode that reuses exactly what Hermes has configured in `config.yaml` (`stt:`/`tts:` —
  on our setup a LAN-hosted Parakeet + Qwen3-TTS endpoint) — no second speech stack, no bundled
  whisper/piper.

Unlike WhatsApp's linked-device model, Telegram voice calls require a **real user account** — bots
cannot make or receive calls. The bot runs on a spare Telegram account you log in once via SMS.

## How it works

```
 your phone ──Telegram call──▶ bot account ──▶ hermes-tg-voice sidecar (Python: Telethon + ntgcalls)
                                                 │  call audio (48 kHz PCM, WebRTC/SRTP, resampled)
                                                 ▼
                                          SpeechBus — live | hermes-configured
                                            live: Gemini Live (speech-to-speech)
                                            hermes: VAD → Hermes-configured STT → Hermes turn → Hermes TTS
                                                 │  ask_hermes(...) tool calls
                                                 ▼
                                            Hermes API server (/v1/runs)
```

- **The sidecar** (Phase 2) logs in as a **Telegram user account** (MTProto via
  [Telethon](https://github.com/LonamiWebs/Telethon) for call signaling,
  [ntgcalls](https://github.com/LonamiWebs/ntgcalls) for the WebRTC/SRTP audio stream) and
  auto-answers calls from your number only. It answers nothing else and never calls third parties.
- Call audio is bridged to **Gemini Live** (`gemini-3.8-live` by default) for natural, interruptible
  speech-to-speech.
- The voice model gets two tools:
  - `ask_hermes`, which runs a Hermes turn through the API server and speaks the result when it
    arrives;
  - `end_call`, which hangs up after a goodbye.
- The **Hermes plugin** adds two agent tools: `telegram_call_owner(reason, details?)` and
  `telegram_call_status(call_id?)`. It also adds a `hermes telegram-voice` command for setup.

## Requirements

- Hermes `>= 0.21.5` on Linux or macOS.
- A **spare Telegram account** (not your main one): phone number, plus `api_id`/`api_hash` from
  [my.telegram.org/apps](https://my.telegram.org/apps). Bots cannot carry calls; you call the bot
  account as an ordinary user. Auto-answering a main account risks flags — use a throwaway.
- Gemini set up in Hermes with an API key. The plugin reuses the key of Hermes's Gemini provider
  (`GOOGLE_API_KEY`, else `GEMINI_API_KEY`), so there is nothing extra to enter. Without one,
  `hermes model` (choose Gemini) adds it. Google login (Cloud Code OAuth) does not work for the
  Live API. The live models have a free tier, but Google may use free-tier data. Paid use is
  roughly $0.005/min of audio in and $0.018/min out.
- Optional, but needed for the voice agent to reach Hermes: the Hermes
  [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) enabled.
  It must have an `API_SERVER_KEY`.

## Install

**Let your agent do it.** Tell Hermes (or any coding agent on that machine) to follow
[skills/setup/SKILL.md](skills/setup/SKILL.md). It's a step-by-step runbook written for agents,
and it stops to ask you only for the account's number and the SMS login code. After installing,
Hermes can also load it as `hermes-telegram-voice:setup`.

**Or by hand:**

```bash
hermes plugins install Fr3akaz0id/hermes-telegram-voice --enable
```

### 1. Set your number

This is the only number that will ever be called, and the only caller that will be answered.

```bash
hermes config set plugins.entries.hermes-telegram-voice.settings.owner_number +15551234567
```

### 2. Log the sidecar into the bot's Telegram account (once)

```bash
hermes telegram-voice pair --phone +155****1111   # Phase 2: prints an SMS login prompt
```

The first run fetches the sidecar from this repo's GitHub release and stores the account session
in the plugin's private data directory (mode 700). Until the Phase 2 transport lands, `pair`
prints a placeholder and `hermes telegram-voice serve --stub` runs the stub sidecar so you can
exercise the plugin layer.

> **Why a separate account?** Telegram calls ride on a user (MTProto) session — a Bot-API token
> cannot carry calls at all, and running the sidecar on your main account would put it in
> auto-answer/automation territory that Telegram flags. Use a spare number.

### 3. Enable the Hermes API server (recommended)

```bash
# in Hermes's .env (~/.hermes/.env by default)
API_SERVER_ENABLED=true
API_SERVER_KEY=<a long random string>
```

Restart the gateway after this.

### 4. Run the sidecar

```bash
hermes telegram-voice serve      # foreground, to try it out
hermes telegram-voice service    # prints a systemd user unit (Linux) or launchd plist (macOS)
```

To keep it running, save the printed unit and enable it as the comments in it describe. Check it
with:

```bash
hermes telegram-voice status
hermes telegram-voice call "Testing the new voice line"
```

## Using it

Call your bot's number from your phone. Or ask Hermes to call you:

> Call me at 7:30 tomorrow about the flight check-in, and if I don't answer, set an urgent reminder.

`telegram_call_owner` works from cron runs too, so a scheduled job can escalate to a phone call:

```bash
hermes cron create "0 9 * * 1-5" \
  "If anything on my calendar today is marked urgent, call me about it with telegram_call_owner."
```

The tool waits until the call is answered, declined or unanswered, and returns that status. On
`no_answer`/`rejected`/`failed` it tells the agent to use another channel.

## Configure

Everything is configurable from the **Telegram Voice** tab in the Hermes dashboard, the Desktop
app's plugin settings, or `hermes config set`. That covers the voice, the model, your own
instructions (language, tone, call length), and any Gemini Live option via `live_config`. The
voice takes its name and personality from your own Hermes. Full reference:
[docs/configuration.md](docs/configuration.md).

## What this plugin does that you should know about

- **Long-running process.** The sidecar runs as a separate process that you start (`serve`, or the
  systemd/launchd unit). The plugin itself only talks to it over loopback HTTP, authenticated with a
  random token stored in the plugin's data directory.
- **Stored credentials.** The sidecar's Telegram user-account session (MTProto auth key) is stored
  at `<hermes home>/plugin-data/hermes-telegram-voice/telegram.session` (the folder is private, mode
  700). Anyone with that file can act as that account — treat it like a password. Revoke the session
  any time from the account's phone (Settings › Devices › terminate session).
- **Network calls.**
  - Telegram servers (signaling and call media relays).
  - `generativelanguage.googleapis.com` (Gemini), using the Gemini key Hermes already has. It
    receives call audio during calls, plus, as text: the persona briefing (Hermes's name,
    personality and what it knows about you), the reason and details of an outgoing call, and the
    results of `ask_hermes` requests.
  - Your local Hermes API server, using `API_SERVER_KEY`, for `ask_hermes` (which runs a full
    Hermes turn with your tools), the persona briefing (fetched at startup and after each call),
    and the post-call summary. That summary run asks Hermes to note anything worth remembering, so
    it can write to Hermes's memory.
  - `github.com`, to download the sidecar binary on first `pair`/`serve`/`install` (or from the
    dashboard's Pair button).

  Nothing else, and no telemetry.
- **Who it talks to.** It only answers calls from `owner_number` and only calls `owner_number`.
  Neither tool takes a destination parameter, so a prompt injection cannot make it call anyone else.
  Calls from other numbers are left alone, so the bot's other devices can still take them.
- **Commands it runs.**
  - The dashboard tab's backend (inside the Hermes dashboard, behind its login): `hermes config
    set` for this plugin's settings (only keys declared in `plugin.yaml`); the sidecar's `pair`;
    and `systemctl --user restart` or `launchctl kickstart` for the sidecar service.
  - The CLI: `gh attestation verify` when the GitHub CLI is logged in, and `cargo build` for
    `install --from-source`.
  - The setup skill (only when an agent follows it) appends `API_SERVER_ENABLED` and a generated
    `API_SERVER_KEY` to Hermes's `.env` if they're missing.
- **Secrets** are read through Hermes (`get_secret`) and passed to the sidecar in its environment.
  The plugin never reads `.env` itself. The sidecar's local API only binds to a loopback address.

## Caveats

- Telegram calling here uses an **unofficial**, reverse-engineered protocol (via telegram-rust).
  Telegram updates can break it, and automated use may violate Telegram's Terms of Service. Use a
  number you can afford to lose.
- One call at a time. Group calls and video are not supported.
- A Gemini Live connection lasts about 10 minutes. The sidecar resumes the session transparently, but
  a resume can fail and end a long call.

## Development

```bash
python -m pip install -r requirements-dev.txt
ruff check . && python -m pytest tests/ -q
cd sidecar && cargo test
GEMINI_API_KEY=... cargo test -- --ignored   # live round trip against Gemini
```

Layout: the repo root is the Hermes plugin (`plugin.yaml`, `__init__.py`, `tools.py`, `cli.py`,
`sidecar_client.py`), and `sidecar/` is the Rust sidecar. Tagging `vX.Y.Z` builds attested release
binaries (`.github/workflows/release.yml`). Then commit the release's `SHA256SUMS` as
`sidecar/release-checksums.txt`. Keep `version` in `plugin.yaml` and `sidecar/Cargo.toml` in step
with the tag.

## Credits

- [telegram-rust](https://github.com/oxidezap/telegram-rust) (MIT) does the Telegram protocol and the
  call media work.
- Gemini Live provides the voice.

## License

MIT
