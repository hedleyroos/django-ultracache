from unittest import mock

from django import template
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache, caches
from django.db.migrations.recorder import MigrationRecorder
from django.db.models.signals import post_save
from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings

from ultracache import clear_recorder
from ultracache.utils import Ultracache
from ultracache.tests.models import DummyModel, DummyOtherModel
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


class PurgerRuntimeResolutionTestCase(TestCase):
    """Issue 5: if _get_purger ever hits an unimportable dotted path at
    runtime it must raise a clear ImproperlyConfigured, not a raw
    ImportError, and must not memoize the failure away."""

    def test_bad_path_raises_improperly_configured(self):
        from django.core.exceptions import ImproperlyConfigured
        from ultracache.signals import _get_purger

        with override_settings(
            ULTRACACHE={"purge": {"method": "no.such.module.purger"}}
        ):
            with self.assertRaises(ImproperlyConfigured):
                _get_purger()
            # The failure is not memoized as None: it raises again
            with self.assertRaises(ImproperlyConfigured):
                _get_purger()


class SignalGuardsTestCase(TestCase):
    """Item 32: the shared guards in _resolve_content_type."""

    if "django.contrib.sites" in settings.INSTALLED_APPS:
        fixtures = ["sites.json"]

    def setUp(self):
        super().setUp()
        cache.clear()
        dummy_proxy.clear()
        clear_recorder()
        self.addCleanup(clear_recorder)

    def _cache_object(self, obj, name):
        """Cache a block that records obj and return the Ultracache factory
        for checking whether the block is still cached."""
        uc = Ultracache(3600, name)
        obj.title
        uc.cache(obj.title)
        clear_recorder()
        self.assertTrue(Ultracache(3600, name))

    def test_raw_save_does_not_invalidate(self):
        obj = DummyModel.objects.create(title="One", code="one")
        self._cache_object(obj, "raw-save-test")
        post_save.send(
            sender=DummyModel,
            instance=obj,
            created=False,
            raw=True,
            using="default",
            update_fields=None,
        )
        self.assertTrue(Ultracache(3600, "raw-save-test"))
        # Sanity check: a real (non-raw) save does invalidate
        obj.save()
        self.assertFalse(Ultracache(3600, "raw-save-test"))

    def test_migration_recorder_saves_are_ignored(self):
        Migration = MigrationRecorder.Migration
        instance = Migration(app="someapp", name="0001_initial")
        with mock.patch.object(
            ContentType.objects, "get_for_model"
        ) as mocked:
            post_save.send(
                sender=Migration,
                instance=instance,
                created=True,
                raw=False,
                using="default",
                update_fields=None,
            )
        # The sender filter fires before the content type is ever resolved
        mocked.assert_not_called()

    def test_get_for_model_runtime_error_is_swallowed(self):
        obj = DummyModel.objects.create(title="One", code="one")
        self._cache_object(obj, "runtime-error-test")
        obj.title = "Changed"
        # Checking the cached state above re-created the recorder; clear it
        # so the Model.__getattribute__ patch does not call the mocked
        # get_for_model during save()
        clear_recorder()
        with mock.patch.object(
            ContentType.objects, "get_for_model", side_effect=RuntimeError
        ):
            # Must not raise, and must not invalidate
            obj.save()
        self.assertTrue(Ultracache(3600, "runtime-error-test"))

    def test_non_model_sender_is_ignored(self):
        class NotAModel:
            pass

        with mock.patch.object(
            ContentType.objects, "get_for_model"
        ) as mocked:
            post_save.send(
                sender=NotAModel,
                instance=NotAModel(),
                created=True,
                raw=False,
            )
        mocked.assert_not_called()

    def test_non_model_instance_is_ignored(self):
        obj = DummyModel.objects.create(title="One", code="one")
        self._cache_object(obj, "non-model-instance-test")
        post_save.send(
            sender=DummyModel,
            instance=object(),
            created=False,
            raw=False,
        )
        self.assertTrue(Ultracache(3600, "non-model-instance-test"))

    def test_invalidate_false_ignores_delete(self):
        obj = DummyModel.objects.create(title="One", code="one")
        self._cache_object(obj, "invalidate-false-delete-test")
        with override_settings(ULTRACACHE={"invalidate": False}):
            obj.delete()
        self.assertTrue(Ultracache(3600, "invalidate-false-delete-test"))


