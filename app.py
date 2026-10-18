# -*- coding: utf-8 -*-  # 明确指定源码编码，兼容 Windows 下较旧的 Python 解释器。
from datetime import datetime, timedelta  # 解析拍摄时间并计算日期上限。
from functools import wraps  # 保留登录校验装饰器的函数名称。
from io import BytesIO  # 在内存中验证上传的图片。
from pathlib import Path  # 安全组合项目内的文件路径。
import json  # 编解码地点服务返回的数据。
import math  # 检查坐标和数值是否合法。
import os  # 读取运行环境配置。
import secrets  # 生成会话密钥、CSRF 令牌和随机文件名。
import sqlite3  # 使用本地 SQLite 数据库保存相册。
import threading  # 串行限制地点服务的访问频率。
import time  # 计算地点请求的最小间隔。
from urllib.parse import urlencode  # 安全编码地点请求的查询参数。
from urllib.request import Request, urlopen  # 调用在线逆地理编码服务。
from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for  # 提供网页、接口和会话管理。
from PIL import Image, ImageOps, UnidentifiedImageError  # 验证图片、提取 EXIF 并纠正显示方向。
from werkzeug.security import check_password_hash, generate_password_hash  # 以哈希存储并校验管理员密码。
from werkzeug.exceptions import HTTPException  # 将接口错误统一转换为 JSON。

ROOT = Path(__file__).resolve().parent  # 以程序所在目录作为项目根目录。
PHOTO_LIMIT = 20 * 1024 * 1024  # 限制每张照片最多 20 MB。
GEOCODE_LOCK = threading.Lock()  # 在进程内为地点查询建立互斥锁。
LAST_GEOCODE = 0.0  # 记录最近一次地点请求的开始时间。
SCHEMA = [  # 按顺序定义初次启动时需要建立的数据表。
    'CREATE TABLE IF NOT EXISTS admin (id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL, password_hash TEXT NOT NULL)',  # 保存唯一管理员及密码哈希。
    'CREATE TABLE IF NOT EXISTS photos (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, original TEXT NOT NULL, image TEXT NOT NULL, thumbnail TEXT NOT NULL, taken_at TEXT, latitude REAL, longitude REAL, location TEXT NOT NULL DEFAULT "", uploaded_at TEXT NOT NULL)',  # 保存照片、拍摄元数据与服务器文件名。
    'CREATE TABLE IF NOT EXISTS geocache (key TEXT PRIMARY KEY, location TEXT NOT NULL)',  # 缓存成功解析的地点，避免重复联网。
    'CREATE INDEX IF NOT EXISTS photos_time ON photos(taken_at, id)',  # 为时间排序建立索引。
]  # 结束数据库结构定义。


def db():  # 为当前请求按需建立数据库连接。
    if 'db' not in g:  # 同一请求复用连接。
        g.db = sqlite3.connect(g.app_db, timeout=15)  # 使用应用配置的数据库路径。
        g.db.row_factory = sqlite3.Row  # 允许按列名读取查询结果。
    return g.db  # 返回当前请求的连接。


def coordinate(parts, reference, limit):  # 将 EXIF 度分秒和半球转换为十进制度数。
    values = [float(part) for part in parts]  # 将 EXIF 有理数转为浮点数。
    if len(values) != 3 or not all(math.isfinite(v) and v >= 0 for v in values):  # 拒绝缺项和非有限数值。
        raise ValueError('无效的 GPS 度分秒')  # 将错误交给元数据解析层处理。
    if values[1] >= 60 or values[2] >= 60:  # 检查分和秒的有效范围。
        raise ValueError('无效的 GPS 分秒')  # 避免保存不规范的 GPS 数据。
    ref = reference.decode() if isinstance(reference, bytes) else str(reference)  # 兼容字节与字符串半球标记。
    if ref not in ('N', 'S', 'E', 'W'):  # 检查合法的半球标记。
        raise ValueError('无效的 GPS 半球')  # 防止把未知标记当作正坐标。
    value = values[0] + values[1] / 60 + values[2] / 3600  # 将度分秒合并为十进制度数。
    if value > limit:  # 限制纬度与经度的范围。
        raise ValueError('GPS 超出范围')  # 拒绝无效坐标。
    return -value if ref in ('S', 'W') else value  # 南纬和西经保存为负数。


