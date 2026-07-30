# Improvements Roadmap

Target: a 3.0 release supporting Django ≥ 4.1 on Python ≥ 3.11 (matching the tox
matrix). Work through the phases in order — each phase should land with tests and
a green `tox` run before starting the next.

**Status of uncommitted work in progress.** The working tree contains an active
performance investigation: `benchmark.py` (micro-benchmark of the
`Model.__getattribute__` patch), timing instrumentation in `monkey.py`
(`profile_stats`, `import time`), and two `middleware.py` changes — the
marker-name fix (item 6) and lazy recorder creation (`process_request` no longer
seeds `ultracache_recorder`). Phase 3 formalizes this work; the instrumentation
must be stripped before release.

## Phase 1 — Crashers and correctness bugs

### 1. varnish/nginx purgers crash with the documented settings shape

`purgers.py:16` and `purgers.py:27` read
`settings.ULTRACACHE["purge"]["method"]["url"]`. But `signals.py:17` (and the
README) require `method` to be a dotted-path *string* such as
`"ultracache.purgers.varnish"`, with `url` as its sibling key. Indexing a string
with `["url"]` raises `TypeError`, so these purgers crash on every invocation —
the `except RequestException` does not catch it. Fix: read
`settings.ULTRACACHE["purge"]["url"]`. Add a regression test using the
README-shaped settings dict.

### 2. `TemplateSyntaxError` is never imported in `ultracache_tags.py`

The module only imports `from django import template`, yet references the bare
name `TemplateSyntaxError` in three places: `render()` lines 42 and 48, and
`do_ultracache` line 102. Every error path raises `NameError` instead of the
intended exception. Import it from `django.template`.

### 3. Malformed format string in `do_ultracache`

`ultracache_tags.py:102`:
`"" % r" tag requires at least 2 arguments." % tokens[0]` — the leading `""`
with a `%` operand raises `TypeError` ("not all arguments converted"). Should be
`"'%s' tag requires at least 2 arguments." % tokens[0]`.

### 4. Unbound / stale variable in the vary-on loop

`ultracache_tags.py:71-78`: when `var.resolve(context)` raises
`VariableDoesNotExist` the loop does `pass`, then unconditionally appends `r`.
If the *first* vary-on variable is unresolvable this is a `NameError`; otherwise
the previous variable's value is silently appended twice, corrupting the cache
key. Fix: append a stable placeholder (e.g. `""`) on resolution failure, or
`continue`. Add a test caching with an unresolvable first vary-on variable.

### 5. `reduce_list_size()` float division and negative-slice edge

`utils.py:48`: `decrement_by = max(n / 10, 10)` produces a float in Python 3, so
`li[:-n]` raises `TypeError`. Change to `n // 10`. Additionally, `n -=
decrement_by` can step past zero (e.g. `n = 5`, decrement 10 → `n = -5`), which
flips the slices (`li[:-n]`/`li[-n:]`) into nonsense. Clamp `n` at 0 and exit,
tossing the remainder. Cover both in tests (see item 34).

### 6. Marker-name mismatch between `monkey.py` and `middleware.py`

`monkey.py` sets `_ultracache_attr_marker` (leading underscore) but the
committed `middleware.py` cleanup deletes `ultracache_attr_marker` (no
underscore) — a no-op. The marker is a **reentrancy guard** preventing infinite
recursion when `__getattribute__` evaluates `hasattr(self, "pk")`, not a lock.
*Already fixed in the uncommitted WIP*; note that item 14 removes the marker
entirely, which moots this permanently.

### 7. `on_post_delete` never deletes the path registry key when a purger is set

`signals.py:127-134`: the purger branch reads the `ucache-pth-%s-%s` items and
calls the purger, but — unlike `on_post_save` (lines 86-88, whose comment says
the key *must* be deleted first in case the purger fails) — it never calls
`cache.delete(key)`. The stale path list survives and gets re-purged on every
subsequent delete. Add the delete, mirroring `on_post_save`.

