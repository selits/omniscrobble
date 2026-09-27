import sys
import app.clients.sonarr_client

sys.modules[__name__] = app.clients.sonarr_client
SonarrClient = app.clients.sonarr_client.SonarrClient
parse_sonarr_webhook = app.clients.sonarr_client.parse_sonarr_webhook
parse_radarr_webhook = app.clients.sonarr_client.parse_radarr_webhook
map_arr_resolution = app.clients.sonarr_client.map_arr_resolution
map_arr_media_type = app.clients.sonarr_client.map_arr_media_type
