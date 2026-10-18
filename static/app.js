"use strict"; // 启用严格模式，避免意外创建全局变量。
const $ = (selector) => document.querySelector(selector); // 简化当前页面的元素查询。
let toastTimer; // 保存提示自动消失的定时器。
function notify(message) { // 以无障碍状态区域显示操作结果。
    const toast = $("#toast"); // 获取共用消息容器。
    const parent = $("#viewer")?.open ? $("#viewer") : document.body; // 模态框打开时把提示放在顶层框内。
    parent.append(toast); // 确保消息位于当前可见层。
    toast.textContent = message; // 以纯文本写入提示，避免 HTML 注入。
    toast.hidden = false; // 展示提示。
    clearTimeout(toastTimer); // 避免旧定时器提前关闭新提示。
    toastTimer = setTimeout(() => { toast.hidden = true; }, 5000); // 五秒后自动隐藏。
} // 结束消息提示函数。
async function api(url, options = {}) { // 统一处理 JSON 接口与错误提示。
    const headers = new Headers(options.headers || {}); // 保留调用者指定的请求头。
    headers.set("X-CSRF-Token", $("meta[name=csrf-token]").content); // 为写操作提供会话防伪令牌。
    const response = await fetch(url, { ...options, headers }); // 携带同源会话发送请求。
    const body = await response.json().catch(() => ({ error: "服务器返回异常，请刷新后重试。" })); // 兼容服务端非 JSON 错误。
    if (!response.ok) throw new Error(body.error || "操作失败，请重试。"); // 将错误交给对应界面显示。
    return body; // 返回已经解码的接口数据。
} // 结束接口请求封装。
function jsonOptions(method, body) { // 为编辑和认证表单创建 JSON 请求选项。
    return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }; // 编码请求体并声明类型。
} // 结束 JSON 参数封装。
$("#logout")?.addEventListener("click", async () => { // 为管理员导航绑定退出操作。
    try { // 捕获退出时的网络错误。
        await api("/api/logout", { method: "POST" }); // 清除服务器会话。
        window.location.href = "/"; // 返回公开照片浏览页。
    } catch (error) { notify(error.message); } // 留在当前页并提示失败。
}); // 结束退出事件绑定。
$("#auth-form")?.addEventListener("submit", async (event) => { // 处理首次设置或登录表单。
    event.preventDefault(); // 阻止浏览器以传统方式提交表单。
    const form = event.currentTarget; // 记录当前认证表单。
    const body = Object.fromEntries(new FormData(form)); // 收集账号、密码与确认密码。
    const errorBox = $("#auth-error"); // 定位表单错误区域。
    errorBox.hidden = true; // 清除上一次错误显示。
    if (form.dataset.mode === "setup" && body.password !== body.confirm) { // 首次设置必须两次密码一致。
        errorBox.textContent = "两次输入的密码不一致。"; // 给出明确的修正提示。
        errorBox.hidden = false; // 展示表单校验结果。
        return; // 不发送不一致的密码。
    } // 结束确认密码检查。
    const button = form.querySelector("button"); // 获取提交按钮。
    button.disabled = true; // 防止重复创建或重复登录。
    try { // 捕获认证服务错误。
        await api(`/api/${form.dataset.mode}`, jsonOptions("POST", body)); // 根据表单模式请求设置或登录。
        window.location.href = "/admin"; // 重新加载页面以获取更新后的 CSRF 令牌。
    } catch (error) { // 认证失败时保持输入内容。
        errorBox.textContent = error.message; // 显示服务器返回的认证错误。
        errorBox.hidden = false; // 让屏幕阅读器读出错误。
    } finally { button.disabled = false; } // 恢复提交按钮以便重试。
}); // 结束认证表单事件绑定。
if ($("#album")) { // 仅在照片浏览页初始化相册交互。
    const filters = $("#filters"); // 获取顶部查询表单。
    const viewer = $("#viewer"); // 获取大图对话框。
    const params = new URLSearchParams(window.location.search); // 支持刷新后恢复查询条件。
    let page = Math.max(1, Number(params.get("page")) || 1); // 初始化当前页码。
    let active = null; // 保存当前大图及相邻照片编号。
    let listVersion = 0; // 用版本号防止旧列表响应覆盖新查询。
    let detailVersion = 0; // 用版本号防止快速翻看时出现旧大图。
    let editing = false; // 记录后台写操作是否正在进行。
    for (const name of ["location", "start", "end", "order"]) { // 从地址栏恢复各项筛选设置。
        if (params.has(name)) filters.elements[name].value = params.get(name); // 只恢复已传入的字段。
    } // 结束筛选初始化。
    if (!["asc", "desc"].includes(filters.elements.order.value)) filters.elements.order.value = "desc"; // 非法排序值回到默认倒序。
    let query = new URLSearchParams(new FormData(filters)); // 保存已提交的条件，避免未提交输入影响翻看。
    function queryUrl(path, includePage = false) { // 为列表与详情生成相同查询条件。
        const search = new URLSearchParams(query); // 避免修改已经提交的筛选状态。
        if (includePage) search.set("page", String(page)); // 只有列表需要分页参数。
        return `${path}?${search}`; // 将编码后的筛选条件附加到接口地址。
    } // 结束查询地址生成。
    function card(photo) { // 为一张照片创建安全的展示卡片。
        const button = document.createElement("button"); // 使用原生按钮支持键盘激活。
        button.className = "photo-card"; // 应用网格卡片样式。
        button.type = "button"; // 防止意外提交其他表单。
        button.setAttribute("aria-label", `查看照片：${photo.name}`); // 给无障碍用户提供照片名称。
        const image = document.createElement("img"); // 创建缩略图元素。
        image.src = photo.thumbnail_url; // 从本机服务器加载轻量缩略图。
        image.alt = photo.name; // 图片失败或屏幕阅读时显示文件名。
        image.loading = "lazy"; // 延迟加载尚未进入视口的照片。
        const info = document.createElement("div"); // 创建卡片元数据区域。
        info.className = "card-info"; // 设置卡片内边距。
        const title = document.createElement("h3"); // 为卡片创建照片标题。
        title.className = "card-title"; // 长文件名会自动省略。
        title.textContent = photo.name; // 不将文件名作为 HTML 执行。
        title.title = photo.name; // 鼠标悬浮可查看完整名称。
        const date = document.createElement("p"); // 创建拍摄时间说明。
        date.className = "card-meta"; // 使用次要文本样式。
        date.textContent = `◷ ${photo.taken_at ? photo.taken_at.replace("T", " ") : "拍摄时间未知"}`; // 明确区分缺少元数据。
        const location = document.createElement("p"); // 创建拍摄地点说明。
        location.className = "card-meta"; // 与时间统一样式。
        location.textContent = `⌖ ${photo.location || "地点未知"}`; // 使用手动或自动记录的地址。
        location.title = photo.location || "地点未知"; // 便于查看长地址。
        info.append(title, date, location); // 按名称、时间、地点排列元数据。
        button.append(image, info); // 将图片与信息组成完整卡片。
        button.addEventListener("click", () => openPhoto(photo.id)); // 点击时沿用当前筛选打开大图。
        return button; // 返回可加入网格的照片卡片。
    } // 结束卡片创建。
    async function loadPhotos() { // 从服务端加载当前筛选的照片列表。
        const version = ++listVersion; // 标记这次列表请求。
        $("#list-error").hidden = true; // 清除上一次请求错误。
        $("#gallery").setAttribute("aria-busy", "true"); // 告知辅助技术正在更新内容。
        $("#previous-page").disabled = true; // 加载时防止重复翻页。
        $("#next-page").disabled = true; // 加载完成前暂停下一页。
        try { // 捕获网络或查询参数错误。
            const data = await api(queryUrl("/api/photos", true)); // 获取分页照片和统计。
            if (version !== listVersion) return; // 丢弃已经过时的响应。
            page = data.page; // 使用服务端修正后的有效页码。
            $("#gallery").replaceChildren(...data.items.map(card)); // 安全更新照片网格。
            $("#overall").textContent = data.overall; // 显示全部相册照片数。
            $("#places").textContent = data.places; // 显示已记录的不同地点数。
            $("#result-count").textContent = `${data.total} 张照片`; // 标注当前查询结果数量。
            $("#order-label").textContent = query.get("order") === "asc" ? "拍摄时间 · 从早到晚" : "拍摄时间 · 从晚到早"; // 明确当前排列方向。
            $("#page-info").textContent = `第 ${data.page} / ${data.pages} 页`; // 更新分页状态。
            $("#previous-page").disabled = data.page <= 1; // 第一页不能继续向前翻。
            $("#next-page").disabled = data.page >= data.pages; // 最后一页不能继续向后翻。
            $("#empty").hidden = data.total > 0; // 有照片时隐藏空状态。
            $("#empty-title").textContent = data.overall ? "没有找到符合条件的照片" : "相册还是空的"; // 区分筛选无结果与没有上传。
            $("#empty-text").textContent = data.overall ? "试试其他地点或日期范围，也可以重置查询条件。" : "从一张照片开始，收藏你的第一个瞬间。"; // 为当前状态给出下一步提示。
            $("#empty a").hidden = data.overall > 0; // 查询无结果时不显示无关的上传入口。
            const address = new URLSearchParams(query); // 将已提交条件保存到地址栏。
            address.set("page", String(page)); // 同时记录当前页码。
            window.history.replaceState(null, "", `${window.location.pathname}?${address}`); // 刷新页面后保留查询状态。
        } catch (error) { // 查询失败时明确显示错误。
            if (version !== listVersion) return; // 不显示过时请求的错误。
            $("#gallery").replaceChildren(); // 避免把旧照片误认为当前结果。
            $("#empty").hidden = true; // 错误不等于相册为空。
            $("#list-error").textContent = `${error.message} 请调整条件或再次点击查询。`; // 提供可重试的查询说明。
            $("#list-error").hidden = false; // 展示错误面板。
        } finally { // 无论成功与否都清理加载状态。
            if (version === listVersion) $("#gallery").setAttribute("aria-busy", "false"); // 只结束当前版本的忙碌状态。
        } // 结束查询结果处理。
    } // 结束照片列表加载。
    async function openPhoto(id) { // 获取照片详情并支持跨列表页翻看。
        if (editing) return; // 写操作进行时固定当前照片。
        const version = ++detailVersion; // 标记当前大图请求。
        try { // 捕获照片失效与网络错误。
            const data = await api(queryUrl(`/api/photos/${id}`)); // 服务端返回同一筛选下的前后照片。
            if (version !== detailVersion) return; // 防止旧照片覆盖最新选择。
            active = data; // 保存当前照片和导航编号。
            $("#large-image").src = data.photo.image_url; // 显示已经按 EXIF 纠正方向的大图。
            $("#large-image").alt = data.photo.name; // 以照片名称提供替代文本。
            $("#photo-title").textContent = data.photo.name; // 安全显示完整照片名称。
            $("#photo-position").textContent = `${data.position} / ${data.total} · 使用 ← → 方向键翻看`; // 提示当前位置和快捷键。
            $("#photo-time").textContent = data.photo.taken_at?.replace("T", " ") || "拍摄时间未知"; // 展示拍摄时间而非上传时间。
            $("#photo-location").textContent = data.photo.location || "地点未知"; // 无地址时显示未知。
            $("#photo-gps").textContent = data.photo.latitude === null ? "未记录 GPS" : `${data.photo.latitude.toFixed(6)}, ${data.photo.longitude.toFixed(6)}`; // 坐标为零也视为有效值。
            $("#download-original").href = data.photo.original_url; // 原始文件通过下载接口获取。
            $("#previous-photo").disabled = data.previous === null; // 首张照片禁用向前翻看。
            $("#next-photo").disabled = data.next === null; // 尾张照片禁用向后翻看。
            if ($("#edit-form")) { // 后台同时填充可编辑信息。
                $("#edit-form").elements.taken_at.value = data.photo.taken_at || ""; // 将未知时间显示为空输入。
                $("#edit-form").elements.location.value = data.photo.location; // 保留当前人工或自动地址。
                $("#retry-geocode").disabled = data.photo.latitude === null; // 没有 GPS 时不可重试解析。
            } // 结束编辑表单填充。
            if (!viewer.open) viewer.showModal(); // 使用原生模态框阻止背景交互。
        } catch (error) { notify(error.message); } // 通过统一提示说明照片无法打开。
    } // 结束大图加载。
    function closeViewer() { // 安全关闭大图并恢复页面提示层。
        if (editing) return; // 写操作完成前避免编辑对象发生变化。
        ++detailVersion; // 关闭后不再接受尚未完成的大图请求。
        viewer.close(); // 原生对话框会将焦点还给原控件。
        document.body.append($("#toast")); // 防止关闭对话框后提示也被隐藏。
    } // 结束大图关闭逻辑。
    $("#close-viewer").addEventListener("click", closeViewer); // 绑定显式关闭入口。
    viewer.addEventListener("cancel", (event) => { event.preventDefault(); closeViewer(); }); // Escape 关闭也沿用写操作保护。
    $("#previous-photo").addEventListener("click", () => { if (active?.previous) openPhoto(active.previous); }); // 前翻不会受当前网格页码限制。
    $("#next-photo").addEventListener("click", () => { if (active?.next) openPhoto(active.next); }); // 后翻保持当前查询和排序。
    document.addEventListener("keydown", (event) => { // 支持方向键快速翻看。
        if (!viewer.open || editing || ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return; // 编辑字段时保留输入控件自己的方向键行为。
        if (event.key === "ArrowLeft" && active?.previous) { event.preventDefault(); openPhoto(active.previous); } // 按左键查看上一张。
        if (event.key === "ArrowRight" && active?.next) { event.preventDefault(); openPhoto(active.next); } // 按右键查看下一张。
    }); // 结束方向键处理。
    filters.addEventListener("submit", (event) => { // 提交顶部组合查询。
        event.preventDefault(); // 通过接口更新网格而不整页跳转。
        query = new URLSearchParams(new FormData(filters)); // 将当前输入固定为已提交查询。
        page = 1; // 新查询从第一页开始。
        loadPhotos(); // 查询服务端照片列表。
    }); // 结束条件查询处理。
    filters.addEventListener("reset", () => { // 重置后恢复所有照片和默认倒序。
        setTimeout(() => { // 等待原生表单重置完成。
            filters.elements.order.value = "desc"; // 明确恢复最新拍摄在前。
            query = new URLSearchParams(new FormData(filters)); // 更新已提交条件。
            page = 1; // 回到第一页。
            loadPhotos(); // 加载全部照片。
        }, 0); // 在下一次事件循环读取已重置控件。
    }); // 结束重置逻辑。
    $("#previous-page").addEventListener("click", () => { page -= 1; loadPhotos(); }); // 向前翻一页网格。
    $("#next-page").addEventListener("click", () => { page += 1; loadPhotos(); }); // 向后翻一页网格。
    $("#files")?.addEventListener("change", async (event) => { // 一次选择多张照片后顺序上传。
        const input = event.currentTarget; // 获取文件选择控件。
        const files = Array.from(input.files); // 固定本次选择，避免上传中列表变化。
        if (!files.length) return; // 取消选择不触发上传。
        if (files.length > 50) { notify("每批最多选择 50 张照片。"); input.value = ""; return; } // 超量时不发送任何文件。
        input.disabled = true; // 上传期间避免重复选择。
        $("#choose-label").setAttribute("aria-disabled", "true"); // 告知辅助技术当前按钮不可用。
        $("#upload-results").replaceChildren(); // 清空上一批结果。
        $("#upload-results").hidden = false; // 展开逐张结果列表。
        $("#upload-status").hidden = false; // 显示批次状态。
        $("#upload-progress").hidden = false; // 展示进度条。
        $("#upload-progress").value = 0; // 将进度归零。
        let succeeded = 0; // 统计本批成功数量。
        for (let index = 0; index < files.length; index += 1) { // 逐张请求以便实时展示结果并避免大批次占用内存。
            const file = files[index]; // 取得当前上传文件。
            $("#upload-status").textContent = `正在处理 ${index + 1} / ${files.length}：${file.name}`; // 上传和地名解析共用处理状态。
            const line = document.createElement("li"); // 为每张照片创建结果行。
            try { // 一张失败仍继续处理剩余照片。
                if (file.size > 20 * 1024 * 1024) throw new Error("超过每张 20 MB 的限制"); // 在浏览器先拦截超大文件。
                const body = new FormData(); // 创建二进制文件上传表单。
                body.append("photos", file); // 与服务端批量字段保持一致。
                const response = await api("/api/photos", { method: "POST", body }); // 服务端验证图片并自动提取 EXIF。
                const result = response.results[0]; // 读取当前照片独立处理结果。
                if (!result.ok) throw new Error(result.message); // 将图片校验失败转为当前行错误。
                succeeded += 1; // 累加成功照片数量。
                line.className = "success"; // 成功使用绿色文字。
                line.textContent = `✓ ${file.name} · ${result.message}`; // 展示是否需要补充拍摄信息。
            } catch (error) { // 网络、权限和文件错误都显示在对应文件旁。
                line.className = "failure"; // 失败使用红色提示。
                line.textContent = `× ${file.name} · ${error.message}`; // 不执行文件名中的 HTML 内容。
            } // 结束单张处理。
            $("#upload-results").append(line); // 按选择顺序显示结果。
            $("#upload-progress").value = ((index + 1) / files.length) * 100; // 按已处理文件数量更新进度。
        } // 结束整个批次。
        $("#upload-status").textContent = `处理完成：成功 ${succeeded} 张，失败 ${files.length - succeeded} 张。`; // 保留批次总结供用户核对。
        input.disabled = false; // 恢复批量文件选择。
        $("#choose-label").removeAttribute("aria-disabled"); // 解除无障碍禁用标记。
        input.value = ""; // 支持重新选择相同文件。
        await loadPhotos(); // 上传后刷新实际照片统计与查询结果。
    }); // 结束批量上传绑定。
    async function writePhoto(action) { // 写操作期间锁定当前编辑照片。
        if (editing || !active) return; // 拒绝重入与无照片状态。
        editing = true; // 开启编辑锁。
        const controls = Array.from(viewer.querySelectorAll("button, input, textarea")); // 收集需要暂时禁用的操作控件。
        const states = controls.map((control) => control.disabled); // 保存每个控件原本的禁用状态。
        controls.forEach((control) => { control.disabled = true; }); // 防止保存中翻看或重复删除。
        try { await action(active.photo.id); } catch (error) { notify(error.message); } finally { // 执行写入并统一处理错误与恢复。
            editing = false; // 无论结果如何都解除编辑锁。
            controls.forEach((control, index) => { control.disabled = states[index]; }); // 恢复首尾导航和 GPS 控件状态。
        } // 结束写入状态恢复。
    } // 结束后台写操作包装。
    $("#edit-form")?.addEventListener("submit", async (event) => { // 保存人工补充的拍摄时间和地点。
        event.preventDefault(); // 通过 API 完成更新。
        const body = Object.fromEntries(new FormData(event.currentTarget)); // 在禁用控件前读取字段值。
        let saved = false; // 只有保存成功才关闭详情并刷新查询。
        await writePhoto(async (id) => { // 固定要更新的照片编号。
            await api(`/api/photos/${id}`, jsonOptions("PATCH", body)); // 服务端再次校验时间与地点。
            saved = true; // 标记持久化成功。
        }); // 结束写入，恢复导航。
        if (saved) { // 修改后照片可能移出当前地点或日期筛选。
            closeViewer(); // 关闭旧详情，避免显示已经不匹配查询的照片。
            await loadPhotos(); // 更新查询结果，照片可能因修改而移出当前筛选。
            notify("照片信息已保存。"); // 在可见页面上提示保存成功。
        } // 结束保存后刷新。
    }); // 结束编辑提交绑定。
    $("#retry-geocode")?.addEventListener("click", () => writePhoto(async (id) => { // 网络恢复后重新尝试坐标解析。
        const result = await api(`/api/photos/${id}/geocode`, { method: "POST" }); // 使用服务器上的原始 GPS。
        $("#edit-form").elements.location.value = result.location; // 将成功解析地址填入编辑框。
        $("#photo-location").textContent = result.location; // 同步只读地点展示。
        notify("地点已解析并保存。"); // 告知解析已经持久化。
        await loadPhotos(); // 更新地点统计和查询列表。
    })); // 结束地点重试绑定。
    $("#delete-photo")?.addEventListener("click", async () => { // 删除照片前明确说明不可恢复。
        if (!active || !window.confirm(`确定删除「${active.photo.name}」及服务器上的原图吗？此操作无法恢复。`)) return; // 取消时保留照片。
        let deleted = false; // 只在真正成功后关闭大图。
        await writePhoto(async (id) => { // 固定当前删除对象。
            await api(`/api/photos/${id}`, { method: "DELETE" }); // 同步清理服务器图片和数据库记录。
            deleted = true; // 记录删除成功。
        }); // 等待解除编辑锁。
        if (deleted) { closeViewer(); active = null; await loadPhotos(); notify("照片已删除。"); } // 关闭详情并回退到有效列表页。
    }); // 结束删除绑定。
    loadPhotos(); // 首次进入页面即加载真实相册数据。
} // 结束照片页面初始化。
