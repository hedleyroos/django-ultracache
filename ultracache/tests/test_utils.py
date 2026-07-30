from unittest import mock

from django import template
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache, caches
from django.test import SimpleTestCase, TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings

from ultracache import clear_recorder, utils
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