def metadata(image):  # 从图片中提取拍摄时间和 GPS，允许缺失或损坏的 EXIF。
    taken_at, latitude, longitude = None, None, None  # 默认将未知元数据保持为空。
    try:  # 将元数据错误与图片内容校验分开处理。
        exif = image.getexif()  # 获取图片主 EXIF 目录。
        detail = exif.get_ifd(34665) if 34665 in exif else {}  # 获取包含原始拍摄时间的 EXIF 子目录。
        raw = detail.get(36867) or exif.get(36867) or detail.get(36868) or exif.get(306)  # 优先使用原始拍摄时间，再使用数字化或图像日期。
        if raw:  # 仅解析照片实际携带的日期字段。
            taken_at = datetime.strptime(str(raw).strip('\x00 '), '%Y:%m:%d %H:%M:%S').isoformat(timespec='seconds')  # 保存不擅自推断时区的本地拍摄时间。
    except (ValueError, TypeError, KeyError, SyntaxError, OSError):  # 容忍损坏或非标准日期字段。
        taken_at = None  # 不使用上传日期伪造拍摄时间。
    try:  # GPS 单独解析，避免时间字段错误导致坐标丢失。
        gps = image.getexif().get_ifd(34853)  # 获取 GPS 子目录。
        if gps:  # 只有照片包含坐标时才转换。
            latitude = coordinate(gps[2], gps[1], 90)  # 根据纬度度分秒和南北半球转换坐标。
            longitude = coordinate(gps[4], gps[3], 180)  # 根据经度度分秒和东西半球转换坐标。
    except (ValueError, TypeError, KeyError, ZeroDivisionError, SyntaxError, OSError):  # 缺少任意坐标部分时保持地点未知。
        latitude, longitude = None, None  # 防止保存只有一半的 GPS 坐标。
    return taken_at, latitude, longitude  # 向上传流程返回提取结果。


def reverse_geocode(latitude, longitude):  # 将 GPS 坐标解析为可检索的中文地址。
    from flask import current_app  # 获取当前应用配置，支持测试时关闭联网。
    global LAST_GEOCODE  # 在所有请求之间共享最近访问时间。
    if latitude is None or not current_app.config['GEOCODING_ENABLED']:  # 缺少 GPS 或关闭联网时直接退出。
        return ''  # 保留坐标供手动维护。
    key = f'{latitude:.5f},{longitude:.5f}'  # 以约米级坐标生成缓存键。
    with GEOCODE_LOCK:  # 串行访问缓存和公共服务，防止并发超频。
        cached = db().execute('SELECT location FROM geocache WHERE key=?', (key,)).fetchone()  # 查询持久化地点缓存。
        if cached:  # 优先复用已成功解析的地址。
            return cached['location']  # 避免重复网络请求。
        time.sleep(max(0, 1.05 - (time.monotonic() - LAST_GEOCODE)))  # 保证相邻请求至少间隔一秒。
        LAST_GEOCODE = time.monotonic()  # 在发送前记录请求时间。
        params = urlencode({'lat': latitude, 'lon': longitude, 'format': 'jsonv2', 'accept-language': 'zh-CN'})  # 请求中文逆地理编码结果。
        req = Request(f'https://nominatim.openstreetmap.org/reverse?{params}', headers={'User-Agent': 'PhotoBrowserHomework/1.0 (local educational photo album)'})  # 标识本作业应用。
        try:  # 网络不可用不阻止上传。
            with urlopen(req, timeout=3) as response:  # 为外部服务设置三秒超时。
                location = str(json.load(response).get('display_name', ''))[:500]  # 限制返回地址长度。
            if location:  # 只缓存成功解析的地点，允许失败后重试。
                db().execute('INSERT OR REPLACE INTO geocache(key,location) VALUES (?,?)', (key, location))  # 保存地点缓存。
                db().commit()  # 立即持久化缓存结果。
            return location  # 将地址交给照片记录。
        except Exception:  # 外部服务失败统一降级为可手动维护的未知地点。
            return ''  # 保留上传成功及原始 GPS。


