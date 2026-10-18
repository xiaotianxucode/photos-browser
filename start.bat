@echo off
rem 隐藏命令回显，使启动提示更清晰。
chcp 65001 >nul
rem 将控制台切换到 UTF-8，避免中文提示乱码。
cd /d "%~dp0"
rem 切换到启动脚本所在目录，确保从任意位置都能启动。
set "PHOTO_PYTHON=python"
rem 默认使用系统 Python，变量名称避开系统保留名称。
if exist ".venv\Scripts\python.exe" set "PHOTO_PYTHON=.venv\Scripts\python.exe"
rem 优先使用本项目独立虚拟环境。
"%PHOTO_PYTHON%" -c "from importlib.metadata import version; assert int(version('Flask').split('.')[0]) >= 3; assert int(version('Pillow').split('.')[0]) >= 10" >nul 2>&1
rem 检查运行依赖是否存在且版本满足要求。
if errorlevel 1 goto missing
rem 缺少依赖时进入安装说明分支。
echo 拾光照片浏览器：http://127.0.0.1:5000
rem 显示前台访问地址。
echo 管理后台：http://127.0.0.1:5000/admin
rem 显示首次设置及后台上传入口。
echo 保持此窗口开启；按 Ctrl+C 停止服务。
rem 提示用户不要提前关闭服务器窗口。
"%PHOTO_PYTHON%" -X utf8 app.py
rem 在本机启动 Flask 服务器，不开启调试模式。
goto end
rem 服务结束后跳过缺少依赖的提示。
:missing
rem 定义依赖缺失时的处理入口。
echo 请先安装依赖：%PHOTO_PYTHON% -X utf8 -m pip install -r requirements.txt
rem 提供明确的依赖安装命令，不自动修改系统环境。
:end
rem 定义脚本结束入口。
pause
rem 保留窗口，便于查看启动错误或退出信息。
