import logging

import requests

from django.conf import settings


logger = logging.getLogger(__name__)


def broadcast(path, headers=None):
    # The preferred method requires RabbitMQ and celery being installed and
    # configured.
    from ultracache.tasks import broadcast_purge

    broadcast_purge.delay(path, headers)


def _http_purge(path, headers=None, method="PURGE"):
    loc = settings.ULTRACACHE["purge"]["url"].rstrip("/") + "/" + path.lstrip("/")
    try:
        requests.request(method, loc, timeout=1, headers=headers or {})
    except requests.exceptions.RequestException as exc:
        logger.warning(
            "ultracache failed to purge path %s at %s: %s", path, loc, exc
        )


def varnish(path, headers=None):
    # See https://www.varnish-software.com/static/book/Cache_invalidation.html
    _http_purge(path, headers=headers)


def nginx(path, headers=None):
    # See https://github.com/FRiCKLE/ngx_cache_purge

    # Simplest case - one node
    _http_purge(path, headers=headers)
