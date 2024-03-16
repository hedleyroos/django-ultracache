try:
    from django.urls import include, re_path
except ImportError:
    from django.conf.urls import include, re_path


from ultracache.tests import views


urlpatterns = [
    re_path(r"^render-view/$", views.RenderView.as_view(), name="render-view"),
    re_path(r"^method-cached-view/$", views.MethodCachedView.as_view(), name="method-cached-view"),
    re_path(r"^class-cached-view/$", views.ClassCachedView.as_view(), name="class-cached-view"),
    re_path(r"^cached-header-view/$", views.CachedHeaderView.as_view(), name="cached-header-view"),
    re_path(
        r"^bustable-cached-view/$", views.BustableCachedView.as_view(), name="bustable-cached-view"
    ),
    re_path(
        r"^non-bustable-cached-view/$",
        views.NonBustableCachedView.as_view(),
        name="non-bustable-cached-view",
    ),
]
