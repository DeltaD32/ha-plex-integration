"""Constants for the Plex Voice integration."""

DOMAIN = "plex_voice"
PLATFORMS = ["media_player", "sensor", "binary_sensor"]

# Config entry keys
CONF_PLEX_URL = "plex_url"
CONF_PLEX_TOKEN = "plex_token"
CONF_SERVER_NAME = "server_name"
# List of {"id": machine_id, "name": display_name} dicts. Empty = monitor all.
CONF_MONITORED_CLIENTS = "monitored_clients"

# Data keys
DATA_COORDINATOR = "coordinator"

# Intent names (must match intents.yaml)
INTENT_PLAY_MEDIA = "PlexPlayMedia"
INTENT_CLARIFY_TYPE = "PlexClarifyType"
INTENT_CLARIFY_TITLE = "PlexClarifyTitle"

# Plex media types
PLEX_TYPE_MOVIE = "movie"
PLEX_TYPE_SHOW = "show"
PLEX_TYPE_SEASON = "season"
PLEX_TYPE_EPISODE = "episode"
PLEX_TYPE_MUSIC = "artist"
PLEX_TYPE_ALBUM = "album"
PLEX_TYPE_TRACK = "track"

# Session key for multi-turn voice conversations
SESSION_KEY = "plex_voice_session"

# Media player entity prefix
PLAYER_ENTITY_PREFIX = "media_player."

# Polling interval for active sessions (seconds)
POLL_INTERVAL = 30
