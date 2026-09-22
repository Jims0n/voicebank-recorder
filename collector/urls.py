from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("consent/", views.consent, name="consent"),
    path("about-you/", views.profile, name="profile"),
    path("record/", views.record, name="record"),
    path("done/", views.done, name="done"),
    path("more/", views.next_batch, name="next_batch"),
    path("withdraw/", views.withdraw, name="withdraw"),
    path("api/next/", views.api_next, name="api_next"),
    path("api/skip/", views.api_skip, name="api_skip"),
    path("api/upload/", views.api_upload, name="api_upload"),
]
