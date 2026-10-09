"""Keep rejected persisted mutations out of live runtime state."""

from copy import deepcopy
from functools import wraps


def persisted_mutation(state_attribute, error_type=RuntimeError):
    """Serialize changes, refuse future schemas, and roll back failed mutations."""
    def decorate(method):
        @wraps(method)
        def mutate(self, *args, **kwargs):
            with self._lock:
                if self._future_schema_version is not None:
                    raise error_type("Data uses a newer schema; refusing to modify it with this version.")
                attributes = (state_attribute,) if isinstance(state_attribute, str) else state_attribute
                previous = {name: deepcopy(getattr(self, name)) for name in attributes}
                self._mutation_depth = getattr(self, "_mutation_depth", 0) + 1
                try:
                    return method(self, *args, **kwargs)
                except BaseException:
                    for name, value in previous.items():
                        setattr(self, name, value)
                    raise
                finally:
                    self._mutation_depth -= 1
        return mutate
    return decorate
