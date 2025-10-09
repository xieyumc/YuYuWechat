# Create your models here.
from django.db import models
from django.utils import timezone


class WeChatConfig(models.Model):
    path = models.CharField(max_length=255, verbose_name="WeChat Path",
                            default="C:/Program Files/Tencent/WeChat/WeChat.exe")
    locale = models.CharField(max_length=10, verbose_name="Locale", default="zh-CN")

    def __str__(self):
        return f"WeChat Path: {self.path} (Locale: {self.locale})"


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
