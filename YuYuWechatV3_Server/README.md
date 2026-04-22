# YuYuWechat V3 Server

`YuYuWechatV3_Server` 是独立于 `YuYuWechatV2_Server` 的 Django 服务端，负责在不改动客户端接口的前提下，把旧的 `/wechat/*` HTTP API 旁路到 `pywechat/pyweixin`，从而适配微信 `4.1.6+`。

## 设计目标

- 不修改 `/Users/mona/PycharmProjects/YuYuWechat/pywechat`
- 保持 `YuYuWechatV2_Client` 可直接复用
- 保持旧接口路径、请求字段、响应 JSON 结构不变
- 所有微信操作继续串行执行，避免多请求同时抢占 UI

## 运行前提

- Windows 10/11
- Python 3.10+
- 微信 `4.1.6+`
- 微信界面语言为简体中文
- 建议在微信登录前先启用讲述人/无障碍服务，保证 UI Automation 可见

## 安装

```bash
cd /Users/mona/PycharmProjects/YuYuWechat/YuYuWechatV3_Server
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 0.0.0.0:8000
```

## 配置

在 Django Admin 中维护 `WeChatConfig`：

- `path`: `Weixin.exe` 路径
- `locale`: 固定为 `zh-CN`
- `search_pages`: 搜索页数
- `send_delay`: 发送延迟
- `is_maximize`: 是否全屏
- `window_size`: 窗口大小字符串，如 `1000,1000`
- `auto_start_wechat`: 未启动时是否自动拉起微信

## 接口

- `GET /wechat/ping/`
- `POST /wechat/send_message/`
- `POST /wechat/send_file/`
- `POST /wechat/check_wechat_status/`
- `POST /wechat/get_dialogs/`
- `POST /wechat/get_dialogs_by_time_blocks/`
- `GET /wechat/request_logs/`

Swagger:

- `/api/schema/swagger-ui/`
- `/api/schema/redoc/`

## 结构说明

- `wechat_app/`: Django API、模型、admin、测试
- `wechat_bridge/`: V3 旁路适配层，只负责调用 `pyweixin`

更新 `pywechat` 时，不需要改 V3 服务端结构；保持同仓 sibling 目录即可。
