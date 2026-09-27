import sys
import app.services.user_manager

sys.modules[__name__] = app.services.user_manager
UserClientManager = app.services.user_manager.UserClientManager
user_mgr = app.services.user_manager.user_mgr
