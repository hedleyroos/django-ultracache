import importlib
from unittest import mock

from django import template
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache, caches
from django.test import SimpleTestCase, TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings

from ultracache import Recorder, clear_recorder, utils
from ultracache.utils import Ultracache, reduce_list_size
from ultracache.tests.models import DummyModel


class ReduceListSizeTestCase(SimpleTestCase):
    """Regression tests for item 5: float division and the negative-slice
    edge in reduce_list_size."""

    def test_list_under_max_size_untouched(self):
        li = ["a", "b"]
        keep, toss = reduce_list_size(li)
        self.assertEqual(keep, li)
        self.assertEqual(toss, [])

    def test_trims_oversize_list(self):
        # 100 items triggers decrement_by = n / 10, which was a float and
        # made the slice raise TypeError.
        li = [str(i) * 10 for i in range(100)]
        max_size = len(repr(li)) // 2
        with mock.patch.object(utils, "MAX_SIZE", max_size):
            keep, toss = reduce_list_size(li)
        self.assertTrue(toss)
        self.assertTrue(keep)
        # The last N items are kept, the rest tossed
        self.assertEqual(toss + keep, li)
        self.assertLess(len(repr(keep)), max_size)

    def test_overshoot_clamps_to_empty(self):
        # 15 items with a MAX_SIZE small enough that even the last 5 items
        # are too large. n steps 15 -> 5 -> -5 and must clamp at 0 instead
        # of flipping the slices and returning an oversize keep list.
        li = ["x" * 10] * 15
        with mock.patch.object(utils, "MAX_SIZE", 30):
            keep, toss = reduce_list_size(li)
        self.assertEqual(keep, [])
        self.assertEqual(toss, li)

    def test_small_list_with_large_items(self):
        # n = 5 with decrement_by = 10 steps straight past zero.
        li = ["x" * 100] * 5
        with mock.patch.object(utils, "MAX_SIZE", 50):
            keep, toss = reduce_list_size(li)
        self.assertEqual(keep, [])
        self.assertEqual(toss, li)


