from django import template
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase
from django.test.client import RequestFactory

from ultracache.tests.models import DummyModel
from ultracache.tests.utils import dummy_proxy


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
        key = "ucache-pth-%s-%s" % (ct.id, pk)
        self.assertTrue(cache.get(key))
        self.assertTrue(dummy_proxy.is_cached("/post-delete-purge/"))

        obj.delete()

        # The purger ran against the reverse caching proxy
        self.assertFalse(dummy_proxy.is_cached("/post-delete-purge/"))
        # The stale path registry key must not survive the delete
        self.assertIsNone(cache.get(key))