### 8. `eval()` in `decorators.py`

`decorators.py:69` uses `eval(param)` to resolve decorator parameter strings
like `"request.is_secure()"`. The strings are developer-supplied, not user
input, so this is not remote code execution — but it is still fragile and an
anti-pattern. Replace with:

- **New API**: accept callables — `cached_get(300, lambda request: request.is_secure())`.
- **Legacy strings**: a restricted resolver that supports dotted attribute
  traversal plus a trailing no-arg call (covers `request.is_secure()`), scoped
  to `request` only. Deprecate string params with a warning.

## Phase 2 — Dead code and deprecated patterns

### 9. The entire `Variable._resolve_lookup` patch is dead code

The recording branch in `monkey.py:88-92` guards on
`hasattr(context["request"], "_ultracache")` — but nothing in the codebase sets
`request._ultracache` any more; recording moved to
`_thread_locals.ultracache_recorder` and the `Model.__getattribute__` patch
supersedes template-level recording (it fires for template variable access too).
Delete `my_resolve_lookup` and the `Variable._resolve_lookup` assignment
(~85 lines). This also eliminates the risk of the Django 1.11-era copy diverging
from current Django behaviour. Verify with the full test suite; also update
`architecture.md`, which still describes variable-resolution patching.

### 10. Python 2 / old-Django compat blocks

All dead given the Django ≥ 4.1 / Python ≥ 3.11 floor:

- `tasks.py:3-8`: `urllib.parse` vs `urlparse` fallback
- `signals.py:11-14`: `import_by_path` fallback (removed Django 1.9)
- `utils.py:233-236` (`get_current_site_pk`): `get_current_site` fallback import —
  keep the function (it centralizes the import) but drop the try/except
- `models.py`: `TEMPLATE_CONTEXT_PROCESSORS` fallback and
  `django.core.context_processors.request` check (removed Django 1.10 / 1.8)
- `ultracache_tags.py:3-10`: `force_text` and `ugettext` fallbacks
- `ultracache_tags.py:30-36`: the `cache_name` try/except for Django < 1.7
- `monkey.py:21-24`: `logger` import fallback (present in all supported versions)

### 11. Unused imports

- `monkey.py`: `hashlib`, `pickle`, `types`, `OrderedDict`, `cache`, `Manager`,
  `cache_meta`, `get_current_site_pk` (the last four were missed by the original
  audit); `inspect` becomes unused once item 9 lands, `time` once item 14 lands
- `signals.py`: `threading`
- `decorators.py`: `types`
- `__init__.py`: `threading` (only the commented-out line uses it)

Run a linter (e.g. `ruff check`) after Phases 1-2 to catch stragglers.

### 12. `models.py` misnamed; move startup logic to `apps.py:ready()`

`models.py` defines no models — it validates the request context processor at
import time and imports `monkey.py`. Move both into
`UltracacheAppConfig.ready()` (which already imports `signals`), delete
`models.py`, and raise `ImproperlyConfigured` instead of `RuntimeError` for the
context-processor check. Also remove the deprecated `default_app_config`
(`__init__.py:5`, removed in Django 5.1) and the commented-out
`threading.local()` line (`__init__.py:40`).

### 13. Cosmetic

- `middleware.py`: drop the `(object)` base class
- `purgers.py:7`: "methody" → "method"

## Phase 3 — Recording hot-path performance

Formalizes the WIP benchmark investigation. The hot path is
`my__getattribute__` in `monkey.py`, which runs on **every attribute access on
every model instance** in the application.

### 14. Eliminate the reentrancy marker

The marker exists only because `hasattr(self, "pk")` recurses into
`__getattribute__`. Read the pk without recursion instead:

```python
d = object.__getattribute__(self, "__dict__")
pk = d.get(object.__getattribute__(self, "_meta").pk.attname)
```

