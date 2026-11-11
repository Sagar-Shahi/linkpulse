from django.urls import path

from . import views

app_name = 'links'

urlpatterns = [
    path('r/<slug:code>/', views.redirect_view, name='redirect'),
    path('app/', views.dashboard, name='dashboard'),
    path('app/links/', views.link_list, name='list'),
    path('app/links/new/', views.link_create, name='create'),
    path('app/links/export.csv', views.export_links_csv, name='export'),
    path('app/links/<int:pk>/', views.link_detail, name='detail'),
    path('app/links/<int:pk>/edit/', views.link_edit, name='edit'),
    path('app/links/<int:pk>/delete/', views.link_delete, name='delete'),
    path('app/links/<int:pk>/toggle/', views.link_toggle, name='toggle'),
]