class CacheMetaTrimTestCase(TestCase):
    """Item 29 gap: exercise the reduce_list_size MAX_SIZE trim loop through
    cache_meta with oversized metadata lists, end-to-end against the cache
    backend. All four registry key families must be trimmed, and the tossed
    cache keys of the object and content-type registries must actually be
    deleted from the cache."""

    def setUp(self):
        super().setUp()
        cache.clear()
        clear_recorder()
        self.addCleanup(clear_recorder)
        self.factory = RequestFactory()

    def _populate_oversized_registries(self, ct_id, pk):
        obj_key = "ucache3-%s-%s" % (ct_id, pk)
        pth_key = "ucache3-pth-%s-%s" % (ct_id, pk)
        ct_key = "ucache3-ct-%s" % ct_id
        ct_pth_key = "ucache3-ct-pth-%s" % ct_id
        stale_keys = ["stale-fragment-key-%03d" % i for i in range(50)]
        for k in stale_keys:
            cache.set(k, "stale cached fragment")
        stale_paths = [["/old-path-%03d/" % i, {}] for i in range(50)]
        cache.set(obj_key, list(stale_keys))
        cache.set(ct_key, list(stale_keys))
        cache.set(pth_key, list(stale_paths))
        cache.set(ct_pth_key, list(stale_paths))
        return obj_key, pth_key, ct_key, ct_pth_key, stale_keys, stale_paths

    def test_oversized_metadata_lists_are_trimmed_and_tossed_keys_deleted(self):
        ct_id, pk = 991, 7
        (
            obj_key,
            pth_key,
            ct_key,
            ct_pth_key,
            stale_keys,
            stale_paths,
        ) = self._populate_oversized_registries(ct_id, pk)

        recorder = Recorder()
        recorder.append((ct_id, pk))
        request = self.factory.get("/trim-test/")
        with mock.patch.object(utils, "MAX_SIZE", 400):
            utils.cache_meta(recorder, "trim-content-key", request=request)

        # Object registry: trimmed to a tail of the stale keys plus the new
        # cache key.
        for key in (obj_key, ct_key):
            value = cache.get(key)
            self.assertEqual(value[-1], "trim-content-key")
            kept = value[:-1]
            self.assertTrue(kept)
            self.assertLess(len(kept), len(stale_keys))
            # The kept entries are the *last* N of the original list
            self.assertEqual(kept, stale_keys[-len(kept):])

        # The tossed fragment keys were deleted from the cache, the kept
        # ones survive. Both registries tossed the same prefix.
        value = cache.get(obj_key)
        kept = value[:-1]
        tossed = stale_keys[: len(stale_keys) - len(kept)]
        self.assertTrue(tossed)
        for k in tossed:
            self.assertIsNone(cache.get(k))
        for k in kept:
            self.assertEqual(cache.get(k), "stale cached fragment")

        # Path registries: trimmed to a tail plus the new [path, headers]
        # entry. Tossed paths are not cache keys so nothing is deleted for
        # them.
        for key in (pth_key, ct_pth_key):
            value = cache.get(key)
            self.assertEqual(value[-1][0], "/trim-test/")
            kept = value[:-1]
            self.assertTrue(kept)
            self.assertLess(len(kept), len(stale_paths))
            self.assertEqual(kept, stale_paths[-len(kept):])

    def test_trim_with_delete_many_unsupported_falls_back_to_delete(self):
        ct_id, pk = 992, 3
        obj_key, _, _, _, stale_keys, _ = self._populate_oversized_registries(
            ct_id, pk
        )
        recorder = Recorder()
        recorder.append((ct_id, pk))
        backend = caches["default"]
        with mock.patch.object(
            backend, "delete_many", side_effect=NotImplementedError
        ):
            with mock.patch.object(utils, "MAX_SIZE", 400):
                utils.cache_meta(recorder, "trim-fallback-key")
        value = cache.get(obj_key)
        self.assertEqual(value[-1], "trim-fallback-key")
        tossed = stale_keys[: len(stale_keys) - len(value[:-1])]
        self.assertTrue(tossed)
        for k in tossed:
            self.assertIsNone(cache.get(k))

    def test_set_many_unsupported_falls_back_to_set(self):
        recorder = Recorder()
        recorder.append((993, 5))
        backend = caches["default"]
        with mock.patch.object(
            backend, "set_many", side_effect=NotImplementedError
        ):
            utils.cache_meta(recorder, "set-fallback-key")
        self.assertEqual(cache.get("ucache3-993-5"), ["set-fallback-key"])
        self.assertEqual(cache.get("set-fallback-key-objs"), [(993, 5)])

    def test_default_metadata_timeout_when_timeout_not_passed(self):
        # Calling cache_meta without a timeout keeps the historic one day
        # metadata TTL.
        recorder = Recorder()
        recorder.append((994, 1))
        backend = caches["default"]
        with mock.patch.object(
            backend, "set_many", wraps=backend.set_many
        ) as mocked:
            utils.cache_meta(recorder, "default-timeout-key")
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.args[1], 86400)


class CacheMetaHeadersTestCase(TestCase):
    """Item 35: the CONSIDER_COOKIES and consider-headers paths of
    cache_meta()."""

    def setUp(self):
        super().setUp()
        cache.clear()
        clear_recorder()
        self.addCleanup(clear_recorder)
        self.factory = RequestFactory()

    def test_consider_cookies_filters_and_sorts_cookie_header(self):
        request = self.factory.get(
            "/cookie-path/", HTTP_COOKIE="zeta=2; alpha=1; ignored=x"
        )
        recorder = Recorder()
        recorder.append((771, 1))
        with mock.patch.object(utils, "CONSIDER_COOKIES", ["alpha", "zeta"]):
            with mock.patch.object(utils, "CONSIDER_HEADERS", []):
                utils.cache_meta(recorder, "cookie-key", request=request)
        value = cache.get("ucache3-pth-771-1")
        self.assertEqual(len(value), 1)
        path, headers = value[0]
        self.assertEqual(path, "/cookie-path/")
        # Only the considered cookies survive, sorted by name
        self.assertEqual(dict(headers), {"cookie": "alpha=1; zeta=2"})

    def test_consider_cookies_with_empty_cookie_header(self):
        # The test client (and RequestFactory) always sends a HTTP_COOKIE
        # header, empty by default. Pin that an empty cookie header still
        # contributes a stable empty "cookie" entry.
        request = self.factory.get("/no-cookie-path/")
        recorder = Recorder()
        recorder.append((772, 1))
        with mock.patch.object(utils, "CONSIDER_COOKIES", ["alpha"]):
            with mock.patch.object(utils, "CONSIDER_HEADERS", []):
                utils.cache_meta(recorder, "no-cookie-key", request=request)
        value = cache.get("ucache3-pth-772-1")
        self.assertEqual(value, [["/no-cookie-path/", {"cookie": ""}]])

    def test_consider_headers_filters_request_headers(self):
        request = self.factory.get(
            "/header-path/",
            HTTP_X_CUSTOM="custom-value",
            HTTP_X_OTHER="other-value",
        )
        recorder = Recorder()
        recorder.append((773, 1))
        with mock.patch.object(utils, "CONSIDER_COOKIES", []):
            with mock.patch.object(utils, "CONSIDER_HEADERS", ["x-custom"]):
                utils.cache_meta(recorder, "header-key", request=request)
        value = cache.get("ucache3-pth-773-1")
        self.assertEqual(len(value), 1)
        path, headers = value[0]
        self.assertEqual(path, "/header-path/")
        # Only the considered header survives, with the HTTP_ prefix
        # stripped and the name lowercased
        self.assertEqual(dict(headers), {"x-custom": "custom-value"})


