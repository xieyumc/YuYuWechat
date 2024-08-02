from django.test import TestCase
from django.urls import reverse
from .models import WechatUser, Message, ServerConfig, ScheduledMessage, Log
from django.utils import timezone
import json
from unittest.mock import patch
from django.test import Client
from io import BytesIO
import subprocess
from django.contrib.auth.models import User


class ViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = WechatUser.objects.create(username='user1')
        self.server_config = ServerConfig.objects.create(server_ip='127.0.0.1')

        # 创建并登录用户
        self.test_user = User.objects.create_user(username='testuser', password='12345')

    def login(self):
        self.client.login(username='testuser', password='12345')

    def test_home_view(self):
        self.login()
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
        self.login()
        response = self.client.get(reverse('send_message_management'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'send_message_management.html')

    def test_schedule_management_view(self):
        self.login()
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
        self.login()
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
        self.assertJSONEqual(str(response.content, encoding='utf8'),
                             '{"status": "success", "message": "WeChat status checked successfully"}')

        mock_post.return_value.status_code = 500

        response = self.client.post(reverse('check_wechat_status'))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "failure", "message": "微信不在线"}')

    def test_log_view(self):
        self.login()
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

    def test_protected_views_without_login(self):
        protected_urls = [
            reverse('home'),
            reverse('error_detection'),
            reverse('send_message_management'),
            reverse('schedule_management'),
            reverse('log_view'),
        ]

        for url in protected_urls:
            response = self.client.get(url)
            try:
                self.assertEqual(response.status_code, 302)
                self.assertIn('/login/', response.url)
            except AssertionError as e:
                print(f"Error accessing {url} without login: {e}")
                print(f"Response status code: {response.status_code}")
                print(f"Response content: {response.content}")

        for url in protected_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/login/', response.url)

    def test_protected_views_with_login(self):
        self.login()
        protected_urls = [
            reverse('home'),
            reverse('error_detection'),
            reverse('send_message_management'),
            reverse('schedule_management'),
            reverse('log_view'),
        ]

        for url in protected_urls:
            response = self.client.get(url)
            try:
                self.assertEqual(response.status_code, 200)
            except AssertionError as e:
                print(f"Error accessing {url} with login: {e}")
                print(f"Response status code: {response.status_code}")
                print(f"Response content: {response.content}")