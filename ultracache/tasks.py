import json
from urllib.parse import urlparse
from urllib.parse import quote

from celery import shared_task

try:
    import pika

    DO_TASK = True
except ImportError:
    DO_TASK = False

from django.conf import settings


@shared_task(bind=True, max_retries=3, ignore_result=True)
def broadcast_purge(self, path, headers=None):
    if not DO_TASK:
        raise RuntimeError("Library pika>=1.0 not found")

    try:
        url = settings.ULTRACACHE["rabbitmq-url"]
    except (AttributeError, KeyError):
        # Use same host as celery. Pika requires the path to be url
        # encoded. A typical broker URL setting remains effectively
        # unchanged but as soon as sub-paths are encountered this encoding
        # becomes necessary.
        parsed = urlparse(settings.CELERY_BROKER_URL)
        url = "%s://%s/%s" % (parsed.scheme, parsed.netloc, quote(parsed.path[1:], safe=""))
    # NOTE: a new BlockingConnection is set up and torn down on every
    # invocation. That is simple and robust but inefficient at high purge
    # throughput; if it proves to matter in practice, reuse a module-level
    # connection with reconnect-on-failure.
    try:
        connection = pika.BlockingConnection(pika.URLParameters(url))
        channel = connection.channel()
        channel.exchange_declare(exchange="purgatory", exchange_type="fanout")
        channel.basic_publish(
            exchange="purgatory",
            routing_key="",
            body=json.dumps({"path": path, "headers": headers or {}}),
        )
        connection.close()
    except pika.exceptions.AMQPError as exc:
        # Exponential backoff: 5s, 10s, 20s
        raise self.retry(exc=exc, countdown=5 * 2 ** self.request.retries)
    return True
