import sys
import app.clients.trakt_client

sys.modules[__name__] = app.clients.trakt_client
TraktClient = app.clients.trakt_client.TraktClient
