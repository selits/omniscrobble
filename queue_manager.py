import sys
import app.services.queue_manager

sys.modules[__name__] = app.services.queue_manager
QueueManager = app.services.queue_manager.QueueManager
process_queue = app.services.queue_manager.process_queue