class CookieHeaderSettingsTestCase(SimpleTestCase):
    """Item 35: the settings are read at utils module import time, so the
    RuntimeError guard for the cookie/header conflict fires on (re)import."""

    def _reload_utils(self):
        importlib.reload(utils)

    def test_conflicting_cookie_and_header_settings_raise(self):
        # Reload the module with real settings afterwards, whatever happens
        self.addCleanup(self._reload_utils)
        with override_settings(
            ULTRACACHE={
                "consider-cookies": ["sessionid"],
                "consider-headers": ["cookie"],
            }
        ):
            with self.assertRaises(RuntimeError) as cm:
                importlib.reload(utils)
        self.assertIn("consider-cookies", str(cm.exception))
        self.assertIn("consider-headers", str(cm.exception))

    def test_settings_defaults_when_ultracache_not_configured(self):
        self.addCleanup(self._reload_utils)
        with override_settings(ULTRACACHE={}):
            importlib.reload(utils)
            self.assertEqual(utils.MAX_SIZE, 1000000)
            self.assertEqual(utils.CONSIDER_HEADERS, [])
            self.assertEqual(utils.CONSIDER_COOKIES, [])

    def test_settings_values_are_lowercased(self):
        self.addCleanup(self._reload_utils)
        with override_settings(
            ULTRACACHE={
                "max-registry-value-size": 5000,
                "consider-headers": ["X-Custom"],
                "consider-cookies": ["SessionID"],
            }
        ):
            importlib.reload(utils)
            self.assertEqual(utils.MAX_SIZE, 5000)
            self.assertEqual(utils.CONSIDER_HEADERS, ["x-custom"])
            self.assertEqual(utils.CONSIDER_COOKIES, ["sessionid"])


class UltracacheObjectTestCase(TestCase):
    """Edge cases of the Ultracache helper object."""

    def setUp(self):
        super().setUp()
        cache.clear()
        clear_recorder()
        self.addCleanup(clear_recorder)

    def test_cache_method_may_only_be_called_once(self):
        uc = Ultracache(60, "double-cache-test")
        uc.cache("value")
        with self.assertRaises(RuntimeError):
            uc.cache("value again")


class CacheMetaTimeoutTestCase(TestCase):
    """Regression tests for item 20: the invalidation metadata must live at
    least as long as the content it invalidates. Content cached longer than
    a day used to outlive its metadata and could never be invalidated."""

    if "django.contrib.sites" in settings.INSTALLED_APPS:
        fixtures = ["sites.json"]

    def setUp(self):
        super().setUp()
        cache.clear()
        clear_recorder()
        self.addCleanup(clear_recorder)

    def test_metadata_ttl_at_least_content_timeout(self):
        one = DummyModel.objects.create(title="One", code="one")
        timeout = 86400 * 7
        backend = caches["default"]
        uc = Ultracache(timeout, "meta-ttl-test")
        one.title  # record the object against the caching block
        with mock.patch.object(
            backend, "set_many", wraps=backend.set_many
        ) as mocked:
            uc.cache("content")
        mocked.assert_called_once()
        self.assertGreaterEqual(mocked.call_args.args[1], timeout)

    def test_metadata_ttl_no_shorter_than_a_day(self):
        # Short content timeouts keep the historic one day metadata TTL:
        # metadata may outlive content harmlessly.
        one = DummyModel.objects.create(title="One", code="one")
        backend = caches["default"]
        uc = Ultracache(60, "meta-ttl-short-test")
        one.title
        with mock.patch.object(
            backend, "set_many", wraps=backend.set_many
        ) as mocked:
            uc.cache("content")
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.args[1], 86400)

    def test_none_timeout_metadata_never_expires(self):
        # A timeout of None means the content is cached forever, so the
        # metadata must not expire either.
        one = DummyModel.objects.create(title="One", code="one")
        backend = caches["default"]
        uc = Ultracache(None, "meta-ttl-none-test")
        one.title
        with mock.patch.object(
            backend, "set_many", wraps=backend.set_many
        ) as mocked:
            uc.cache("content")
        mocked.assert_called_once()
        self.assertIsNone(mocked.call_args.args[1])


