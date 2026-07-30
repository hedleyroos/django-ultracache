import warnings

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.test import TestCase
from django.test.client import RequestFactory

from ultracache.decorators import cached_get, resolve_legacy_param


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
