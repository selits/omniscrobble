import sys
import app.services.notifier

sys.modules[__name__] = app.services.notifier
Notifier = app.services.notifier.Notifier
notifier = app.services.notifier.notifier
format_media_title = app.services.notifier.format_media_title
get_trakt_url = app.services.notifier.get_trakt_url