ALIAS_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    },
    "ultracache": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "ultracache-secondary",
    },
}


@override_settings(
    CACHES=ALIAS_CACHES,
    ULTRACACHE={
        "purge": {"method": "ultracache.tests.utils.dummy_purger"},
        "cache_alias": "ultracache",
    },
)
class CacheAliasTestCase(TestCase):
    """Item 26: the documented cache_alias setting stores all ultracache data
    in the configured cache backend."""

    if "django.contrib.sites" in settings.INSTALLED_APPS:
        fixtures = ["sites.json"]

    def setUp(self):
        super().setUp()
        caches["default"].clear()
        caches["ultracache"].clear()
        self.factory = RequestFactory()

    def render(self, obj, path):
        t = template.Template(
            "{% load ultracache_tags %}"
            "{% ultracache 1200 'test_cache_alias' %}"
            "title = {{ obj.title }}{% endultracache %}"
        )
        request = self.factory.get(path)
        return t.render(template.Context({"request": request, "obj": obj}))

    def test_get_cache_returns_aliased_cache(self):
        from ultracache.utils import get_cache

        self.assertIs(get_cache(), caches["ultracache"])

    def test_get_cache_defaults_to_default_alias(self):
        from ultracache.utils import get_cache

        with override_settings(ULTRACACHE={}):
            self.assertIs(get_cache(), caches["default"])
        with override_settings():
            del settings.ULTRACACHE
            self.assertIs(get_cache(), caches["default"])

    def test_keys_land_in_aliased_cache_and_invalidation_works(self):
        obj = DummyModel.objects.create(title="One", code="one")
        result = self.render(obj, "/cache-alias/")
        self.assertIn("title = One", result)

        ct = ContentType.objects.get_for_model(DummyModel)
        key = "ucache3-%s-%s" % (ct.id, obj.pk)
        self.assertTrue(caches["ultracache"].get(key))
        self.assertIsNone(caches["default"].get(key))
        # No ultracache data leaked into the default cache
        self.assertFalse(
            [
                k
                for k in caches["default"]._cache
                if "ucache" in k or "template.cache" in k
            ]
        )

        # The cached content is served from the aliased cache
        obj.title = "Two"
        result = self.render(obj, "/cache-alias/")
        self.assertIn("title = One", result)

        # Saving invalidates through the aliased cache
        obj.save()
        self.assertIsNone(caches["ultracache"].get(key))
        result = self.render(obj, "/cache-alias/")
        self.assertIn("title = Two", result)


class UtilsTestCase(TestCase):
    if "django.contrib.sites" in settings.INSTALLED_APPS:
        fixtures = ["sites.json"]

    def setUp(self):
        super(UtilsTestCase, self).setUp()
        cache.clear()

    def test_context_manager_like_thing(self):
        one = DummyModel.objects.create(title="One", code="one")
        two = DummyModel.objects.create(title="Two", code="two")

        # Caching with object one
        uc = Ultracache(3600, "a", "b")
        self.assertFalse(uc)
        uc.cache(one.title)

        uc = Ultracache(3600, "a", "b")
        self.assertTrue(uc)
        self.assertEqual(uc.cached, one.title)

        one.title = "Onex"
        one.save()

        uc = Ultracache(3600, "a", "b")
        self.assertFalse(uc)

        # Caching with object two. Ensure object one doesn't bleed into this
        # section.
        uc = Ultracache(3600, "c", "d")
        self.assertFalse(uc)
        uc.cache(two.title)

        uc = Ultracache(3600, "c", "d")
        self.assertTrue(uc)
        self.assertEqual(uc.cached, two.title)

        two.title = "Onez"
        one.save()
        uc = Ultracache(3600, "c", "d")
        self.assertTrue(uc)

        two.title = "Twox"
        two.save()

        uc = Ultracache(3600, "c", "d")
        self.assertFalse(uc)
