from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.core.signals import request_finished
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import Model
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from django.utils.module_loading import import_string

from ultracache import clear_recorder

try:
    purger = import_string(settings.ULTRACACHE["purge"]["method"])
except (AttributeError, KeyError):
    purger = None

try:
    invalidate = settings.ULTRACACHE["invalidate"]
except (AttributeError, KeyError):
    invalidate = True


@receiver(request_finished, dispatch_uid="ultracache.request_finished")
def on_request_finished(sender, **kwargs):
    """Safety net for deployments that do not install UltraCacheMiddleware:
    without cleanup a lazily created recorder would leak between requests
    served by the same worker thread under WSGI."""
    clear_recorder()


@receiver(post_save)
def on_post_save(sender, **kwargs):
    """Expire ultracache cache keys affected by this object"""
    if not invalidate:
        return
    if kwargs.get("raw", False):
        return
    if sender is MigrationRecorder.Migration:
        return
    if issubclass(sender, Model):
        obj = kwargs["instance"]
        if isinstance(obj, Model):
            # get_for_model itself is cached
            try:
                ct = ContentType.objects.get_for_model(sender)
            except RuntimeError:
                # This happens when ultracache is being used by another product
                # during a test run.
                return

            if kwargs.get("created", False):
                # Expire cache keys that contain objects of this content type
                key = "ucache-ct-%s" % ct.id
                to_delete = cache.get(key, [])
                if to_delete:
                    try:
                        cache.delete_many(to_delete)
                    except NotImplementedError:
                        for k in to_delete:
                            cache.delete(k)
                cache.delete(key)

                # Purge paths in reverse caching proxy that contain objects of
                # this content type.
                key = "ucache-ct-pth-%s" % ct.id
                if purger is not None:
                    # The key *must* be deleted first in case the purger fails
                    items = cache.get(key, [])
                    cache.delete(key)
                    for li in items:
                        purger(li[0], li[1])
                else:
                    cache.delete(key)

            else:
                # Expire cache keys
                key = "ucache-%s-%s" % (ct.id, obj.pk)
                to_delete = cache.get(key, [])
                if to_delete:
                    try:
                        cache.delete_many(to_delete)
                    except NotImplementedError:
                        for k in to_delete:
                            cache.delete(k)
                cache.delete(key)

                # Purge paths in reverse caching proxy
                key = "ucache-pth-%s-%s" % (ct.id, obj.pk)
                if purger is not None:
                    # The key *must* be deleted first in case the purger fails
                    items = cache.get(key, [])
                    cache.delete(key)
                    for li in items:
                        purger(li[0], li[1])
                else:
                    cache.delete(key)


@receiver(post_delete)
def on_post_delete(sender, **kwargs):
    """Expire ultracache cache keys affected by this object"""
    if not invalidate:
        return
    if kwargs.get("raw", False):
        return
    if sender is MigrationRecorder.Migration:
        return
    if issubclass(sender, Model):
        obj = kwargs["instance"]
        if isinstance(obj, Model):
            # get_for_model itself is cached
            try:
                ct = ContentType.objects.get_for_model(sender)
            except RuntimeError:
                # This happens when ultracache is being used by another product
                # during a test run.
                return

            # Expire cache keys
            key = "ucache-%s-%s" % (ct.id, obj.pk)
            to_delete = cache.get(key, [])
            if to_delete:
                try:
                    cache.delete_many(to_delete)
                except NotImplementedError:
                    for k in to_delete:
                        cache.delete(k)
            cache.delete(key)

            # Invalidate paths in reverse caching proxy
            key = "ucache-pth-%s-%s" % (ct.id, obj.pk)
            if purger is not None:
                # The key *must* be deleted first in case the purger fails
                items = cache.get(key, [])
                cache.delete(key)
                for li in items:
                    purger(li[0], li[1])
            else:
                cache.delete(key)
