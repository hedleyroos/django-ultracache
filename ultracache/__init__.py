from contextvars import ContextVar


class Recorder:
    """Records ``(content_type_id, pk)`` tuples in insertion order,
    deduplicating on insert.

    Consumers (the template tag, ``cached_get`` and ``Ultracache``) snapshot
    ``len(recorder)`` when a caching block starts and later read
    ``recorder[start_index:]``. A tuple is only skipped when it has already
    been recorded at or after the current effective dedup *barrier*. A tuple
    recorded before the barrier is recorded again so that it still lands in
    the active block's slice; enclosing blocks may then see it more than
    once, which ``cache_meta`` deduplicates cheaply.

    Each active caching construct pushes its start index as a barrier with
    ``push_barrier`` and pops it with ``pop_barrier`` when it stops
    consuming the recorder. The effective barrier is the MAX of all active
    barriers: constructs may outlive the block they were created in
    (deferred compute), so a single saved/restored integer would lose
    dependencies. A barrier that is too high is safe — it only causes
    re-records which ``cache_meta`` dedups; one that is too low loses
    dependencies.
    """

    __slots__ = ("_items", "_last_index", "_barriers", "_effective_barrier")

    def __init__(self):
        self._items = []
        self._last_index = {}
        self._barriers = []
        # Cached max of the active barriers. append() runs once per recorded
        # attribute access, so the max is maintained on push/pop rather than
        # recomputed per append.
        self._effective_barrier = 0

    def append(self, tu):
        last = self._last_index.get(tu)
        if last is not None and last >= self._effective_barrier:
            return
        self._last_index[tu] = len(self._items)
        self._items.append(tu)

    def push_barrier(self, barrier):
        """Activate a dedup barrier. Barriers form a multiset: the same
        value may be pushed by several constructs and must be popped once
        per push."""
        self._barriers.append(barrier)
        if barrier > self._effective_barrier:
            self._effective_barrier = barrier

    def pop_barrier(self, barrier):
        """Deactivate one occurrence of an active dedup barrier."""
        self._barriers.remove(barrier)
        if barrier == self._effective_barrier:
            self._effective_barrier = max(self._barriers, default=0)

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
