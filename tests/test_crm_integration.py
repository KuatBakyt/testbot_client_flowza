"""Real HTTP and Django DB test, enabled with FLOWZA_CRM_PATH.

Uses a disposable SQLite database; never connects to the user's database.
"""
import copy
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from wsgiref.simple_server import make_server, WSGIRequestHandler

from flowza_bot.config import Config
from flowza_bot.crm import CRM
from flowza_bot.http import HTTPError
from .helpers import Harness


@unittest.skipUnless(os.environ.get('FLOWZA_CRM_PATH'), 'Set FLOWZA_CRM_PATH for real Django integration')
class DjangoHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        sys.path[:0]=[os.environ['FLOWZA_CRM_PATH'], str(Path(__file__).resolve().parents[1]/'integrations')]
        os.environ['DJANGO_SETTINGS_MODULE']='flowza_bot_smoke_settings'
        os.environ['DJANGO_SECRET_KEY']='test-only-bot-smoke-secret-12345678901234567890'
        # Importing CRM settings must not consume a developer's DATABASE_URL.
        os.environ['DATABASE_URL']='sqlite:///:memory:'
        from config import test_settings
        settings=types.ModuleType('flowza_bot_smoke_settings')
        for key in dir(test_settings):
            if key.isupper():setattr(settings,key,getattr(test_settings,key))
        settings.DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':cls.temp.name+'/crm.sqlite3'}}
        settings.INSTALLED_APPS=list(settings.INSTALLED_APPS)
        if 'flowza_bot_api' not in settings.INSTALLED_APPS:
            settings.INSTALLED_APPS.append('flowza_bot_api')
        settings.ROOT_URLCONF='flowza_bot_smoke_urls'
        settings.ALLOWED_HOSTS=['localhost','127.0.0.1','testserver']
        sys.modules[settings.__name__]=settings
        import django;django.setup()
        from django.urls import path,include
        urls=types.ModuleType('flowza_bot_smoke_urls')
        urls.urlpatterns=[path('api/v1/bot/',include('flowza_bot_api.urls')),path('',include('config.urls'))]
        sys.modules[urls.__name__]=urls
        from django.core.management import call_command
        call_command('migrate',verbosity=0)
        call_command('makemigrations','flowza_bot_api',check=True,dry_run=True,verbosity=0)
        call_command('spectacular',file=cls.temp.name+'/schema.yaml',validate=True,fail_on_warn=True,verbosity=0)
        from django.core.wsgi import get_wsgi_application
        class QuietHandler(WSGIRequestHandler):
            def log_message(self,*args):pass
        cls.server=make_server('127.0.0.1',0,get_wsgi_application(),handler_class=QuietHandler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}/api/v1/'
        from accounts.models import User
        from crm.models import MasterProfile,Specialization,MasterSpecialization
        cls.user=User.objects.create_user(phone='+77000000000',password='test-password-only')
        cls.master=MasterProfile.objects.create(user=cls.user,full_name='Test master',city='Алматы',districts=['Бостандыкский'])
        cls.spec=Specialization.objects.create(name='Сантехника')
        MasterSpecialization.objects.create(master=cls.master,specialization=cls.spec)
        cls.other=User.objects.create_user(phone='+77000000001',password='test-password-only')
        cls.other_master=MasterProfile.objects.create(user=cls.other,full_name='Other master')
        cls.admin=User.objects.create_superuser('+77000000009','test-password-only')

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.thread.join();cls.server.server_close()
        from django.db import connections
        connections.close_all();cls.temp.cleanup()

    def client(self,phone='+77000000000'):
        return CRM(Config('not-used',self.base,phone,'test-password-only'))

    def payload(self):
        start=datetime.now(timezone.utc)+timedelta(days=3)
        return {'request_id':str(uuid4()),'client':{'name':'Әлия','phone':'+77012345678','external_id':'telegram:123:'+str(uuid4())},
                'order':{'specialization':str(self.spec.pk),'title':'Сантехника','description':'Кран ағып тұр',
                         'address':'Алматы, Абая 10','district':'Бостандыкский',
                         'start_at':start.isoformat(),'end_at':(start+timedelta(hours=1)).isoformat()}}

    def test_real_http_submission_replay_and_client_reuse(self):
        from crm.models import Order,Client,Notification
        crm=self.client();crm.check_endpoint();self.assertEqual(crm.catalog()['master_id'],str(self.master.pk))
        body=self.payload();first=crm.submit(body);second=crm.submit(body)
        self.assertEqual(first['id'],second['id']);self.assertFalse(first['replayed']);self.assertTrue(second['replayed'])
        order=Order.objects.get(pk=first['id'])
        self.assertEqual(order.master_id,self.master.pk);self.assertEqual(order.source,'BOT');self.assertEqual(order.status,'NEW')
        self.assertEqual(order.client.name,'Әлия');self.assertEqual(order.description,'Кран ағып тұр')
        self.assertEqual(order.status_history.count(),1)
        self.assertEqual(Notification.objects.filter(payload__order_id=first['id']).count(),1)
        new=copy.deepcopy(body);new['request_id']=str(uuid4());third=crm.submit(new)
        self.assertNotEqual(first['id'],third['id']);self.assertEqual(first['client_id'],third['client_id'])
        changed=copy.deepcopy(body);changed['order']['address']='Another address'
        with self.assertRaises(HTTPError) as error:crm.submit(changed)
        self.assertEqual(error.exception.status,409)
        self.assertEqual(error.exception.data['code'],'idempotency_conflict')
        # Account-scoped idempotency cannot disclose another master's receipt.
        with self.assertRaises(HTTPError):self.client('+77000000001').submit(body)

    def test_rollback_bad_fields_and_access(self):
        from crm.models import Client,Order,ScheduleBlock,Specialization
        crm=self.client()
        clients,orders=Client.objects.count(),Order.objects.count()
        for field,value in [('master',str(self.other_master.pk)),('status','CONFIRMED'),('source','MANUAL')]:
            body=self.payload();body['order'][field]=value
            with self.assertRaises(HTTPError) as e:crm.submit(body)
            self.assertEqual(e.exception.status,400)
        body=self.payload();body['order']['start_at']=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        with self.assertRaises(HTTPError) as e:crm.submit(body)
        self.assertEqual(e.exception.status,400)
        body=self.payload();body['order']['specialization']=str(Specialization.objects.create(name=str(uuid4())).pk)
        with self.assertRaises(HTTPError) as e:crm.submit(body)
        self.assertEqual(e.exception.status,400)
        self.assertEqual(Client.objects.count(),clients);self.assertEqual(Order.objects.count(),orders)
        body=self.payload();block=ScheduleBlock.objects.create(master=self.master,type='MANUAL',
            start_at=body['order']['start_at'],end_at=body['order']['end_at'])
        try:
            with self.assertRaises(HTTPError) as e:crm.submit(body)
            self.assertEqual(e.exception.status,409)
        finally:block.delete()
        with self.assertRaises(HTTPError) as e:self.client('+77000000009').submit(self.payload())
        self.assertEqual(e.exception.status,403)
        from flowza_bot.http import json_request
        with self.assertRaises(HTTPError) as e:json_request('POST',self.base+'bot/orders/',self.payload())
        self.assertEqual(e.exception.status,401)

    def test_full_dialogue_uses_real_crm(self):
        from crm.models import Order
        h=Harness(self.client())
        try:
            from flowza_bot.dialogue import Dialogue
            h.engine=Dialogue(h.store,h.crm,h.config,123)
            h.text('/start');h.click('lang:kk');h.click('continue');h.text('Әлия');h.text('+77012345678')
            h.click('service:0');h.text('Кран ағып тұр');h.text('Алматы, Абая 10');h.click('district:0')
            local=(datetime.now(timezone.utc)+timedelta(days=5)).astimezone(h.engine.zone)
            h.text(local.strftime('%d.%m.%Y %H:%M'));h.click('duration:1');h.click('send')
            self.assertEqual(h.state()['step'],'done')
            order=Order.objects.get(pk=h.state()['result']['id'])
            self.assertEqual(order.master_id,self.master.pk);self.assertEqual(order.description,'Кран ағып тұр')
        finally:h.close()
