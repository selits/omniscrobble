import sys
import app.services.playback_manager

sys.modules[__name__] = app.services.playback_manager
PlaybackManager = app.services.playback_manager.PlaybackManager
playback_mgr = app.services.playback_manager.playback_mgr
mask_username_simple = app.services.playback_manager.mask_username_simple
