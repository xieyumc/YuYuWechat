import datetime

from client_app.models import ScheduledMessage, MessageCheck
from croniter import croniter
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "根据 ScheduledMessage 生成 MessageCheck，时间为第二天的 15:00"

    def handle(self, *args, **kwargs):
        # 获取所有的 ScheduledMessage 对象
        scheduled_messages = ScheduledMessage.objects.all()
        created_count = 0  # 记录成功创建的 MessageCheck 数量

        # 公共的 MessageCheck 默认配置
        default_message_check_config = {
            'keyword': "",
            'message_count': 1,
            'use_time_blocks': False,
            'report_on_found': False
        }

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

            # 将原始的 cron 表达式拆解为分钟、小时、天、月、星期
            cron_parts = cron_expression.split()
            if len(cron_parts) != 5:
                self.stdout.write(self.style.ERROR(f"无效的 cron 表达式: {cron_expression}"))
                continue

            # 生成新的 cron 表达式，分钟和小时为 0 15，天为计算后的一天，月份和星期保持不变
            cron_expression_day_after = f"0 15 {day_after_next_execution.day} {cron_parts[3]} {cron_parts[4]}"

            # 检查是否已经存在相同的 MessageCheck
            existing_check = MessageCheck.objects.filter(
                is_active=scheduled_message.is_active,
                user=scheduled_message.user,
                cron_expression=cron_expression_day_after,
                **default_message_check_config  # 使用默认配置
            ).first()

            if existing_check:
                self.stdout.write(self.style.WARNING(
                    f"已存在相同的 MessageCheck（ScheduledMessage ID: {scheduled_message.id}），跳过创建。"
                ))
                continue

            # 创建一个新的 MessageCheck 实例，设置相关的字段
            MessageCheck.objects.create(
                is_active=scheduled_message.is_active,  # 保持与 ScheduledMessage 一致的激活状态
                user=scheduled_message.user,  # 关联的用户与 ScheduledMessage 相同
                cron_expression=cron_expression_day_after,  # 设置为第二天 15:00 的 cron 表达式
                **default_message_check_config  # 使用默认配置
            )

            # 每创建一个 MessageCheck，就增加计数
            created_count += 1

        # 输出成功创建的 MessageCheck 数量
        self.stdout.write(self.style.SUCCESS(f"成功创建 {created_count} 条 MessageCheck"))