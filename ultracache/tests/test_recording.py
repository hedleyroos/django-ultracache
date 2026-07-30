import contextvars

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.signals import request_finished
from django.test import SimpleTestCase, TestCase, modify_settings
from django.test.client import RequestFactory
from django.urls import reverse

from ultracache import (
    Recorder,
    clear_recorder,
    get_or_create_recorder,
    get_recorder,
    set_recorder,
)
from ultracache.middleware import UltraCacheMiddleware
from ultracache.tests.models import DummyForeignModel, DummyModel


class RecorderTestCase(SimpleTestCase):
    """Pin the recorder structure introduced by item 17: dedup on insert,
    insertion order, start_index slicing and the dedup barrier."""

    def test_dedups_repeated_entries(self):
        recorder = Recorder()
        recorder.append((1, 1))
        recorder.append((1, 1))
        recorder.append((1, 1))
        self.assertEqual(list(recorder), [(1, 1)])

    def test_preserves_insertion_order(self):
        recorder = Recorder()
        recorder.append((1, 1))
        recorder.append((2, 5))
        recorder.append((1, 1))
        recorder.append((1, 2))
        self.assertEqual(list(recorder), [(1, 1), (2, 5), (1, 2)])

    def test_len_and_slicing(self):
        recorder = Recorder()
        recorder.append((1, 1))
        recorder.append((1, 2))
        recorder.append((1, 3))
        self.assertEqual(len(recorder), 3)
        self.assertEqual(recorder[1:], [(1, 2), (1, 3)])
        self.assertEqual(recorder[0], (1, 1))

    def test_barrier_allows_re_recording_for_new_block(self):
        # An entry recorded before a block starts must be recorded again
        # inside the block so it appears in the block's [start_index:] slice.
        recorder = Recorder()
        recorder.append((1, 1))
        start_index = len(recorder)
        old_barrier = recorder.set_barrier(start_index)
        self.assertEqual(old_barrier, 0)
        recorder.append((1, 1))
        self.assertEqual(recorder[start_index:], [(1, 1)])
        # Within the block the entry still dedups
        recorder.append((1, 1))
        self.assertEqual(recorder[start_index:], [(1, 1)])
        recorder.set_barrier(old_barrier)


class RecordingTestCase(TestCase):
    """Direct unit tests for the Model.__getattribute__ patch."""

    def setUp(self):
        super().setUp()
        clear_recorder()
        self.addCleanup(clear_recorder)

    def test_inactive_records_nothing(self):
        obj = DummyModel.objects.create(title="One", code="one")
        clear_recorder()
        self.assertIsNone(get_recorder())
        # Attribute access with recording inactive must not crash and must
        # not create a recorder.
        self.assertEqual(obj.title, "One")
        self.assertEqual(obj.pk, obj.id)
        self.assertIsNone(get_recorder())

    def test_active_records_ct_and_pk(self):
        obj = DummyModel.objects.create(title="One", code="one")
        ct = ContentType.objects.get_for_model(DummyModel)
        recorder = Recorder()
        set_recorder(recorder)
        obj.title
        self.assertEqual(list(recorder), [(ct.id, obj.pk)])

    def test_repeated_access_records_once(self):
        obj = DummyModel.objects.create(title="One", code="one")
        ct = ContentType.objects.get_for_model(DummyModel)
        recorder = Recorder()
        set_recorder(recorder)
        # Many attribute accesses on the same object dedup to a single entry
        for _ in range(10):
            obj.title
            obj.code
            obj.pk
        self.assertEqual(list(recorder), [(ct.id, obj.pk)])

    def test_no_infinite_recursion(self):
        # Attribute access with a recorder active must not recurse: reading
        # the pk bypasses __getattribute__ and ContentType instances are
        # excluded from recording.
        obj = DummyModel.objects.create(title="One", code="one")
        recorder = Recorder()
        set_recorder(recorder)
        for name in ("title", "code", "pk", "id", "_meta", "__dict__"):
            getattr(obj, name)

    def test_content_type_instances_not_recorded(self):
        ct = ContentType.objects.get_for_model(DummyModel)
        recorder = Recorder()
        set_recorder(recorder)
        # Accessing attributes of a ContentType instance must not recurse
        # and must not be recorded.
        ct.app_label
        ct.model
        ct.pk
        self.assertEqual(list(recorder), [])

    def test_unsaved_instance_records_none_pk(self):
        # Pin the behaviour noted in roadmap item 31: an unsaved instance
        # records a pk of None.
        obj = DummyModel(title="Unsaved", code="unsaved")
        ct = ContentType.objects.get_for_model(DummyModel)
        recorder = Recorder()
        set_recorder(recorder)
        obj.title
        self.assertEqual(list(recorder), [(ct.id, None)])


