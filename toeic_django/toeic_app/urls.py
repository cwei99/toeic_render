from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('api/question/', views.get_question, name='get_question'),
]
