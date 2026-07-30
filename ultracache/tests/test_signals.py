from django import template
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings

from ultracache.tests.models import DummyModel
from ultracache.tests.utils import alt_purge_log, dummy_proxy


class PostDeletePurgerTestCase(TestCase):
    if "django.contrib.sites" in settings.INSTALLED_APPS:
        fixtures = ["sites.json"]

    def setUp(self):
        super().setUp()
        cache.clear()
        dummy_proxy.clear()
        self.factory = RequestFactory()

    def test_post_delete_deletes_path_registry_key(self):
        """Regression test for item 7: on_post_delete must delete the
        ucache-pth-%s-%s registry key when a purger is configured, mirroring
        on_post_save."""
        obj = DummyModel.objects.create(title="One", code="one")
        request = self.factory.get("/post-delete-purge/")
        t = template.Template(
            "{% load ultracache_tags %}\
            {% ultracache 1200 'test_post_delete_purge' %}{{ obj.title }}{% endultracache %}"
        )
        result = t.render(template.Context({"request": request, "obj": obj}))
        dummy_proxy.cache(request, result)

        ct = ContentType.objects.get_for_model(DummyModel)
        pk = obj.pk
        # Key prefix versioned to ucache3- by item 24
        key = "ucache3-pth-%s-%s" % (ct.id, pk)
        self.assertTrue(cache.get(key))
        self.assertTrue(dummy_proxy.is_cached("/post-delete-purge/"))

        obj.delete()

        # The purger ran against the reverse caching proxy
        self.assertFalse(dummy_proxy.is_cached("/post-delete-purge/"))
        # The stale path registry key must not survive the delete
        self.assertIsNone(cache.get(key))


class LazySettingsTestCase(TestCase):
    """Regression tests for item 21: the purger and invalidate settings used
    to be resolved at import time, so override_settings (and any runtime
    reconfiguration) was ignored."""

    if "django.contrib.sites" in settings.INSTALLED_APPS:
        fixtures = ["sites.json"]

    def setUp(self):
        super().setUp()
        cache.clear()
        dummy_proxy.clear()
        del alt_purge_log[:]
        self.factory = RequestFactory()

    def render(self, obj, path, counter):
        t = template.Template(
            "{% load ultracache_tags %}"
            "{% ultracache 1200 'test_lazy_settings' %}"
            "title = {{ obj.title }} counter = {{ counter }}"
            "{% endultracache %}"
        )
        request = self.factory.get(path)
        result = t.render(
            template.Context({"request": request, "obj": obj, "counter": counter})
        )
        dummy_proxy.cache(request, result)
        return result

    def test_overridden_purger_is_used(self):
        obj = DummyModel.objects.create(title="One", code="one")
        self.render(obj, "/lazy-purger/", 1)
        with override_settings(
            ULTRACACHE={"purge": {"method": "ultracache.tests.utils.alt_purger"}}
        ):
            obj.title = "Changed"
            obj.save()
        self.assertTrue(alt_purge_log)
        self.assertEqual(alt_purge_log[0][0], "/lazy-purger/")

    def test_overridden_invalidate_false_is_respected(self):
        obj = DummyModel.objects.create(title="One", code="one")
        result = self.render(obj, "/lazy-invalidate/", 1)
        self.assertIn("counter = 1", result)
        with override_settings(ULTRACACHE={"invalidate": False}):
            obj.title = "Changed"
            obj.save()
            # The save must not have invalidated the cached block
            result = self.render(obj, "/lazy-invalidate/", 2)
        self.assertIn("counter = 1", result)
        self.assertNotIn("counter = 2", result)

    def test_default_purger_still_used_after_override_exits(self):
        # The memoized settings must be reset when the override exits
        with override_settings(
            ULTRACACHE={"purge": {"method": "ultracache.tests.utils.alt_purger"}}
        ):
            pass
        obj = DummyModel.objects.create(title="One", code="one")
        self.render(obj, "/lazy-reset/", 1)
        self.assertTrue(dummy_proxy.is_cached("/lazy-reset/"))
        obj.title = "Changed"
        obj.save()
        self.assertFalse(dummy_proxy.is_cached("/lazy-reset/"))
        self.assertFalse(alt_purge_log)
