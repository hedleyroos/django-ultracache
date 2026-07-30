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
            # Must not raise
            purgers.varnish("/some/path/")
