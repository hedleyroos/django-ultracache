# Improvements Plan

## Critical / Bugs

### 1. `eval()` in decorators.py

`decorators.py` line 74 uses `eval(param)` to resolve decorator parameter strings like `"request.is_secure()"`. This is a code execution anti-pattern. Replace with safe attribute traversal using `operator.attrgetter` or a manual dotted-path resolver.

### 2. Float division bug in utils.py

`utils.py` `reduce_list_size()` has `decrement_by = max(n / 10, 10)` which produces a float in Python 3. Using this as a slice index causes `TypeError`. Fix: change to `n // 10`.

### 3. Attribute name mismatch between monkey.py and middleware.py

`monkey.py` sets `_ultracache_attr_marker` (with leading underscore) on `_thread_locals`, but `middleware.py` cleanup deletes `ultracache_attr_marker` (without leading underscore). The cleanup is a no-op — the marker is never cleared between requests.

### 4. Malformed TemplateSyntaxError format string

`ultracache_tags.py` line 104: `"" % r" tag requires at least 2 arguments." % tokens[0]` is a broken format string. The `""` on the left produces an empty string. Should be `"'%s' tag requires at least 2 arguments." % tokens[0]`.

### 5. TemplateSyntaxError potentially not imported

`UltraCacheNode.render()` in `ultracache_tags.py` references `TemplateSyntaxError` but may not have it imported. Verify and fix.

## Dead Code / Deprecated Patterns

### 6. `default_app_config` in `__init__.py`

`default_app_config` (line 5) is deprecated since Django 3.2 and removed in Django 5.1. Remove it — app discovery handles this automatically.

### 7. Python 2 / old Django compat code

Multiple files contain `try/except ImportError` blocks for APIs that no longer exist, all dead code since the project targets Django 4.1+:

- `tasks.py`: `urllib.parse` vs `urlparse` (Python 2)
- `signals.py`: `import_by_path` fallback (removed Django 1.9)
- `utils.py`: `get_current_site` fallback import (removed Django 1.9)
- `models.py`: `TEMPLATE_CONTEXT_PROCESSORS` check (removed Django 1.10), `django.core.context_processors.request` check (removed Django 1.8)

### 8. Unused imports

- `monkey.py`: `pickle`, `OrderedDict` (from collections), `types` — all imported but never used
- `signals.py`: `threading` — imported but never used

### 9. Commented-out code in `__init__.py`

Line 43 has a commented-out `threading.local()` instantiation. Remove it.

## Architecture / Design

### 10. Global `Model.__getattribute__` monkey-patch

`monkey.py` patches `Model.__getattribute__` globally, firing on every attribute access on every model instance in the entire application. This has significant performance overhead. Consider a more targeted approach — e.g., only recording during active cache sessions, or using a faster guard check.

### 11. `_resolve_lookup` copy pinned to Django 1.11

`monkey.py` contains a copy of Django's `Variable._resolve_lookup` with a docstring stating it's from Django 1.11. Current Django versions may have diverged, introducing subtle bugs if behaviour has changed.

### 12. Hardcoded metadata TTL (86400s)

`utils.py` line 224 uses `cache.set_many(di, 86400)` for metadata entries regardless of the actual cache entry timeout. If content has a longer TTL, metadata can expire first, leaving stale entries that never get invalidated. The metadata TTL should be at least as long as the content TTL.

### 13. `models.py` misnamed

`models.py` defines no Django models. It only performs import-time side effects: validates template context processor settings and imports `monkey.py`. This logic belongs in `apps.py:ready()`.

### 14. Signals config evaluated at import time

`signals.py` evaluates `purger` and `invalidate` from settings at module import time. These values won't update when using `@override_settings` in tests, making test isolation fragile.

### 15. varnish/nginx purgers nearly identical

`purgers.py` defines `varnish()` and `nginx()` as separate functions that are almost line-for-line identical. Unify into a single function parameterized by HTTP method.

### 16. No logging on purge failure

`purgers.py` catches `RequestException` with a bare `except` and silently discards the error. Add logging so purge failures are observable.

## Test Coverage Gaps

### 17. Middleware

`UltraCacheMiddleware` has no dedicated tests. The `process_exception` cleanup path is never exercised.

### 18. monkey.py

The core recording logic (`Model.__getattribute__` patch and `Variable._resolve_lookup` patch) has no unit tests — only tested indirectly through integration tests.

### 19. signals.py edge cases

`on_post_save` and `on_post_delete` have untested branches: `raw=True` saves, `MigrationRecorder.Migration` sender filtering, `RuntimeError` from `get_for_model`.

### 20. purgers.py

`broadcast()`, `varnish()`, and `nginx()` are completely untested.

### 21. tasks.py

`broadcast_purge` Celery task only has an import smoke test. Actual task execution (RabbitMQ message publishing) is untested.

### 22. `reduce_list_size()` overflow path

The `MAX_SIZE` overflow/trimming logic in `utils.py` is never exercised in tests.

### 23. `ContextVarsLocal` isolation

The custom `ContextVar`-based thread-local replacement in `__init__.py` has no dedicated tests for cross-context isolation or `__delattr__` error paths.

### 24. Cookie/header consideration settings

The `CONSIDER_COOKIES` and `consider-headers` settings paths in `cache_meta()` have no test coverage.

## Minor

### 25. MD5 without `usedforsecurity=False`

Both `utils.py` and `decorators.py` use `hashlib.md5()` for cache keys. Add `usedforsecurity=False` to avoid warnings in FIPS-mode environments.

### 26. `response.headers._store` access in decorators.py

Line 87 accesses `response.headers._store`, a private Django API. This is fragile across Django versions. Use the public `response.headers` dict-like interface instead.

### 27. `WrappedClass` doesn't preserve class metadata

The `ultracache()` class decorator in `decorators.py` creates a `WrappedClass` that doesn't copy `__name__`, `__module__`, or `__qualname__` from the original class. This breaks introspection and Django URL reversing.

### 28. Middleware inherits from `object`

`middleware.py` has `class UltraCacheMiddleware(object)` — the explicit `object` base is unnecessary in Python 3.

### 29. Typo in purgers.py

Comment says "methody" instead of "method" (line 8).

### 30. Outdated pika version constraint

`tasks.py` guards against `pika>=0.11,<1.0`. Pika 1.x has been stable since 2019. Update the constraint or remove the version check.

### 31. No connection pooling in tasks.py

`broadcast_purge` creates a new `pika.BlockingConnection` on every task invocation. For high-throughput scenarios this is inefficient.

### 32. No retry backoff in Celery task

`broadcast_purge` sets `max_retries=3` but has no `retry_backoff` or explicit `self.retry()` call, so failures are not actually retried.
