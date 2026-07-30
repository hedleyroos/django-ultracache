# Architecture of django-ultracache

## Overview

`django-ultracache` is a drop-in replacement for Django's template fragment caching that adds **automatic, fine-grained invalidation**. 

While standard Django caching relies on time-based expiry or manual key invalidation, `django-ultracache` automatically tracks which database objects are rendered within a cached block. When any of those objects are modified (saved) or deleted, the relevant cache blocks are immediately invalidated.

## How It Works

The system operates on a "record and purge" dependency cycle:

1.  **Record**: When a cached block is rendering, the system "watches" for any access to Django models.
2.  **Registry**: It builds a reverse-index mapping Database Objects to Cache Keys.
3.  **Purge**: When a Database Object changes, it looks up the keys and deletes them.

## Core Components

### 1. Object Access Recording (`monkey.py`)
This is the "magic" underlying the library. To detect which objects are used without requiring explicit declaration by the developer, `django-ultracache` monkey-patches `django.db.models.Model.__getattribute__`.

*   **Mechanism**: When a Model attribute is accessed, the patch checks if an "ultracache recording session" is active. The recorder lives in a `contextvars.ContextVar` (`ultracache.get_recorder()` / `get_or_create_recorder()`), so the inactive check on this very hot path is a single `ContextVar.get()` returning `None`.
*   **Action**: If active, the object's `ContentType` id and primary key are appended to the context-local `Recorder`, which deduplicates on insert while preserving insertion order.
*   **Lifecycle**: The recorder is created lazily by the first caching construct in a request and cleared afterwards by `UltraCacheMiddleware` (recommended) or, as a safety net, by a `request_finished` signal receiver.

### 2. The Cache Tag (`ultracache_tags.py`)
The `{% ultracache %}` template tag extends Django's standard `CacheNode`.

*   **Runtime**: 
    1.  Gets (or lazily creates) the context-local recorder and notes its current length as the block's start index.
    2.  Renders the template body.
    3.  If the cache key is missing (cache miss), it saves the output *and* calls `utils.cache_meta` to save the dependency registry.
    4.  If the cache key exists (cache hit), it retrieves the content and also "re-plays" the recorded objects into the parent context (so nested caches function correctly).

### 3. The Registry (Meta-Caching)
The library maintains a secondary data structure in your cache backend to track dependencies.

*   **Mapping**: `ucache3-<ContentTypeID>-<ObjectID>` -> `[List of dependent Cache Keys]` (the `3` in the `ucache3-` prefix is a cache format version; 2.x entries are ignored after an upgrade)
*   **Logic**: This allows checking a specific database object ID and getting a list of every cached HTML fragment that contains it.

### 4. Automatic Invalidation (`signals.py`)
Invalidation is event-driven using Django's signal framework.

*   **Triggers**: Listens for `post_save` and `post_delete` signals on all Models.
*   **Workflow**:
    1.  Signal fires for `Object A`.
    2.  Handler constructs the registry key for `Object A`.
    3.  It retrieves the list of dependent cache keys.
    4.  It calls `cache.delete_many()` to remove those specific fragments.

### 5. View Caching (`decorators.py`)
The `@cached_get` decorator brings this functionality to entire views.
*   It caches the response of a GET request.
*   It uses the same underlying recording mechanism (the context-local recorder) to track objects accessed during the view execution and template rendering.

## File Structure Summary

| File | Purpose |
|------|---------|
| `ultracache/templatetags/ultracache_tags.py` | Implementation of the `{% ultracache %}` tag. |
| `ultracache/monkey.py` | Patches `Model.__getattribute__` to intercept object access. |
| `ultracache/signals.py` | Signal handlers that trigger invalidation on model save/delete. |
| `ultracache/utils.py` | Logic for writing the registry (metadata) to the cache backend. |
| `ultracache/decorators.py` | `@cached_get` decorator for view-level caching. |
| `ultracache/purgers.py` | Pluggable strategies for how to execute the purge (e.g., immediate or via task queue). |

## Concurrency Model (WSGI and ASGI)

Recording state is context-local (`contextvars`), not thread-local. The
practical consequences, pinned by the tests in
`ultracache/tests/test_asgi.py`:

*   **ASGI**: each request is handled in its own copy of the context, so a
    recorder created during one request cannot leak into another request's
    context.
*   **WSGI**: worker threads are reused and the context persists across
    requests on the same thread — this is why the middleware /
    `request_finished` cleanup exists.
*   **`sync_to_async` hops**: Django runs sync views, sync middleware and
    template rendering under ASGI through
    `sync_to_async(thread_sensitive=True)`. asgiref executes the sync
    function in a copy of the calling async context and restores context
    variable changes back into the caller's context afterwards. So a
    recorder active in the async context is visible where rendering
    happens, and a recorder created lazily inside the sync hop is visible
    to the async middleware cleanup that runs afterwards.

`UltraCacheMiddleware` is both `sync_capable` and `async_capable`; it does
no I/O, so under ASGI it participates in the middleware chain without
forcing a thread shift.

## Dependencies
*   **Django**: >= 4.1 on Python >= 3.11. Works under WSGI and ASGI.
*   **contextvars**: recording state lives in a `ContextVar`, giving correct isolation for both threaded and async request handling.
