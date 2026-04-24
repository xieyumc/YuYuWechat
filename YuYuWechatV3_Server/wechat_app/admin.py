from .models import WeChatConfig, RequestLog
from django.contrib import admin

@admin.register(WeChatConfig)
class WeChatConfigAdmin(admin.ModelAdmin):
    list_display = (
        "path",
        "locale",
        "search_pages",
        "send_delay",
        "payment_reply_delay",
        "is_maximize",
        "window_size",
        "auto_start_wechat",
        "auto_thank_after_red_packet",
    )
    list_editable = (
        "search_pages",
        "send_delay",
        "payment_reply_delay",
        "is_maximize",
        "window_size",
        "auto_start_wechat",
        "auto_thank_after_red_packet",
    )


@admin.register(RequestLog)
class RequestLogAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "action", "endpoint", "status", "duration_ms")
    list_filter = ("status", "action",)
    search_fields = ("action", "endpoint", "error")
    readonly_fields = (
        "created_at",
        "started_at",
        "finished_at",
        "duration_ms",
        "request_data",
        "result_data",
        "response_data",
    )
