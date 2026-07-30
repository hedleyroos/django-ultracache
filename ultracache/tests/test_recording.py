import asyncio
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
from ultracache.tests.models import (
    DummyForeignModel,
    DummyModel,
    DummyProxyModel,
)


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

    def test_deferred_field_access_via_only_records(self):
        # Accessing a deferred field triggers a refresh query; that path
        # must record the object and must not recurse. Materializing the
        # refresh instance touches attributes before its pk lands in
        # __dict__, which records one harmless (ct, None) entry — the same
        # pinned behaviour as unsaved instances.
        obj = DummyModel.objects.create(title="One", code="one")
        deferred = DummyModel.objects.only("id").get(pk=obj.pk)
        ct = ContentType.objects.get_for_model(DummyModel)
        recorder = Recorder()
        set_recorder(recorder)
        self.assertEqual(deferred.title, "One")
        self.assertEqual(list(recorder), [(ct.id, obj.pk), (ct.id, None)])

    def test_deferred_field_access_via_defer_records(self):
        obj = DummyModel.objects.create(title="One", code="one")
        deferred = DummyModel.objects.defer("title", "code").get(pk=obj.pk)
        ct = ContentType.objects.get_for_model(DummyModel)
        recorder = Recorder()
        set_recorder(recorder)
        self.assertEqual(deferred.code, "one")
        self.assertEqual(deferred.title, "One")
        # The (ct, None) entry stems from materializing the refresh
        # instance, see test_deferred_field_access_via_only_records
        self.assertEqual(list(recorder), [(ct.id, obj.pk), (ct.id, None)])

    def test_records_during_queryset_iteration(self):
        objs = [
            DummyModel.objects.create(title="T%s" % i, code="c%s" % i)
            for i in range(3)
        ]
        ct = ContentType.objects.get_for_model(DummyModel)
        recorder = Recorder()
        set_recorder(recorder)
        titles = [
            o.title for o in DummyModel.objects.all().order_by("pk")
        ]
        self.assertEqual(titles, ["T0", "T1", "T2"])
        # Materializing each row touches attributes before the pk is
        # assigned, so a single deduplicated (ct, None) entry precedes the
        # real per-object entries.
        self.assertEqual(
            list(recorder),
            [(ct.id, None)] + [(ct.id, o.pk) for o in objs],
        )

    def test_proxy_model_records_concrete_content_type(self):
        # get_for_model resolves proxies to their concrete model, so blocks
        # recorded through a proxy instance are invalidated by saves of the
        # concrete model.
        obj = DummyModel.objects.create(title="One", code="one")
        proxy = DummyProxyModel.objects.get(pk=obj.pk)
        concrete_ct = ContentType.objects.get_for_model(DummyModel)
        recorder = Recorder()
        set_recorder(recorder)
        proxy.title
        self.assertEqual(list(recorder), [(concrete_ct.id, obj.pk)])

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

    if "django.contrib.sites" in settings.INSTALLED_APPS:
        fixtures = ["sites.json"]

    def setUp(self):
        super().setUp()
        clear_recorder()
        self.addCleanup(clear_recorder)
        self.factory = RequestFactory()

    def test_lazy_recorder_does_not_leak_into_next_request(self):
        # Item 30: a recorder created lazily mid-request must not leak into
        # the next request handled on the same thread/context.
        recorders_at_entry = []

        def get_response(request):
            recorders_at_entry.append(get_recorder())
            get_or_create_recorder().append((1, 1))
            return "response"

        middleware = UltraCacheMiddleware(get_response)
        middleware(self.factory.get("/first/"))
        middleware(self.factory.get("/second/"))
        # Each request started without a recorder
        self.assertEqual(recorders_at_entry, [None, None])
        self.assertIsNone(get_recorder())

    def test_sequential_client_requests_do_not_leak(self):
        # End-to-end variant with the full middleware stack: the recorder
        # created lazily by cached_get must be gone after each request.
        one = DummyModel.objects.create(title="One", code="one")
        DummyModel.objects.create(title="Two", code="two")
        DummyForeignModel.objects.create(
            title="Three", points_to=one, code="three"
        )
        DummyModel.objects.create(title="Four", code="four")
        DummyModel.objects.create(title="Five", code="five")
        clear_recorder()

        url = reverse("method-cached-view")
        response1 = self.client.get(url)
        self.assertEqual(response1.status_code, 200)
        self.assertIsNone(get_recorder())
        response2 = self.client.get(url)
        self.assertEqual(response2.status_code, 200)
        self.assertIsNone(get_recorder())
        # The second request was served from the cache
        self.assertEqual(response1.content, response2.content)

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


