import threading
from contextvars import ContextVar


default_app_config = "ultracache.apps.UltracacheAppConfig"


class ContextVarsLocal:
    def __init__(self):
        self._storage = ContextVar("ultracache_storage", default={})

    def __getattr__(self, name):
        storage = self._storage.get()
        try:
            return storage[name]
        except KeyError:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def __setattr__(self, name, value):
        if name == "_storage":
            super().__setattr__(name, value)
            return
        
        # We need to copy the storage to ensure isolation between contexts
        storage = self._storage.get().copy()
        storage[name] = value
        self._storage.set(storage)

    def __delattr__(self, name):
        storage = self._storage.get()
        if name in storage:
            # We need to copy the storage to ensure isolation between contexts
            new_storage = storage.copy()
            del new_storage[name]
            self._storage.set(new_storage)
        else:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


# _thread_locals = threading.local()
_thread_locals = ContextVarsLocal()
