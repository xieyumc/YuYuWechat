import datetime
from croniter import croniter
from django.core.management.base import BaseCommand
from client_app.models import ScheduledMessage, MessageCheck


class Command(BaseCommand):
    help = "根据 ScheduledMessage 生成 MessageCheck，时间为第二天的 15:00"

    def handle(self, *args, **kwargs):
        # 获取所有的 ScheduledMessage 对象
        scheduled_messages = ScheduledMessage.objects.all()
        created_count = 0  # 记录成功创建的 MessageCheck 数量

        # 遍历每一个 ScheduledMessage 对象
        for scheduled_message in scheduled_messages:
            # 获取当前 ScheduledMessage 的 cron 表达式
            cron_expression = scheduled_message.cron_expression

            # 获取当前时间作为基准时间
            base_time = datetime.datetime.now()

            # 使用 croniter 解析 ScheduledMessage 的 cron 表达式，并获取下一个执行时间
            cron_iter = croniter(cron_expression, base_time)
            next_execution_time = cron_iter.get_next(datetime.datetime)

            # 将下一个执行时间推迟一天，并设置为 15:00
            day_after_next_execution = next_execution_time + datetime.timedelta(days=1)
            day_after_next_execution = day_after_next_execution.replace(hour=15, minute=0, second=0, microsecond=0)

            # 根据计算后的时间生成新的 cron 表达式，设置为第二天 15:00 执行
            # 这里 cron 表达式的格式为： 分 小时 天 * *
            cron_expression_day_after = f"0 15 {day_after_next_execution.day} * *"

            # 创建一个新的 MessageCheck 实例，设置相关的字段
            MessageCheck.objects.create(
                is_active=scheduled_message.is_active,  # 保持与 ScheduledMessage 一致的激活状态
                user=scheduled_message.user,  # 关联的用户与 ScheduledMessage 相同
                keyword="",  # keyword 留空
                cron_expression=cron_expression_day_after,  # 设置为第二天 15:00 的 cron 表达式
                message_count=1,  # 仅检查一条消息
                report_on_found=False  # 默认不报告找到的关键词
            )

            # 每创建一个 MessageCheck，就增加计数
            created_count += 1

        # 输出成功创建的 MessageCheck 数量
        self.stdout.write(self.style.SUCCESS(f"成功创建 {created_count} 条 MessageCheck"))