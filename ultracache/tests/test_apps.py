from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from ultracache.apps import check_request_context_processor


class CheckRequestContextProcessorTestCase(SimpleTestCase):

    def test_present_request_context_processor_passes(self):
        # The test settings include the request context processor
        check_request_context_processor()

    @override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {"context_processors": []},
            }
        ]
    )
    def test_missing_request_context_processor_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            check_request_context_processor()

    @override_settings(TEMPLATES=[])
    def test_empty_templates_setting_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            check_request_context_processor()
