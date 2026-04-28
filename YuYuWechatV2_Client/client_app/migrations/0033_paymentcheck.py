from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("client_app", "0032_delete_log"),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentCheck",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_active", models.BooleanField(default=True, help_text="检查规则是否激活")),
                ("cron_expression", models.CharField(help_text="用于定时检查红包/转账的 cron 表达式", max_length=255)),
                (
                    "reply",
                    models.TextField(blank=True, default="", help_text="领取红包后的可选回复。留空时使用服务端默认自动回复配置。"),
                ),
                ("last_checked", models.DateTimeField(blank=True, help_text="上次检查时间", null=True)),
                ("last_red_packets", models.IntegerField(default=0, help_text="上次领取红包数量")),
                ("last_transfers", models.IntegerField(default=0, help_text="上次收取转账数量")),
                ("last_result", models.TextField(blank=True, default="", help_text="上次检查结果")),
                (
                    "user",
                    models.ForeignKey(
                        help_text="关联的微信用户",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payment_checks",
                        to="client_app.wechatuser",
                    ),
                ),
            ],
        ),
    ]
