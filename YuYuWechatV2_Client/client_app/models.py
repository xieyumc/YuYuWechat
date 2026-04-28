from django.db import models


class WechatUser(models.Model):
    username = models.CharField(max_length=255, unique=True, help_text="微信用户的用户名")
    wechatid = models.CharField(max_length=255, unique=True, blank=True, null=True, help_text="微信用户的微信ID")
    date_added = models.DateTimeField(blank=True, null=True, help_text="用户添加的日期")
    group = models.CharField(max_length=255, blank=True, null=True, help_text="用户分组")

    def __str__(self):
        return self.username


class Message(models.Model):
    user = models.ForeignKey(WechatUser, on_delete=models.CASCADE, help_text="关联的微信用户")
    text = models.TextField(help_text="发送消息")

    @property
    def group(self):
        return self.user.group

    def __str__(self):
        return self.user.username


class ServerConfig(models.Model):
    server_ip = models.CharField(max_length=255, unique=True, help_text="服务器的IP地址")

    def __str__(self):
        return f"Server IP: {self.server_ip}"


class BackupSettings(models.Model):
    retention_days = models.PositiveIntegerField(default=30, help_text="备份文件保留天数")
    updated_at = models.DateTimeField(auto_now=True, help_text="配置更新时间")

    def __str__(self):
        return f"Backup retention: {self.retention_days} days"


class ScheduledMessage(models.Model):
    is_active = models.BooleanField(default=True, help_text="是否激活该定时消息")
    user = models.ForeignKey(WechatUser, on_delete=models.CASCADE, help_text="关联的微信用户")
    text = models.TextField(help_text="定时发送的消息内容")
    cron_expression = models.CharField(max_length=255, help_text="定时任务的 cron 表达式")
    execution_count = models.IntegerField(default=0, help_text="任务执行的次数")
    execution_skip = models.IntegerField(default=0, help_text="任务被跳过的次数")
    last_executed = models.DateTimeField(null=True, blank=True, help_text="任务上次执行的时间")

    @property
    def group(self):
        return self.user.group

    def __str__(self):
        return f"{self.user.username} - {self.text[:30]}"


class ScheduledFileMessage(models.Model):
    is_active = models.BooleanField(default=True, help_text="是否激活该定时文件消息")
    user = models.ForeignKey(WechatUser, on_delete=models.CASCADE, help_text="关联的微信用户")
    file_path = models.CharField(max_length=1024, help_text="定时发送的文件路径，不需要带引号")
    cron_expression = models.CharField(max_length=255, help_text="定时任务的 cron 表达式")
    execution_count = models.IntegerField(default=0, help_text="任务执行的次数")
    execution_skip = models.IntegerField(default=0, help_text="任务被跳过的次数")
    last_executed = models.DateTimeField(null=True, blank=True, help_text="任务上次执行的时间")

    @property
    def group(self):
        return self.user.group

    def __str__(self):
        return f"{self.user.username} - {self.file_path}"


class MessageCheck(models.Model):
    """
    定期检测微信好友聊天记录的模型
    """
    is_active = models.BooleanField(default=True, help_text="检测规则是否激活")
    user = models.ForeignKey(WechatUser, on_delete=models.CASCADE, related_name="message_checks",
                             help_text="关联的微信用户")
    keyword = models.CharField(max_length=255, help_text="检测的关键词/正则表达式")
    cron_expression = models.CharField(max_length=255, help_text="用于定时检测的 cron 表达式")
    message_count = models.IntegerField(default=10, help_text="要检测的最近消息数目或时间分组的数量")
    use_time_blocks = models.BooleanField(default=False,
                                          help_text="如果为 True，则 message_count 代表时间分组的个数；如果为 False，则 message_count 代表消息条数")
    report_on_found = models.BooleanField(default=True,
                                          help_text="如果为 True，则在检测到关键词时报错；否则在未检测到时报错")
    last_checked = models.DateTimeField(null=True, blank=True, help_text="上次检测的时间")

    @property
    def group(self):
        return self.user.group

    def __str__(self):
        if self.use_time_blocks:
            message_detail = f"{self.message_count} 个时间分组"
        else:
            message_detail = f"{self.message_count} 条消息"

        report_condition = "关键词检测到" if self.report_on_found else "关键词未检测到"
        return f"检测 {self.user.username} 的 {message_detail}, {report_condition}: {self.keyword}"


