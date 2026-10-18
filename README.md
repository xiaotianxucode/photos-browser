# 拾光 · 照片浏览器

基于 Python、Flask、SQLite 和原生 HTML/CSS/JavaScript 的本机网页相册。管理员上传照片后，可以按拍摄时间和地点查询，并通过大图窗口连续浏览。

## 功能

- 首次创建唯一管理员，支持登录、退出及后台照片管理；访客无需登录即可浏览相册。
- 批量上传 JPEG、PNG、WebP，每张最多 20 MB，每批最多 50 张；逐张显示处理结果。
- 自动提取 EXIF 拍摄时间和 GPS，保留原图，生成方向纠正后的展示图及缩略图。
- 根据 GPS 尝试解析地点，也可手动补充拍摄时间和地点。
- 支持地点关键词、起止日期组合查询，以及拍摄时间升序或降序排列。
- 每页显示 12 张照片；大图支持左右按钮、方向键跨页翻看，Escape 关闭。
- 支持下载原图、修改照片信息和删除照片。

## 环境与安装

需要 Python 3.9 或更高版本。以下命令适用于 Windows PowerShell：

```powershell
git clone https://github.com/xiaotianxucode/photos-browser.git
cd photos-browser
python -m venv .venv
.\.venv\Scripts\python.exe -X utf8 -m pip install -r requirements.txt
```

主要依赖：Flask 提供网站服务，Pillow 处理图片。`requirements.txt` 还保留了 pytest 依赖；当前精简版不包含测试脚本。

## 启动与使用

安装完成后，双击 `start.bat`，或运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 app.py
```

- 前台相册：http://127.0.0.1:5000/
- 管理后台：http://127.0.0.1:5000/admin

首次访问后台时创建管理员：用户名为 2–40 个字符，密码为 8–128 个字符。没有预设账号密码，创建后不再开放第二个账号注册。前台展示同一相册中的所有照片，不按上传账号划分。

启动后保持控制台窗口开启，按 Ctrl+C 停止服务。程序默认监听 `127.0.0.1`，用于本机演示；当前启动方式不是公网生产部署配置。

## 项目结构

```text
photos-browser/
├── app.py                 后端：认证、上传、查询、图片处理和数据库操作
├── start.bat              Windows 启动脚本，优先使用 .venv 中的 Python
├── requirements.txt       Python 依赖及版本范围
├── README.md              安装、使用和项目结构说明
├── .gitignore             排除本机环境、账号照片数据及缓存
├── templates/
│   ├── base.html          公共导航、页脚及资源引用
│   ├── index.html         前台相册与后台照片管理页面
│   ├── auth.html          管理员创建和登录表单
│   └── message.html       错误提示页面
├── static/
│   ├── style.css          样式、布局及手机适配
│   └── app.js             上传、筛选、分页、大图和管理交互
├── instance/              首次运行自动创建，不上传到 GitHub
│   ├── photos.sqlite3     账号、密码哈希、照片信息和地点缓存
│   ├── secret.key         登录会话签名密钥
│   ├── originals/         原始照片，保留原文件内容及 EXIF
│   ├── images/            纠正方向后的 JPEG 展示图
│   └── thumbs/            照片列表缩略图
└── .venv/                 本机创建的 Python 环境，不上传到 GitHub
```

## 数据与备份

SQLite 中的 `admin` 表保存唯一管理员及密码哈希，`photos` 表保存照片信息和文件名，`geocache` 表缓存坐标对应的地点。图片内容保存在 `instance/` 下的三个照片目录中，不直接存入数据库。

备份时先停止服务，再复制整个 `instance/`；恢复时同样先停止服务。不要只复制图片而遗漏数据库。`secret.key` 用于会话签名，不是管理员密码。

`.gitignore` 已排除 `instance/`、`.venv/`、缓存和测试产物，因此从 GitHub 下载项目后，需要重新创建环境和管理员，不会包含本机已有的账号或照片。

## 时间与地点

- 优先读取照片 EXIF 中的原始拍摄时间；缺少拍摄时间时显示未知，不使用上传时间代替。
- EXIF 不含时区时按照片记录的当地时间处理。未知拍摄时间在升序和降序列表中均排在最后。
- GPS 地点解析使用 OpenStreetMap 的 Nominatim 服务：会发送坐标，不发送照片图像。网络失败时仍保存照片，可手动填写地点或在后台重试解析。
- 截图或社交软件转存图片可能没有 EXIF；当前不支持 HEIC，可先转换为 JPEG，注意转换可能丢失元数据。

地点服务使用政策：[Nominatim Usage Policy](https://operations.osmfoundation.org/policies/nominatim/)。

## 可选配置

关闭联网地点解析，仍提取照片中的 GPS：

```powershell
$env:PHOTO_GEOCODING = "0"
.\.venv\Scripts\python.exe -X utf8 app.py
```

更换端口：

```powershell
$env:PORT = "5001"
.\.venv\Scripts\python.exe -X utf8 app.py
```

此时访问 `http://127.0.0.1:5001/`。`start.bat` 的提示地址固定为默认 5000 端口，实际监听端口以服务启动输出为准。

## 常见问题

- **提示缺少依赖**：使用 `.venv` 中的 Python 执行上面的依赖安装命令。
- **启动后无法访问**：查看启动窗口中的报错，确认服务仍在运行，且访问地址与实际端口一致。
- **照片没有时间或地点**：确认原图包含 EXIF；可在后台手动补充。
- **忘记密码**：当前没有密码找回功能，请妥善保存管理员账号；不要删除整个 `instance/`，以免丢失照片和记录。
