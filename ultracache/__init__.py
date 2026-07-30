from contextvars import ContextVar


class Recorder:
    """Records ``(content_type_id, pk)`` tuples in insertion order,
    deduplicating on insert.

    Consumers (the template tag, ``cached_get`` and ``Ultracache``) snapshot
    ``len(recorder)`` when a caching block starts and later read
    ``recorder[start_index:]``. A tuple is only skipped when it has already
    been recorded at or after the current dedup *barrier* — the start index
    of the innermost active caching block. A tuple recorded before the
    barrier is recorded again so that it still lands in the active block's
    slice; enclosing blocks may then see it more than once, which
    ``cache_meta`` deduplicates cheaply.
    """

    __slots__ = ("_items", "_last_index", "_barrier")

    def __init__(self):
        self._items = []
        self._last_index = {}
        self._barrier = 0

    def append(self, tu):
        last = self._last_index.get(tu)
        if last is not None and last >= self._barrier:
            return
        self._last_index[tu] = len(self._items)
        self._items.append(tu)

    def set_barrier(self, barrier):
        """Set the dedup barrier and return the previous barrier."""
        previous = self._barrier
        self._barrier = barrier
        return previous

    def __len__(self):
        return len(self._items)

    def __getitem__(self, index):
        return self._items[index]

    def __iter__(self):
        return iter(self._items)


# The recorder for the current thread / async context. ``None`` means
# recording is inactive, which is the common case and must stay cheap: the
# guard in the ``Model.__getattribute__`` patch is a single ``.get()``.
_recorder: ContextVar = ContextVar("ultracache_recorder", default=None)


def get_recorder():
    """Return the recorder for the current context, or None if recording is
    inactive."""
    return _recorder.get()


def get_or_create_recorder():
    """Return the recorder for the current context, creating it lazily."""
    recorder = _recorder.get()
    if recorder is None:
        recorder = Recorder()
        _recorder.set(recorder)
    return recorder


def set_recorder(recorder):
    """Install ``recorder`` as the recorder for the current context."""
    _recorder.set(recorder)


def clear_recorder():
    """Deactivate recording for the current context."""
    _recorder.set(None)
