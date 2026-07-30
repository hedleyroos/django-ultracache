import hashlib
from collections import OrderedDict

from django.core.cache import caches
from django.conf import settings
from django.http.cookie import SimpleCookie

from ultracache import get_or_create_recorder


# Prefix for every key ultracache stores in the cache backend. The "3" is a
# cache format version: 3.0 changed the shape of cached payloads (public
# header mapping in cached_get), so entries written by 2.x must be ignored
# rather than mis-parsed after an upgrade.
KEY_PREFIX = "ucache3-"

# Historic default lifetime of the invalidation metadata. Metadata is never
# stored for less time than the content it invalidates (see cache_meta).
METADATA_TIMEOUT = 86400

# Sentinel distinguishing "timeout not passed" from an explicit None
# (Django's "cache forever").
_timeout_unset = object()


def get_cache():
    """Return the cache backend ultracache stores its data in.

    Defaults to the "default" alias and can be pointed at another backend
    with the ULTRACACHE["cache_alias"] setting. Resolved on every call so
    override_settings and runtime reconfiguration are respected."""
    ultracache_settings = getattr(settings, "ULTRACACHE", None) or {}
    return caches[ultracache_settings.get("cache_alias", "default")]


# The metadata itself can't be allowed to grow endlessly. This value is the
# maximum size in bytes of a metadata list. If your caching backend supports
# compression set a larger value.
try:
    MAX_SIZE = settings.ULTRACACHE["max-registry-value-size"]
except (AttributeError, KeyError):
    MAX_SIZE = 1000000

try:
    CONSIDER_HEADERS = [header.lower() for header in settings.ULTRACACHE["consider-headers"]]
except (AttributeError, KeyError):
    CONSIDER_HEADERS = []

try:
    CONSIDER_COOKIES = [cookie.lower() for cookie in settings.ULTRACACHE["consider-cookies"]]
except (AttributeError, KeyError):
    CONSIDER_COOKIES = []

# Raise on potentially confusing settings
if CONSIDER_COOKIES and ("cookie" in CONSIDER_HEADERS):
    raise RuntimeError(
        "consider-cookies has a value but cookie is also present in \
consider-headers"
    )