class PaymentCheck(models.Model):
    """
    定期检查并领取指定好友的红包/转账。
    """
    is_active = models.BooleanField(default=True, help_text="检查规则是否激活")
    user = models.ForeignKey(
        WechatUser,
        on_delete=models.CASCADE,
        related_name="payment_checks",
        help_text="关联的微信用户",
    )
    cron_expression = models.CharField(max_length=255, help_text="用于定时检查红包/转账的 cron 表达式")
    reply = models.TextField(
        blank=True,
        default="",
        help_text="领取红包后的可选回复。留空时使用服务端默认自动回复配置。",
    )
    last_checked = models.DateTimeField(null=True, blank=True, help_text="上次检查时间")
    last_red_packets = models.IntegerField(default=0, help_text="上次领取红包数量")
    last_transfers = models.IntegerField(default=0, help_text="上次收取转账数量")
    last_result = models.TextField(blank=True, default="", help_text="上次检查结果")

    @property
    def group(self):
        return self.user.group

    def __str__(self):
        return f"检查 {self.user.username} 的红包/转账: {self.cron_expression}"


class EmailSettings(models.Model):
    SECURITY_CHOICES = [
        ('tls', 'TLS'),
        ('ssl', 'SSL'),
    ]

    email_host = models.CharField(max_length=255, default='smtp.163.com', help_text="邮箱主机地址")
    email_port = models.IntegerField(default=25, help_text="邮箱主机端口")
    email_security = models.CharField(max_length=3, choices=SECURITY_CHOICES, default='tls', help_text="安全协议选择")
    email_host_user = models.CharField(max_length=255, help_text="邮箱用户名")
    email_host_password = models.CharField(max_length=255, help_text="邮箱密码（这里一般是授权码，请自行申请）")
    default_from_email = models.EmailField(help_text="发送邮件的邮箱，一般也是email_host_user")
    recipient_list = models.TextField(help_text="收件人邮箱列表，用逗号分隔")

    def __str__(self):
        return self.default_from_email


class ErrorLog(models.Model):
    error_type = models.CharField(max_length=255, help_text="错误的类型")
    error_detail = models.TextField(help_text="错误的详细描述")
    timestamp = models.DateTimeField(auto_now_add=True, help_text="错误日志创建的时间")
    emailed = models.BooleanField(default=False, help_text="错误是否已通过邮件发送")
    task_id = models.CharField(max_length=255, null=True, blank=True, help_text="相关任务的 ID")

    def __str__(self):
        return f"{self.error_type} - {self.error_detail}"


class CustomScript(models.Model):
    SLOT_CHOICES = (
        (1, '脚本 1'),
        (2, '脚本 2'),
        (3, '脚本 3'),
    )
    slot = models.PositiveSmallIntegerField(choices=SLOT_CHOICES, unique=True)
    code = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"脚本槽位 {self.slot}"


class TaskLog(models.Model):
    task_name = models.CharField(max_length=255, help_text="任务的名称")
    timestamp = models.DateTimeField(auto_now_add=True, help_text="任务执行的时间")
    status = models.CharField(max_length=50, help_text="任务执行的状态 (e.g., 'success', 'failure')")
    details = models.TextField(blank=True, null=True, help_text="任务执行的详细信息或输出")

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.task_name} - {self.status} at {self.timestamp}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # 保持最多1000条日志
        if TaskLog.objects.count() > 1000:
            oldest_log = TaskLog.objects.earliest('timestamp')
            oldest_log.delete()