(or equivalent via `type(self)._meta`). No recursion → no marker → no marker
set/delete per recorded access. With `ContextVarsLocal` the marker currently
costs **two full dict copies per recorded attribute access** (one `__setattr__`,
one `__delattr__`) — this is the single biggest win the profiling points at.
Also mooted: item 6 permanently, and the marker branch of middleware cleanup.

### 15. Replace `ContextVarsLocal` with dedicated `ContextVar`s

The generic copy-on-write dict in `__init__.py` makes the guard check
(`hasattr(_thread_locals, "ultracache_recorder")`) exception-driven — a
`ContextVar.get` plus dict lookup plus raised-and-caught `AttributeError` on the
common inactive path. Replace with module-level vars:

```python
_recorder: ContextVar["list | None"] = ContextVar("ultracache_recorder", default=None)
```

The inactive-path guard becomes a single `_recorder.get() is None`, and
activation/cleanup become `.set()` calls. Keep `_thread_locals` as a thin
compatibility shim only if external code touches it; otherwise delete
`ContextVarsLocal`.

### 16. Lazy recorder creation (adopt the WIP middleware change)

The uncommitted `middleware.py` no longer seeds the recorder in
`process_request`; the template tag, `cached_get`, and `Ultracache` create it
lazily. Keep this — recording then only happens once a caching construct is
active instead of for the whole request. Consequence to handle explicitly: the
recorder now persists in the thread/context after the response unless cleaned
up, so the middleware's cleanup role becomes **mandatory**. Either document that
clearly (see item 41) or drop the middleware requirement entirely by cleaning up
via the `request_finished` signal, which works without any configuration.

### 17. Dedup at record time

The recorder is a list that receives one entry per attribute access — rendering
a 50-object list touches hundreds of appends of the same `(ct_id, pk)` tuples,
which `cache_meta` then dedups quadratically. Record into a structure that
dedups on insert (an ordered dict keyed by tuple, or list + companion set),
preserving insertion order for `start_index` slicing.

### 18. `cache_meta` is O(n²)

`utils.py:100-129` does repeated `key not in <list>` membership checks over four
parallel lists (plus `tu not in to_set_objects`). With item 17 much of this
disappears; convert the rest to `dict`/`set` lookups.

### 19. Benchmark methodology and cleanup

- Strip `profile_stats`, `import time`, and all `perf_counter` calls from
  `monkey.py` (restore the clean control flow).
- Promote `benchmark.py` into the repo properly (e.g. `bin/benchmark.py` next to
  the other operational scripts), and extend it to cover the three states it
  already measures (baseline / patched-inactive / patched-active) **after** items
  14-17 land.
- Record before/after numbers in the changelog so the 3.0 release notes can
  quantify the overhead reduction.
- Decide whether `toxb.ini` (untracked, legacy Django 2.0-4.2 matrix) should be
  deleted or kept as a historical reference.

## Phase 4 — Robustness and design

### 20. Metadata TTL is hardcoded to 86400s

`utils.py:222` uses `cache.set_many(di, 86400)` regardless of the content
entry's timeout. Content cached longer than a day outlives its invalidation
metadata and can never be invalidated. Pass the content timeout into
`cache_meta` and use `max(timeout, 86400)` (metadata may outlive content
harmlessly; the reverse is the bug).

### 21. `signals.py` reads settings at import time

`purger` and `invalidate` (`signals.py:16-24`) are evaluated once at import, so
`@override_settings` in tests (and any runtime reconfiguration) is ignored.
Resolve them lazily inside the handlers, memoized with a
`setting_changed`-signal reset for test friendliness.

### 22. Deduplicate the signal handlers

The update branch of `on_post_save` (lines 71-92) and the body of
`on_post_delete` (lines 115-134) are nearly identical, and the created-branch
differs only in key prefixes. Extract a helper
`_invalidate(keys_key, purge_key)` used by all three paths. This also
naturally fixes item 7 in one place.

### 23. Unify `varnish()` and `nginx()` and log purge failures

