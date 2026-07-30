import importlib
import json
import sys
import types
from unittest import mock

from celery.exceptions import Retry
from django.test import SimpleTestCase
from django.test.utils import override_settings


def make_pika_stub():
    """Build a minimal stand-in for pika 1.x. pika is not installed in the
    test environment, so the module is injected into sys.modules."""
    pika = types.ModuleType("pika")
    exceptions = types.ModuleType("pika.exceptions")

    class AMQPError(Exception):
        pass

    exceptions.AMQPError = AMQPError
    pika.exceptions = exceptions
    pika.URLParameters = mock.Mock(side_effect=lambda url: ("url-parameters", url))
    channel = mock.Mock()
    connection = mock.Mock()
    connection.channel.return_value = channel
    pika.BlockingConnection = mock.Mock(return_value=connection)
    return pika, connection, channel


class TasksTestCase(SimpleTestCase):
    def test_import(self):
        from ultracache import tasks


class TasksGuardTestCase(SimpleTestCase):
    def test_guard_message_supports_pika_1(self):
        """Item 27: the guard message must not demand pika<1.0 any more."""
        from ultracache import tasks

        with mock.patch.object(tasks, "DO_TASK", False):
            with self.assertRaises(RuntimeError) as cm:
                tasks.broadcast_purge("/some/path/")
        message = str(cm.exception)
        self.assertIn("pika>=1.0", message)
        self.assertNotIn("<1.0", message)


class BroadcastPurgeTestCase(SimpleTestCase):
    """Item 27: broadcast_purge with a stubbed pika module."""

    def setUp(self):
        super().setUp()
        self.pika, self.connection, self.channel = make_pika_stub()
        self._saved = {
            name: sys.modules.get(name) for name in ("pika", "pika.exceptions")
        }
        sys.modules["pika"] = self.pika
        sys.modules["pika.exceptions"] = self.pika.exceptions
        from ultracache import tasks

        self.tasks = importlib.reload(tasks)
        self.addCleanup(self._restore)

    def _restore(self):
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        importlib.reload(self.tasks)

    @override_settings(ULTRACACHE={"rabbitmq-url": "amqp://guest:guest@rabbit/"})
    def test_publishes_purge_instruction(self):
        result = self.tasks.broadcast_purge("/some/path/", {"Host": "example.com"})
        self.assertTrue(result)
        self.pika.URLParameters.assert_called_once_with(
            "amqp://guest:guest@rabbit/"
        )
        self.pika.BlockingConnection.assert_called_once_with(
            ("url-parameters", "amqp://guest:guest@rabbit/")
        )
        self.channel.exchange_declare.assert_called_once_with(
            exchange="purgatory", exchange_type="fanout"
        )
        self.channel.basic_publish.assert_called_once_with(
            exchange="purgatory",
            routing_key="",
            body=json.dumps(
                {"path": "/some/path/", "headers": {"Host": "example.com"}}
            ),
        )
        self.connection.close.assert_called_once_with()

    @override_settings(ULTRACACHE={"rabbitmq-url": "amqp://guest:guest@rabbit/"})
    def test_amqp_error_triggers_retry(self):
        """A connection failure must be retried with backoff instead of
        failing outright."""
        self.pika.BlockingConnection.side_effect = self.pika.exceptions.AMQPError(
            "connection refused"
        )
        task = self.tasks.broadcast_purge
        with mock.patch.object(
            task, "retry", side_effect=Retry("retrying")
        ) as mocked_retry:
            with self.assertRaises(Retry):
                task("/some/path/")
        mocked_retry.assert_called_once()
        kwargs = mocked_retry.call_args.kwargs
        self.assertIsInstance(kwargs["exc"], self.pika.exceptions.AMQPError)
        self.assertGreater(kwargs["countdown"], 0)
