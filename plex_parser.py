import sys
import app.plex_parser

sys.modules[__name__] = app.plex_parser
ParsedMedia = app.plex_parser.ParsedMedia
parse_plex_webhook = app.plex_parser.parse_plex_webhook
map_plex_resolution = app.plex_parser.map_plex_resolution
map_plex_audio_channels = app.plex_parser.map_plex_audio_channels
parse_plex_ids = app.plex_parser.parse_plex_ids
