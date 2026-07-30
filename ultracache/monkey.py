"""Monkey patch Model.__getattribute__ so we can recognize which objects are
covered within a containing caching template tag or view."""

from django.contrib.contenttypes.models import ContentType
from django.db.models import Model

from ultracache import _recorder


# Dark magic enables us to record object access. This enables us keep track of
# objects accessed in any Python code and not just in the templates being
# rendered.

def my__getattribute__(self, name):
    recorder = _recorder.get()
    # ContentType instances are excluded from recording: resolving the
    # content type id of an instance itself touches ContentType instance
    # attributes, so recording them would recurse without end. Their rows
    # are effectively immutable at runtime, so nothing is lost. The class
    # check uses type() and issubclass() because isinstance() reads
    # self.__class__ and would re-enter this function.
    if recorder is not None and not issubclass(cls := type(self), ContentType):
        # Read the pk directly off the instance, bypassing __getattribute__,
        # so recording an access cannot re-enter this function. An unsaved
        # instance records a pk of None. ``_meta.pk`` is None for abstract
        # models, which cannot be recorded.
        pk_field = object.__getattribute__(self, "_meta").pk
        if pk_field is not None:
            pk = object.__getattribute__(self, "__dict__").get(pk_field.attname)
            # get_for_model itself is cached
            ct = ContentType.objects.get_for_model(cls)
            recorder.append((ct.id, pk))
    return super(Model, self).__getattribute__(name)


Model.__getattribute__ = my__getattribute__
