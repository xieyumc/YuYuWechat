from django.contrib import admin
from .models import Message, WechatUser, ServerConfig, ScheduledMessage,Log

admin.site.register(WechatUser)
admin.site.register(Message)
admin.site.register(ServerConfig)
admin.site.register(ScheduledMessage)
class LogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'function_name', 'result', 'return_data')  # 在列表页显示字段
    readonly_fields = ('timestamp', 'function_name', 'result', 'return_data')  # 设置字段为只读
    ordering = ('-timestamp',)  # 按照 timestamp 字段倒序排列记录

admin.site.register(Log, LogAdmin)