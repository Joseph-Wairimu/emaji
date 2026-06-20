from django.urls import path
from . import views_card_terminal as v

urlpatterns = [
    path('ServerTime', v.server_time, name='ct-server-time'),
    path('Water/ConsumTransactions', v.consum_transactions, name='ct-consum-transactions'),
    path('Water/OffLines', v.offline_transactions, name='ct-offline-transactions'),
    path('Water/WhiteList', v.whitelist, name='ct-whitelist'),
]
