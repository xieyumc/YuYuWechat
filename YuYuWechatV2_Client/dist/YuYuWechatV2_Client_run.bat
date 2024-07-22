@echo off
REM 获取当前目录
set current_dir=%~dp0

REM 运行可执行文件并添加参数
"%current_dir%YuYuWechatV2_Client.exe" runserver 0.0.0.0:7500