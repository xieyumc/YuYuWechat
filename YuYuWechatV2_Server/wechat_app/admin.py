from django.contrib import admin

# Register your models here.
from .models import WeChatConfig, RequestLog

@admin.register(WeChatConfig)
class WeChatConfigAdmin(admin.ModelAdmin):
    list_display = ("path", "locale")


@admin.register(RequestLog)
class RequestLogAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "action", "endpoint", "status", "duration_ms")
    list_filter = ("status", "action",)
    search_fields = ("action", "endpoint", "error")
    readonly_fields = ("created_at", "started_at", "finished_at", "duration_ms")
