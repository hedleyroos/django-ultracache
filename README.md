# Django Ultracache

**Cache views, template fragments and arbitrary Python code. Monitor Django object changes to perform automatic fine-grained cache invalidation from Django level, through proxies, to the browser. Make Django really fast.**

Standard Django caching is great, but invalidation is hard. You usually end up setting short timeouts or writing complex logic to clear keys when data changes.

**Ultracache solves this complexity.** It automatically tracks every database object accessed within a cached block. If a tracked object is modified or deleted, the cache is expired. Crucially, if a *new* object is created that shares a content type with any tracked object, the cache is also expired to ensure lists remain up-to-date.

## Why use it?

*   **Zero-Config Invalidation**: No manual `cache.delete()`. It just works.
*   **Longer Timeouts**: Cache things for days, not minutes. They'll update the instant your data does.
*   **Granular**: Nested caches work perfectly. Updating a single comment won't bust the entire page cache, only the affected fragments.
*   **Drop-in Replacement**: Works just like Django's standard caching tools.

## Quick Start

### 1. In Templates

Use the `{% ultracache %}` tag exactly like Django's `{% cache %}`.

```html
{% load ultracache_tags %}

{# Cache this sidebar for 24 hours #}
{% ultracache 86400 "sidebar_widget" %}
    
    {# INVALIDATION LOGIC: #}
    {# 1. Modifying or deleting any 'book' displayed here -> Invalidates this block. #}
    {# 2. Creating a NEW Book -> Invalidates this block (because it tracks the Book ContentType). #}
    {% for book in books %}
        <div class="book">
             {{ book.title }}
        </div>
    {% endfor %}

{% endultracache %}
```

### 2. In Views

Cache entire views with the decorator. It intelligently observes the template rendering process.

```python
from ultracache.decorators import ultracache
from django.views.generic import TemplateView

# Cache the result of this view for 1 hour
@ultracache(3600)
class BookListView(TemplateView):
    template_name = "books.html"

    def get_context_data(self, **kwargs):
        # Even though the fetching happens here, Ultracache 
        # tracks the specific objects rendered in the template.
        
        # INVALIDATION LOGIC:
        # - Any change to an existing Book -> Invalidates the view cache.
        # - Any new Book created -> Invalidates the view cache.
        return {"books": Book.objects.all()}
```

### 3. Arbitrary Python Code

You can manually cache the result of complex calculations or code blocks.

```python
from ultracache.utils import Ultracache

# Define a cache key and timeout (300 seconds)
uc = Ultracache(300, "country-median-age", country_code)

if uc:
    # Cache hit! Get the result immediately.
    median_age = uc.cached
else:
    # Cache miss. Perform the calculation.
    
    # Ultracache is "watching"! 
    
    # 1. Accessing 'country' registers it as a dependency.
    #    -> If 'country' is saved/deleted, this cache invalidates.
    country = Country.objects.get(code=country_code)
    
    # 2. Iterating over Persons registers them AND the Person ContentType.
    #    -> If any Person in this list is modified -> Invalidates.
    #    -> If a NEW Person is created -> Invalidates.
    median_age = country.calculate_median_age()
    
    # Store the result
    uc.cache(median_age)
```

## View Caching & URL Patterns

While the `@ultracache` decorator is recommended for Class Based Views (as seen in Quick Start), you can also apply caching directly in your `urls.py`.

This is the preferred method if:
1.  **You reuse the same View class** for multiple URL patterns (applying the decorator on the class would cause cache key collisions between the patterns).
2.  **You want to cache a third-party view** that you cannot modify.

Use the `cached_get` decorator for this:

```python
from django.urls import path
from ultracache.decorators import cached_get
from myapp.views import MyReusableView

urlpatterns = [
    path(
        "cached-view/",
        # Cache for 1 hour (3600s)
        cached_get(3600)(MyReusableView.as_view()),
        name="cached-view"
    ),
]
```

### Important Notes on View Caching
*   **Implicit Key**: `request.get_full_path()` is automatically included in the cache key. You don't need to specify it.
*   **Automatic Invalidation**: Just like the template tag, any objects accessed during the view's execution (e.g., in `get_context_data`) are tracked.
*   **Safety**: Only `GET` and `HEAD` requests are cached.
*   **Session Data**: Be careful if your view renders user-specific content (e.g. from `request.session` or `request.user`) without including it in the cache key. Use `@ultracache(300, "request.user.id")` to ensure uniqueness per user.

## Advanced Features

### Full Stack Invalidation

Ultracache isn't limited to Django's internal cache. It is designed to be the source of truth for your entire caching strategy, extending all the way to the user's browser or CDNs.

*   **Reverse Proxy Purging**: The invalidation mechanism can be extended to issue PURGE commands to Varnish, Nginx, or Cloudflare when your Django models change.
*   **Browser Caching**: By managing `Cache-Control` headers intelligently, Ultracache allows you to cache content in the user's browser, knowing you can bust it via the URL or ETag if data changes.

## Installation

1.  Install the package:

    ```bash
    pip install django-ultracache
    ```

2.  Add to `INSTALLED_APPS` and `MIDDLEWARE`:

    ```python
    INSTALLED_APPS = [
        ...,
        "ultracache",
    ]

    MIDDLEWARE = [
        # Recommended to be near the top
        "ultracache.middleware.UltraCacheMiddleware",
        ...,
    ]
    ```

3.  Ensure the `request` context processor is active (standard in Django):

    ```python
    TEMPLATES = [{
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                ...
            ],
        },
    }]
    ```

## Strategies

*   **Nested Caching**: You can nest `{% ultracache %}` tags. Inner tags are tracked by outer tags. Invalidating an inner fragment (e.g., a single book) will also invalidate the outer fragment (e.g., the book list), ensuring consistency.
*   **Performance**: Ultracache uses a lightweight registry in your cache backend to map Objects to Cache Keys. This adds minimal overhead (~1-2 cache operations) for massive gains in hit-rates.

## Best Practices: Cache Keys

The cache key dictates **uniqueness**. Since Ultracache handles invalidation (staleness) automatically via object tracking, your cache key only needs to identify the **context**.

The specific cache key should answer the question: *"For whom or under what conditions does this content differ?"*

**Example**:
Imagine a product widget.
*   If it looks the same for everyone: Key = `"product_widget"`
*   If it shows a "Edit" button for admins: Key = `"product_widget", user.is_staff`
*   If it changes based on the URL query string: Key = `"product_widget", request.GET.sort`

**Do not** include object timestamps or versions in the key (e.g., `product.updated_at`). Ultracache handles that for you automatically. Including them generates unnecessary new cache entries instead of refreshing the existing one.

## Contributing

Tests are run via `tox`.

```bash
tox
```

---

*Verified with Python 3.12 and Django 5.x/6.0*
