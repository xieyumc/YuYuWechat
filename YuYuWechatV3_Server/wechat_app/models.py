from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

class WeChatConfig(models.Model):
    path = models.CharField(
        max_length=255,
        verbose_name="WeChat Path",
        default="C:/Program Files/Tencent/Weixin/Weixin.exe",
        help_text="微信 4.x 可执行文件路径，通常为 Weixin.exe。",
    )
    locale = models.CharField(max_length=10, verbose_name="Locale", default="zh-CN")
    search_pages = models.PositiveIntegerField(default=5, help_text="会话列表搜索页数。")
    send_delay = models.FloatField(default=0.2, help_text="发送消息/文件时的单次延迟（秒）。")
    is_maximize = models.BooleanField(default=False, help_text="微信主窗口是否全屏。")
    window_size = models.CharField(
        max_length=32,
        default="1000,1000",
        help_text='窗口大小，格式为 "宽,高"，例如 "1000,1000"。',
    )
    auto_start_wechat = models.BooleanField(
        default=True,
        help_text="若微信未启动，服务端是否尝试自动拉起 Weixin.exe。",
    )
    auto_thank_after_red_packet = models.BooleanField(
        default=False,
        help_text="领取红包成功后，是否自动发送感谢消息。",
    )
    red_packet_thanks_message = models.TextField(
        default="",
        blank=True,
        help_text='成功领取红包后发送给客户的感谢消息模板。支持 {friend} 或 {name} 占位符。',
    )

    def clean(self):
        if self.locale != "zh-CN":
            raise ValidationError({"locale": 'YuYuWechat V3 当前仅支持 "zh-CN"。'})
        if self.auto_thank_after_red_packet and not self.red_packet_thanks_message.strip():
            raise ValidationError({"red_packet_thanks_message": "启用自动感谢消息时，内容不能为空。"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"WeChat Path: {self.path} (Locale: {self.locale})"

    @classmethod
    def get_solo(cls):
        config = cls.objects.order_by("id").first()
        if config is None:
            config = cls.objects.create()
        return config


class RequestLog(models.Model):
    STATUS_CHOICES = (
        ("received", "received"),
        ("queued", "queued"),
        ("running", "running"),
        ("success", "success"),
        ("failed", "failed"),
    )

    action = models.CharField(max_length=64)
    endpoint = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="received")

    request_data = models.JSONField(null=True, blank=True)
    result_data = models.JSONField(null=True, blank=True)
    response_data = models.JSONField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)

    client_ip = models.CharField(max_length=64, blank=True, default="")

    def mark_running(self):
        self.status = "running"
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at"])

    def mark_finished(self, success: bool, result_data=None, response_data=None, error=None):
        self.finished_at = timezone.now()
        if self.started_at:
            self.duration_ms = int((self.finished_at - self.started_at).total_seconds() * 1000)
        self.status = "success" if success else "failed"
        self.result_data = result_data
        self.response_data = response_data
        self.error = error
        self.save(update_fields=[
            "finished_at", "duration_ms", "status", "result_data", "response_data", "error"
        ])

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.action} [{self.status}]"
