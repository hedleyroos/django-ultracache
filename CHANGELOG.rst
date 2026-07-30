Changelog
=========

3.0
---

Requires Django >= 4.1 on Python >= 3.11. **Cache format bump**: every key
ultracache writes is now prefixed ``ucache3-``, because 3.0 changed the shape
of cached payloads (public header mapping in ``cached_get``). Entries written
by 2.x are ignored after an upgrade — expect a cold cache; stale 2.x entries
expire on their own.

Crashers and correctness
~~~~~~~~~~~~~~~~~~~~~~~~

#. The varnish and nginx purgers work with the documented settings shape
   (``method`` dotted-path string with a sibling ``url`` key); previously they
   crashed on every invocation.
#. Template tag error paths raise ``TemplateSyntaxError`` with a proper
   message instead of ``NameError``/``TypeError``.
#. An unresolvable vary-on variable contributes a stable placeholder to the
   cache key instead of corrupting it (or crashing when first).
#. ``reduce_list_size`` no longer crashes on integer division and clamps
   correctly when even the smallest tail is too large.
#. ``post_delete`` with a configured purger now deletes the path registry key,
   preventing endless re-purging of stale paths.
#. ``eval()`` removed from ``cached_get``: callables are the replacement
   (``cached_get(300, lambda request: request.is_secure())``). Legacy string
   parameters are restricted to dotted attribute traversal of the request
   (optionally with a trailing no-argument call, no underscore-prefixed
   segments), go through a request-scoped resolver and emit a
   ``DeprecationWarning``. **Breaking**: unsupported strings now raise
   ``ValueError`` at decoration time instead of evaluating arbitrary code
   per request.
#. ``ContentType`` instances are no longer recorded as dependencies of
   caching blocks: their rows are effectively immutable at runtime, and
   recording them from within the ``Model.__getattribute__`` patch would
   recurse.

Performance
~~~~~~~~~~~

#. The ``Model.__getattribute__`` recording hot path was reworked: the
   reentrancy marker is gone (the pk is read without recursion), recording
   state lives in a dedicated ``ContextVar``, the recorder deduplicates on
   insert, and ``cache_meta`` no longer does quadratic membership scans.
#. Micro-benchmark (``bin/benchmark.py``, 5 runs of 100,000 iterations of 4
   attribute accesses, Python 3.12/Django 6.0): patched-with-recording-inactive
   dropped from 0.588s to 0.070s (~8x faster) and patched-with-recording-active
   from 3.086s to 0.662s (~4.7x faster; the old number was additionally
   inflated by profiling instrumentation). Unpatched baseline is ~0.012s.
   Note: the pre-3.0 benchmark's "baseline" was itself the patched function,
   so historical comparisons should use the numbers above.
#. The recorder is created lazily by the first caching construct instead of on
   every request.

Robustness and design
~~~~~~~~~~~~~~~~~~~~~

#. Invalidation metadata is stored for at least as long as the content it
   invalidates (``max(timeout, 86400)``; a ``None`` timeout propagates), and
   a registry key's TTL never decreases: the expiry is embedded in the
   stored payload and later writes only ever extend it, so a short-timeout
   block touching the same object cannot cut a longer-lived block off from
   invalidation. Long-lived content can no longer outlive its invalidation
   metadata.
#. The ``purge`` and ``invalidate`` settings are resolved lazily and respect
   ``override_settings`` / runtime reconfiguration.
#. Purge failures are logged (path, target URL, exception) instead of being
   silently swallowed.
#. ``cached_get`` stores response headers via the public mapping API.
#. The ``ultracache`` class decorator preserves ``__name__``, ``__qualname__``,
   ``__module__`` and ``__doc__``.
#. The documented ``cache_alias`` setting is now actually implemented.
#. ``broadcast_purge`` retries on AMQP errors with exponential backoff and
   supports pika 1.x.
#. MD5 cache-key hashing passes ``usedforsecurity=False`` for FIPS
   environments.

Cleanup, async, packaging
~~~~~~~~~~~~~~~~~~~~~~~~~

#. Removed the dead ``Variable._resolve_lookup`` patch, Python 2 / old-Django
   compatibility fallbacks, and ``models.py`` (startup checks moved to
   ``AppConfig.ready()``, raising ``ImproperlyConfigured``).
#. ``UltraCacheMiddleware`` is now recommended rather than required (a
   ``request_finished`` receiver cleans up as a safety net) and is both sync-
   and async-capable under ASGI.
#. Packaging migrated from ``setup.py`` to ``pyproject.toml`` with declared
   dependencies and a ``broadcast`` extra (``celery``, ``pika>=1.0``).
