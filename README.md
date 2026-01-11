# Django Ultracache

**Cache views, template fragments, and arbitrary Python code with automatic, fine-grained invalidation.**

`django-ultracache` solves the hardest problem in caching: **invalidation**.

Standard Django caching requires you to manually manage cache keys or set short timeouts. Ultracache is different. It automatically tracks every database object accessed during the rendering of a cached block. When those objects are modified or deleted, the relevant cache entries are immediately and automatically invalidated.

Crucially, it also handles the "new object" problem: if a list of objects is cached, and a *new* object is created that should appear in that list, Ultracache knows to invalidate the list.

## Features

*   **Zero-Config Invalidation**: No manual `cache.delete()`. It just works.
*   **Granular Updates**: Change one comment, and only the fragments displaying that comment are purged. The rest of the page stays cached.
*   **Long-Term Caching**: Set timeouts to days or weeks. Content updates instantly when data changes.
*   **Full Stack Integration**: Can issue PURGE requests to Varnish, Nginx, or via RabbitMQ to clear downstream caches.
*   **Nested Caching**: Fully supports nested `{% ultracache %}` tags.

## Installation

1.  **Install the package:**

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

3.  **Add Middleware:**
    Add `UltraCacheMiddleware` to `MIDDLEWARE`. It should be placed near the top, receiving requests early and sending responses late.

    ```python
    MIDDLEWARE = [
        "ultracache.middleware.UltraCacheMiddleware",
        ...,
        "django.middleware.common.CommonMiddleware",
        ...,
    ]
    ```

4.  **Check Context Processors:**
    Ensure `django.template.context_processors.request` is enabled (it usually is by default).

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

## Usage

### 1. Template Fragments

Use the `{% ultracache %}` tag like Django's standard `{% cache %}`.

```html
{% load ultracache_tags %}

{# Cache this sidebar for 24 hours #}
{% ultracache 86400 "sidebar_widget" %}
    
    {# If any object in 'promotions' is modified/deleted -> Invalidate #}
    {# If a new Promotion is created -> Invalidate (tracks ContentType) #}
    {% for promo in promotions %}
        <div class="promo">
             {{ promo.title }}
        </div>
    {% endfor %}

    {# If this specific user object changes -> Invalidate #}
    <div>Welcome, {{ request.user.first_name }}</div>

{% endultracache %}
```

### 2. View Caching

You can cache entire views. Ultracache will execute the view code, render the template, and track all database accesses during the process.

**Class-Based Views:**

Use the `@ultracache` decorator on the class.

```python
from ultracache.decorators import ultracache
from django.views.generic import TemplateView

# Cache for 1 hour. Invalidation happens if any referenced data changes.
@ultracache(3600)
class PostListView(TemplateView):
    template_name = "posts.html"

    def get_context_data(self, **kwargs):
        return {"posts": Post.objects.all()}
```

**URL Patterns:**

If you are reusing views or cannot modify the view code, apply caching in `urls.py` using `cached_get`.

```python
from django.urls import path
from ultracache.decorators import cached_get
from myapp.views import MyView

urlpatterns = [
    path("my-view/", cached_get(3600)(MyView.as_view()), name="my-view"),
]
```

*Note: `request.get_full_path()` is automatically added to the cache key, so query parameters are handled correctly.*

### 3. Arbitrary Python Code

You can manually cache complex calculations.

```python
from ultracache.utils import Ultracache

# Define a cache key and timeout
def get_user_metrics(user):
    # 'request' is optional but recommended if in a view context
    uc = Ultracache(300, "user-metrics", user.id)
    
    if uc:
        return uc.cached
    
    # --- Start Calculation ---
    # Ultracache records object access here
    
    score = calculate_complex_score(user)
    stats = user.statistics_set.all()
    
    result = {"score": score, "stats": list(stats)}
    
    # --- End Calculation ---
    
    uc.cache(result)
    return result
```

## How It Works

Ultracache monkey-patches `django.db.models.Model.__getattribute__` to detect when any attribute of a model instance is accessed.

1.  **Recording**: When you enter a `{% ultracache %}` block or a decorated view, a "recorder" is started in thread-local storage.
2.  **Tracking**: As you iterate over querysets or access model attributes (e.g., `{{ product.price }}`), Ultracache notes the object's `ContentType` and `primary key`.
3.  **Registry**: When the block finishes rendering, Ultracache saves the content to the cache *and* writes a "registry" entry linking those objects to this specific cache key.
4.  **Invalidation**: When an object is saved or deleted, a `post_save` or `post_delete` signal triggers. Ultracache checks the registry for any cache keys dependent on that object and deletes them.

## Advanced Configuration

### Reverse Proxy Purging (Varnish, Nginx)

Ultracache can issue HTTP PURGE requests to downstream caches when data changes. Configure this in your `settings.py`:

```python
ULTRACACHE = {
    "purge": {
        "method": "ultracache.purgers.varnish",  # or ultracache.purgers.nginx
        "url": "http://127.0.0.1:80/",
    }
}
```

The `url` should point to your caching proxy. Ultracache will append the resource path to this URL when issuing a purge.

### Broadcast Purging (Celery)

For multi-server setups, you can use RabbitMQ/Celery to broadcast purge instructions to all workers.

```python
ULTRACACHE = {
    "purge": {
        "method": "ultracache.purgers.broadcast",
    }
}
```

*Requires `celery` and `kombu` to be installed and configured.*

### Custom Cache Backend

Ultracache uses the `default` cache alias by default. To use a different backend:

```python
ULTRACACHE = {
    "cache_alias": "secondary",
}
```

## Best Practices

1.  **Cache Keys**: Keep them simple. You don't need to include `updated_at` timestamps in your keys—Ultracache handles staleness for you. Use keys to differentiate *context* (e.g., `user.id` for private content, `language_code` for translations).
2.  **Order of Operations**: Place `{% ultracache %}` as high as possible in your template DOM tree to maximize performance, but be mindful of parts that *must* remain dynamic (like CSRF tokens).
3.  **Context Processors**: If your context processors access the database (e.g., loading a site menu), that access is also recorded. This means global site changes can invalidate page caches, which is usually desired behavior.

## Running Tests

```bash
pip install -r requirements.txt
tox
```
