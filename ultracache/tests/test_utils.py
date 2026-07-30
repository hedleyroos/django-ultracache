from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from ultracache import utils
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
