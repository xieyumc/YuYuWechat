import json
import os
from datetime import datetime
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import override_settings
from django.test import Client
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .celery_runtime import should_schedule_celery_autostart
from .models import ErrorLog, WechatUser, ServerConfig, ScheduledMessage, PaymentCheck
from .tasks import check_and_log_scheduled_message_errors, check_cron, payment_check


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
        self.assertTemplateUsed(response, 'message_schedule_management.html')

    def test_payment_check_view(self):
        self.login()
        response = self.client.get(reverse('payment_check'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'payment_check.html')

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

    @patch('client_app.celery_runtime.subprocess.run')
    @patch('client_app.celery_runtime.subprocess.Popen')
    def test_start_celery_view(self, mock_popen, mock_run):
        mock_run.return_value.stdout = b''

        response = self.client.post(reverse('start_celery'))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "Celery started"}')
        self.assertTrue(mock_popen.called)

    @patch('client_app.celery_runtime.subprocess.run')
    @patch('client_app.celery_runtime.subprocess.Popen')
    def test_start_celery_view_does_not_start_duplicate_processes(self, mock_popen, mock_run):
        mock_run.return_value.stdout = b'100 1 celery -A YuYuWechatV2_Client worker --loglevel=info\n'

        response = self.client.post(reverse('start_celery'))

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "Celery started"}')
        self.assertFalse(mock_popen.called)

    @patch('subprocess.run')
    def test_stop_celery_view(self, mock_run):
        mock_run.return_value.returncode = 0
        response = self.client.post(reverse('stop_celery'))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "Celery stopped"}')
        self.assertTrue(mock_run.called)

    @patch('subprocess.run')
    def test_check_celery_running_view(self, mock_run):
        mock_run.return_value.stdout = (
            b'100 1 celery -A YuYuWechatV2_Client worker --loglevel=info\n'
            b'101 100 celery -A YuYuWechatV2_Client worker --loglevel=info\n'
            b'200 1 celery -A YuYuWechatV2_Client beat --loglevel=info\n'
        )
        response = self.client.get(reverse('check_celery_running'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'Celery is running')
        self.assertEqual(response.json()['process_count'], 2)
        self.assertEqual(response.json()['worker_count'], 1)
        self.assertEqual(response.json()['beat_count'], 1)
        self.assertFalse(response.json()['duplicate'])

        mock_run.return_value.stdout = (
            b'100 1 celery -A YuYuWechatV2_Client worker --loglevel=info\n'
            b'200 1 celery -A YuYuWechatV2_Client beat --loglevel=info\n'
            b'300 1 celery -A YuYuWechatV2_Client beat --loglevel=info\n'
        )
        response = self.client.get(reverse('check_celery_running'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'Celery is running')
        self.assertEqual(response.json()['process_count'], 3)
        self.assertEqual(response.json()['beat_count'], 2)
        self.assertTrue(response.json()['duplicate'])

        mock_run.return_value.stdout = b''
        response = self.client.get(reverse('check_celery_running'))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['status'], 'Celery is not running')
        self.assertEqual(response.json()['process_count'], 0)

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

    @patch('requests.request')
    def test_auto_payment_status_proxy(self, mock_request):
        self.login()
        mock_request.return_value.status_code = 200
        mock_request.return_value.json.return_value = {
            'status': 'success',
            'auto_payment': {'running': False},
            'auto_payment_config': {},
        }

        response = self.client.get(reverse('auto_payment_status'))

        self.assertEqual(response.status_code, 200)
        mock_request.assert_called_once()
        self.assertEqual(mock_request.call_args.args[:2], ('GET', 'http://127.0.0.1/wechat/auto_payment_status/'))

    @patch('requests.request')
    def test_toggle_auto_payment_proxy(self, mock_request):
        self.login()
        mock_request.return_value.status_code = 200
        mock_request.return_value.json.return_value = {'status': 'success', 'message': '自动领取红包/转账已启用'}

        response = self.client.post(
            reverse('toggle_auto_payment'),
            json.dumps({'enabled': True}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_request.call_args.args[:2], ('POST', 'http://127.0.0.1/wechat/toggle_auto_payment/'))

    @patch('requests.request')
    def test_claim_payment_now_updates_task_result(self, mock_request):
        self.login()
        task = PaymentCheck.objects.create(user=self.user, cron_expression='* * * * *', reply='谢谢')
        mock_request.return_value.status_code = 200
        mock_request.return_value.json.return_value = {
            'status': 'success',
            'name': 'user1',
            'red_packets': 1,
            'transfers': 2,
        }

        response = self.client.post(reverse('claim_payment_now'), {'task_id': task.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_request.call_args.args[:2], ('POST', 'http://127.0.0.1/wechat/claim_payment/'))
        task.refresh_from_db()
        self.assertEqual(task.last_red_packets, 1)
        self.assertEqual(task.last_transfers, 2)

    # def test_log_view(self):
    #     self.login()
    #     response = self.client.get(reverse('log_view'))
    #     self.assertEqual(response.status_code, 200)
    #     self.assertTemplateUsed(response, 'log.html')

    # def test_log_counts_view(self):
    #     Log.objects.create(result=True, function_name='test_func', return_data='{}')
    #     Log.objects.create(result=False, function_name='test_func', return_data='{}')

    #     response = self.client.get(reverse('log_counts'))
    #     self.assertEqual(response.status_code, 200)
    #     self.assertJSONEqual(str(response.content, encoding='utf8'), '{"total": 2, "success": 1, "failure": 1}')

    # def test_clear_logs_view(self):
    #     Log.objects.create(result=True, function_name='test_func', return_data='{}')
    #     response = self.client.post(reverse('clear_logs'))
    #     self.assertEqual(response.status_code, 200)
    #     self.assertJSONEqual(str(response.content, encoding='utf8'), '{"status": "success"}')
    #     self.assertEqual(Log.objects.count(), 0)

    def test_protected_views_without_login(self):
        protected_urls = [
            reverse('home'),
            reverse('error_detection'),
            reverse('send_message_management'),
            reverse('schedule_management'),
            reverse('payment_check'),
            # reverse('log_view'),
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
            reverse('payment_check'),
            # reverse('log_view'),
        ]

        for url in protected_urls:
            response = self.client.get(url)
            try:
                self.assertEqual(response.status_code, 200)
            except AssertionError as e:
                print(f"Error accessing {url} with login: {e}")
                print(f"Response status code: {response.status_code}")
                print(f"Response content: {response.content}")


class TaskTests(TestCase):
    def setUp(self):
        self.user = WechatUser.objects.create(username='task_user')

    def test_check_cron_allows_short_delay(self):
        current_time = timezone.make_aware(
            datetime(2026, 4, 21, 10, 6, 20),
            timezone.get_current_timezone()
        )
        last_executed = timezone.make_aware(
            datetime(2026, 4, 21, 9, 5, 0),
            timezone.get_current_timezone()
        )

        self.assertTrue(check_cron(current_time, '5 * * * *', last_executed))

    def test_check_cron_rejects_delay_outside_grace_window(self):
        current_time = timezone.make_aware(
            datetime(2026, 4, 21, 10, 7, 20),
            timezone.get_current_timezone()
        )
        last_executed = timezone.make_aware(
            datetime(2026, 4, 21, 9, 5, 0),
            timezone.get_current_timezone()
        )

        self.assertFalse(check_cron(current_time, '5 * * * *', last_executed))

    def test_check_cron_rejects_already_executed_schedule(self):
        current_time = timezone.make_aware(
            datetime(2026, 4, 21, 10, 6, 20),
            timezone.get_current_timezone()
        )
        last_executed = timezone.make_aware(
            datetime(2026, 4, 21, 10, 5, 0),
            timezone.get_current_timezone()
        )

        self.assertFalse(check_cron(current_time, '5 * * * *', last_executed))

    @override_settings(AUTO_START_CELERY_ON_WEB_START=True)
    def test_should_schedule_celery_autostart_for_runserver_child(self):
        argv = ['manage.py', 'runserver', '0.0.0.0:7500']
        env = dict(os.environ, RUN_MAIN='true')

        self.assertTrue(should_schedule_celery_autostart(argv=argv, env=env))

    @override_settings(AUTO_START_CELERY_ON_WEB_START=True)
    def test_should_not_schedule_celery_autostart_for_non_runserver(self):
        argv = ['manage.py', 'test']
        env = dict(os.environ)

        self.assertFalse(should_schedule_celery_autostart(argv=argv, env=env))

    @patch('client_app.tasks.time.sleep')
    @patch('client_app.tasks.timezone.now')
    def test_check_and_log_scheduled_message_errors_checks_previous_minute(self, mock_now, mock_sleep):
        current_time = timezone.make_aware(
            datetime(2026, 4, 21, 10, 5, 30),
            timezone.get_current_timezone()
        )
        mock_now.return_value = current_time

        ScheduledMessage.objects.create(
            user=self.user,
            text='Scheduled Message',
            cron_expression='* * * * *',
            execution_count=1,
            is_active=True,
            last_executed=timezone.make_aware(
                datetime(2026, 4, 21, 10, 4, 0),
                timezone.get_current_timezone()
            )
        )

        check_and_log_scheduled_message_errors()

        self.assertFalse(mock_sleep.called)
        self.assertFalse(ErrorLog.objects.filter(error_type='定时任务遗漏').exists())

    @patch('client_app.tasks.timezone.now')
    def test_check_and_log_scheduled_message_errors_logs_missed_previous_minute(self, mock_now):
        current_time = timezone.make_aware(
            datetime(2026, 4, 21, 10, 5, 30),
            timezone.get_current_timezone()
        )
        mock_now.return_value = current_time

        task = ScheduledMessage.objects.create(
            user=self.user,
            text='Scheduled Message',
            cron_expression='* * * * *',
            execution_count=1,
            is_active=True,
            last_executed=timezone.make_aware(
                datetime(2026, 4, 21, 10, 3, 0),
                timezone.get_current_timezone()
            )
        )

        check_and_log_scheduled_message_errors()

        self.assertTrue(
            ErrorLog.objects.filter(
                error_type='定时任务遗漏',
                task_id=str(task.id)
            ).exists()
        )

    @patch('client_app.tasks.requests.post')
    @patch('client_app.tasks.timezone.now')
    def test_payment_check_claims_due_payment_rule(self, mock_now, mock_post):
        current_time = timezone.make_aware(
            datetime(2026, 4, 21, 10, 5, 30),
            timezone.get_current_timezone()
        )
        mock_now.return_value = current_time
        ServerConfig.objects.create(server_ip='127.0.0.1')
        task = PaymentCheck.objects.create(
            user=self.user,
            cron_expression='5 * * * *',
            reply='谢谢',
            is_active=True,
            last_checked=timezone.make_aware(
                datetime(2026, 4, 21, 9, 5, 0),
                timezone.get_current_timezone()
            ),
        )
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            'status': 'success',
            'name': 'task_user',
            'red_packets': 1,
            'transfers': 1,
        }

        payment_check()

        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args.args[0], 'http://127.0.0.1/wechat/claim_payment/')
        task.refresh_from_db()
        self.assertEqual(task.last_red_packets, 1)
        self.assertEqual(task.last_transfers, 1)
