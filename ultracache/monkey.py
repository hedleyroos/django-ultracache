"""Monkey patch Model.__getattribute__ so we can recognize which objects are
covered within a containing caching template tag or view."""

from django.db.models import Model
from django.contrib.contenttypes.models import ContentType

from ultracache import _thread_locals


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
