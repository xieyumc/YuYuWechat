from django.test import TestCase
from django.urls import reverse
from .models import WechatUser, Message, ServerConfig, ScheduledMessage, Log
from django.utils import timezone
import json
from unittest.mock import patch
from django.test import Client
from io import BytesIO
import subprocess

class WechatUserModelTests(TestCase):
    def setUp(self):
        WechatUser.objects.create(username='user1', wechatid='wx1')
        WechatUser.objects.create(username='user2')

    def test_wechat_user_creation(self):
        user1 = WechatUser.objects.get(username='user1')
        user2 = WechatUser.objects.get(username='user2')
        self.assertEqual(user1.wechatid, 'wx1')
        self.assertIsNone(user2.wechatid)

    def test_wechat_user_str(self):
        user1 = WechatUser.objects.get(username='user1')
        self.assertEqual(str(user1), 'user1')


class MessageModelTests(TestCase):
    def setUp(self):
        user = WechatUser.objects.create(username='user1')
        Message.objects.create(user=user, text='Hello World')

    def test_message_creation(self):
        message = Message.objects.get(text='Hello World')
        self.assertEqual(message.text, 'Hello World')

    def test_message_str(self):
        message = Message.objects.get(text='Hello World')
        self.assertEqual(str(message), 'user1')

    def test_message_group_property(self):
        user = WechatUser.objects.create(username='user2', group='group1')
        message = Message.objects.create(user=user, text='Group Message')
        self.assertEqual(message.group, 'group1')


class ServerConfigModelTests(TestCase):
    def setUp(self):
        ServerConfig.objects.create(server_ip='192.168.1.1')

    def test_server_config_creation(self):
        config = ServerConfig.objects.get(server_ip='192.168.1.1')
        self.assertEqual(str(config), 'Server IP: 192.168.1.1')


class ScheduledMessageModelTests(TestCase):
    def setUp(self):
        user = WechatUser.objects.create(username='user1')
        ScheduledMessage.objects.create(user=user, text='Scheduled Message', cron_expression='* * * * *')

    def test_scheduled_message_creation(self):
        scheduled_message = ScheduledMessage.objects.get(text='Scheduled Message')
        self.assertEqual(scheduled_message.text, 'Scheduled Message')

    def test_scheduled_message_str(self):
        scheduled_message = ScheduledMessage.objects.get(text='Scheduled Message')
        self.assertEqual(str(scheduled_message), 'user1 - Scheduled Message')

    def test_scheduled_message_group_property(self):
        user = WechatUser.objects.create(username='user2', group='group1')
        scheduled_message = ScheduledMessage.objects.create(user=user, text='Group Scheduled Message',
                                                            cron_expression='* * * * *')
        self.assertEqual(scheduled_message.group, 'group1')


class ViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = WechatUser.objects.create(username='user1')
        self.server_config = ServerConfig.objects.create(server_ip='127.0.0.1')

    def test_home_view(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'home.html')

    def test_get_server_ip_view(self):
        response = self.client.get(reverse('get_server_ip'))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"server_ip": "127.0.0.1"}')

    def test_set_server_ip_view(self):
        response = self.client.post(reverse('set_server_ip'), json.dumps({'server_ip': '192.168.0.1'}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "Server IP set to 192.168.0.1"}')
        self.assertEqual(ServerConfig.objects.latest('id').server_ip, '192.168.0.1')

    def test_send_message_management_view(self):
        response = self.client.get(reverse('send_message_management'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'send_message_management.html')

    def test_schedule_management_view(self):
        response = self.client.get(reverse('schedule_management'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'schedule_management.html')

    @patch('requests.post')
    def test_send_message_view(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {'status': 'Message sent to user1'}

        response = self.client.post(reverse('send_message'), {
            'username': 'user1',
            'text': 'Hello'
        })
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "Hello sent to user1"}')

    @patch('requests.post')
    def test_skip_execution_view(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {'status': 'Message sent to user1'}

        scheduled_message = ScheduledMessage.objects.create(user=self.user, text='Scheduled Message',
                                                            cron_expression='* * * * *')
        response = self.client.post(reverse('skip_execution'), {
            'task_id': scheduled_message.id
        })
        self.assertEqual(response.status_code, 200)
        scheduled_message.refresh_from_db()
        self.assertEqual(scheduled_message.execution_skip, 1)

    def test_export_import_database_views(self):
        # 测试导出数据库
        response = self.client.post(reverse('export_database'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Disposition'].startswith('attachment'))

        # 获取导出的内容
        export_content = response.content

        # 使用导出的内容作为导入测试的输入
        import_file = BytesIO(export_content)
        import_file.name = 'db_backup.json'  # 设置一个文件名，有些处理可能依赖文件名

        # 测试导入数据库
        response = self.client.post(reverse('import_database'), {'db_file': import_file})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'Database imported successfully.')

    @patch('subprocess.Popen')
    def test_start_celery_view(self, mock_popen):
        response = self.client.post(reverse('start_celery'))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "Celery started"}')
        self.assertTrue(mock_popen.called)

    @patch('subprocess.call')
    def test_stop_celery_view(self, mock_call):
        response = self.client.post(reverse('stop_celery'))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "Celery stopped"}')
        self.assertTrue(mock_call.called)

    @patch('subprocess.run')
    def test_check_celery_running_view(self, mock_run):
        mock_run.return_value.stdout = b'celery process'
        response = self.client.get(reverse('check_celery_running'))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "Celery is running"}')

        mock_run.return_value.stdout = b''
        response = self.client.get(reverse('check_celery_running'))
        self.assertEqual(response.status_code, 404)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "Celery is not running"}')

    @patch('requests.post')
    def test_check_wechat_status_view(self, mock_post):
        mock_post.return_value.status_code = 200

        response = self.client.post(reverse('check_wechat_status'))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "success", "message": "WeChat status checked successfully"}')

        mock_post.return_value.status_code = 500

        response = self.client.post(reverse('check_wechat_status'))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "failure", "message": "微信不在线"}')

    def test_log_view(self):
        response = self.client.get(reverse('log_view'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'log.html')

    def test_log_counts_view(self):
        Log.objects.create(result=True, function_name='test_func', return_data='{}')
        Log.objects.create(result=False, function_name='test_func', return_data='{}')

        response = self.client.get(reverse('log_counts'))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"total": 2, "success": 1, "failure": 1}')

    def test_clear_logs_view(self):
        Log.objects.create(result=True, function_name='test_func', return_data='{}')
        response = self.client.post(reverse('clear_logs'))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "success"}')
        self.assertEqual(Log.objects.count(), 0)