class RecorderApiTestCase(SimpleTestCase):
    """Item 34: error and edge paths of the recorder ContextVar API."""

    def setUp(self):
        super().setUp()
        clear_recorder()
        self.addCleanup(clear_recorder)

    def test_get_recorder_defaults_to_none(self):
        self.assertIsNone(get_recorder())

    def test_clear_recorder_is_idempotent(self):
        clear_recorder()
        clear_recorder()
        self.assertIsNone(get_recorder())
        get_or_create_recorder()
        clear_recorder()
        clear_recorder()
        self.assertIsNone(get_recorder())

    def test_get_or_create_recorder_returns_same_instance(self):
        recorder = get_or_create_recorder()
        self.assertIs(get_or_create_recorder(), recorder)
        self.assertIs(get_recorder(), recorder)

    def test_set_recorder_installs_given_instance(self):
        recorder = Recorder()
        set_recorder(recorder)
        self.assertIs(get_recorder(), recorder)
        self.assertIs(get_or_create_recorder(), recorder)

    def test_recorder_index_error(self):
        recorder = Recorder()
        with self.assertRaises(IndexError):
            recorder[0]


class AsyncContextIsolationTestCase(SimpleTestCase):
    """Item 34: recorders must not cross-contaminate between concurrent
    asyncio tasks. asyncio.gather runs each coroutine in its own copy of
    the context, so each task gets its own recorder."""

    def setUp(self):
        super().setUp()
        clear_recorder()
        self.addCleanup(clear_recorder)

    def test_gather_concurrent_recorders_are_isolated(self):
        async def worker(tuples):
            recorder = get_or_create_recorder()
            for tu in tuples:
                recorder.append(tu)
                # Force interleaving with the other task
                await asyncio.sleep(0)
            # The task still sees its own recorder after switching
            self.assertIs(get_recorder(), recorder)
            return recorder

        async def main():
            return await asyncio.gather(
                worker([(1, 1), (1, 2)]),
                worker([(2, 1), (2, 2)]),
            )

        recorder_a, recorder_b = asyncio.run(main())
        self.assertIsNot(recorder_a, recorder_b)
        self.assertEqual(list(recorder_a), [(1, 1), (1, 2)])
        self.assertEqual(list(recorder_b), [(2, 1), (2, 2)])
        # Nothing leaked into the test's own context
        self.assertIsNone(get_recorder())

    def test_gather_tasks_do_not_see_parent_recorder_mutations(self):
        # A recorder created before the tasks spawn is shared via context
        # copies; tasks that install their own recorder do not disturb the
        # parent's.
        parent_recorder = get_or_create_recorder()
        parent_recorder.append((9, 9))

        async def replace_and_record(tu):
            recorder = Recorder()
            set_recorder(recorder)
            recorder.append(tu)
            await asyncio.sleep(0)
            return recorder

        async def main():
            return await asyncio.gather(
                replace_and_record((1, 1)),
                replace_and_record((2, 2)),
            )

        recorder_a, recorder_b = asyncio.run(main())
        self.assertEqual(list(recorder_a), [(1, 1)])
        self.assertEqual(list(recorder_b), [(2, 2)])
        # The parent context still has its own recorder, unchanged
        self.assertIs(get_recorder(), parent_recorder)
        self.assertEqual(list(parent_recorder), [(9, 9)])