def create_app(test_config=None):  # 创建应用工厂，允许测试使用隔离的数据目录。
    app = Flask(__name__)  # 初始化 Flask 网页应用。
    app.config.update(DATA_DIR=str(ROOT / 'instance'), MAX_CONTENT_LENGTH=PHOTO_LIMIT * 50 + 1024 * 1024, GEOCODING_ENABLED=os.environ.get('PHOTO_GEOCODING', '1') != '0', SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')  # 配置存储、批次大小、联网和会话属性。
    if test_config:  # 测试配置覆盖正式配置。
        app.config.update(test_config)  # 支持临时数据库及禁止联网。
    data = Path(app.config['DATA_DIR'])  # 解析实际数据目录。
    data.mkdir(parents=True, exist_ok=True)  # 首次运行创建数据目录。
    for folder in ('originals', 'images', 'thumbs'):  # 为原图、方向纠正大图和缩略图分开存储。
        (data / folder).mkdir(exist_ok=True)  # 重启时保留已有照片。
    secret_file = data / 'secret.key'  # 将会话密钥保存在本机运行数据内。
    if not secret_file.exists():  # 首次运行生成固定密钥。
        secret_file.write_text(secrets.token_hex(32), encoding='utf-8')  # 重启后继续使用相同密钥。
    app.secret_key = secret_file.read_text(encoding='utf-8')  # 为签名会话启用密钥。
    database = str(data / 'photos.sqlite3')  # 定义数据库文件路径。
    with sqlite3.connect(database) as connection:  # 创建或打开持久化数据库。
        for statement in SCHEMA:  # 依次建立不存在的数据表和索引。
            connection.execute(statement)  # 不改动已经保存的相册数据。

    @app.before_request  # 在每次请求前准备数据库与 CSRF 令牌。
    def prepare():  # 统一验证所有写操作。
        g.app_db = database  # 将数据库路径传给当前请求的连接函数。
        if 'csrf' not in session:  # 首次访问为浏览器生成令牌。
            session['csrf'] = secrets.token_hex(24)  # 防止跨站伪造后台操作。
        if request.method in ('POST', 'PATCH', 'DELETE'):  # 对所有写接口校验 CSRF。
            token = request.headers.get('X-CSRF-Token', '')  # 从前端请求头读取令牌。
            if not secrets.compare_digest(token, session['csrf']):  # 使用恒定时间比较令牌。
                abort(400, description='页面已过期，请刷新后重试。')  # 阻止无效的写入请求。

    @app.teardown_appcontext  # 请求结束后回收数据库连接。
    def close_db(error=None):  # 即使接口出错也关闭连接。
        connection = g.pop('db', None)  # 取出已创建的连接。
        if connection is not None:  # 没有连接时无需清理。
            connection.close()  # 避免连接和文件句柄泄漏。

    @app.errorhandler(HTTPException)  # 将接口中的 HTTP 错误转为易读提示。
    def http_error(error):  # 页面错误继续保留对应状态码。
        if request.path.startswith('/api/'):  # API 使用一致的 JSON 错误格式。
            return jsonify(error=error.description), error.code  # 向前端显示具体错误。
        return render_template('message.html', message=error.description), error.code  # 用中文布局显示网页错误。

    def authenticated(function):  # 声明管理员写操作的统一权限校验。
        @wraps(function)  # 保留被包装路由的名称。
        def wrapped(*args, **kwargs):  # 转发路由参数。
            if not session.get('admin'):  # 检查浏览器是否已成功登录。
                abort(401, description='请先登录管理员账号。')  # 阻止匿名修改相册。
            return function(*args, **kwargs)  # 执行已经授权的操作。
        return wrapped  # 返回受保护的函数。

    def has_admin():  # 查询是否已经完成首次管理员设置。
        return db().execute('SELECT id FROM admin WHERE id=1').fetchone() is not None  # 管理员只有一个。

    @app.get('/')  # 注册公开浏览页。
    def index():  # 前台不要求登录。
        return render_template('index.html', admin=False)  # 渲染照片浏览界面。

    @app.get('/admin')  # 注册独立后台入口。
    def admin():  # 根据配置和登录状态进入正确页面。
        if not has_admin():  # 未设置管理员时先创建账号。
            return redirect(url_for('setup'))  # 进入首次设置页。
        if not session.get('admin'):  # 未登录时跳转登录页。
            return redirect(url_for('login'))  # 避免公开后台编辑控件。
        return render_template('index.html', admin=True)  # 复用相册并启用管理功能。

    @app.get('/setup')  # 注册首次设置页。
    def setup():  # 设置完成后不允许重新创建管理员。
        if has_admin():  # 检查是否已有管理员。
            return redirect(url_for('login'))  # 将重复设置引导到登录页。
        return render_template('auth.html', setup=True)  # 显示创建管理员表单。

    @app.get('/login')  # 注册管理员登录页。
    def login():  # 未初始化时自动引导设置。
        if not has_admin():  # 避免出现无法登录的空账号状态。
            return redirect(url_for('setup'))  # 首次运行先设置账号。
        return render_template('auth.html', setup=False)  # 显示普通登录表单。

    @app.post('/api/setup')  # 创建首次管理员的接口。
    def setup_api():  # 只允许空数据库初始化一次。
        body = request.get_json(silent=True) or {}  # 安全读取 JSON 表单。
        username, password = str(body.get('username', '')).strip(), str(body.get('password', ''))  # 提取账号和密码。
        if not 2 <= len(username) <= 40 or not 8 <= len(password) <= 128:  # 约束账号长度和密码强度。
            abort(400, description='账号需要 2–40 个字符，密码需要 8–128 个字符。')  # 返回可操作的表单提示。
        try:  # 通过数据库事务防止并发重复初始化。
            db().execute('INSERT INTO admin(id,username,password_hash) VALUES (1,?,?)', (username, generate_password_hash(password)))  # 保存唯一管理员与密码哈希。
            db().commit()  # 持久化管理员配置。
        except sqlite3.IntegrityError:  # 已存在管理员时禁止覆盖。
            db().rollback()  # 回滚重复插入。
            abort(409, description='管理员已设置，请直接登录。')  # 告知设置已经完成。
        session['admin'] = True  # 首次设置成功后直接进入后台。
        return jsonify(ok=True)  # 通知前端跳转。

    @app.post('/api/login')  # 注册管理员登录接口。
    def login_api():  # 校验账号和密码后签发管理员会话。
        body = request.get_json(silent=True) or {}  # 读取登录参数。
        row = db().execute('SELECT * FROM admin WHERE id=1').fetchone()  # 获取已配置的账号。
        if not row or str(body.get('username', '')).strip() != row['username'] or not check_password_hash(row['password_hash'], str(body.get('password', ''))):  # 同时校验账号与密码哈希。
            abort(401, description='账号或密码不正确。')  # 不向浏览器泄露密码信息。
        session.clear()  # 清除登录前的会话内容。
        session.update(admin=True, csrf=secrets.token_hex(24))  # 登录时更新防伪令牌。
        return jsonify(ok=True)  # 由前端重新载入后台。

    @app.post('/api/logout')  # 注册退出接口。
    def logout():  # 清除当前浏览器的管理员权限。
        session.clear()  # 删除管理员标记和 CSRF 令牌。
        return jsonify(ok=True)  # 前端返回公开相册。

    def selection():  # 从查询字符串构造安全的查询与排序条件。
        clauses, values = [], []  # 分离 SQL 结构和参数，避免字符串注入。
        location = request.args.get('location', '').strip()  # 获取地点关键词。
        if location:  # 地点条件为空时浏览所有地点。
            clauses.append('instr(lower(location), lower(?)) > 0')  # 使用子串匹配，百分号也按普通文字查询。
            values.append(location)  # 使用参数绑定传递用户输入。
        start, end = request.args.get('start', ''), request.args.get('end', '')  # 获取起止日期。
        try:  # 统一验证日期格式和起止顺序。
            start_date = datetime.strptime(start, '%Y-%m-%d') if start else None  # 解析开始日期。
            end_date = datetime.strptime(end, '%Y-%m-%d') if end else None  # 解析结束日期。
            if start_date and end_date and start_date > end_date:  # 拒绝倒置的日期范围。
                raise ValueError('日期范围倒置')  # 转为统一的日期错误。
            if start_date:  # 起始日期从当天零点开始。
                clauses.append('taken_at >= ?')  # 日期范围自动排除拍摄时间未知的照片。
                values.append(start_date.isoformat(timespec='seconds'))  # 使用标准时间字符串比较。
            if end_date:  # 截止日期包含当天的全部拍摄时间。
                clauses.append('taken_at < ?')  # 使用次日零点作为开区间上界。
                values.append((end_date + timedelta(days=1)).isoformat(timespec='seconds'))  # 避免遗漏当天最后一秒。
        except (ValueError, OverflowError):  # 统一处理无效日期和超出范围的日期。
            abort(400, description='请输入有效日期，且开始日期不能晚于结束日期。')  # 返回查询条件错误。
        order = 'ASC' if request.args.get('order', 'desc') == 'asc' else 'DESC'  # 只从固定排序关键字中选择。
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''  # 只有存在条件时添加 WHERE。
        return where, values, f' ORDER BY (taken_at IS NULL) ASC, taken_at {order}, id {order}'  # 未知时间始终置后，ID 提供稳定排序。

    def photo_json(row):  # 将数据库记录转换为前端可使用的照片对象。
        result = dict(row)  # 不修改原始数据库行。
        result.update(image_url=url_for('media', kind='images', filename=row['image']), thumbnail_url=url_for('media', kind='thumbs', filename=row['thumbnail']), original_url=url_for('media', kind='originals', filename=row['original']))  # 生成由服务器控制的文件地址。
        return result  # 返回完整展示和编辑信息。

    @app.get('/api/photos')  # 提供公开照片分页查询接口。
    def photos():  # 同时返回筛选结果数与全部相册数。
        where, values, order = selection()  # 复用统一的查询和排序规则。
        try:  # 分页参数必须是正整数。
            page = max(1, int(request.args.get('page', 1)))  # 将页码下限固定为第一页。
        except ValueError:  # 非数字页码返回友好错误。
            abort(400, description='页码必须是整数。')  # 避免数据库异常。
        total = db().execute('SELECT COUNT(*) FROM photos' + where, values).fetchone()[0]  # 统计当前筛选结果。
        pages = max(1, math.ceil(total / 12))  # 每页显示十二张照片。
        page = min(page, pages)  # 删除或筛选后自动回退到有效页。
        rows = db().execute('SELECT * FROM photos' + where + order + ' LIMIT 12 OFFSET ?', [*values, (page - 1) * 12]).fetchall()  # 查询当前页照片。
        overall = db().execute('SELECT COUNT(*) FROM photos').fetchone()[0]  # 统计相册总照片数。
        places = db().execute('SELECT COUNT(DISTINCT location) FROM photos WHERE location != ""').fetchone()[0]  # 统计已记录的不同地点。
        return jsonify(items=[photo_json(row) for row in rows], total=total, overall=overall, places=places, page=page, pages=pages)  # 返回分页数据及首页统计。

    @app.get('/api/photos/<int:photo_id>')  # 获取大图详情和相邻照片。
    def detail(photo_id):  # 大图翻看始终遵守当前筛选条件。
        where, values, order = selection()  # 与列表使用完全相同的条件。
        ids = [row[0] for row in db().execute('SELECT id FROM photos' + where + order, values)]  # 只读取匹配照片的有序 ID。
        if photo_id not in ids:  # 删除后的照片或不匹配当前筛选的照片不可打开。
            abort(404, description='该照片不存在或不在当前查询结果中。')  # 提供明确的失效提示。
        position = ids.index(photo_id)  # 定位照片在筛选结果中的顺序。
        row = db().execute('SELECT * FROM photos WHERE id=?', (photo_id,)).fetchone()  # 获取当前照片详情。
        return jsonify(photo=photo_json(row), previous=ids[position - 1] if position else None, next=ids[position + 1] if position + 1 < len(ids) else None, position=position + 1, total=len(ids))  # 返回首尾安全的翻看导航。

    @app.get('/media/<kind>/<filename>')  # 提供原图、大图和缩略图文件。
    def media(kind, filename):  # 文件目录只能从固定类型中选择。
        if kind not in ('originals', 'images', 'thumbs'):  # 拒绝任意目录访问。
            abort(404)  # 隐藏数据目录里的其他文件。
        return send_from_directory(data / kind, filename, as_attachment=kind == 'originals')  # 原图供下载，展示图供浏览。

    @app.post('/api/photos')  # 后台批量上传接口。
    @authenticated  # 要求管理员权限。
    def upload():  # 每个文件独立处理，失败不会回滚其他照片。
        files = request.files.getlist('photos')  # 接受同一个 multipart 字段中的多张图片。
        if not files or len(files) > 50:  # 对每个请求限制文件数量。
            abort(400, description='每批请选择 1–50 张照片。')  # 返回批次验证错误。
        results = []  # 收集每张照片的独立处理结果。
        for file in files:  # 按所选顺序处理上传文件。
            paths = []  # 跟踪当前文件产生的资源，便于失败清理。
            name = (file.filename or '未命名照片').replace('\\', '/').split('/')[-1][:200]  # 仅保存原文件的显示名称。
            try:  # 限制单张图片的失败范围。
                raw = file.stream.read(PHOTO_LIMIT + 1)  # 最多读取单张上限多一个字节。
                if len(raw) > PHOTO_LIMIT:  # 大文件不能进入图片解析阶段。
                    raise ValueError('超过每张 20 MB 的限制')  # 将超限原因返回到逐张结果。
                with Image.open(BytesIO(raw)) as probe:  # 根据真实内容识别格式。
                    format_name = probe.format  # 不依赖文件扩展名或浏览器 MIME。
                    if format_name not in ('JPEG', 'PNG', 'WEBP'):  # 仅允许作业支持的图片格式。
                        raise ValueError('仅支持 JPEG、PNG、WebP 图片')  # 拒绝其他文件内容。
                    probe.verify()  # 检查图片编码的基本完整性。
                with Image.open(BytesIO(raw)) as image:  # 重新打开已经验证的图片供解码。
                    image.load()  # 完整解码，避免保存截断图片。
                    taken_at, latitude, longitude = metadata(image)  # 自动提取原始拍摄时间和坐标。
                    display = ImageOps.exif_transpose(image).convert('RGB')  # 根据 EXIF 方向纠正大图并统一颜色模式。
                    token = secrets.token_hex(16)  # 避免同名上传覆盖文件。
                    original = token + {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}[format_name]  # 按真实格式确定原图后缀。
                    image_name, thumbnail = token + '.jpg', token + '.jpg'  # 不同目录可使用相同随机文件名。
                    paths = [data / 'originals' / original, data / 'images' / image_name, data / 'thumbs' / thumbnail]  # 记录待写入的三个文件。
                    paths[0].write_bytes(raw)  # 原样保存上传图片及其 EXIF。
                    display.save(paths[1], 'JPEG', quality=90)  # 保存方向正确且不携带敏感 EXIF 的展示图。
                    display.thumbnail((640, 480))  # 为网格生成轻量缩略图。
                    display.save(paths[2], 'JPEG', quality=85)  # 避免列表加载全部原图。
                location = reverse_geocode(latitude, longitude)  # 联网失败时仍继续保存照片记录。
                cursor = db().execute('INSERT INTO photos(name,original,image,thumbnail,taken_at,latitude,longitude,location,uploaded_at) VALUES (?,?,?,?,?,?,?,?,?)', (name, original, image_name, thumbnail, taken_at, latitude, longitude, location, datetime.now().isoformat(timespec='seconds')))  # 一并保存图像路径和拍摄元数据。
                db().commit()  # 逐张持久化成功的照片。
                results.append(dict(name=name, ok=True, id=cursor.lastrowid, message='上传成功' if taken_at and location else '上传成功；缺少的信息可手动补充'))  # 告知元数据是否完整。
            except (ValueError, UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as error:  # 捕获文件、图片和资源限制错误。
                db().rollback()  # 撤销当前照片未完成的数据库操作。
                for path in paths:  # 清除当前失败文件的派生资源。
                    path.unlink(missing_ok=True)  # 不删除其他已经成功上传的照片。
                results.append(dict(name=name, ok=False, message=str(error) if isinstance(error, ValueError) else '图片无效、过大或无法保存'))  # 向前端提供安全的失败原因。
        return jsonify(results=results)  # 一次返回批次内每张照片的处理结果。

    @app.patch('/api/photos/<int:photo_id>')  # 修改照片管理信息。
    @authenticated  # 只允许管理员修改。
    def update(photo_id):  # 允许补充或清空未知拍摄信息。
        body = request.get_json(silent=True) or {}  # 读取前端编辑表单。
        taken_at, location = body.get('taken_at') or None, str(body.get('location', '')).strip()  # 空时间保存为 NULL。
        if taken_at:  # 非空时间必须能解析为合法本地日期时间。
            try:  # 校验后统一数据库存储格式。
                parsed = datetime.fromisoformat(str(taken_at))  # 兼容 datetime-local 提交的分钟或秒精度。
                if parsed.tzinfo is not None:  # 当前模型按当地拍摄时间存储。
                    raise ValueError('不接受时区')  # 防止混合不同时区格式影响排序。
                taken_at = parsed.isoformat(timespec='seconds')  # 补齐秒以维持稳定字符串排序。
            except ValueError:  # 拒绝无效拍摄时间。
                abort(400, description='请输入有效的当地拍摄时间。')  # 显示编辑错误。
        if len(location) > 500:  # 保持地点字段长度可控。
            abort(400, description='地点最多 500 个字符。')  # 返回长度提示。
        cursor = db().execute('UPDATE photos SET taken_at=?,location=? WHERE id=?', (taken_at, location, photo_id))  # 只更新可编辑字段，保留原始 GPS。
        if cursor.rowcount == 0:  # 被删除的照片不可再编辑。
            abort(404, description='照片不存在。')  # 返回失效错误。
        db().commit()  # 保存更新并让查询立即生效。
        return jsonify(ok=True)  # 提示前端刷新列表。

    @app.post('/api/photos/<int:photo_id>/geocode')  # 允许网络恢复后重新解析地点。
    @authenticated  # 外部地点调用也要求管理员身份。
    def retry_geocode(photo_id):  # 不覆盖照片的原始坐标。
        row = db().execute('SELECT * FROM photos WHERE id=?', (photo_id,)).fetchone()  # 获取已保存的 GPS。
        if not row:  # 照片可能已删除。
            abort(404, description='照片不存在。')  # 提示刷新页面。
        location = reverse_geocode(row['latitude'], row['longitude'])  # 使用同样的限流和缓存策略。
        if not location:  # 无坐标或断网时保留手工地点。
            abort(400, description='没有可用 GPS，或暂时无法解析地点，请手动填写。')  # 不影响已有照片信息。
        db().execute('UPDATE photos SET location=? WHERE id=?', (location, photo_id))  # 将解析成功的地址写入照片。
        db().commit()  # 持久化新地址。
        return jsonify(location=location)  # 填入当前编辑表单。

    @app.delete('/api/photos/<int:photo_id>')  # 删除照片及其派生文件。
    @authenticated  # 防止游客删除照片。
    def delete(photo_id):  # 先验证资源存在，再清理文件与记录。
        row = db().execute('SELECT * FROM photos WHERE id=?', (photo_id,)).fetchone()  # 查询要删除的文件名。
        if not row:  # 重复删除返回明确错误。
            abort(404, description='照片不存在。')  # 不影响其他数据。
        for folder, field in (('originals', 'original'), ('images', 'image'), ('thumbs', 'thumbnail')):  # 按同一照片的记录定位三个资源。
            (data / folder / row[field]).unlink(missing_ok=True)  # 清理原图、展示图和缩略图。
        db().execute('DELETE FROM photos WHERE id=?', (photo_id,))  # 移除管理和查询记录。
        db().commit()  # 让删除结果立即可见。
        return jsonify(ok=True)  # 通知前端关闭大图并刷新。

    return app  # 返回配置完整的应用。


if __name__ == '__main__':  # 直接运行时启动本机演示服务器。
    create_app().run(host='127.0.0.1', port=int(os.environ.get('PORT', '5000')), debug=False)  # 默认只允许本机通过 5000 端口访问。
