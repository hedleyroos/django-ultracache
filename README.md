# django-ultracache

**Cache views, template fragments and arbitrary Python code — with automatic, fine-grained invalidation that reaches from Django, through your reverse proxies, all the way to the browser.**

[![PyPI version](https://img.shields.io/pypi/v/django-ultracache)](https://pypi.org/project/django-ultracache/)
[![Python versions](https://img.shields.io/pypi/pyversions/django-ultracache)](https://pypi.org/project/django-ultracache/)
[![Django versions](https://img.shields.io/pypi/frameworkversions/django/django-ultracache)](https://pypi.org/project/django-ultracache/)
[![License](https://img.shields.io/pypi/l/django-ultracache)](https://github.com/hedleyroos/django-ultracache/blob/develop/LICENSE)

There are two hard things in computer science, and cache invalidation is the one this library solves. Standard Django caching makes you choose between short timeouts (safe but slow) and manual `cache.delete()` bookkeeping (fast but fragile). Ultracache removes the choice: it **watches which database objects each cached block actually renders**, and the moment one of those objects is saved or deleted, exactly the affected cache entries disappear — nothing more, nothing less.

It even solves the *"new object"* problem: cache a list of promotions, create a brand-new promotion, and the cached list is invalidated — because ultracache tracks content types, not just individual rows.

## Why not just `{% cache %}`?

|                        | Django `{% cache %}`                          | `{% ultracache %}`                                    |
| ---------------------- | --------------------------------------------- | ----------------------------------------------------- |
| Expiry                 | Timeout only                                  | Timeout **or the instant the data changes**           |
| Invalidation           | Manual keys, manual `cache.delete()`          | Automatic, per-object                                 |
| "New object" problem   | Not handled — stale lists until timeout       | Handled — new rows invalidate cached lists            |
| Nested fragments       | Inner fragments invisible to the outer        | Fully supported — dependencies propagate outward      |
| View caching           | Separate machinery, no invalidation           | Same engine: decorate a view, get the same tracking   |
| Reverse proxy purging  | Not handled                                   | HTTP `PURGE` to Varnish/Nginx, or RabbitMQ fan-out    |

The payoff in practice:

```html
{% ultracache 604800 "product_detail" product.pk %}
    <h1>{{ product.title }}</h1>
    <p>{{ product.price }}</p>
{% endultracache %}
```

That fragment is cached for **a week**. Somewhere else, someone runs `product.save()` — and that fragment, and only that fragment, is gone. You never wrote an invalidation line.

## The big picture

```mermaid
flowchart LR
    subgraph record["Render and record"]
        A["Request"] --> B["Cached block or view renders"]
        B --> C["Patched model attribute access"]
        C --> D["Recorder collects (content type, pk) pairs"]
        D --> E[("Cache backend:<br/>content + dependency registry")]
    end
    subgraph purge["Change and invalidate"]
        F["Model save / delete"] --> G["post_save / post_delete signal"]
        G --> H["Registry lookup"]
        H --> I["Delete exactly the dependent entries"]
        I --> J["Optional PURGE to Varnish / Nginx / RabbitMQ"]
    end
    E -.->|"consulted by"| H
```

## Quickstart

Requires Python >= 3.11 and Django >= 4.1. Works under both WSGI and ASGI.

1.  **Install:**

    ```bash
    pip install django-ultracache
    ```

2.  **Add to `INSTALLED_APPS`:**

    ```python
    INSTALLED_APPS = [
        ...,
        "ultracache",
    ]
    ```

3.  **Add the middleware (recommended)** near the top of the stack:

    ```python
    MIDDLEWARE = [
        "ultracache.middleware.UltraCacheMiddleware",
        ...,
    ]
    ```

    <details>
    <summary>Why "recommended" and not "required"?</summary>

    The middleware clears ultracache's per-request recording state as soon
    as the response leaves the middleware stack (also when a view raises).
    Nothing breaks functionally without it, because a `request_finished`
    signal receiver performs the same cleanup as a safety net at the very
    end of each request. The middleware is both sync- and async-capable,
    so it adds no thread-shifting overhead under ASGI.
    </details>

4.  **Check context processors** — `django.template.context_processors.request` must be enabled (it usually is by default):

    ```python
    TEMPLATES = [{
        "OPTIONS": {
            "context_processors": [
                ...,
                "django.template.context_processors.request",
            ],
        },
    }]
    ```

That's it. There is no step where you register models or declare dependencies — ultracache discovers them by watching your code run.

## Three ways to cache

### 1. Template fragments

`{% ultracache %}` is a drop-in for Django's `{% cache %}` — same syntax, plus automatic invalidation:

```html
{% load ultracache_tags %}

{% ultracache 86400 "sidebar" %}

    {# Edit any of these promos -> this block is invalidated.       #}
    {# Create a NEW Promotion   -> this block is also invalidated,  #}
    {# because ultracache tracks the content type, not just rows.   #}
    {% for promo in promotions %}
        <div class="promo">{{ promo.title }}</div>
    {% endfor %}

    {# This user object changes -> invalidated too. #}
    <div>Welcome, {{ request.user.first_name }}</div>

{% endultracache %}
```

**Nesting is where it gets powerful.** Inner fragments invalidate independently, and outer fragments automatically learn everything their inner fragments depend on:

```html
{% ultracache 86400 "product_page" product.pk %}
    <h1>{{ product.title }}</h1>

    {% ultracache 86400 "product_reviews" product.pk %}
        {% for review in product.reviews.all %}
            <blockquote>{{ review.body }}</blockquote>
        {% endfor %}
    {% endultracache %}
{% endultracache %}
```

Change the *product* → the outer fragment re-renders, but the reviews block inside it is still a cache hit, so the re-render is cheap. Change a *review* → both fragments are invalidated, because the outer fragment's output contains the review too. Ultracache gets both cases right without any hints.

Notes: the fragment name must be a literal string; only GET and HEAD requests are cached; when `django.contrib.sites` is installed the current site's pk is automatically part of the key.

### 2. Whole views

Decorate a class-based view and the entire response — view code, template rendering, headers — is cached and tracked:

```python
from django.views.generic import TemplateView
from ultracache.decorators import ultracache

@ultracache(3600)
class PostListView(TemplateView):
    template_name = "posts.html"

    def get_context_data(self, **kwargs):
        return {"posts": Post.objects.all()}
```

Publish a new post, edit a post, delete a post — the cached response is invalidated each time.

If you can't touch the view (third-party apps), apply caching in `urls.py` with `cached_get`:

```python
from django.urls import path
from ultracache.decorators import cached_get
from myapp.views import PostListView

urlpatterns = [
    path("posts/", cached_get(3600)(PostListView.as_view()), name="posts"),
]
```

To vary the cache key on request state, pass callables (each receives the request):

```python
@ultracache(3600, lambda request: request.is_secure())
class PostListView(TemplateView):
    ...
```

`request.get_full_path()` is automatically part of the key, so query parameters are handled correctly out of the box. Only GET and HEAD requests are cached, and responses for requests carrying `django.contrib.messages` are never cached. (Legacy string parameters like `"request.is_secure()"` still work but emit a `DeprecationWarning` — prefer callables.)

### 3. Arbitrary Python code

The same engine works outside templates and views, for any expensive computation:

```python
from ultracache.utils import Ultracache

def shop_summary(shop):
    uc = Ultracache(3600, "shop-summary", shop.pk)
    if uc:
        return uc.cached

    # Every model instance touched below is recorded. When any of
    # them changes, this cache entry is invalidated automatically.
    products = list(shop.product_set.all())
    result = {
        "product_count": len(products),
        "top_seller": max(products, key=lambda p: p.sales).title,
    }

    uc.cache(result)
    return result
```

## How it works

Ultracache patches `django.db.models.Model.__getattribute__` — that is the "magic" that makes explicit dependency declarations unnecessary:

1.  **Recording** — entering an `{% ultracache %}` block or a decorated view starts a *recorder* in context-local storage (a `contextvars.ContextVar`, safe under both threaded WSGI and async ASGI).
2.  **Tracking** — as your code accesses model attributes (`{{ product.price }}`, `p.sales`, ...), ultracache notes each object's content type and primary key.
3.  **Registry** — when the block finishes, ultracache stores the rendered content *and* a reverse index: for each recorded object, which cache keys (and which URL paths) depend on it.
4.  **Invalidation** — a `post_save`/`post_delete` signal looks the object up in the registry, deletes exactly the dependent cache entries, and optionally purges the dependent paths from your reverse proxies.

```mermaid
sequenceDiagram
    participant B as Browser
    participant D as Django
    participant R as Recorder
    participant C as Cache backend

    Note over B,C: First request — render, record, store
    B->>D: GET /products/
    D->>R: model attribute accesses record (content type, pk)
    D->>C: store rendered content
    D->>C: store registry entries (object → cache keys, paths)
    D-->>B: response

    Note over B,C: Later — the data changes
    D->>D: product.save() fires post_save
    D->>C: registry lookup for product
    C-->>D: dependent cache keys and paths
    D->>C: delete_many(cache keys)
    D->>D: purger sends PURGE for paths (if configured)
```

The overhead of the patch is negligible: when no cached block is active, the hot path is a single `ContextVar` lookup that returns `None`. Recording itself deduplicates on insert and stores nothing but `(content_type_id, pk)` pairs.

Curious about the internals — the `Recorder`, dedup barriers, registry TTL handling, and the WSGI/ASGI concurrency model? Read [architecture.md](architecture.md).

## Full-stack purging

A Django-level cache hit is good; not hitting Django at all is better. When data changes, ultracache can also purge the affected URL paths from downstream HTTP caches, so long-lived proxy caching becomes safe.

### Direct purging (Varnish, Nginx)

```python
ULTRACACHE = {
    "purge": {
        "method": "ultracache.purgers.varnish",  # or ultracache.purgers.nginx
        "url": "http://127.0.0.1:80/",
    }
}
```

Ultracache appends the resource path to `url` and issues an HTTP `PURGE` request. Purge failures are logged, never raised into your request cycle.

### Broadcast purging (Celery + RabbitMQ)

With multiple proxy servers, purge instructions are broadcast to all of them:

```mermaid
flowchart LR
    A["Django: model change"] --> B["Celery task broadcast_purge"]
    B --> C[("RabbitMQ fanout exchange<br/>purgatory")]
    C --> D1["cache-purge-consumer.py<br/>on proxy host 1"]
    C --> D2["cache-purge-consumer.py<br/>on proxy host 2"]
    D1 --> E1["HTTP PURGE to local Varnish / Nginx"]
    D2 --> E2["HTTP PURGE to local Varnish / Nginx"]
```

```python
ULTRACACHE = {
    "purge": {
        "method": "ultracache.purgers.broadcast",
    }
}
```

This requires `celery` **and** `pika` (available together as the `broadcast`
extra: `pip install django-ultracache[broadcast]`), plus a running RabbitMQ
broker. Celery must be configured for your project; the purge instruction is
queued as a Celery task which publishes the path (and relevant headers) to a
fanout exchange named `purgatory`.

By default the RabbitMQ connection is derived from `CELERY_BROKER_URL`. To
use a different broker for purging, set:

```python
ULTRACACHE = {
    "purge": {
        "method": "ultracache.purgers.broadcast",
    },
    "rabbitmq-url": "amqp://guest:guest@127.0.0.1:5672/%2F",
}
```

**The consumer script**: each server that fronts a reverse proxy runs the
companion script `bin/cache-purge-consumer.py` (manage it with e.g.
supervisor). It subscribes to the `purgatory` exchange and issues an HTTP
`PURGE` request to the local proxy for every purge instruction it receives.
It accepts a `-c`/`--config` option pointing at a YAML file with these
optional keys:

*   `rabbit-url`: the RabbitMQ connection URL (default `amqp://guest:guest@127.0.0.1:5672/%2F`).
*   `proxy-address`: host/address of the proxy to purge (default `127.0.0.1`).
*   `host`: if set, sent as the `Host` header with each purge request.
*   `logfile`: a file path to log purges to, or `stdout`.

### Fine-grained proxy purging

Proxies usually cache multiple variants of the same path (`Vary: Accept-Language`, cookie-split caches, ...). Tell ultracache which request headers and cookies distinguish those variants, and it records them alongside each cached path — so a purge hits the exact variant, not a blunt path wildcard:

```python
ULTRACACHE = {
    "purge": {
        "method": "ultracache.purgers.varnish",
        "url": "http://127.0.0.1:80/",
    },
    "consider-headers": ["accept-language"],
    "consider-cookies": ["country"],
}
```

The listed cookie names are folded into a synthetic `cookie` header on the purge request. (Setting `"cookie"` in `consider-headers` together with `consider-cookies` is contradictory and raises an error at startup.)

## Settings reference

All settings live in a single `ULTRACACHE` dict in `settings.py`. Every key is optional.

| Key                       | Default                     | Purpose                                                                                          |
| ------------------------- | --------------------------- | ------------------------------------------------------------------------------------------------ |
| `purge.method`            | –                           | Dotted path to a purger callable (`ultracache.purgers.varnish` / `.nginx` / `.broadcast`). Validated at startup. |
| `purge.url`               | –                           | Base URL of the proxy that receives `PURGE` requests.                                            |
| `invalidate`              | `True`                      | Master switch for signal-driven invalidation. Set `False` to record and cache without invalidating. |
| `cache_alias`             | `"default"`                 | Which `CACHES` alias ultracache stores content and registry data in.                             |
| `rabbitmq-url`            | from `CELERY_BROKER_URL`    | Broker URL used by broadcast purging.                                                            |
| `consider-headers`        | `[]`                        | Request headers recorded with each cached path, replayed on `PURGE` for variant-exact purging.   |
| `consider-cookies`        | `[]`                        | Cookie names folded into a synthetic `cookie` header for variant-exact purging.                  |
| `max-registry-value-size` | `1000000`                   | Max size in bytes of one registry list; oldest entries are evicted beyond it.                    |

## Best practices

1.  **Keep cache keys simple.** You don't need `updated_at` timestamps in your keys — ultracache handles staleness for you. Use keys to differentiate *context* (e.g. `user.id` for private content, `language_code` for translations).
2.  **Cache high in the template tree.** Place `{% ultracache %}` as high as possible to maximize the win, but keep genuinely dynamic parts (like CSRF tokens) outside cached blocks.
3.  **Context processors count.** If a context processor touches the database (e.g. a site menu), that access is recorded too — so global site changes invalidate page caches, which is usually exactly what you want.

## Upgrading from 2.x

Version 3.0 changed the format of cached payloads, so all cache keys are
now prefixed with `ucache3-`. Entries written by 2.x are simply ignored
after an upgrade — no migration is needed; stale 2.x entries expire on
their own. Expect a cold cache immediately after upgrading.

Note that template fragments cached by 2.x are stored under Django's own
`template.cache.*` keys, which 3.0 no longer knows how to invalidate.
Those fragments persist until their TTL expires, so after upgrading they
may serve stale content for up to their configured timeout — not just a
cold cache. If that is unacceptable, flush the cache backend as part of
the upgrade.

## Running the tests

```bash
pip install tox
tox
```

## License and links

BSD-3-Clause. Written by Hedley Roos.

*   [Architecture deep-dive](architecture.md) — recorder internals, dedup barriers, registry TTLs, WSGI/ASGI concurrency model
*   [Changelog](CHANGELOG.rst)
*   [PyPI](https://pypi.org/project/django-ultracache/) · [GitHub](https://github.com/hedleyroos/django-ultracache)
