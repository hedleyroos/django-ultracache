import warnings
from functools import partial
from unittest import mock

from django.conf import settings
from django.core.cache import cache, caches
from django.http import HttpResponse
from django.test import SimpleTestCase, TestCase
from django.test.client import RequestFactory
from django.urls import reverse
from django.views.generic.base import TemplateView

from ultracache.decorators import cached_get, resolve_legacy_param, ultracache


class CachedGetParamTestCase(TestCase):
    """Tests for item 8: the callable parameter API and the restricted
    legacy string parameter resolver that replaces eval()."""

    if "django.contrib.sites" in settings.INSTALLED_APPS:
        fixtures = ["sites.json"]

    def setUp(self):
        super().setUp()
        cache.clear()
        self.factory = RequestFactory()

    def test_callable_param(self):
        calls = []

        @cached_get(300, lambda request: getattr(request, "flag", ""))
        def view(request):
            calls.append(1)
            return HttpResponse("rendered %s" % len(calls))

        request = self.factory.get("/callable-param/")
        request.flag = "aaa"
        response1 = view(request)
        response2 = view(request)
        # Second call was served from cache
        self.assertEqual(len(calls), 1)
        self.assertEqual(response1.content, response2.content)

        # A different callable result produces a different cache key
        request = self.factory.get("/callable-param/")
        request.flag = "bbb"
        response3 = view(request)
        self.assertEqual(len(calls), 2)

    def test_string_param_emits_deprecation_warning(self):
        with self.assertWarns(DeprecationWarning):

            @cached_get(300, "request.is_secure()")
            def view(request):
                return HttpResponse("x")

    def test_legacy_string_param_still_resolves(self):
        calls = []
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            @cached_get(300, "request.is_secure()")
            def view(request):
                calls.append(1)
                return HttpResponse("rendered %s" % len(calls))

        response1 = view(self.factory.get("/legacy-param/"))
        response2 = view(self.factory.get("/legacy-param/"))
        # Second call was served from cache
        self.assertEqual(len(calls), 1)
        self.assertEqual(response1.content, response2.content)

        # is_secure() differs, so the cache key differs
        view(self.factory.get("/legacy-param/", secure=True))
        self.assertEqual(len(calls), 2)

    def test_resolve_legacy_param(self):
        request = self.factory.get("/resolver/")
        self.assertEqual(
            resolve_legacy_param("request.path_info", request), "/resolver/"
        )
        self.assertIs(resolve_legacy_param("request.is_secure()", request), False)
        self.assertIs(resolve_legacy_param("request", request), request)
        # Dotted traversal more than one level deep with a trailing call
        self.assertEqual(
            resolve_legacy_param("request.path.upper()", request), "/RESOLVER/"
        )

    def test_arbitrary_string_expressions_rejected(self):
        request = self.factory.get("/rejected/")
        for param in [
            "__import__('os')",
            "__import__('os').system('true')",
            "request.is_secure() or __import__('os')",
            "request.META['HTTP_HOST']",
            "request.get_host() + 'x'",
            "eval('1')",
            "1 + 1",
            "os.system('true')",
            "request.build_absolute_uri('/x/')",
        ]:
            with self.assertRaises(ValueError):
                resolve_legacy_param(param, request)

    def test_view_with_rejected_string_param(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            @cached_get(300, "__import__('os')")
            def view(request):
                return HttpResponse("x")

        with self.assertRaises(ValueError):
            view(self.factory.get("/rejected-view/"))


class CachedGetHeadersTestCase(TestCase):
    """Regression tests for item 24: cached_get must store headers via the
    public mapping API under a versioned (ucache3-) key, and replay them
    correctly on a cache hit."""

    if "django.contrib.sites" in settings.INSTALLED_APPS:
        fixtures = ["sites.json"]

    def setUp(self):
        super().setUp()
        cache.clear()

    def test_payload_shape_and_header_replay(self):
        url = reverse("cached-header-view")
        backend = caches["default"]
        captured = {}
        orig_set = backend.set

        def spy(key, value, *args, **kwargs):
            captured[key] = value
            return orig_set(key, value, *args, **kwargs)

        with mock.patch.object(backend, "set", side_effect=spy):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        payloads = [
            (key, value)
            for key, value in captured.items()
            if isinstance(value, dict) and "headers" in value and "content" in value
        ]
        self.assertEqual(len(payloads), 1)
        key, payload = payloads[0]
        # The key prefix is versioned so 2.x payloads are ignored rather
        # than mis-parsed after an upgrade
        self.assertTrue(key.startswith("ucache3-"), key)
        # Headers are stored as a plain name -> value mapping, not the
        # private _store (name, value) tuple format
        headers = {name.lower(): value for name, value in payload["headers"].items()}
        self.assertEqual(headers.get("foo"), "bar")
        self.assertEqual(headers.get("content-type"), "application/json")

        # The second request is served from the cache and replays the headers
        response = self.client.get(url)
        self.assertEqual(response.headers["foo"], "bar")
        self.assertEqual(response.headers["content-type"], "application/json")


class ClassDecoratorMetadataTestCase(SimpleTestCase):
    """Regression tests for item 25: the ultracache() class decorator must
    not clobber the wrapped class's introspection metadata."""

    def test_wrapped_class_preserves_metadata(self):
        class Original(TemplateView):
            """Original docstring."""

            template_name = "ultracache/cached_header_view.html"

        Decorated = ultracache(300)(Original)
        self.assertTrue(issubclass(Decorated, Original))
        self.assertEqual(Decorated.__name__, "Original")
        self.assertEqual(Decorated.__qualname__, Original.__qualname__)
        self.assertEqual(Decorated.__module__, Original.__module__)
        self.assertEqual(Decorated.__doc__, "Original docstring.")


class CachedGetBypassTestCase(TestCase):
    """cached_get must bypass caching for non-GET requests and requests
    carrying messages."""

    if "django.contrib.sites" in settings.INSTALLED_APPS:
        fixtures = ["sites.json"]

    def setUp(self):
        super().setUp()
        cache.clear()
        self.factory = RequestFactory()

    def test_post_request_is_never_cached(self):
        calls = []

        @cached_get(300)
        def view(request):
            calls.append(1)
            return HttpResponse("rendered %s" % len(calls))

        view(self.factory.post("/no-cache-post/"))
        response = view(self.factory.post("/no-cache-post/"))
        # The view ran both times, nothing was cached
        self.assertEqual(len(calls), 2)
        self.assertEqual(response.content, b"rendered 2")

    def test_request_with_messages_is_never_cached(self):
        calls = []

        @cached_get(300)
        def view(request):
            calls.append(1)
            return HttpResponse("rendered %s" % len(calls))

        request = self.factory.get("/no-cache-messages/")
        request._messages = ["a message"]
        view(request)
        response = view(request)
        self.assertEqual(len(calls), 2)
        self.assertEqual(response.content, b"rendered 2")


class CachedGetKeyComputationTestCase(TestCase):
    """Cache key computation edge paths: partial view functions and view
    kwargs."""

    if "django.contrib.sites" in settings.INSTALLED_APPS:
        fixtures = ["sites.json"]

    def setUp(self):
        super().setUp()
        cache.clear()
        self.factory = RequestFactory()

    def test_partial_view_func(self):
        calls = []

        def base_view(request):
            calls.append(1)
            return HttpResponse("rendered %s" % len(calls))

        view = cached_get(300)(partial(base_view))
        response1 = view(self.factory.get("/partial-view/"))
        response2 = view(self.factory.get("/partial-view/"))
        # Second call was served from cache
        self.assertEqual(len(calls), 1)
        self.assertEqual(response1.content, response2.content)

    def test_view_kwargs_contribute_to_cache_key(self):
        calls = []

        @cached_get(300)
        def view(request, **kwargs):
            calls.append(1)
            return HttpResponse("rendered %s" % len(calls))

        view(self.factory.get("/kwargs-view/"), slug="aaa")
        view(self.factory.get("/kwargs-view/"), slug="aaa")
        # Same kwargs: cached
        self.assertEqual(len(calls), 1)
        # Different kwargs: fresh render
        view(self.factory.get("/kwargs-view/"), slug="bbb")
        self.assertEqual(len(calls), 2)
