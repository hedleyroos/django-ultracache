import hashlib
import re
import warnings
from functools import wraps, WRAPPER_ASSIGNMENTS, partial

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.template.response import TemplateResponse

from ultracache import _thread_locals
from ultracache.utils import cache_meta, get_current_site_pk


# Legacy string parameters may only be dotted attribute traversal rooted in
# ``request``, optionally followed by a single trailing no-argument call,
# e.g. "request.is_secure()" or "request.path_info".
_legacy_param_pattern = re.compile(
    r"^request(?P<attrs>(?:\.[A-Za-z_][A-Za-z0-9_]*)*)(?P<call>\(\))?$"
)


def resolve_legacy_param(param, request):
    """Resolve a legacy string parameter against the request.

    Only dotted attribute traversal rooted in ``request``, optionally with a
    trailing no-argument call, is supported. Anything else raises ValueError.
    """
    match = _legacy_param_pattern.match(param.strip())
    if match is None:
        raise ValueError(
            "Unsupported string parameter %r: only dotted attribute "
            "traversal of the request, optionally with a trailing "
            "no-argument call, is allowed (eg. \"request.is_secure()\"). "
            "Pass a callable accepting the request instead." % param
        )
    value = request
    attrs = match.group("attrs")
    if attrs:
        for attr in attrs.lstrip(".").split("."):
            value = getattr(value, attr)
    if match.group("call"):
        value = value()
    return value


def cached_get(timeout, *params):
    """Decorator applied specifically to a view's get method"""

    for param in params:
        if isinstance(param, str):
            warnings.warn(
                "Passing string parameters like %r to cached_get/ultracache "
                "is deprecated; pass a callable accepting the request "
                "instead, eg. cached_get(300, lambda request: "
                "request.is_secure())." % param,
                DeprecationWarning,
                stacklevel=2,
            )

    def decorator(view_func):
        @wraps(view_func, assigned=WRAPPER_ASSIGNMENTS)
        def _wrapped_view(view_or_request, *args, **kwargs):

            # The type of the request gets muddled when using a function based
            # decorator. We must use a function based decorator so it can be
            # used in urls.py.
            request = getattr(view_or_request, "request", view_or_request)

            # If request not GET or HEAD never cache
            if request.method.lower() not in ("get", "head"):
                return view_func(view_or_request, *args, **kwargs)

            # If request contains messages never cache
            l = 0
            try:
                l = len(request._messages)
            except (AttributeError, TypeError):
                pass
            if l:
                return view_func(view_or_request, *args, **kwargs)

            # Compute a cache key
            if isinstance(view_func, partial):
                func_name = view_func.func.__name__
            else:
                func_name = view_func.__name__
            li = [str(view_or_request.__class__), func_name]

            # request.get_full_path is implicitly added if no other request
            # path is provided. get_full_path includes the querystring and is
            # the more conservative approach but makes it trivially easy for a
            # request to bust through the cache.
            if not set(params).intersection(
                set(("request.get_full_path()", "request.path", "request.path_info"))
            ):
                li.append(request.get_full_path())

            if "django.contrib.sites" in settings.INSTALLED_APPS:
                li.append(get_current_site_pk(request))

            # Pre-sort kwargs
            keys = list(kwargs.keys())
            keys.sort()
            for key in keys:
                li.append("%s,%s" % (key, kwargs[key]))

            # Extend cache key with custom variables
            for param in params:
                if callable(param):
                    li.append(param(request))
                elif isinstance(param, str):
                    li.append(resolve_legacy_param(param, request))
                else:
                    li.append(param)

            s = ":".join([str(l) for l in li])
            hashed = hashlib.md5(s.encode("utf-8")).hexdigest()
            cache_key = "ucache-%s" % hashed
            cached = cache.get(cache_key, None)
            if cached is None:
                # The get view as outermost caller may bluntly set recorder to empty
                _thread_locals.ultracache_recorder = []
                response = view_func(view_or_request, *args, **kwargs)
                content = None
                if isinstance(response, TemplateResponse):
                    content = response.render().rendered_content
                elif isinstance(response, HttpResponse):
                    content = response.content
                if content is not None:
                    # Django 4 deprecates _headers and introduces headers. Drop to private API for compatibility.
                    if hasattr(response, "headers"):
                        headers = response.headers._store
                    else:
                        headers = getattr(response, "_headers", {})
                    cache.set(cache_key, {"content": content, "headers": headers}, timeout)
                    cache_meta(_thread_locals.ultracache_recorder, cache_key, request=request)
            else:
                response = HttpResponse(cached["content"])
                # Headers has a non-obvious format
                for k, v in cached["headers"].items():
                    response[v[0]] = v[1]

            return response

        return _wrapped_view

    return decorator


def ultracache(timeout, *params):
    """Decorator applied to a view class. The get method is decorated
    implicitly."""

    def decorator(cls):
        class WrappedClass(cls):
            def __init__(self, *args, **kwargs):
                super(WrappedClass, self).__init__(*args, **kwargs)

            @cached_get(timeout, *params)
            def get(self, *args, **kwargs):
                return super(WrappedClass, self).get(*args, **kwargs)

        return WrappedClass

    return decorator
