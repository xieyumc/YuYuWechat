from django.test import TestCase
from django.urls import reverse
from .models import WechatUser, Message, ServerConfig, ScheduledMessage
from django.utils import timezone
import json
from unittest.mock import patch
from django.test import Client
from io import BytesIO

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

    @patch('requests.post')
    def test_send_message_view(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {'status': 'Message sent to user1'}

        response = self.client.post(reverse('send_message'), {
            'username': 'user1',
            'text': 'Hello'
        })
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "Message sent to user1"}')

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

    def test_start_celery_view(self):
        response = self.client.post(reverse('start_celery'))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "Celery started"}')

    def test_stop_celery_view(self):
        response = self.client.post(reverse('stop_celery'))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "Celery stopped"}')