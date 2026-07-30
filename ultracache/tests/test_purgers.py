from unittest import mock

from django.test import SimpleTestCase
from django.test.utils import override_settings

from ultracache import purgers


class PurgersTestCase(SimpleTestCase):
    """Regression tests for item 1: the varnish and nginx purgers must work
    with the documented (README-shaped) settings dict, where ``method`` is a
    dotted-path string and ``url`` is its sibling key."""

    @override_settings(
        ULTRACACHE={
            "purge": {
                "method": "ultracache.purgers.varnish",
                "url": "http://localhost:8080/",
            }
        }
    )
    def test_varnish_with_readme_shaped_settings(self):
        with mock.patch("ultracache.purgers.requests.request") as mocked:
            purgers.varnish("/some/path/", headers={"Host": "example.com"})
        mocked.assert_called_once_with(
            "PURGE",
            "http://localhost:8080/some/path/",
            timeout=1,
            headers={"Host": "example.com"},
        )

    @override_settings(
        ULTRACACHE={
            "purge": {
                "method": "ultracache.purgers.nginx",
                "url": "http://localhost:8081",
            }
        }
    )
    def test_nginx_with_readme_shaped_settings(self):
        with mock.patch("ultracache.purgers.requests.request") as mocked:
            purgers.nginx("/some/path/")
        mocked.assert_called_once_with(
            "PURGE",
            "http://localhost:8081/some/path/",
            timeout=1,
            headers={},
        )

    @override_settings(
        ULTRACACHE={
            "purge": {
                "method": "ultracache.purgers.varnish",
                "url": "http://localhost:8080/",
            }
        }
    )
    def test_varnish_swallows_request_exception(self):
        with mock.patch(
            "ultracache.purgers.requests.request",
            side_effect=purgers.requests.exceptions.RequestException("boom"),
        ):
            # Must not raise. Item 23: the failure is logged instead of
            # silently swallowed; capture it to keep the test output clean.
            with self.assertLogs("ultracache.purgers", level="WARNING"):
                purgers.varnish("/some/path/")

    @override_settings(
        ULTRACACHE={
            "purge": {
                "method": "ultracache.purgers.nginx",
                "url": "http://localhost:8080/",
            }
        }
    )
    def test_purge_failure_is_logged(self):
        """Regression test for item 23: purge failures used to be silently
        swallowed. They must be logged with path, target URL and the
        exception."""
        with mock.patch(
            "ultracache.purgers.requests.request",
            side_effect=purgers.requests.exceptions.RequestException("boom"),
        ):
            with self.assertLogs("ultracache.purgers", level="WARNING") as logs:
                purgers.nginx("/some/path/")
        output = "\n".join(logs.output)
        self.assertIn("/some/path/", output)
        self.assertIn("http://localhost:8080/some/path/", output)
        self.assertIn("boom", output)


class BroadcastPurgerTestCase(SimpleTestCase):
    """The broadcast purger delegates to the celery task."""

    def test_broadcast_delegates_to_task(self):
        with mock.patch("ultracache.tasks.broadcast_purge") as mocked:
            purgers.broadcast("/some/path/", {"Host": "example.com"})
        mocked.delay.assert_called_once_with(
            "/some/path/", {"Host": "example.com"}
        )

    def test_broadcast_default_headers(self):
        with mock.patch("ultracache.tasks.broadcast_purge") as mocked:
            purgers.broadcast("/some/path/")
        mocked.delay.assert_called_once_with("/some/path/", None)
