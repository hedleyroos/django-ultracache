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


class CheckPurgerTestCase(SimpleTestCase):
    """Issue 5: a misconfigured purger dotted path must fail fast at
    startup with ImproperlyConfigured instead of raising ImportError inside
    the first post_save."""

    def test_good_dotted_path_passes(self):
        from ultracache.apps import check_purger

        with override_settings(
            ULTRACACHE={"purge": {"method": "ultracache.tests.utils.dummy_purger"}}
        ):
            check_purger()

    def test_no_purger_configured_passes(self):
        from ultracache.apps import check_purger

        with override_settings(ULTRACACHE={}):
            check_purger()

    def test_bad_dotted_path_raises_improperly_configured(self):
        from ultracache.apps import check_purger

        with override_settings(
            ULTRACACHE={"purge": {"method": "no.such.module.purger"}}
        ):
            with self.assertRaises(ImproperlyConfigured) as cm:
                check_purger()
        # The original ImportError is chained for debugging
        self.assertIsInstance(cm.exception.__cause__, ImportError)

    def test_ready_validates_purger(self):
        from django.apps import apps as django_apps

        with override_settings(
            ULTRACACHE={"purge": {"method": "no.such.module.purger"}}
        ):
            with self.assertRaises(ImproperlyConfigured):
                django_apps.get_app_config("ultracache").ready()
