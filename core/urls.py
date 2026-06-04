from django.urls import path, re_path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.index, name='index'),
    path('calendar/<slug:club_slug>/', views.gantt_day, name='gantt_day'),
    path('calendar/<slug:club_slug>/<int:year>/<int:month>/<int:day>/', views.gantt_day, name='gantt_day_date'),
    path('api/booking/create/', views.create_booking, name='create_booking'),
    path('api/booking/<int:booking_id>/edit/', views.edit_booking, name='edit_booking'),
    path('api/booking/<int:booking_id>/reschedule/', views.reschedule_booking, name='reschedule_booking'),
    path('api/booking/<int:booking_id>/reschedule-options/', views.reschedule_options, name='reschedule_options'),
    path('api/booking/<int:booking_id>/update/', views.update_booking, name='update_booking'),
    path('api/booking/<int:booking_id>/confirm/', views.confirm_booking, name='confirm_booking'),
    path('api/booking/<int:booking_id>/reject/', views.reject_booking, name='reject_booking'),
    path('api/booking/<int:booking_id>/watch/', views.toggle_watch, name='toggle_watch'),
    path('search/<slug:club_slug>/', views.availability_search, name='availability_search'),
    path('settings/<slug:club_slug>/', views.club_settings, name='club_settings'),
    path('manage/<slug:club_slug>/', views.manage_bookings, name='manage_bookings'),
    path('manage/<slug:club_slug>/blockouts/', views.manage_blockouts, name='manage_blockouts'),
    path('manage/<slug:club_slug>/members/', views.manage_members, name='manage_members'),
    path('manage/<slug:club_slug>/members/<int:member_id>/', views.manage_member_detail, name='manage_member_detail'),
    path('manage/<slug:club_slug>/aircraft/', views.manage_aircraft, name='manage_aircraft'),
    path('manage/<slug:club_slug>/instructors/', views.manage_instructors, name='manage_instructors'),
    path('profile/<slug:club_slug>/', views.my_profile, name='my_profile'),
    path('api/blockout/create/', views.create_blockout, name='create_blockout'),
]