def reduce_list_size(li):
    """Return two lists
    - the last N items of li whose total size is less than MAX_SIZE
    - the rest of the original list li
    """
    # sys.getsizeof is nearly useless. All our data is stringable so rather
    # use that as a measure of size.
    size = len(repr(li))
    keep = li
    toss = []
    n = len(li)
    decrement_by = max(n // 10, 10)
    while (size >= MAX_SIZE) and (n > 0):
        n -= decrement_by
        if n <= 0:
            # Even the smallest tail is too large. Toss everything.
            keep = []
            toss = li
            break
        toss = li[:-n]
        keep = li[-n:]
        size = len(repr(keep))
    return keep, toss


def cache_meta(recorder, cache_key, start_index=0, request=None, timeout=_timeout_unset):
    """Set the invalidation metadata entries for the objects recorded for
    cache_key.

    ``timeout`` is the timeout of the content entry itself. The metadata is
    stored for at least as long as the content: metadata outliving content
    is harmless, but content outliving metadata could never be invalidated.
    A timeout of None means the content never expires, so neither does the
    metadata."""

    cache = get_cache()

    if timeout is _timeout_unset:
        meta_timeout = METADATA_TIMEOUT
    elif timeout is None:
        meta_timeout = None
    else:
        meta_timeout = max(timeout, METADATA_TIMEOUT)

    path = None
    if request is not None:
        path = request.get_full_path()
        # todo: cache headers on the recorder since they never change during the
        # request.

        # Reduce headers to the subset as defined by the settings
        headers = OrderedDict()
        for k, v in sorted(request.META.items()):
            if (k == "HTTP_COOKIE") and CONSIDER_COOKIES:
                cookie = SimpleCookie()
                cookie.load(v)
                headers["cookie"] = "; ".join(
                    [
                        "%s=%s" % (k, morsel.value)
                        for k, morsel in sorted(cookie.items())
                        if k in CONSIDER_COOKIES
                    ]
                )
            elif k.startswith("HTTP_"):
                k = k[5:].replace("_", "-").lower()
                if k in CONSIDER_HEADERS:
                    headers[k] = v

    # The recorder dedups on insert per caching block but may still contain
    # cross-block repeats. Dedup the slice once, preserving insertion order.
    recorded = list(dict.fromkeys(recorder[start_index:]))

    # Key lists needed for cache.get_many, deduplicated while preserving
    # order. dict.fromkeys avoids the quadratic list membership scans.

    # The object appears in these cache entries. If the object is modified
    # then these cache entries are deleted.
    to_set_get_keys = list(
        dict.fromkeys(
            "%s%s-%s" % (KEY_PREFIX, ctid, obj_pk) for ctid, obj_pk in recorded
        )
    )

    # The object appears in these paths. If the object is modified then any
    # caches that are read from when browsing to this path are cleared.
    to_set_paths_get_keys = list(
        dict.fromkeys(
            "%spth-%s-%s" % (KEY_PREFIX, ctid, obj_pk) for ctid, obj_pk in recorded
        )
    )

    # The content type appears in these cache entries. If an object of this
    # content type is created then these cache entries are cleared.
    to_set_content_types_get_keys = list(
        dict.fromkeys("%sct-%s" % (KEY_PREFIX, ctid) for ctid, obj_pk in recorded)
    )

    # The content type appears in these paths. If an object of this content
    # type is created then any caches that are read from when browsing to
    # this path are cleared.
    to_set_content_types_paths_get_keys = list(
        dict.fromkeys("%sct-pth-%s" % (KEY_PREFIX, ctid) for ctid, obj_pk in recorded)
    )

    # Dictionaries needed for cache.set_many
    to_set = {}
    to_set_paths = {}
    to_set_content_types = {}
    to_set_content_types_paths = {}

    to_delete = []

    # A list of objects that contribute to a cache entry
    to_set_objects = recorded

    # todo: rewrite to handle absence of get_many
    di = cache.get_many(to_set_get_keys)
    for key in to_set_get_keys:
        v = di.get(key, None)
        keep = []
        if v is not None:
            keep, toss = reduce_list_size(v)
            if toss:
                to_set[key] = keep
                to_delete.extend(toss)
        if cache_key not in keep:
            if key not in to_set:
                to_set[key] = keep
            to_set[key] = to_set[key] + [cache_key]
    if to_set == di:
        to_set = {}

    di = cache.get_many(to_set_paths_get_keys)
    for key in to_set_paths_get_keys:
        v = di.get(key, None)
        keep = []
        if v is not None:
            keep, toss = reduce_list_size(v)
            if toss:
                to_set_paths[key] = keep
        if path is not None:
            if [path, headers] not in keep:
                if key not in to_set_paths:
                    to_set_paths[key] = keep
                to_set_paths[key] = to_set_paths[key] + [[path, headers]]
    if to_set_paths == di:
        to_set_paths = {}

    di = cache.get_many(to_set_content_types_get_keys)
    for key in to_set_content_types_get_keys:
        v = di.get(key, None)
        keep = []
        if v is not None:
            keep, toss = reduce_list_size(v)
            if toss:
                to_set_content_types[key] = keep
                to_delete.extend(toss)
        if cache_key not in keep:
            if key not in to_set_content_types:
                to_set_content_types[key] = keep
            to_set_content_types[key] = to_set_content_types[key] + [cache_key]
    if to_set_content_types == di:
        to_set_content_types = {}

    di = cache.get_many(to_set_content_types_paths_get_keys)
    for key in to_set_content_types_paths_get_keys:
        v = di.get(key, None)
        keep = []
        if v is not None:
            keep, toss = reduce_list_size(v)
            if toss:
                to_set_content_types_paths[key] = keep
        if path is not None:
            if [path, headers] not in keep:
                if key not in to_set_content_types_paths:
                    to_set_content_types_paths[key] = keep
                to_set_content_types_paths[key] = to_set_content_types_paths[key] + [
                    [path, headers]
                ]
    if to_set_content_types_paths == di:
        to_set_content_types_paths = {}

    # Deletion must happen first because set may set some of these keys
    if to_delete:
        try:
            cache.delete_many(to_delete)
        except NotImplementedError:
            for k in to_delete:
                cache.delete(k)

    # Do one set_many
    di = {}
    di.update(to_set)
    del to_set
    di.update(to_set_paths)
    del to_set_paths
    di.update(to_set_content_types)
    del to_set_content_types
    di.update(to_set_content_types_paths)
    del to_set_content_types_paths

    if to_set_objects:
        di[cache_key + "-objs"] = to_set_objects

    if di:
        try:
            cache.set_many(di, meta_timeout)
        except NotImplementedError:
            for k, v in di.items():
                cache.set(k, v, meta_timeout)


def get_current_site_pk(request):
    """Centralize the import so calling code doesn't require the sites
    framework to be installed."""
    from django.contrib.sites.shortcuts import get_current_site

    return get_current_site(request).pk


class EmptyMarker:
    pass


empty_marker_1 = EmptyMarker()
empty_marker_2 = EmptyMarker()


class Ultracache:
    """Cache arbitrary pieces of Python code."""

    def __init__(self, timeout, name, *params, request=None):
        self.timeout = timeout
        self.request = request
        self._cached = empty_marker_1
        s = ":".join([name] + [str(p) for p in params])
        hashed = hashlib.md5(s.encode("utf-8"), usedforsecurity=False).hexdigest()
        self.cache_key = KEY_PREFIX + hashed
        self.recorder = get_or_create_recorder()
        self.start_index = len(self.recorder)
        # Objects recorded before this point must be recorded again so they
        # land in this block's slice of the recorder. There is no reliable
        # "end of block" hook on the cache-hit path so the barrier is not
        # restored; a stale high barrier is harmless because every caching
        # block raises the barrier to its own start index.
        self.recorder.set_barrier(self.start_index)
        self.used = False

    @property
    def cached(self):
        if self._cached is empty_marker_1:
            self._cached = get_cache().get(self.cache_key, empty_marker_2)
        return self._cached

    def __bool__(self):
        return self.cached is not empty_marker_2

    def cache(self, value):
        if self.used:
            raise RuntimeError("The cache method may only be called once per Ultracache object.")
        get_cache().set(self.cache_key, value, self.timeout)
        cache_meta(
            self.recorder,
            self.cache_key,
            start_index=self.start_index,
            request=self.request,
            timeout=self.timeout,
        )
        self.used = True
