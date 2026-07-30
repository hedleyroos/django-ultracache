"""Monkey patch template variable resolution so we can recognize which objects
are covered within a containing caching template tag. The patch is based on
Django 1.11 but is backwards compatible with 1.9."""

import hashlib
import inspect
import pickle
import types
from collections import OrderedDict

from django.core.cache import cache
from django.db.models import Model, Manager
from django.template.base import Variable, VariableDoesNotExist
from django.template.context import BaseContext
from django.contrib.contenttypes.models import ContentType
from django.conf import settings

from ultracache import _thread_locals
from ultracache.utils import cache_meta, get_current_site_pk

try:
    from django.template.base import logger
except ImportError:
    logger = None


def my_resolve_lookup(self, context):
    """
    Performs resolution of a real variable (i.e. not a literal) against the
    given context.

    As indicated by the method's name, this method is an implementation
    detail and shouldn"t be called by external code. Use Variable.resolve()
    instead.
    """
    current = context
    try:  # catch-all for silent variable failures
        for bit in self.lookups:
            try:  # dictionary lookup
                current = current[bit]
                # ValueError/IndexError are for numpy.array lookup on
                # numpy < 1.9 and 1.9+ respectively
            except (TypeError, AttributeError, KeyError, ValueError, IndexError):
                try:  # attribute lookup
                    # Don"t return class attributes if the class is the context:
                    if isinstance(current, BaseContext) and getattr(type(current), bit):
                        raise AttributeError
                    current = getattr(current, bit)
                except (TypeError, AttributeError) as e:
                    # Reraise an AttributeError raised by a @property
                    if (
                        isinstance(e, AttributeError)
                        and not isinstance(current, BaseContext)
                        and bit in dir(current)
                    ):
                        raise
                    try:  # list-index lookup
                        current = current[int(bit)]
                    except (
                        IndexError,  # list index out of range
                        ValueError,  # invalid literal for int()
                        KeyError,  # current is a dict without `int(bit)` key
                        TypeError,
                    ):  # unsubscriptable object
                        raise VariableDoesNotExist(
                            "Failed lookup for key " "[%s] in %r", (bit, current)
                        )  # missing attribute
            if callable(current):
                if getattr(current, "do_not_call_in_templates", False):
                    pass
                elif getattr(current, "alters_data", False):
                    try:
                        current = context.template.engine.string_if_invalid
                    except AttributeError:
                        current = settings.TEMPLATE_STRING_IF_INVALID
                else:
                    try:  # method call (assuming no args required)
                        current = current()
                    except TypeError:
                        try:
                            inspect.getcallargs(current)
                        except TypeError:  # arguments *were* required
                            current = (
                                context.template.engine.string_if_invalid
                            )  # invalid method call
                        else:
                            raise
            elif isinstance(current, Model):
                if ("request" in context) and hasattr(context["request"], "_ultracache"):
                    # get_for_model itself is cached
                    ct = ContentType.objects.get_for_model(current.__class__)
                    context["request"]._ultracache.append((ct.id, current.pk))

    except Exception as e:
        template_name = getattr(context, "template_name", None) or "unknown"
        if logger is not None:
            logger.debug(
                'Exception while resolving variable "%s" in template "%s".',
                bit,
                template_name,
                exc_info=True,
            )

        if getattr(e, "silent_variable_failure", False):
            current = context.template.engine.string_if_invalid
        else:
            raise

    return current


Variable._resolve_lookup = my_resolve_lookup


# Dark magic enables us to record object access. This enables us keep track of
# objects accessed in any Python code and not just in the templates being
# rendered.


import time

profile_stats = {
    'checks': 0.0,
    'marker_set': 0.0,
    'has_pk': 0.0,
    'get_for_model': 0.0,
    'append': 0.0,
    'marker_del': 0.0,
}

def my__getattribute__(self, name):
    t0 = time.perf_counter()
    has_rec = hasattr(_thread_locals, "ultracache_recorder")
    has_mark = hasattr(_thread_locals, "_ultracache_attr_marker")
    t1 = time.perf_counter()
    profile_stats['checks'] += (t1 - t0)

    if has_rec and not has_mark:
        # The marker acts as a reentrancy guard to prevent infinite recursion
        # when we evaluate hasattr(self, "pk") below.
        setattr(_thread_locals, "_ultracache_attr_marker", 1)
        t2 = time.perf_counter()
        profile_stats['marker_set'] += (t2 - t1)

        has_p = hasattr(self, "pk")
        t3 = time.perf_counter()
        profile_stats['has_pk'] += (t3 - t2)

        if has_p:
            # get_for_model itself is cached
            ct = ContentType.objects.get_for_model(self.__class__)
            t4 = time.perf_counter()
            profile_stats['get_for_model'] += (t4 - t3)

            _thread_locals.ultracache_recorder.append((ct.id, self.pk))
            t5 = time.perf_counter()
            profile_stats['append'] += (t5 - t4)
        else:
            t5 = time.perf_counter()

        delattr(_thread_locals, "_ultracache_attr_marker")
        t6 = time.perf_counter()
        profile_stats['marker_del'] += (t6 - t5)

    return super(Model, self).__getattribute__(name)


Model.__getattribute__ = my__getattribute__
