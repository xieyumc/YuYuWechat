# Create your models here.
from django.db import models


class WeChatConfig(models.Model):
    path = models.CharField(max_length=255, verbose_name="WeChat Path",
                            default="C:/Program Files/Tencent/WeChat/WeChat.exe")
    locale = models.CharField(max_length=10, verbose_name="Locale", default="zh-CN")

    def __str__(self):
        return f"WeChat Path: {self.path} (Locale: {self.locale})"