`purgers.py`: the two functions are line-for-line identical (both issue
`PURGE`). Implement one `_http_purge(path, headers, method="PURGE")` and keep
`varnish`/`nginx` as thin aliases for backwards compatibility of the
settings dotted path. Replace the silent `except RequestException: pass` with a
module logger warning (path, target URL, exception). Also remove the unused
`r =` assignment.

### 24. Public headers API in `cached_get` + cache format versioning

`decorators.py:87` stores `response.headers._store` (private API) and replays
its `(orig_name, value)` tuple format on hit (lines 95-96). Switch to the public
mapping (`dict(response.headers)` / `response[k] = v`). Because this changes the
shape of cached payloads, version the key prefix (e.g. `ucache3-`) or bump a
`KEY_PREFIX` constant so 2.x entries are ignored rather than mis-parsed after
upgrade. Apply the same prefix constant everywhere keys are built
(`utils.py`, `signals.py`, `decorators.py`).

### 25. `WrappedClass` loses class metadata

The `ultracache()` class decorator (`decorators.py:110-118`) returns a subclass
without copying `__name__`, `__module__`, `__qualname__`, or `__doc__`,
breaking introspection and debugging output. Copy them from `cls`; also drop
the pointless `__init__` override.

### 26. Implement the documented `cache_alias` setting

The README's "Custom Cache Backend" section documents
`ULTRACACHE = {"cache_alias": "secondary"}`, but no code reads it — every module
hardcodes `from django.core.cache import cache`. Implement it: a `get_cache()`
accessor in `utils.py` returning
`caches[settings.ULTRACACHE.get("cache_alias", "default")]`, used by `utils.py`,
`signals.py`, `decorators.py`, and `ultracache_tags.py`. (Alternative if scope
must shrink: delete the README section — but implementation is ~10 lines and
the feature is genuinely useful.)

### 27. `tasks.py` improvements

- The `pika` guard message says `pika>=0.11,<1.0`; pika 1.x has been stable
  since 2019 — support and test against pika 1.x, update the message.
- `broadcast_purge` sets `max_retries=3` but never calls `self.retry()`, so
  failures are not retried. Make it `bind=True`, catch
  `pika.exceptions.AMQPError`, and `raise self.retry(exc=exc, countdown=...)`
  with backoff.
- A new `pika.BlockingConnection` per task invocation is inefficient at high
  throughput; document it, and reuse a module-level connection with
  reconnect-on-failure if it proves to matter in practice.

### 28. MD5 without `usedforsecurity=False`

`utils.py:256` and `decorators.py:72` use `hashlib.md5()` for cache keys. Add
`usedforsecurity=False` (Python 3.9+) to keep FIPS environments happy.

## Phase 5 — Test coverage

Regression tests for every Phase 1 fix, plus the pre-existing gaps:

### 29. Phase 1 regression tests

- Purgers invoked with the README-shaped `ULTRACACHE["purge"]` settings
  (item 1), using `requests-mock` or a stub.
- `{% ultracache %}` with an unresolvable first vary-on variable (item 4).
- Template tag error paths raise `TemplateSyntaxError`, not `NameError`
  (items 2-3).
- `post_delete` with a configured purger deletes the path registry key (item 7).
- `reduce_list_size` overflow: exercise the `MAX_SIZE` trim loop, including the
  small-list/large-item edge (item 5); currently never exercised by any test.

### 30. Middleware

`UltraCacheMiddleware` has no dedicated tests. Cover: cleanup after a normal
response, cleanup via `process_exception` when the view raises, and (post
item 16) that a recorder created lazily mid-request does not leak into the next
request on the same thread/context.

### 31. `monkey.py` recording

Direct unit tests for the `Model.__getattribute__` patch: records `(ct_id, pk)`
when a recorder is active, records nothing when inactive, no infinite recursion,
unsaved instances (`pk is None`) handling — decide and pin the intended
behaviour (currently unsaved instances with a `pk` attribute record `None`).

