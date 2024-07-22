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
        return f"{self.user.username} - {self.text[:20]}"