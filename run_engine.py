# run_engine.py
import re, os, fnmatch, json, time
from datetime import datetime, timedelta
from PySide6.QtCore import QObject, Signal, QTimer, QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from flow_step import FlowStep
import threading


class RunEngine(QObject):
    step_started = Signal(int)
    step_finished = Signal(int)
    finished = Signal(list)
    error_occurred = Signal(str)
    stopped = Signal()

    def __init__(self, webview: QWebEngineView):
        super().__init__()
        self.webview = webview
        self.steps = []
        self.current_index = -1
        self.running = False
        self.stop_requested = False
        self.extracted_data = []
        self.base_delay = 1000

        self.simulated_date = None
        self.download_history = []

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._execute_next_step)

        self.pre_wait_timer = QTimer(self)
        self.pre_wait_timer.timeout.connect(self._on_pre_wait_finished)
        self.pre_wait_remaining = 0
        self.pre_wait_element_check = False
        self.pre_wait_xpath = ""
        self.pre_wait_checking = False

        self.wait_timer = QTimer(self)
        self.wait_timer.setSingleShot(True)
        self.wait_timer.timeout.connect(self._on_wait_finished)
        # 桌面执行器
        self.desktop_executor = None

        # 上传相关
        self.cdp_fetcher = None
        self._cdp_main_url = ""                # 用于匹配正确的主页面 target
        self.upload_check_timer = QTimer(self)
        self.upload_check_timer.timeout.connect(self._check_js_upload_result)
        self._upload_callback = None
        self.last_desktop_target = None

    def _log(self, msg):
        print(msg)
        self.error_occurred.emit(msg)

    def run(self, steps):
        if self.running:
            return
        self.steps = steps
        self.current_index = 0
        self.running = True
        self.stop_requested = False
        self.extracted_data.clear()
        self.timer.start(500)

    def stop(self):
        self.stop_requested = True
        self.timer.stop()
        self.pre_wait_timer.stop()
        self.wait_timer.stop()
        self.upload_check_timer.stop()
        self.running = False
        self.last_desktop_target = None
        self.stopped.emit()

    def _execute_next_step(self):
        self.timer.stop()
        if self.stop_requested or self.current_index >= len(self.steps):
            self.running = False
            self.finished.emit(self.extracted_data)
            return
        step = self.steps[self.current_index]
        self.step_started.emit(self.current_index)
        if step.type == 'group':
            self._step_done()
            return
        if step.pre_wait_seconds > 0:
            self.pre_wait_remaining = step.pre_wait_seconds
            self.pre_wait_element_check = step.pre_wait_element
            self.pre_wait_xpath = step.pre_wait_xpath
            self.pre_wait_checking = False
            self.pre_wait_timer.start(200)
            return
        self._execute_action(step)

    def _execute_action(self, step):
        if step.type.startswith('desktop_'):
            self._execute_desktop_step(step)
            return
        if step.type == 'click':
            self._execute_click(step)
        elif step.type == 'input':
            self._execute_input(step)
        elif step.type == 'extract':
            self._execute_extract(step)
        elif step.type == 'wait':
            self._execute_wait(step)
        elif step.type == 'navigate':
            self._execute_navigate(step)
        elif step.type == 'js':
            self._execute_js(step)
        elif step.type == 'upload':
            self._execute_upload(step)
        elif step.type == 'python':
            self._execute_python(step)
        else:
            self._step_done()

    # ---------- 日期占位符 ----------
    def _replace_date_placeholders(self, text):
        def replacer(match):
            days = 0
            if match.group(1) is not None:
                days = int(match.group(2))
            base = self.simulated_date if self.simulated_date is not None else datetime.now().date()
            target = base - timedelta(days=days)
            return f"{target.month}/{target.day}/{target.year}"
        return re.sub(r'\{today(-(\d+))?\}', replacer, text)

    def _resolve_upload_path(self, path_expr):
        if not path_expr.startswith('{download:'):
            return self._replace_date_placeholders(path_expr)
        content = path_expr[len('{download:'):-1]
        history = self.download_history
        if not history:
            return None
        if content == 'latest' or content == '1':
            return history[-1]
        elif content.isdigit():
            idx = len(history) - int(content)
            if 0 <= idx < len(history):
                return history[idx]
            return None
        else:
            pattern = content.replace('*', '.*')
            for f in reversed(history):
                if fnmatch.fnmatch(os.path.basename(f), pattern):
                    return f
            return None

    # ---------- 上传文件 ----------
    def _execute_upload(self, step):
        step_num = self.current_index + 1
        self._log(f"[上传步骤{step_num}] 开始执行上传")

        raw_path = step.upload_file_path
        file_path = self._resolve_upload_path(raw_path)
        if not file_path or not os.path.exists(file_path):
            self._log(f"[上传步骤{step_num}] ❌ 文件不存在或无法解析: {raw_path}")
            self._step_done()
            return
        safe_file_path = file_path.replace('\\', '\\\\')
        self._log(f"[上传步骤{step_num}] 文件路径: {file_path}")

        selectors = [s for s in [step.selector.strip(), 'input[type="file"]', '.n-upload-file-input'] if s]
        self._log(f"[上传步骤{step_num}] 选择器列表: {selectors}")

        self._get_cdp_url(lambda ws_url: self._start_js_upload(ws_url, safe_file_path, selectors, step_num))
        QTimer.singleShot(2000, lambda: self._fallback_upload(step, file_path))

    # ---------- 获取正确的 CDP URL（匹配主页面） ----------
    def _get_cdp_url(self, callback):
        if not self.cdp_fetcher:
            self.cdp_fetcher = QWebEngineView()
            self.cdp_fetcher.setVisible(False)

        # 记录主 WebView 当前 URL，用于后续匹配 target
        self._cdp_main_url = self.webview.url().toString()

        try:
            self.cdp_fetcher.loadFinished.disconnect()
        except:
            pass
        self.cdp_fetcher.loadFinished.connect(lambda ok: self._on_cdp_json_loaded(ok, callback))
        self.cdp_fetcher.load(QUrl("http://127.0.0.1:9222/json"))

    def _on_cdp_json_loaded(self, ok, callback):
        try:
            self.cdp_fetcher.loadFinished.disconnect()
        except:
            pass
        if not ok:
            self._log("❌ 无法加载 http://127.0.0.1:9222/json")
            callback(None)
            return
        self.cdp_fetcher.page().toPlainText(lambda text: self._parse_cdp_json(text, callback))

    def _parse_cdp_json(self, text, callback):
        try:
            pages = json.loads(text)
            if not pages:
                callback(None)
                return

            main_url = self._cdp_main_url
            target_page = None

            # 1. 精确匹配主页面 URL
            for p in pages:
                if p.get('url') == main_url:
                    target_page = p
                    break

            # 2. 如果没有精确匹配的，取第一个非 about:blank 的页面
            if not target_page:
                for p in pages:
                    if p.get('url') and p.get('url') != 'about:blank':
                        target_page = p
                        break

            # 3. 最终回退
            if not target_page and pages:
                target_page = pages[0]

            ws_url = target_page.get('webSocketDebuggerUrl') if target_page else None
            callback(ws_url)
        except Exception as e:
            self._log(f"解析 CDP JSON 失败: {e}")
            callback(None)

    # ---------- 核心上传 JS（直接查找 + 后备点击） ----------
    def _start_js_upload(self, ws_url, file_path, selectors, step_num):
        if not ws_url:
            self._log(f"[上传步骤{step_num}] ❌ 无法获取 CDP URL")
            self._step_done()
            return

        self._log(f"[上传步骤{step_num}] 使用浏览器 JS 上传，CDP: {ws_url}")

        selectors_json = json.dumps(selectors)
        js_code = f"""
        (async function() {{
            window.rpa_upload_result = null;

            try {{
                let ws = new WebSocket('{ws_url}');
                ws.onerror = () => window.rpa_upload_result = 'websocket_error';

                await new Promise((resolve, reject) => {{
                    ws.onopen = async function() {{
                        let msgId = 0, pending = new Map();
                        function send(method, params) {{
                            let id = ++msgId;
                            return new Promise((res, rej) => {{
                                pending.set(id, {{resolve: res, reject: rej}});
                                ws.send(JSON.stringify({{id, method, params}}));
                            }});
                        }}
                        ws.onmessage = e => {{
                            let data = JSON.parse(e.data);
                            if (data.id && pending.has(data.id)) {{
                                let h = pending.get(data.id);
                                pending.delete(data.id);
                                if (data.error) h.reject(new Error(data.error.message));
                                else h.resolve(data.result);
                            }}
                        }};

                        try {{
                            await send('Runtime.enable');
                            await send('DOM.enable');
                            await send('Page.enable');

                            let doc = await send('DOM.getDocument');
                            let rootNodeId = doc.root.nodeId;
                            let nodeId = 0;
                            let selectors = {selectors_json};

                            async function findFileInput() {{
                                // 先尝试 CSS 选择器
                                for (let sel of selectors) {{
                                    if (!sel.startsWith('/') && !sel.startsWith('(')) {{
                                        try {{
                                            let q = await send('DOM.querySelector', {{nodeId: rootNodeId, selector: sel}});
                                            if (q.nodeId) return q.nodeId;
                                        }} catch(e) {{}}
                                    }}
                                }}
                                // 再尝试 XPath 选择器
                                for (let sel of selectors) {{
                                    if (sel.startsWith('/') || sel.startsWith('(')) {{
                                        try {{
                                            let obj = await send('Runtime.evaluate', {{
                                                expression: `document.evaluate(${{JSON.stringify(sel)}}, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue`,
                                                returnByValue: false
                                            }});
                                            if (obj.result && obj.result.type === 'object' && obj.result.subtype !== 'null') {{
                                                let nodeInfo = await send('DOM.requestNode', {{objectId: obj.result.objectId}});
                                                if (nodeInfo.nodeId) return nodeInfo.nodeId;
                                            }}
                                        }} catch(e) {{}}
                                    }}
                                }}
                                // 如果找不到，尝试点击触发区域后再次查找
                                let triggerSels = ['.n-upload-trigger', '.n-upload-dragger'];
                                for (let tsel of triggerSels) {{
                                    let tNode = await send('DOM.querySelector', {{nodeId: rootNodeId, selector: tsel}});
                                    if (tNode.nodeId) {{
                                        let boxModel = await send('DOM.getBoxModel', {{nodeId: tNode.nodeId}});
                                        if (boxModel && boxModel.model) {{
                                            let quad = boxModel.model.content;
                                            let x = (quad[0] + quad[2] + quad[4] + quad[6]) / 4;
                                            let y = (quad[1] + quad[3] + quad[5] + quad[7]) / 4;
                                            await send('Page.dispatchMouseEvent', {{type:'mousePressed', x, y, button:'left', clickCount:1}});
                                            await send('Page.dispatchMouseEvent', {{type:'mouseReleased', x, y, button:'left', clickCount:1}});
                                            await new Promise(r => setTimeout(r, 2000));
                                            // 再次查找
                                            for (let sel of selectors) {{
                                                if (!sel.startsWith('/') && !sel.startsWith('(')) {{
                                                    try {{
                                                        let q = await send('DOM.querySelector', {{nodeId: rootNodeId, selector: sel}});
                                                        if (q.nodeId) return q.nodeId;
                                                    }} catch(e) {{}}
                                                }}
                                            }}
                                        }}
                                    }}
                                }}
                                return 0;
                            }}

                            nodeId = await findFileInput();

                            if (!nodeId) {{
                                let diag = await send('Runtime.evaluate', {{
                                    expression: `JSON.stringify({{inputs: document.querySelectorAll('input[type="file"]').length, iframes: document.querySelectorAll('iframe').length, url: document.location.href}})`,
                                    returnByValue: true
                                }});
                                throw new Error('未找到文件上传元素。页面状态: ' + diag.result.value);
                            }}

                            await send('DOM.setFileInputFiles', {{nodeId: nodeId, files: ['{file_path}']}});

                            let cssSel = selectors.find(s => !s.startsWith('/') && !s.startsWith('(')) || 'input[type="file"]';
                            await send('Runtime.evaluate', {{
                                expression: `(function() {{
                                    let input = document.querySelector('${{cssSel}}');
                                    if (input) {{
                                        input.focus();
                                        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                        input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                    }}
                                }})()`,
                                returnByValue: true
                            }});

                            window.rpa_upload_result = 'success';
                            resolve();
                        }} catch(e) {{
                            window.rpa_upload_result = 'error: ' + e.message;
                            reject(e);
                        }}
                    }};
                }});
            }} catch(e) {{
                if (!window.rpa_upload_result) window.rpa_upload_result = 'unknown_error';
            }}
        }})();
        """

        self.webview.page().runJavaScript(js_code)
        self._upload_callback = lambda result: self._on_js_upload_result(result, step_num)
        self.upload_check_timer.start(300)
        QTimer.singleShot(20000, self._on_js_upload_timeout)

    def _on_upload_dialog_handled(self, success):
        if not success:
            self._log("备选上传：文件对话框未触发")
        self.upload_timeout_timer.stop()
        self._step_done()

    def _upload_via_choosefiles(self, file_path, step):
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt, QPoint
        main_win = self.webview.window()
        if hasattr(main_win, '_install_upload_interceptor'):
            main_win._install_upload_interceptor()
        # 模拟一次点击获得用户手势
        self.webview.setFocus()
        QTest.mouseClick(self.webview, Qt.MouseButton.LeftButton, Qt.KeyboardModifiers.NoModifier, QPoint(10, 10))
        # 点击文件输入框
        xpath = step.selector or 'input[type="file"]'
        js = f"""
        (function() {{
            let el = document.evaluate(`{xpath}`, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            if (!el) el = document.querySelector('input[type="file"]');
            if (el) el.click();
        }})();
        """
        self.webview.page().runJavaScript(js)
        self.upload_timeout_timer.start(5000)

    def _fallback_upload(self, step, file_path):
        if self._upload_callback is not None:  # CDP 还在执行
            self._upload_callback = None
            self.upload_check_timer.stop()
        self._upload_via_choosefiles(file_path, step)

    def _check_js_upload_result(self):
        self.webview.page().runJavaScript("window.rpa_upload_result", self._handle_js_result)

    def _handle_js_result(self, result):
        if result is not None and self._upload_callback:
            self.upload_check_timer.stop()
            cb = self._upload_callback
            self._upload_callback = None
            cb(result)

    def _on_js_upload_timeout(self):
        if self._upload_callback:
            self.upload_check_timer.stop()
            cb = self._upload_callback
            self._upload_callback = None
            cb("timeout")

    def _on_js_upload_result(self, result, step_num):
        if result == "success":
            self._log(f"[上传步骤{step_num}] 🎉 上传流程完成")
        else:
            self._log(f"[上传步骤{step_num}] ❌ 上传失败: {result}")
        self._step_done()

    # ---------- 预等待 ----------
    def _on_pre_wait_finished(self):
        if self.pre_wait_element_check and self.pre_wait_xpath:
            if self.pre_wait_checking:
                return
            self.pre_wait_timer.stop()
            self.pre_wait_checking = True
            xpath = self.pre_wait_xpath.replace('\\', '\\\\').replace('`', '\\`')
            js = f"""
            (function() {{
                let el = document.evaluate(`{xpath}`, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                return el !== null;
            }})();
            """
            self.webview.page().runJavaScript(js, self._on_pre_wait_element_check)
        else:
            self.pre_wait_remaining -= 0.2
            if self.pre_wait_remaining <= 0:
                self.pre_wait_timer.stop()
                self._execute_action(self.steps[self.current_index])

    def _on_pre_wait_element_check(self, found):
        self.pre_wait_checking = False
        if found:
            self.pre_wait_timer.stop()
            self._execute_action(self.steps[self.current_index])
        else:
            self.pre_wait_remaining -= 0.2
            if self.pre_wait_remaining <= 0:
                self.pre_wait_timer.stop()
                self._execute_action(self.steps[self.current_index])
            else:
                self.pre_wait_timer.start(200)

    # ---------- 等待 ----------
    def _execute_wait(self, step):
        delay = step.timeout if step.timeout > 0 else 1000
        self.timer.stop()
        self.wait_timer.start(delay)

    def _on_wait_finished(self):
        self._step_done()

    # ---------- 其他动作（保持不变） ----------
    def _execute_navigate(self, step):
        url = step.value.strip()
        if not url:
            self._log(f"步骤 {self.current_index+1} 打开网页失败：URL 为空")
            self._step_done()
            return
        if not url.startswith('http'):
            url = 'https://' + url
        self.webview.setUrl(QUrl(url))
        delay = step.timeout if step.timeout > 0 else 3000
        QTimer.singleShot(delay, self._step_done)

    def _execute_click(self, step):
        xpath = step.selector.replace('\\', '\\\\').replace('`', '\\`')
        js = f"""
        (function() {{
            let el = document.evaluate(`{xpath}`, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            if (el) {{
                el.focus();
                el.click();
                return true;
            }}
            return false;
        }})();
        """
        self.webview.page().runJavaScript(js, self._on_click_result)

    def _on_click_result(self, result):
        if not result:
            self._log(f"步骤 {self.current_index+1} 点击失败：元素未找到")
        self._step_done()

    def _execute_input(self, step):
        value = step.value
        value = self._replace_date_placeholders(value)
        if step.date_format:
            if step.use_today:
                dt = datetime.now() if self.simulated_date is None else datetime.combine(self.simulated_date, datetime.min.time())
            else:
                try:
                    dt = datetime.strptime(step.fixed_date, "%m/%d/%Y")
                except:
                    dt = datetime.now()
            formatted = dt.strftime(step.date_format.replace('dd', '%d').replace('MM', '%m').replace('yyyy', '%Y'))
            value = value.replace('{today}', formatted)
        xpath = step.selector.replace('\\', '\\\\').replace('`', '\\`')
        value = value.replace('\\', '\\\\').replace("'", "\\'")
        js = f"""
        (function() {{
            let el = document.evaluate(`{xpath}`, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            if (el) {{
                el.focus();
                el.value = '{value}';
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return true;
            }}
            return false;
        }})();
        """
        self.webview.page().runJavaScript(js, self._on_input_result)

    def _on_input_result(self, result):
        if not result:
            self._log(f"步骤 {self.current_index+1} 输入失败：元素未找到")
        self._step_done()

    def _execute_extract(self, step):
        xpath = step.selector.replace('\\', '\\\\').replace('`', '\\`')
        attr = step.extract_attr if step.extract_attr else 'text'
        js = f"""
        (function() {{
            let el = document.evaluate(`{xpath}`, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            if (el) {{
                let val;
                if ('{attr}' === 'text') {{
                    val = el.innerText || el.textContent || '';
                }} else {{
                    val = el.getAttribute('{attr}') || '';
                }}
                return val.trim();
            }}
            return null;
        }})();
        """
        self.webview.page().runJavaScript(js, lambda result, s=step: self._on_extract_result(result, s))

    def _on_extract_result(self, result, step):
        if result is None:
            self._log(f"步骤 {self.current_index+1} 提取失败：元素未找到")
        else:
            self.extracted_data.append({
                'field': step.field_name,
                'value': result,
                'selector': step.selector
            })
        self._step_done()

    def _execute_js(self, step):
        js = step.js_code
        if not js.strip():
            self._log(f"步骤 {self.current_index+1} JS 代码为空")
            self._step_done()
            return
        self.webview.page().runJavaScript(js, lambda result, s=step: self._on_js_result(result, s))

    def _on_js_result(self, result, s):
        if s.field_name:
            self.extracted_data.append({
                'field': s.field_name,
                'value': str(result) if result is not None else '',
                'selector': '自定义JS'
            })
        self._step_done()

    def _execute_python(self, step):
        code = step.python_code
        if not code.strip():
            self._log(f"步骤 {self.current_index+1} Python 代码为空")
            self._step_done()
            return

        # 替换 {today} / {today-N} 占位符（格式：20260721）
        def replacer(match):
            days = 0
            if match.group(1) is not None:
                days = int(match.group(2))
            base = self.simulated_date if self.simulated_date is not None else datetime.now().date()
            target = base - timedelta(days=days)
            return target.strftime("%Y%m%d")
        code = re.sub(r'\{today(-(\d+))?\}', replacer, code)

        exec_err = []
        def run_user_code():
            try:
                import os, shutil, glob
                import datetime as dt
                from send2trash import send2trash
                from pywinauto import Desktop
                
                namespace = {
    'os': os, 'shutil': shutil, 'glob': glob, 'datetime': dt,
    'send2trash': send2trash,
    'window': getattr(self, 'current_desktop_window', None),
    'desktop': Desktop(backend=self.desktop_executor.backend) if hasattr(self, 'desktop_executor') else None
}
                exec(code, namespace)
            except Exception as e:
                exec_err.append(str(e))

        t = threading.Thread(target=run_user_code, daemon=True)
        t.start()
        timeout_sec = 10
        t.join(timeout=timeout_sec)

        if t.is_alive():
            self._log(f"步骤 {self.current_index+1} Python脚本执行超时（{timeout_sec}s）")
        elif exec_err:
            self._log(f"步骤 {self.current_index+1} Python 执行错误: {exec_err[0]}")

        self._step_done()

    def _execute_desktop_step(self, step):
        # 延迟导入桌面执行器，避免启动时改变 COM 模式
        if self.desktop_executor is None:
            from desktop_executor import DesktopExecutor
            self.desktop_executor = DesktopExecutor()
        try:
            # 如果之前连接过窗口，且当前步骤不是窗口连接本身，先重新连接以同步窗口状态
            if step.type != 'desktop_focus' and self.last_desktop_target:
                self.desktop_executor.connect(target=self.last_desktop_target)

            if step.type == 'desktop_focus':
                # 连接窗口，value 存储窗口标题或进程名，也可从 selector 获取额外属性
                title = step.value.strip() if step.value else None
                self.desktop_executor.connect(target=title)
                self.last_desktop_target = title
            elif step.type == 'desktop_click':
                self.desktop_executor.click(step.selector)
            elif step.type == 'desktop_input':
                # 支持日期占位符，与 Web 输入一致
                text = self._replace_date_placeholders(step.value)
                self.desktop_executor.input(step.selector, text)
            elif step.type == 'desktop_wait':
                timeout_ms = step.timeout if step.timeout > 0 else 5000
                if not self.desktop_executor.wait(step.selector, timeout_ms):
                    self._log(f"步骤 {self.current_index+1} 桌面等待超时：{step.selector}")
            elif step.type == 'desktop_extract':
                attr = step.extract_attr if step.extract_attr else 'name'
                value = self.desktop_executor.extract(step.selector, attr)
                if value is not None:
                    self.extracted_data.append({
                        'field': step.field_name,
                        'value': value,
                        'selector': step.selector
                    })
                else:
                    self._log(f"步骤 {self.current_index+1} 桌面提取失败：未获取到值")
            elif step.type == 'desktop_python':
                # 将当前窗口和桌面对象注入到 Python 执行环境中
                self.current_desktop_window = self.desktop_executor.current_window
                self._execute_python(step)
                self.current_desktop_window = None
            else:
                self._log(f"未知桌面步骤类型: {step.type}")
        except Exception as e:
            self._log(f"步骤 {self.current_index+1} 桌面操作错误: {str(e)}")
            print(f"[DEBUG] 桌面步骤失败: type={step.type}, selector={step.selector}, value={step.value}, error={str(e)}")
        finally:
            self._step_done()

    def _step_done(self):
        self.step_finished.emit(self.current_index)
        self.current_index += 1
        if self.current_index < len(self.steps):
            self.timer.start(self.base_delay)
        else:
            self.running = False
            self.finished.emit(self.extracted_data)