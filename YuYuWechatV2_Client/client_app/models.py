from django.db import models


class WechatUser(models.Model):
    username = models.CharField(max_length=255, unique=True)
    wechatid = models.CharField(max_length=255, unique=True, blank=True, null=True)
    date_added = models.DateTimeField(blank=True, null=True)
    group = models.CharField(max_length=255, blank=True, null=True)  # 新增分组字段

    def __str__(self):
        return self.username


class Message(models.Model):
    user = models.ForeignKey(WechatUser, on_delete=models.CASCADE)
    text = models.TextField()

    @property
    def group(self):
        return self.user.group

    def __str__(self):
        return self.user.username


class ServerConfig(models.Model):
    server_ip = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return f"Server IP: {self.server_ip}"


class ScheduledMessage(models.Model):
    is_active = models.BooleanField(default=True)
    user = models.ForeignKey(WechatUser, on_delete=models.CASCADE)
    text = models.TextField()
    cron_expression = models.CharField(max_length=255)
    execution_count = models.IntegerField(default=0)
    execution_skip = models.IntegerField(default=0)
    last_executed = models.DateTimeField(null=True, blank=True)

    @property
    def group(self):
        return self.user.group

    def __str__(self):
        return f"{self.user.username} - {self.text[:30]}"


class Log(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)  # 记录日志创建的时间
    result = models.BooleanField()  # 记录函数调用的结果，成功为True，失败为False
    function_name = models.CharField(max_length=255)  # 记录调用的函数名称
    input_params = models.TextField(help_text="JSON string of input parameters", default="null")  # 函数的输入参数
    return_data = models.TextField(default="null")  # 函数的返回数据

    def __str__(self):
        return f"{self.function_name} - {'Success' if self.result else 'Failure'} at {self.timestamp}"


class EmailSettings(models.Model):
    SECURITY_CHOICES = [
        ('tls', 'TLS'),
        ('ssl', 'SSL'),
    ]

    email_host = models.CharField(max_length=255, default='smtp.163.com')
    email_port = models.IntegerField(default=25)
    email_security = models.CharField(max_length=3, choices=SECURITY_CHOICES, default='tls')
    email_host_user = models.CharField(max_length=255)
    email_host_password = models.CharField(max_length=255)
    default_from_email = models.EmailField()
    recipient_list = models.TextField(help_text="Comma-separated list of email addresses")

    def __str__(self):
        return self.default_from_email

class ErrorLog(models.Model):
    error_type = models.CharField(max_length=255)  # 错误类型字符串
    error_detail = models.TextField()  # 详细错误字符串
    timestamp = models.DateTimeField(auto_now_add=True)  # 日志添加时间
    emailed = models.BooleanField(default=False)  # 判断错误是否已经发送邮箱
    def __str__(self):
        return f"{self.error_type}-{self.error_detail}"