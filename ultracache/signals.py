from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import request_finished, setting_changed
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import Model
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from django.utils.module_loading import import_string

from ultracache import clear_recorder
from ultracache.utils import KEY_PREFIX, get_cache


# The purger and invalidate settings are resolved lazily and memoized so
# override_settings (and any runtime reconfiguration) is respected. The memo
# is reset by the setting_changed receiver below.
_settings_memo = {}


def _get_purger():
    if "purger" not in _settings_memo:
        try:
            method = settings.ULTRACACHE["purge"]["method"]
        except (AttributeError, KeyError):
            _settings_memo["purger"] = None
            return None
        try:
            _settings_memo["purger"] = import_string(method)
        except ImportError as error:
            # check_purger() in apps.py validates the configured purger at
            # startup; this can still trigger when the setting is changed
            # at runtime (eg. override_settings). Raise without memoizing
            # so a corrected setting recovers.
            raise ImproperlyConfigured(
                "ULTRACACHE['purge']['method'] = %r cannot be imported"
                % method
            ) from error
    return _settings_memo["purger"]


def _get_invalidate():
    if "invalidate" not in _settings_memo:
        try:
            _settings_memo["invalidate"] = settings.ULTRACACHE["invalidate"]
        except (AttributeError, KeyError):
            _settings_memo["invalidate"] = True
    return _settings_memo["invalidate"]


@receiver(setting_changed, dispatch_uid="ultracache.setting_changed")
def on_setting_changed(sender, setting, **kwargs):
    if setting == "ULTRACACHE":
        _settings_memo.clear()


@receiver(request_finished, dispatch_uid="ultracache.request_finished")
def on_request_finished(sender, **kwargs):
    """Safety net for deployments that do not install UltraCacheMiddleware:
    without cleanup a lazily created recorder would leak between requests
    served by the same worker thread under WSGI."""
    clear_recorder()


def _resolve_content_type(sender, kwargs):
    """Shared handler guards. Return the content type to invalidate for, or
    None if this signal must be ignored."""
    if not _get_invalidate():
        return None
    if kwargs.get("raw", False):
        return None
    if sender is MigrationRecorder.Migration:
        return None
    if not issubclass(sender, Model):
        return None
    if not isinstance(kwargs["instance"], Model):
        return None
    # get_for_model itself is cached
    try:
        return ContentType.objects.get_for_model(sender)
    except RuntimeError:
        # This happens when ultracache is being used by another product
        # during a test run.
        return None


def _invalidate(keys_key, purge_key):
    """Expire the cache entries registered under keys_key and purge the
    reverse caching proxy paths registered under purge_key."""
    cache = get_cache()

    # Expire cache keys. Registry values are {"expires": ..., "items": [...]}
    # payloads (see utils.cache_meta).
    payload = cache.get(keys_key)
    to_delete = payload["items"] if payload else []
    if to_delete:
        try:
            cache.delete_many(to_delete)
        except NotImplementedError:
            for k in to_delete:
                cache.delete(k)
    cache.delete(keys_key)

    # Purge paths in the reverse caching proxy
    purger = _get_purger()
    if purger is not None:
        # The key *must* be deleted first in case the purger fails
        payload = cache.get(purge_key)
        cache.delete(purge_key)
        for li in (payload["items"] if payload else []):
            purger(li[0], li[1])
    else:
        cache.delete(purge_key)


@receiver(post_save)
def on_post_save(sender, **kwargs):
    """Expire ultracache cache keys affected by this object"""
    ct = _resolve_content_type(sender, kwargs)
    if ct is None:
        return
    if kwargs.get("created", False):
        # A new object of this content type expires the cache entries and
        # paths that contain objects of the content type.
        _invalidate(
            "%sct-%s" % (KEY_PREFIX, ct.id),
            "%sct-pth-%s" % (KEY_PREFIX, ct.id),
        )
    else:
        obj = kwargs["instance"]
        _invalidate(
            "%s%s-%s" % (KEY_PREFIX, ct.id, obj.pk),
            "%spth-%s-%s" % (KEY_PREFIX, ct.id, obj.pk),
        )


@receiver(post_delete)
def on_post_delete(sender, **kwargs):
    """Expire ultracache cache keys affected by this object"""
    ct = _resolve_content_type(sender, kwargs)
    if ct is None:
        return
    obj = kwargs["instance"]
    _invalidate(
        "%s%s-%s" % (KEY_PREFIX, ct.id, obj.pk),
        "%spth-%s-%s" % (KEY_PREFIX, ct.id, obj.pk),
    )
