import sys
import app.metrics

sys.modules[__name__] = app.metrics
MetricsRegistry = app.metrics.MetricsRegistry
metrics_registry = app.metrics.metrics_registry
