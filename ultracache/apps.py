from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def check_purger():
    """Resolve the configured purger once at startup so a mistyped dotted
    path fails the deployment immediately instead of raising inside the
    first model save."""
    try:
        method = settings.ULTRACACHE["purge"]["method"]
    except (AttributeError, KeyError):
        return
    try:
        import_string(method)
    except ImportError as error:
        raise ImproperlyConfigured(
            "ULTRACACHE['purge']['method'] = %r cannot be imported" % method
        ) from error


def check_request_context_processor():
    """Ensure the request context processor is active."""
    try:
        context_processors = settings.TEMPLATES[0]["OPTIONS"]["context_processors"]
    except (AttributeError, IndexError, KeyError):
        context_processors = []
    if "django.template.context_processors.request" not in context_processors:
        raise ImproperlyConfigured(
            "django.template.context_processors.request is required"
        )


class UltracacheAppConfig(AppConfig):
    name = "ultracache"
    verbose_name = "Ultracache"

    def ready(self):
        check_request_context_processor()
        check_purger()
        from ultracache import signals
        import ultracache.monkey
