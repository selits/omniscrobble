import sys
import app.config

sys.modules[__name__] = app.config
Config = app.config.Config
BASE_DIR = app.config.BASE_DIR
