"""Async middleware (item 36) and ContextVar semantics under ASGI (item 37).

Findings pinned by these tests:

- Under ASGI each request is handled in its own copy of the context, so a
  recorder created during one request can never leak into another request's
  context. Under WSGI threads the context persists across requests on the
  same thread, which is why the middleware / request_finished cleanup
  exists.
- ``sync_to_async`` (thread_sensitive=True, the default, and what Django
  uses to run sync views and template rendering under ASGI) executes the
  sync function in a *copy* of the calling async context, and asgiref
  restores context variable changes back into the caller's context
  afterwards. Both directions therefore behave as expected: a recorder set
  in the async context is visible where rendering happens, and a recorder
  created lazily inside the sync hop is visible to the async middleware
  cleanup that runs afterwards.
"""

import asyncio

from asgiref.sync import iscoroutinefunction, sync_to_async

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.test.client import RequestFactory
from django.urls import reverse

from ultracache import clear_recorder, get_or_create_recorder, get_recorder
from ultracache.middleware import UltraCacheMiddleware
from ultracache.tests.models import DummyForeignModel, DummyModel


class AsyncMiddlewareTestCase(SimpleTestCase):
    """Item 36: the middleware advertises and implements an async call
    path with the same cleanup semantics as the sync path."""

    def setUp(self):
        super().setUp()
        clear_recorder()
        self.addCleanup(clear_recorder)
        self.factory = RequestFactory()

    def test_capability_flags(self):
        self.assertTrue(UltraCacheMiddleware.sync_capable)
        self.assertTrue(UltraCacheMiddleware.async_capable)

    def test_sync_get_response_keeps_sync_mode(self):
        middleware = UltraCacheMiddleware(lambda request: "response")
        self.assertFalse(iscoroutinefunction(middleware))
        self.assertEqual(middleware(self.factory.get("/")), "response")

    def test_async_get_response_marks_middleware_async(self):
        async def get_response(request):
            return "response"

        middleware = UltraCacheMiddleware(get_response)
        # Django's middleware chain relies on this marker to know it can
        # await the middleware directly instead of thread-shifting it.
        self.assertTrue(iscoroutinefunction(middleware))

    def test_async_cleanup_after_response(self):
        async def get_response(request):
            get_or_create_recorder().append((1, 1))
            return "response"

        middleware = UltraCacheMiddleware(get_response)

        async def main():
            response = await middleware(self.factory.get("/"))
            # Cleanup happened in the same context the recorder was
            # created in.
            return response, get_recorder()

        response, recorder_after = asyncio.run(main())
        self.assertEqual(response, "response")
        self.assertIsNone(recorder_after)
        # Nothing leaked into the test's own context either
        self.assertIsNone(get_recorder())

    def test_async_cleanup_after_exception(self):
        async def get_response(request):
            get_or_create_recorder().append((1, 1))
            raise RuntimeError("boom")

        middleware = UltraCacheMiddleware(get_response)

        async def main():
            with self.assertRaises(RuntimeError):
                await middleware(self.factory.get("/"))
            return get_recorder()

        self.assertIsNone(asyncio.run(main()))
        self.assertIsNone(get_recorder())


class SyncToAsyncHopTestCase(SimpleTestCase):
    """Item 37: pin the ``sync_to_async`` assumptions ultracache relies on
    under ASGI. Django runs sync views and template rendering through
    ``sync_to_async(thread_sensitive=True)``; the recorder must cross that
    boundary in both directions."""

    def setUp(self):
        super().setUp()
        clear_recorder()
        self.addCleanup(clear_recorder)

    def test_recorder_set_in_async_context_visible_in_sync_hop(self):
        # A recorder activated in the async context (e.g. by async-aware
        # calling code) is the same object seen by sync rendering code
        # running under sync_to_async, and its mutations are shared.
        async def main():
            recorder = get_or_create_recorder()

            def sync_part():
                inner = get_recorder()
                self.assertIs(inner, recorder)
                inner.append((1, 1))

            # thread_sensitive=True is the default and is what Django uses
            await sync_to_async(sync_part)()
            self.assertEqual(list(recorder), [(1, 1)])

        asyncio.run(main())

    def test_recorder_created_in_sync_hop_propagates_back(self):
        # The common ASGI flow: a sync view creates the recorder lazily
        # inside the sync_to_async hop. asgiref restores context variable
        # changes to the caller, so the async middleware cleanup that runs
        # afterwards sees (and can clear) the recorder.
        async def main():
            self.assertIsNone(get_recorder())

            def sync_part():
                get_or_create_recorder().append((2, 2))

            await sync_to_async(sync_part)()
            return get_recorder()

        recorder = asyncio.run(main())
        self.assertIsNotNone(recorder)
        self.assertEqual(list(recorder), [(2, 2)])


class AsyncClientTestCase(TestCase):
    """Item 37: end-to-end ASGI request handling with Django's AsyncClient
    against a cached view."""

    if "django.contrib.sites" in settings.INSTALLED_APPS:
        fixtures = ["sites.json"]

    def setUp(self):
        super().setUp()
        cache.clear()
        clear_recorder()
        self.addCleanup(clear_recorder)
        one = DummyModel.objects.create(title="One", code="one")
        DummyModel.objects.create(title="Two", code="two")
        DummyForeignModel.objects.create(title="Three", points_to=one, code="three")
        DummyModel.objects.create(title="Four", code="four")
        DummyModel.objects.create(title="Five", code="five")

    async def test_asgi_cached_view(self):
        url = reverse("method-cached-view")
        response1 = await self.async_client.get(url)
        self.assertEqual(response1.status_code, 200)
        self.assertIn(b"counter four = 1", response1.content)
        # The recorder did not leak into the test context
        self.assertIsNone(get_recorder())

        # Bump the counter: a re-render would now produce different
        # content, so identical content proves the cache was hit.
        cache.set("counter", 2)
        response2 = await self.async_client.get(url)
        self.assertEqual(response2.status_code, 200)
        self.assertEqual(response1.content, response2.content)
        self.assertIsNone(get_recorder())

    async def test_asgi_uncached_view_creates_no_recorder(self):
        # Recorder creation is lazy: a view without caching constructs
        # never creates one, under ASGI just as under WSGI.
        response = await self.async_client.get(reverse("render-view"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"One", response.content)
        self.assertIsNone(get_recorder())
