from django import template

from django.utils.encoding import force_str
from django.utils.functional import Promise
from django.template import TemplateSyntaxError
from django.templatetags.cache import CacheNode
from django.template.base import VariableDoesNotExist
from django.core.cache.utils import make_template_fragment_key
from django.conf import settings

from ultracache import get_or_create_recorder
from ultracache.utils import cache_meta, get_cache, get_current_site_pk


register = template.Library()


class UltraCacheNode(CacheNode):
    """Based on Django's default cache template tag. Add SITE_ID as implicit
    vary on parameter is sites product is installed. Allow unresolvable
    variables. Allow translated strings."""

    def __init__(self, *args):
        # Using different caches makes invalidation difficult. cache_name will
        # be supported in a future version.
        super().__init__(*args, cache_name=None)

    def render(self, context):
        try:
            expire_time = self.expire_time_var.resolve(context)
        except VariableDoesNotExist:
            raise TemplateSyntaxError(
                "ultracache tag got an unknown variable: %r" % self.expire_time_var.var
            )
        try:
            expire_time = int(expire_time)
        except (ValueError, TypeError):
            raise TemplateSyntaxError(
                "ultracache tag got a non-integer timeout value: %r" % expire_time
            )

        request = context["request"]

        # If request not GET or HEAD never cache
        if request.method.lower() not in ("get", "head"):
            return self.nodelist.render(context)

        # Lazily create the recorder for this context. Django's template
        # rendering is recursive and runs in a single context so an
        # insertion-ordered recorder is enough to keep track of contained
        # objects.
        recorder = get_or_create_recorder()
        start_index = len(recorder)

        vary_on = []
        if "django.contrib.sites" in settings.INSTALLED_APPS:
            vary_on.append(str(get_current_site_pk(request)))

        for var in self.vary_on:
            try:
                r = var.resolve(context)
            except VariableDoesNotExist:
                # Unresolvable variables contribute a stable placeholder to
                # the cache key.
                r = ""
            if isinstance(r, Promise):
                r = force_str(r)
            vary_on.append(r)

        cache_key = make_template_fragment_key(self.fragment_name, vary_on)
        # Within this block a distinct object only needs to be recorded once,
        # but an object recorded before the block started must be recorded
        # again so it lands in this block's slice of the recorder.
        recorder.push_barrier(start_index)
        try:
            cache = get_cache()
            value = cache.get(cache_key)
            if value is None:
                value = self.nodelist.render(context)
                cache.set(cache_key, value, expire_time)
                cache_meta(
                    recorder,
                    cache_key,
                    start_index,
                    request=request,
                    timeout=expire_time,
                )
            else:
                # A cached result was found. Replay the recorded tuples so
                # outer template tags are aware of contained objects.
                for tu in cache.get(cache_key + "-objs", []):
                    recorder.append(tu)
        finally:
            recorder.pop_barrier(start_index)

        return value


@register.tag("ultracache")
def do_ultracache(parser, token):
    """Based on Django's default cache template tag"""
    nodelist = parser.parse(("endultracache",))
    parser.delete_first_token()
    tokens = token.split_contents()
    if len(tokens) < 3:
        raise TemplateSyntaxError("'%s' tag requires at least 2 arguments." % tokens[0])
    return UltraCacheNode(
        nodelist,
        parser.compile_filter(tokens[1]),
        tokens[2],  # fragment_name can"t be a variable.
        [parser.compile_filter(token) for token in tokens[3:]],
    )
