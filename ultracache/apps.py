from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


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
        from ultracache import signals
        import ultracache.monkey
