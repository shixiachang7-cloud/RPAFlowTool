# run_engine.py
import re, os, fnmatch, json, time
from datetime import datetime, timedelta
from PySide6.QtCore import QObject, Signal, QTimer, QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from flow_step import FlowStep


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
        self.excel_workbooks = {}  # key: normcase(abspath(path)) -> openpyxl Workbook

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

        # 上传相关
        self.cdp_fetcher = None
        self._cdp_main_url = ""                # 用于匹配正确的主页面 target
        self.upload_check_timer = QTimer(self)
        self.upload_check_timer.timeout.connect(self._check_js_upload_result)
        self._upload_callback = None

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
        elif step.type == 'excel_file':
            self._execute_excel_file(step)
        elif step.type == 'excel_sheet':
            self._execute_excel_sheet(step)
        elif step.type == 'excel_table':
            self._execute_excel_table(step)
        elif step.type == 'excel_cell':
            self._execute_excel_cell(step)
        elif step.type == 'excel_rowcol':
            self._execute_excel_rowcol(step)
        elif step.type == 'excel_style':
            self._execute_excel_style(step)
        elif step.type == 'excel_formula':
            self._execute_excel_formula(step)
        elif step.type == 'excel_image':
            self._execute_excel_image(step)
        elif step.type == 'excel_macro':
            self._execute_excel_macro(step)
        elif step.type == 'email_send':
            self._execute_email_send(step)
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
        return re.sub(r'\{today(?:-(\d+))?\}', replacer, text)

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

                            // ===== 查找函数（主文档 + iframe 简单穿透） =====
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
                                // 简单搜索 iframe（一层深度）
                                let iframes = await send('DOM.querySelectorAll', {{nodeId: rootNodeId, selector: 'iframe'}});
                                for (let iframeId of iframes.nodeIds) {{
                                    try {{
                                        // 获取 iframe 的 contentDocument 节点 ID（通过 DOM.resolveNode 获取对象，再请求 document）
                                        let iframeObj = await send('DOM.resolveNode', {{nodeId: iframeId}});
                                        // 这里简化处理：直接使用 Runtime.evaluate 在全局查找（可能跨域限制）
                                        // 在实际测试中，如果 iframe 同源，下面的方法有效
                                        let findInIframe = `
                                            (function() {{
                                                let iframe = document.querySelector('iframe');
                                                if (iframe && iframe.contentDocument) {{
                                                    let doc = iframe.contentDocument;
                                                    let sel = ${{JSON.stringify(selectors)}};
                                                    for (let s of sel) {{
                                                        let el = s.startsWith('/') || s.startsWith('(') ?
                                                            doc.evaluate(s, doc, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue :
                                                            doc.querySelector(s);
                                                        if (el && el.type === 'file') return el;
                                                    }}
                                                }}
                                                return null;
                                            }})()
                                        `;
                                        let iframeObj2 = await send('Runtime.evaluate', {{expression: findInIframe, returnByValue: false}});
                                        if (iframeObj2.result && iframeObj2.result.type === 'object' && iframeObj2.result.subtype !== 'null') {{
                                            let nodeInfo = await send('DOM.requestNode', {{objectId: iframeObj2.result.objectId}});
                                            if (nodeInfo.nodeId) return nodeInfo.nodeId;
                                        }}
                                    }} catch(e) {{}}
                                }}
                                return 0;
                            }}

                            // 1. 第一次尝试直接查找
                            nodeId = await findFileInput();

                            // 2. 如果找不到，尝试点击触发区域（可信点击）后再查找
                            if (!nodeId) {{
                                // 可能的触发区域选择器
                                let triggerSels = ['.n-upload-trigger', '.n-upload-dragger'];
                                for (let tsel of triggerSels) {{
                                    let tNode = await send('DOM.querySelector', {{nodeId: rootNodeId, selector: tsel}});
                                    if (tNode.nodeId) {{
                                        let boxModel = await send('DOM.getBoxModel', {{nodeId: tNode.nodeId}});
                                        let quad = boxModel.model.content;
                                        if (quad.length >= 8) {{
                                            let x = (quad[0] + quad[2] + quad[4] + quad[6]) / 4;
                                            let y = (quad[1] + quad[3] + quad[5] + quad[7]) / 4;

                                            // 可信鼠标事件
                                            await send('Page.dispatchMouseEvent', {{type:'mousePressed', x, y, button:'left', clickCount:1}});
                                            await send('Page.dispatchMouseEvent', {{type:'mouseReleased', x, y, button:'left', clickCount:1}});

                                            // 等待 Vue 生成 input
                                            await new Promise(r => setTimeout(r, 2000));

                                            nodeId = await findFileInput();
                                            if (nodeId) break;
                                        }}
                                    }}
                                }}
                            }}

                            if (!nodeId) {{
                                // 输出诊断信息
                                let diag = await send('Runtime.evaluate', {{
                                    expression: `JSON.stringify({{inputs: document.querySelectorAll('input[type="file"]').length, iframes: document.querySelectorAll('iframe').length, url: document.location.href}})`,
                                    returnByValue: true
                                }});
                                throw new Error('未找到文件上传元素。页面状态: ' + diag.result.value);
                            }}

                            // 3. 注入文件
                            await send('DOM.setFileInputFiles', {{nodeId: nodeId, files: ['{file_path}']}});

                            // 4. 触发 change / input 事件
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

    # ---------- Excel 公共辅助 ----------
    def _get_excel_wb(self, step):
        import os
        path = self._resolve_upload_path(step.excel_file_path)
        if not path:
            self._log(f"步骤 {self.current_index+1} Excel错误：文件路径为空")
            return None, None
        key = os.path.normcase(os.path.abspath(path))
        wb = self.excel_workbooks.get(key)
        if wb is None:
            self._log(f"步骤 {self.current_index+1} Excel错误：工作簿未打开，请先执行「打开/新建」步骤 ({path})")
        return wb, key

    # ---------- excel_file ----------
    def _execute_excel_file(self, step):
        import os
        import Files
        action = step.excel_action
        path = self._resolve_upload_path(step.excel_file_path)
        try:
            if action == 'new':
                wb = Files.new_workbook()
                key = os.path.normcase(os.path.abspath(path)) if path else str(id(wb))
                self.excel_workbooks[key] = wb
                self._log(f"步骤 {self.current_index+1} 新建工作簿: {path}")
            elif action == 'open':
                wb = Files.open_workbook(path)
                key = os.path.normcase(os.path.abspath(path))
                self.excel_workbooks[key] = wb
                self._log(f"步骤 {self.current_index+1} 打开工作簿: {path}")
            elif action in ('save', 'save_as', 'close'):
                wb, key = self._get_excel_wb(step)
                if wb is None:
                    self._step_done(); return
                if action == 'save':
                    Files.save_workbook(wb, path)
                    self._log(f"步骤 {self.current_index+1} 保存工作簿: {path}")
                elif action == 'save_as':
                    save_path = self._resolve_upload_path(step.excel_save_path)
                    Files.save_workbook(wb, save_path)
                    self._log(f"步骤 {self.current_index+1} 另存为: {save_path}")
                elif action == 'close':
                    Files.close_workbook(wb)
                    del self.excel_workbooks[key]
                    self._log(f"步骤 {self.current_index+1} 关闭工作簿: {path}")
            else:
                self._log(f"步骤 {self.current_index+1} 未知excel_file动作: {action}")
        except Exception as e:
            self._log(f"步骤 {self.current_index+1} excel_file错误: {e}")
        self._step_done()

    # ---------- excel_sheet ----------
    def _execute_excel_sheet(self, step):
        import Files
        wb, _ = self._get_excel_wb(step)
        if wb is None:
            self._step_done(); return
        action = step.excel_action
        try:
            if action == 'add':
                Files.add_sheet(wb, step.excel_new_sheet_name)
            elif action == 'delete':
                Files.delete_sheet(wb, step.excel_sheet_name)
            elif action == 'rename':
                Files.rename_sheet(wb, step.excel_sheet_name, step.excel_new_sheet_name)
            elif action == 'switch':
                wb.active = wb[step.excel_sheet_name]
            else:
                self._log(f"步骤 {self.current_index+1} 未知excel_sheet动作: {action}")
        except Exception as e:
            self._log(f"步骤 {self.current_index+1} excel_sheet错误: {e}")
        self._step_done()

    # ---------- excel_table ----------
    def _execute_excel_table(self, step):
        import json as _json
        import Files
        wb, _ = self._get_excel_wb(step)
        if wb is None:
            self._step_done(); return
        ws = Files.get_sheet(wb, step.excel_sheet_name)
        action = step.excel_action
        try:
            if action == 'read_all':
                data = Files.read_all(ws)
                self.extracted_data.append({'field': step.field_name, 'value': data, 'selector': f"{step.excel_sheet_name}!全表"})
            elif action == 'write_all':
                data = _json.loads(step.excel_range_data)
                Files.write_all(ws, data, step.excel_cell_ref or 'A1')
            elif action == 'clear_all':
                Files.clear_all(ws)
            else:
                self._log(f"步骤 {self.current_index+1} 未知excel_table动作: {action}")
        except Exception as e:
            self._log(f"步骤 {self.current_index+1} excel_table错误: {e}")
        self._step_done()

    # ---------- excel_cell ----------
    def _execute_excel_cell(self, step):
        import json as _json
        import Files
        wb, _ = self._get_excel_wb(step)
        if wb is None:
            self._step_done(); return
        ws = Files.get_sheet(wb, step.excel_sheet_name)
        action = step.excel_action
        ref = step.excel_cell_ref
        try:
            if action == 'read_cell':
                val = Files.read_cell(ws, ref)
                self.extracted_data.append({'field': step.field_name, 'value': val, 'selector': f"{step.excel_sheet_name}!{ref}"})
            elif action == 'write_cell':
                Files.write_cell(ws, ref, step.value)
            elif action == 'read_range':
                val = Files.read_range(ws, ref)
                self.extracted_data.append({'field': step.field_name, 'value': val, 'selector': f"{step.excel_sheet_name}!{ref}"})
            elif action == 'write_range':
                data = _json.loads(step.excel_range_data)
                Files.write_range(ws, ref, data)
            else:
                self._log(f"步骤 {self.current_index+1} 未知excel_cell动作: {action}")
        except Exception as e:
            self._log(f"步骤 {self.current_index+1} excel_cell错误: {e}")
        self._step_done()

    # ---------- excel_rowcol ----------
    def _execute_excel_rowcol(self, step):
        import Files
        wb, _ = self._get_excel_wb(step)
        if wb is None:
            self._step_done(); return
        ws = Files.get_sheet(wb, step.excel_sheet_name)
        action = step.excel_action
        ref = step.excel_row_col_ref
        count = step.excel_count or 1
        try:
            if action == 'insert_row':
                Files.insert_rows(ws, int(ref), count)
            elif action == 'delete_row':
                Files.delete_rows(ws, int(ref), count)
            elif action == 'insert_col':
                Files.insert_cols(ws, ref, count)
            elif action == 'delete_col':
                Files.delete_cols(ws, ref, count)
            else:
                self._log(f"步骤 {self.current_index+1} 未知excel_rowcol动作: {action}")
        except Exception as e:
            self._log(f"步骤 {self.current_index+1} excel_rowcol错误: {e}")
        self._step_done()

    # ---------- excel_style ----------
    def _execute_excel_style(self, step):
        import Files
        wb, _ = self._get_excel_wb(step)
        if wb is None:
            self._step_done(); return
        ws = Files.get_sheet(wb, step.excel_sheet_name)
        action = step.excel_action
        ref = step.excel_cell_ref
        try:
            if action == 'set_font':
                Files.set_font(ws, ref,
                    name=step.excel_font_name or None,
                    size=step.excel_font_size or None,
                    bold=step.excel_font_bold if step.excel_font_bold else None,
                    color=step.excel_font_color or None)
            elif action == 'set_fill_color':
                Files.set_fill_color(ws, ref, step.excel_fill_color)
            elif action == 'set_border':
                Files.set_border(ws, ref, step.excel_border_style or 'thin')
            else:
                self._log(f"步骤 {self.current_index+1} 未知excel_style动作: {action}")
        except Exception as e:
            self._log(f"步骤 {self.current_index+1} excel_style错误: {e}")
        self._step_done()

    # ---------- excel_formula ----------
    def _execute_excel_formula(self, step):
        import Files
        wb, _ = self._get_excel_wb(step)
        if wb is None:
            self._step_done(); return
        ws = Files.get_sheet(wb, step.excel_sheet_name)
        action = step.excel_action
        ref = step.excel_cell_ref
        try:
            if action == 'write_formula':
                Files.write_formula(ws, ref, step.excel_formula_text)
            elif action == 'read_value':
                import Application
                path = self._resolve_upload_path(step.excel_file_path)
                val = Application.read_calculated_value(path, step.excel_sheet_name, ref)
                self.extracted_data.append({'field': step.field_name, 'value': val, 'selector': f"{step.excel_sheet_name}!{ref}"})
            else:
                self._log(f"步骤 {self.current_index+1} 未知excel_formula动作: {action}")
        except Exception as e:
            self._log(f"步骤 {self.current_index+1} excel_formula错误: {e}")
        self._step_done()

    # ---------- excel_image ----------
    def _execute_excel_image(self, step):
        import Files
        wb, _ = self._get_excel_wb(step)
        if wb is None:
            self._step_done(); return
        ws = Files.get_sheet(wb, step.excel_sheet_name)
        action = step.excel_action
        try:
            if action == 'insert_image':
                img_path = self._resolve_upload_path(step.excel_image_path)
                Files.insert_image(ws, step.excel_cell_ref, img_path, step.excel_image_width, step.excel_image_height)
            elif action == 'clear_images':
                Files.clear_images(ws)
            else:
                self._log(f"步骤 {self.current_index+1} 未知excel_image动作: {action}")
        except Exception as e:
            self._log(f"步骤 {self.current_index+1} excel_image错误: {e}")
        self._step_done()

    # ---------- excel_macro ----------
    def _parse_macro_args(self, args_text):
        args = []
        for part in (args_text or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                args.append(int(part))
                continue
            except ValueError:
                pass
            try:
                args.append(float(part))
                continue
            except ValueError:
                pass
            args.append(part)
        return args

    def _execute_excel_macro(self, step):
        import Application
        action = step.excel_action
        try:
            if action == 'run_macro':
                path = self._resolve_upload_path(step.excel_file_path)
                args = self._parse_macro_args(step.excel_macro_args)
                Application.run_macro(path, step.excel_macro_name, args, visible=step.excel_macro_visible)
            else:
                self._log(f"步骤 {self.current_index+1} 未知excel_macro动作: {action}")
        except Exception as e:
            self._log(f"步骤 {self.current_index+1} excel_macro错误: {e}")
        self._step_done()

    def _execute_email_send(self, step):
        import Email
        try:
            to_list = [a.strip() for a in step.email_to.split(',') if a.strip()]
            cc_list = [a.strip() for a in step.email_cc.split(',') if a.strip()]
            bcc_list = [a.strip() for a in step.email_bcc.split(',') if a.strip()]

            subject = self._replace_date_placeholders(step.email_subject)
            body = self._replace_date_placeholders(step.email_body)

            attachment_path = None
            if step.email_attachment_path:
                attachment_path = self._resolve_upload_path(step.email_attachment_path)
                if attachment_path and not Email.os.path.exists(attachment_path):
                    self._log(f"步骤 {self.current_index+1} 邮件警告: 附件未找到 {step.email_attachment_path}")
                    attachment_path = None

            Email.send_email(
                smtp_server=step.email_smtp_server,
                smtp_port=step.email_smtp_port,
                use_ssl=step.email_use_ssl,
                account=step.email_account,
                password=step.email_password,
                to_addresses=to_list,
                subject=subject,
                body=body,
                cc_addresses=cc_list,
                bcc_addresses=bcc_list,
                attachment_path=attachment_path
            )
        except Exception as e:
            self._log(f"步骤 {self.current_index+1} 邮件错误: {e}")
        self._step_done()

    def _step_done(self):
        self.step_finished.emit(self.current_index)
        self.current_index += 1
        if self.current_index < len(self.steps):
            self.timer.start(self.base_delay)
        else:
            self.running = False
            self.finished.emit(self.extracted_data)