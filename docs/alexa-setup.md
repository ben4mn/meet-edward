# Alexa and Echo

Edward connects to Alexa in two independent directions. You can set up either one, both, or neither:

- **Inbound (Alexa → Edward).** A custom Alexa skill lets you ask Edward things through any Echo: "Alexa, ask Edward …".
- **Outbound (Edward → Echo).** Home Assistant speaks on your Echo devices. Edward uses it for a test announcement from Settings → Alexa, and to read out an inbound answer that took too long for Alexa's deadline.

Both are off until configured.

> **Security gap: read this before setting up inbound.** `POST /api/alexa` is a public route, and it checks only the skill id. It does **not** verify Amazon's request signature. An Alexa turn runs as the owner speaking (turn source `alexa`), so anyone who learns your skill id and your URL can run a turn as you. That is acceptable for a private skill on a URL you keep to yourself, and not acceptable for a published skill. See the `ADAPT:` note in `backend/routers/alexa.py`, and [prompt E in ADAPTING.md](ADAPTING.md#e-alexa-request-signature-verification) for adding the verification.

---

## Part A: Inbound (Alexa → Edward)

### Prerequisites

- An **Amazon Developer** account at [developer.amazon.com](https://developer.amazon.com).
- Edward reachable from the internet **over HTTPS**, because Alexa rejects plain HTTP. Use a tunnel or reverse proxy in front of the frontend (e.g. `https://edward.example.com`), or a temporary ngrok URL for testing. The frontend forwards `/api` to the backend.

### Step 1: Create the skill

1. Open the [Alexa Developer Console](https://developer.amazon.com/alexa/console/ask) and click **Create Skill**.
2. Skill name: `Edward`. Primary locale: English (US). Type: **Custom**. Hosting: **Provision your own** (not Alexa-hosted).
3. **Create Skill** → **Start from Scratch**.

### Step 2: The interaction model

1. Left sidebar → **Interaction Model** → **JSON Editor**.
2. Paste:

```json
{
  "interactionModel": {
    "languageModel": {
      "invocationName": "edward",
      "intents": [
        {
          "name": "AskEdwardIntent",
          "slots": [{ "name": "query", "type": "AMAZON.SearchQuery" }],
          "samples": [
            "{query}",
            "ask {query}",
            "tell me {query}",
            "what is {query}",
            "can you {query}",
            "I need {query}",
            "help me with {query}"
          ]
        },
        { "name": "AMAZON.StopIntent", "samples": [] },
        { "name": "AMAZON.CancelIntent", "samples": [] },
        { "name": "AMAZON.HelpIntent", "samples": [] }
      ]
    }
  }
}
```

3. **Save Model** → **Build Model** (takes about 30 seconds).

### Step 3: The endpoint

1. Left sidebar → **Endpoint** → **HTTPS**.
2. Default Region: `https://edward.example.com/api/alexa`, or your ngrok URL followed by `/api/alexa`.
3. SSL certificate type: **My development endpoint has a certificate from a trusted CA**.
4. **Save Endpoints**.

### Step 4: The skill id

Copy the Skill ID shown at the top of the skill page (`amzn1.ask.skill.xxxxxxxx-…`), then add it to `.env` in the checkout:

```bash
ALEXA_SKILL_ID=amzn1.ask.skill.your-skill-id-here
```

Restart the backend with `./restart.sh backend`. While `ALEXA_SKILL_ID` is unset, the route answers 503 and does nothing.

### Step 5: Try it

On any Echo signed in to the same Amazon account as the developer console (the skill stays in Development mode): *"Alexa, open Edward"* or *"Alexa, ask Edward what's on my calendar today."*

### How a request is handled

- Alexa posts the speech to `/api/alexa`. A request whose skill id doesn't match answers 403.
- `LaunchRequest` gets a greeting, Stop/Cancel get "Goodbye", Help gets an example, and `AskEdwardIntent` runs an ordinary Edward turn (`chat_with_memory`, turn source `alexa`) in Edward's Alexa conversation. The prompt tells him to answer in two or three spoken sentences.
- Alexa allows 8 seconds, and Edward gets 6.5 of them. An answer within that time is spoken, followed by "Anything else?".
- A longer answer: Alexa says "Let me work on that. I'll send you a notification when I have an answer." The turn finishes in the background, the answer arrives as a push notification (with Web Push set up) and is also announced on your Echo through Home Assistant (with Part B set up). Either way, it is in the Alexa conversation in the app.

---

## Part B: Outbound (Edward → Echo, through Home Assistant)

### Prerequisites

- A running **Home Assistant** on the same network as the Mac.
- The **Alexa Media Player** integration (from HACS), signed in to your Amazon account. It exposes your Echos as `media_player.echo_*` entities and provides the `notify.alexa_media` service.

### Step 1: A Home Assistant token

In Home Assistant, open your profile → **Long-Lived Access Tokens** → **Create Token**, name it `Edward`, and copy it.

### Step 2: Configure Edward

Add to `.env`:

```bash
HOME_ASSISTANT_URL=http://homeassistant.local:8123
HOME_ASSISTANT_TOKEN=your-long-lived-token

# Optional: the entity prefix that marks an Echo (default media_player.echo),
# and the device used when none is named (default <prefix>_dot).
# HOME_ASSISTANT_ECHO_PREFIX=media_player.echo
# HOME_ASSISTANT_DEFAULT_DEVICE=media_player.echo_kitchen
```

Restart the backend (`./restart.sh backend`), then open **Settings → Alexa**. The panel lists the Echos it found and has a test announcement (`POST /api/alexa/test`).

### What outbound is used for

- The test announcement in Settings → Alexa.
- Reading out an inbound answer that missed Alexa's deadline (Part A).

The model-callable Alexa tools (announce, alarm, sound, list devices) were removed when the 14-tool set replaced the old registry (2026-10-11), so Edward does not announce on Echo by himself. `backend/services/home_assistant_service.py` still has `announce`, `play_sound` and `set_volume` if you want to wire them back in.

---

## Settings reference

| Variable | For | Default | What it is |
|---|---|---|---|
| `ALEXA_SKILL_ID` | Inbound | unset (route answers 503) | The skill id checked on every request |
| `HOME_ASSISTANT_URL` | Outbound | `http://localhost:8123` | Home Assistant's base URL |
| `HOME_ASSISTANT_TOKEN` | Outbound | unset (outbound off) | A long-lived access token |
| `HOME_ASSISTANT_ECHO_PREFIX` | Outbound | `media_player.echo` | The entity prefix that marks an Echo |
| `HOME_ASSISTANT_DEFAULT_DEVICE` | Outbound | `<prefix>_dot` | The Echo used when no device is named |

## Known limitations

- **No request signature verification** on the inbound webhook (see the warning at the top).
- **The Skills switches don't gate anything.** The **Alexa Inbound** and **Alexa Outbound** rows under Settings → Skills show status. To turn inbound off, remove `ALEXA_SKILL_ID`. To turn outbound off, remove `HOME_ASSISTANT_TOKEN`.
- **A scheduled event with `delivery_channel="alexa"`** still tells the model to call `announce_on_alexa()`, a tool that no longer exists, and to fall back to a push notification if the announcement fails. Don't rely on that channel.
- **No announcement history** is kept.

## Troubleshooting

- **"Skill not found".** Make sure the skill is in Development mode and your Echo is on the same Amazon account as the developer console.
- **Alexa says the skill didn't respond.** Check that `https://<your-host>/api/alexa` is reachable from the internet, and look for the request in `/tmp/edward-backend.log`. A 503 means `ALEXA_SKILL_ID` is unset, and a 403 means it doesn't match the skill.
- **No Echos in Settings → Alexa.** Check that Alexa Media Player in Home Assistant shows your devices as `media_player.echo_*`, or set `HOME_ASSISTANT_ECHO_PREFIX` to match your entity names.
- **The announcement fails silently.** Check Home Assistant's logs: the `notify.alexa_media` service must be available.
- **Answers keep arriving as notifications.** Edward has 6.5 s. Questions that need web searches or several tools will usually miss it, and their answers come as a push.