### 32. `signals.py` edge cases

`raw=True` saves, `MigrationRecorder.Migration` sender filtering, `RuntimeError`
from `get_for_model`, `invalidate=False` setting, created vs updated branches.

### 33. `tasks.py`

`broadcast_purge` execution with a mocked `pika`: URL derivation from
`CELERY_BROKER_URL` (including the path-quoting branch), explicit
`rabbitmq-url`, exchange declaration and publish payload, retry behaviour
(post item 27).

### 34. State isolation

`ContextVarsLocal` (or its item-15 replacement): cross-context isolation,
`__delattr__`/missing-attribute error paths, and behaviour under
`asyncio.gather`-style concurrent contexts.

### 35. Cookie/header settings

`CONSIDER_COOKIES` and `consider-headers` paths in `cache_meta()` are untested,
as is the `RuntimeError` guard for the cookie/header settings conflict
(`utils.py:30-34`).

## Phase 6 — Async/ASGI, packaging, and docs

### 36. Async-capable middleware

`UltraCacheMiddleware` is sync-only, forcing Django to thread-shift every
request under ASGI. Mark it `sync_capable = True` / `async_capable = True` and
implement the async call path (the middleware does no I/O, so this is
mechanical). If item 16 chooses `request_finished` cleanup instead, this item
shrinks to documentation.

### 37. Verify `ContextVar` semantics under ASGI

The `ContextVarsLocal` work already targets async safety, but verify the
assumptions: under ASGI each request runs in its own context copy (leak-safe),
while under WSGI threads the context persists across requests (cleanup
required). Under `sync_to_async` thread-sensitive hops, confirm the recorder
set in the async context is visible where rendering happens. Add an ASGI test
(Django's `AsyncClient`) exercising a cached view.

### 38. Packaging: migrate `setup.py` to `pyproject.toml`

Current `setup.py` gaps, all fixed in one migration:

- **No `install_requires` at all** — yet `django` and `requests` are
  unconditional imports. Declare `Django>=4.1` and `requests`; make
  `celery`/`pika` an optional extra (`django-ultracache[broadcast]`).
- No `python_requires` (`>=3.11` per the tox matrix).
- No Python/Django version classifiers.
- `long_description` concatenates `README.md` + `AUTHORS.rst` + `CHANGELOG.rst`
  and declares the mix as `text/markdown` — render only the README as the PyPI
  description.
- Remove the obsolete `dependency_links=[]` and `zip_safe`.

### 39. Docs accuracy pass (README + architecture.md)

- Purge configuration example must match the fixed code from item 1 (`method`
  string + sibling `url`).
- "Broadcast Purging" says *requires celery and kombu* — it actually requires
  `celery` **and `pika`** (`tasks.py`), plus a RabbitMQ broker. Also document
  the `rabbitmq-url` setting and the `bin/cache-purge-consumer.py` companion
  script, which is currently undocumented.
- "Running Tests" references `requirements.txt`, which does not exist — replace
  with `pip install tox && tox`.
- `cache_alias` section: aligns with item 26 once implemented.
- State explicitly that the middleware is required (or not, per item 16), and
  what breaks without it.
- `architecture.md`: remove the variable-resolution patching description after
  item 9; update the thread-locals description after item 15.

### 40. Changelog and release

Update `CHANGELOG.rst` per phase; note the cache-format version bump (item 24)
and the overhead numbers (item 19) in the 3.0 entry.

---

## Mapping from the previous plan

All 32 original items are accounted for: 1→8, 2→5, 3→6, 4→3, 5→2, 6→12, 7→10,
8→11, 9→12, 10→14-19, 11→subsumed by 9, 12→20, 13→12, 14→21, 15→23, 16→23,
17→30, 18→31, 19→32, 20→29, 21→33, 22→29, 23→34, 24→35, 25→28, 26→24, 27→25,
28→13, 29→13, 30→27, 31→27, 32→27.