#. Test suite grew from 9 to 138 tests; ``ultracache/`` (excluding tests) is
   at 100% line coverage.

2.3
---

#. Django 4.2, 5.0, and 6.0 compatibility.
#. Remove tests for versions older than Django 4.1.
#. Remove support for Django Rest Framework caching.

2.2
---
#. Django 4.0 compatibility.

2.1.1
-----
#. Ensure cache coherency should a purger fail.

2.1.0
-----
#. Django 3 compatibility.
#. Fix potential thread local residual data issue.

2.0.0
-----
#. Remove dependency on the sites framework everywhere. The sites framework is still automatically
   considered if an installed app.
#. Do not store metadata in the request anymore but in a list on thread locals.
#. Introduce class utils.Ultracache to subject arbitrary pieces of Python code to caching.
#. Drop Django 1 support.

1.11.12
-------
#. Simpler class based decorator.
#. Add Django 2.1 and Python 3.6 tests.

1.11.11
-------
#. Add a test for tasks.

1.11.10
-------
#. Ensure a working error message if pika is not found.
#. `cached_get` now considers any object accessed in get_context_data and not just objects accessed in the view template.
#. The original request headers are now sent to the purgers along with the path. This enables fine-grained proxy invalidation.
#. Django 2.0 and Python 3 compatibility. Django 1.9 support has been dropped.

1.11.9
------
#. Simplify the DRF caching implementation. It also now considers objects touched by sub-serializers.

1.11.8
------
#. The DRF settings now accept dotted names.
#. The DRF setting now accepts a callable whose result forms part of the cache key.

1.11.7
------
#. Use pickle to cache DRF data because DRF uses a Decimal type that isn't recognized by Python's json library.

1.11.6
------
#. Adjust the DRF decorator so it can be used in more places.

1.11.5
------
#. Django Rest Framework caching does not cache the entire response anymore, only the data and headers.

1.11.4
------
#. Move the twisted work to `django-ultracache-twisted`.
#. Clearly raise exception if libraries are not found.

1.11.3
------
#. Move the twisted directory one lower.

1.11.2
------
#. Package the product properly so all directories are included.

1.11.1
------
#. More defensive code to ensure we don't interfere during migrations in a test run.

1.11.0
------
#. Introduce `rabbitmq-url` setting for use by `broadcast_purge` task.
#. Django 1.11 support.
#. Deprecate Django 1.6 support.

1.10.2
------
#. Remove logic that depends on SITE_ID so site can also be inferred from the request.

1.10.1
------
#. Add caching for Django Rest Framework viewsets.
#. Django 1.10 compatibility.

1.9.1
-----
#. Add missing import only surfacing in certain code paths.
#. `Invalidate` setting was not being loaded properly. Fixed.
#. Handle content types RuntimeError when content types have not been migrated yet.

1.9.0
-----
#. Move to tox for tests.
#. Django 1.9 compatibility.

0.3.8
-----
#. Honor the `raw` parameter send along by loaddata. It prevents redundant post_save handling.

0.3.7
-----
#. Revert the adding of the template name. It introduces a performance penalty in a WSGI environment.
#. Further reduce the number of writes to the cache.

0.3.6
-----
#. Add template name (if possible) to the caching key.
#. Reduce number of calls to set_many.

0.3.5
-----
#. Keep the metadata cache size in check to prevent possibly infinite growth.

0.3.4
-----
#. Prevent redundant sets.
#. Work around an apparent Python bug related to `di[k].append(v)` vs `di[k] = di[k] + [v]`. The latter is safe.

0.3.3
-----
#. Handle case where one cached view renders another cached view inside it, thus potentially sharing the same cache key.

0.3.2
-----
#. The `ultracache` template tag now only caches HEAD and GET requests.

0.3.1
-----
#. Trivial release to work around Pypi errors of the day.

0.3
---
#. Replace `cache.get` in for loop with `cache.get_many`.

0.2
---
#. Do not automatically add `request.get_full_path()` if any of `request.get_full_path()`, `request.path` or `request.path_info` is an argument for `cached_get`.

0.1.6
-----
#. Also cache response headers.

0.1.5
-----
#. Explicitly check for GET and HEAD request method and cache only those requests.

0.1.4
-----
#. Rewrite decorator to be function based instead of class based so it is easier to use in urls.py.

0.1.3
-----
#. `cached_get` decorator now does not cache if request contains messages.

0.1.2
-----
#. Fix HTTPResponse caching bug.

0.1.1
-----
#. Handle case where a view returns an HTTPResponse object.

0.1
---
#. Initial release.