class InvalidationBranchesTestCase(TestCase):
    """Item 32: created vs updated branches, end-to-end through the
    template tag: creating a new object invalidates the ct- registries,
    updating invalidates the object registries."""

    if "django.contrib.sites" in settings.INSTALLED_APPS:
        fixtures = ["sites.json"]

    def setUp(self):
        super().setUp()
        cache.clear()
        dummy_proxy.clear()
        clear_recorder()
        self.addCleanup(clear_recorder)
        self.factory = RequestFactory()
        self.ct = ContentType.objects.get_for_model(DummyOtherModel)
        self.ct_key = "ucache3-ct-%s" % self.ct.id

    def render_list(self, path, counter):
        t = template.Template(
            "{% load ultracache_tags %}"
            "{% ultracache 1200 'test_created_vs_updated' %}"
            "titles = {% for m in objs %}{{ m.title }},{% endfor %}"
            "counter = {{ counter }}"
            "{% endultracache %}"
        )
        request = self.factory.get(path)
        return t.render(
            template.Context(
                {
                    "request": request,
                    "objs": DummyOtherModel.objects.all().order_by("pk"),
                    "counter": counter,
                }
            )
        )

    def test_created_invalidates_content_type_keys(self):
        DummyOtherModel.objects.create(title="One", code="one")
        result = self.render_list("/created-test/", 1)
        self.assertIn("titles = One,", result)
        self.assertIn("counter = 1", result)
        # The block is cached and registered against the content type
        self.assertTrue(cache.get(self.ct_key))
        result = self.render_list("/created-test/", 2)
        self.assertIn("counter = 1", result)

        # Creating a new object of the content type invalidates the block
        DummyOtherModel.objects.create(title="Two", code="two")
        self.assertIsNone(cache.get(self.ct_key))
        result = self.render_list("/created-test/", 3)
        self.assertIn("titles = One,Two,", result)
        self.assertIn("counter = 3", result)

    def test_updated_invalidates_object_keys_not_content_type_keys(self):
        obj = DummyOtherModel.objects.create(title="One", code="one")
        obj_key = "ucache3-%s-%s" % (self.ct.id, obj.pk)
        result = self.render_list("/updated-test/", 1)
        self.assertIn("titles = One,", result)
        self.assertTrue(cache.get(obj_key))
        self.assertTrue(cache.get(self.ct_key))

        obj.title = "Changed"
        obj.save()
        # The object registry key is consumed, the content type registry
        # key is untouched by the update branch
        self.assertIsNone(cache.get(obj_key))
        self.assertTrue(cache.get(self.ct_key))
        result = self.render_list("/updated-test/", 2)
        self.assertIn("titles = Changed,", result)
        self.assertIn("counter = 2", result)


class InvalidateFallbacksTestCase(TestCase):
    """Edge paths of _invalidate: no purger configured, and cache backends
    without delete_many support."""

    if "django.contrib.sites" in settings.INSTALLED_APPS:
        fixtures = ["sites.json"]

    def setUp(self):
        super().setUp()
        cache.clear()
        clear_recorder()
        self.addCleanup(clear_recorder)
        self.factory = RequestFactory()

    def test_save_without_purger_configured(self):
        obj = DummyModel.objects.create(title="One", code="one")
        ct = ContentType.objects.get_for_model(DummyModel)
        request = self.factory.get("/no-purger/")
        uc = Ultracache(3600, "no-purger-test", request=request)
        obj.title
        uc.cache(obj.title)
        clear_recorder()
        obj_key = "ucache3-%s-%s" % (ct.id, obj.pk)
        pth_key = "ucache3-pth-%s-%s" % (ct.id, obj.pk)
        self.assertTrue(cache.get(obj_key))
        self.assertTrue(cache.get(pth_key))

        with override_settings(ULTRACACHE={}):
            obj.title = "Changed"
            obj.save()
        # The cache entries are invalidated and both registry keys are
        # consumed even without a purger
        self.assertFalse(Ultracache(3600, "no-purger-test", request=request))
        self.assertIsNone(cache.get(obj_key))
        self.assertIsNone(cache.get(pth_key))

    def test_invalidation_falls_back_when_delete_many_unsupported(self):
        obj = DummyModel.objects.create(title="One", code="one")
        uc = Ultracache(3600, "delete-many-fallback-test")
        obj.title
        uc.cache(obj.title)
        clear_recorder()
        self.assertTrue(Ultracache(3600, "delete-many-fallback-test"))

        backend = caches["default"]
        with mock.patch.object(
            backend, "delete_many", side_effect=NotImplementedError
        ):
            obj.title = "Changed"
            obj.save()
        self.assertFalse(Ultracache(3600, "delete-many-fallback-test"))
