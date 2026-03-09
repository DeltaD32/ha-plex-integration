# Plex Voice — Home Assistant Custom Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/yourusername/ha-plex-voice.svg)](https://github.com/yourusername/ha-plex-voice/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=yourusername&repository=ha-plex-voice&category=integration)

A fully packaged Home Assistant custom integration that connects your Plex Media Server with:

- **Media browser** — browse libraries and play content from the HA Media panel
- **Voice assistant** — conversational multi-turn flows for hands-free playback
- **Media player entities** — one entity per active Plex client (TV, phone, etc.)

---

## Voice Flow Example

> "Play Star Wars on the living room TV"

If Plex has both a movie and a show named Star Wars:
> "I found Star Wars as both a movie and a show on Plex. Would you like the movie or the show?"

> "Show"

> "I found these shows: Star Wars: The Clone Wars, Star Wars: Rebels, and Star Wars: Andor. Which one would you like?"

> "Andor"

> "Okay! Playing Star Wars: Andor on Living Room TV."

---

## Installation

### Option A — HACS (Recommended)

1. In Home Assistant, go to **HACS → Integrations**
2. Click the **⋮ menu → Custom Repositories**
3. Add `https://github.com/yourusername/ha-plex-voice` as category **Integration**
4. Search for **Plex Voice** and click **Download**
5. Restart Home Assistant

Or click the button at the top of this README to jump straight there.

### Option B — Manual

Copy the `custom_components/plex_voice/` folder into your HA config directory:

```
config/
  custom_components/
    plex_voice/
      __init__.py
      config_flow.py
      const.py
      coordinator.py
      intents.py
      manifest.json
      media_player.py
      translations/
        en.json
```

### After installing (both methods)

**Add the voice intents** — copy (or merge) `intents.yaml` into your HA config root:

```
config/intents.yaml
```

If you already have an `intents.yaml`, merge the `intents:` block into it.

**Restart Home Assistant** fully (not just reload).

**Add the integration:**
1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **"Plex Voice"**
3. Enter your Plex server URL: `http://YOUR-PLEX-IP:32400`
4. Enter your [Plex token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)
5. Click Submit

---

## Finding Your Plex Token

1. Open Plex Web in your browser
2. Play any media item
3. Open browser DevTools → Network tab
4. Look for a request to your Plex server
5. Find `X-Plex-Token=` in the URL

Or follow the [official guide](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/).

---

## Voice Assistant Setup

This integration registers intent handlers with HA's built-in intent system, which works with:

- **Assist** (built-in HA voice assistant)
- **Wyoming + Whisper** (local speech-to-text — great for self-hosted setups)
- **Google Assistant** (via Nabu Casa)
- **Amazon Alexa** (via Nabu Casa)

### Sentence patterns supported

| You say | Action |
|---|---|
| "Play Inception on the living room TV" | Searches Plex, plays if found |
| "Play The Office on the bedroom TV" | Finds show, plays or asks which season |
| "Play Star Wars" | Asks movie or show if both exist |
| "Movie" | Clarifies to movie results |
| "The Clone Wars" | Picks from a list of matches |

### Room → Entity matching

The integration matches your spoken room name to `media_player` entity friendly names. For example:
- "living room TV" → matches `media_player.plex_living_room_tv`
- "bedroom" → matches `media_player.plex_bedroom`

Rename your entities' friendly names in HA to match how you naturally say them.

---

## Media Browser

Once installed, Plex libraries are accessible from:

**HA Dashboard → Media tab → Plex Voice**

You can browse Movies, TV Shows, and Music, then cast directly to any Plex client.

---

## Unraid / Self-Hosted Notes

If you're running HA in Docker on Unraid and Plex is also in Docker:

- Use your **Unraid host IP** (not `localhost`) for the Plex URL: `http://192.168.1.X:32400`
- Make sure the Plex container port `32400` is exposed on the host
- For voice: pair with Wyoming + Whisper + openWakeWord containers for fully local voice pipeline

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Cannot connect to Plex" | Check IP/port, ensure Plex is running and accessible from HA |
| "Invalid auth" | Regenerate your X-Plex-Token |
| No player entities created | Open Plex on your TV/device first so it shows as an active client |
| Voice not triggering | Check `intents.yaml` is in `/config/`, restart HA, check Assist is enabled |
| Room not found | Check that your `media_player` entity friendly name contains the spoken room name |

---

## Architecture

```
__init__.py         Entry setup, intent registration
config_flow.py      UI-based setup wizard
const.py            Constants and config keys
coordinator.py      Plex API client (aiohttp, local network)
media_player.py     MediaPlayerEntity + BrowseMedia tree
intents.py          Voice intent handlers (multi-turn conversation)
translations/       UI strings
intents.yaml        Sentence patterns for HA Assist
```
