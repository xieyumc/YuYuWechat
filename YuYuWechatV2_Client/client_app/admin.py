from django.contrib import admin
from .models import Message, WechatUser, ServerConfig, ScheduledMessage, Log, EmailSettings, ErrorLog,MessageCheck


class WechatUserAdmin(admin.ModelAdmin):
    list_display = ('username', 'wechatid', 'date_added', 'group')  # 在列表页显示字段
    search_fields = ('username', 'wechatid')  # 支持按用户名和微信ID搜索
    list_filter = ('group',)  # 按分组过滤
    ordering = ('-date_added',)  # 按照 date_added 字段倒序排列记录


class MessageAdmin(admin.ModelAdmin):
    list_display = ('user', 'text', 'group')  # 在列表页显示字段
    search_fields = ('text', 'user__username')  # 支持按消息内容和用户名搜索
    list_filter = ('user__group',)  # 按用户分组过滤
    ordering = ('-user',)  # 按照 user 字段倒序排列记录


class ServerConfigAdmin(admin.ModelAdmin):
    list_display = ('server_ip',)  # 在列表页显示字段
    search_fields = ('server_ip',)  # 支持按服务器IP搜索
    ordering = ('server_ip',)  # 按照 server_ip 字段排序


class ScheduledMessageAdmin(admin.ModelAdmin):
    list_display = ('user', 'text', 'cron_expression', 'execution_count', 'last_executed', 'is_active')  # 在列表页显示字段
    search_fields = ('text', 'user__username')  # 支持按消息内容和用户名搜索
    list_filter = ('is_active', 'user__group')  # 按是否激活和用户分组过滤
    ordering = ('-last_executed',)  # 按照 last_executed 字段倒序排列记录


class EmailSettingsAdmin(admin.ModelAdmin):
    list_display = ('email_host', 'email_port', 'email_security', 'email_host_user', 'default_from_email')  # 在列表页显示字段
    search_fields = ('email_host_user', 'default_from_email')  # 支持按用户邮箱和发件人邮箱搜索
    ordering = ('email_host',)  # 按照 email_host 字段排序


class ErrorLogAdmin(admin.ModelAdmin):
    list_display = ('error_type', 'error_detail', 'timestamp', 'emailed', 'task_id')  # 在列表页显示字段
    search_fields = ('error_type', 'error_detail', 'task_id')  # 支持按错误类型、错误详情和任务ID搜索
    list_filter = ('emailed',)  # 按错误是否已发送过滤
    ordering = ('-timestamp',)  # 按照 timestamp 字段倒序排列记录


class LogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'function_name', 'result', 'return_data')  # 在列表页显示字段
    ordering = ('-timestamp',)  # 按照 timestamp 字段倒序排列记录
    search_fields = ('function_name', 'result')  # 支持按函数名和结果搜索

class MessageCheckAdmin(admin.ModelAdmin):
    list_display = ('user', 'keyword', 'cron_expression', 'message_count', 'report_on_found', 'is_active')  # 在列表页显示字段
    search_fields = ('keyword', 'user__username')  # 支持按关键词和用户名搜索
    list_filter = ('is_active', 'user__group')  # 按是否激活和用户分组过滤
    ordering = ('user',)  # 按照 execution_count 字段倒序排列记录

admin.site.register(Log, LogAdmin)
admin.site.register(WechatUser, WechatUserAdmin)
admin.site.register(Message, MessageAdmin)
admin.site.register(ServerConfig, ServerConfigAdmin)
admin.site.register(ScheduledMessage, ScheduledMessageAdmin)
admin.site.register(EmailSettings, EmailSettingsAdmin)
admin.site.register(ErrorLog, ErrorLogAdmin)
admin.site.register(MessageCheck, MessageCheckAdmin)