class MiddlewareTestCase(TestCase):
    """Item 16: the middleware must clean up the recorder after a normal
    response and when the view raises."""

    def setUp(self):
        super().setUp()
        clear_recorder()
        self.addCleanup(clear_recorder)
        self.factory = RequestFactory()

    def test_cleanup_after_response(self):
        def get_response(request):
            get_or_create_recorder().append((1, 1))
            return "response"

        middleware = UltraCacheMiddleware(get_response)
        response = middleware(self.factory.get("/"))
        self.assertEqual(response, "response")
        self.assertIsNone(get_recorder())

    def test_cleanup_after_exception(self):
        def get_response(request):
            get_or_create_recorder().append((1, 1))
            raise RuntimeError("boom")

        middleware = UltraCacheMiddleware(get_response)
        with self.assertRaises(RuntimeError):
            middleware(self.factory.get("/"))
        self.assertIsNone(get_recorder())


class RequestFinishedCleanupTestCase(TestCase):
    """Item 16: deployments without the middleware are cleaned up by the
    request_finished signal receiver."""

    if "django.contrib.sites" in settings.INSTALLED_APPS:
        fixtures = ["sites.json"]

    def setUp(self):
        super().setUp()
        clear_recorder()
        self.addCleanup(clear_recorder)

    def test_signal_clears_recorder(self):
        get_or_create_recorder().append((1, 1))
        self.assertIsNotNone(get_recorder())
        request_finished.send(sender=self.__class__)
        self.assertIsNone(get_recorder())

    @modify_settings(
        MIDDLEWARE={"remove": "ultracache.middleware.UltraCacheMiddleware"}
    )
    def test_request_without_middleware_cleans_up(self):
        one = DummyModel.objects.create(title="One", code="one")
        DummyModel.objects.create(title="Two", code="two")
        DummyForeignModel.objects.create(title="Three", points_to=one, code="three")
        DummyModel.objects.create(title="Four", code="four")
        DummyModel.objects.create(title="Five", code="five")
        clear_recorder()
        response = self.client.get(reverse("method-cached-view"))
        self.assertEqual(response.status_code, 200)
        # The view created a recorder lazily; the test client fires
        # request_finished via response.close(), which must have cleared it.
        self.assertIsNone(get_recorder())


class ContextIsolationTestCase(TestCase):
    """Item 15: a recorder set in one context must not leak into another."""

    def setUp(self):
        super().setUp()
        clear_recorder()
        self.addCleanup(clear_recorder)

    def test_fresh_context_has_no_recorder(self):
        recorder = get_or_create_recorder()
        recorder.append((1, 1))
        ctx = contextvars.Context()
        self.assertIsNone(ctx.run(get_recorder))

    def test_recorder_set_in_other_context_does_not_leak(self):
        clear_recorder()

        def record_in_context():
            recorder = get_or_create_recorder()
            recorder.append((1, 1))
            return recorder

        ctx = contextvars.Context()
        other = ctx.run(record_in_context)
        self.assertEqual(list(other), [(1, 1)])
        # The current context is unaffected
        self.assertIsNone(get_recorder())

    def test_copied_context_set_does_not_affect_parent(self):
        recorder = get_or_create_recorder()
        ctx = contextvars.copy_context()
        ctx.run(set_recorder, Recorder())
        # The set in the copied context must not replace the parent's
        # recorder.
        self.assertIs(get_recorder(), recorder)
