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

*   **Mechanism**: When a Model attribute is accessed, the patch checks if an "ultracache recording session" is active (via `_thread_locals`).
*   **Action**: If active, the object's `ContentType` and `Primary Key` are added to a list in the thread local storage.

### 2. The Cache Tag (`ultracache_tags.py`)
The `{% ultracache %}` template tag extends Django's standard `CacheNode`.

*   **Runtime**: 
    1.  Initializes a recorder list in `_thread_locals`.
    2.  Renders the template body.
    3.  If the cache key is missing (cache miss), it saves the output *and* calls `utils.cache_meta` to save the dependency registry.
    4.  If the cache key exists (cache hit), it retrieves the content and also "re-plays" the recorded objects into the parent context (so nested caches function correctly).

### 3. The Registry (Meta-Caching)
The library maintains a secondary data structure in your cache backend to track dependencies.

*   **Mapping**: `ucache-<ContentTypeID>-<ObjectID>` -> `[List of dependent Cache Keys]`
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
*   It uses the same underlying recording mechanism (via `_thread_locals`) to track objects accessed during the view execution and template rendering.

## File Structure Summary

| File | Purpose |
|------|---------|
| `ultracache/templatetags/ultracache_tags.py` | Implementation of the `{% ultracache %}` tag. |
| `ultracache/monkey.py` | Patches `Model.__getattribute__` to intercept object access. |
| `ultracache/signals.py` | Signal handlers that trigger invalidation on model save/delete. |
| `ultracache/utils.py` | Logic for writing the registry (metadata) to the cache backend. |
| `ultracache/decorators.py` | `@cached_get` decorator for view-level caching. |
| `ultracache/purgers.py` | Pluggable strategies for how to execute the purge (e.g., immediate or via task queue). |

## Dependencies
*   **Django**: Core framework.
*   **Threading**: Heavily relies on `_thread_locals` to manage context state during the request-response cycle.
