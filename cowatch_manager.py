import sys
import app.services.cowatch_manager

sys.modules[__name__] = app.services.cowatch_manager
CowatchManager = app.services.cowatch_manager.CowatchManager
cowatch_mgr = app.services.cowatch_manager.cowatch_mgr
