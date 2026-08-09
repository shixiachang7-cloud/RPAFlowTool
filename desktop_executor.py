# desktop_executor.py
import time
import re as regex
from pywinauto import Application, Desktop
from pywinauto.findbestmatch import MatchError
from pywinauto.keyboard import send_keys

# 属性映射：用户输入 -> pywinauto child_window 参数名
ATTR_MAP = {
    'automationid': 'auto_id',
    'controltype': 'control_type',
    'classname': 'class_name',
    'name': 'title',          # UIA "Name" -> "title"
}

class DesktopExecutor:
    """桌面操作执行器，优先使用 child_window，后备手动遍历"""

    def __init__(self, backend='uia'):
        self.backend = backend
        self.current_window = None

    def _parse_selector(self, selector_str):
        if not selector_str:
            return {}, 0
        criteria = {}
        found_index = 0
        for part in selector_str.split(';'):
            part = part.strip()
            if '=' in part:
                key, _, value = part.partition('=')
                key = key.strip().lower()
                value = value.strip()
                if key == 'foundindex':
                    try:
                        found_index = int(value)
                    except ValueError:
                        pass
                else:
                    mapped_key = ATTR_MAP.get(key, key)
                    if mapped_key == 'control_type':
                        value = value.split('(')[0]
                    criteria[mapped_key] = value
        return criteria, found_index

    def _get_control_child(self, criteria, found_index=0):
        """使用 child_window 查找，默认 found_index=0 避免多匹配异常"""
        try:
            return self.current_window.child_window(**criteria, found_index=found_index)
        except MatchError:
            return None

    def _get_control_manual(self, criteria, found_index=0):
        """手动遍历后代"""
        if not self.current_window:
            return None
        try:
            descendants = self.current_window.descendants()
        except Exception:
            return None
        matches = []
        for ctrl in descendants:
            try:
                ok = True
                for attr, val in criteria.items():
                    actual = getattr(ctrl.element_info, attr, None)
                    if actual is None:
                        ok = False
                        break
                    if attr == 'title' and val.lower() not in actual.lower():
                        ok = False
                        break
                    elif isinstance(actual, str) and val.lower() != actual.lower():
                        ok = False
                        break
                    elif not isinstance(actual, str) and val != actual:
                        ok = False
                        break
                if ok:
                    matches.append(ctrl)
            except Exception:
                continue
        if matches:
            if 0 <= found_index < len(matches):
                return matches[found_index]
            return matches[0]
        return None

    def _get_control(self, selector, allow_global=False, wait_seconds=5):
        if not self.current_window and not allow_global:
            raise RuntimeError("尚未连接任何窗口")
        criteria, found_index = self._parse_selector(selector)

        ctrl = None
        if self.current_window:
            ctrl = self._get_control_child(criteria, found_index)
            if ctrl is None:
                ctrl = self._get_control_manual(criteria, found_index)

        if ctrl is None and allow_global:
            # 全局查找：遍历所有顶层窗口
            try:
                top_windows = Desktop(backend=self.backend).windows()
            except Exception:
                top_windows = []
            for win in top_windows:
                # 保存原窗口，临时切换
                old_win = self.current_window
                self.current_window = win
                ctrl = self._get_control_child(criteria, found_index)
                if ctrl is None:
                    ctrl = self._get_control_manual(criteria, found_index)
                self.current_window = old_win
                if ctrl is not None:
                    break

        if ctrl is None:
            raise RuntimeError(f"未找到匹配控件: {selector}")

        # 等待就绪
        try:
            ctrl.wait('visible', timeout=wait_seconds)
            ctrl.wait('enabled', timeout=wait_seconds)
        except Exception:
            pass
        return ctrl

    def connect(self, target=None):
        self.current_window = None
        if not target:
            try:
                self.current_window = Desktop(backend=self.backend).active()
                return
            except Exception as e:
                raise RuntimeError(f"无法获取活动窗口: {e}")

        if target.lower().endswith('.exe'):
            try:
                app = Application(backend=self.backend).connect(process=target)
                self.current_window = app.top_window()
                self.current_window.set_focus()
                return
            except Exception as e:
                raise RuntimeError(f"通过进程 '{target}' 连接失败: {e}")

        try:
            top_windows = Desktop(backend=self.backend).windows()
        except Exception as e:
            raise RuntimeError(f"无法枚举窗口: {e}")

        matched_title = None
        for win in top_windows:
            try:
                if win.window_text() == target:
                    matched_title = win.window_text()
                    break
            except:
                continue
        if not matched_title:
            for win in top_windows:
                try:
                    if regex.search(target, win.window_text()):
                        matched_title = win.window_text()
                        break
                except:
                    continue
        if not matched_title:
            titles = [w.window_text() for w in top_windows if w.window_text()]
            raise RuntimeError(f"未找到匹配 '{target}' 的窗口。可见窗口: {titles[:10]}")

        self.current_window = Desktop(backend=self.backend).window(title=matched_title)
        self.current_window.set_focus()

    def click(self, selector):
        if not self.current_window:
            raise RuntimeError("请先连接窗口")
        self.current_window.set_focus()
        ctrl = self._get_control(selector, allow_global=True)

        # 方法1：直接调用 Invoke（不移动鼠标，最可靠）
        try:
            ctrl.invoke()
            print(f"控件 {selector} 已点击（Invoke）")
            return
        except Exception:
            pass

        # 方法2：click_input()（通常有效，少数情况会移动鼠标但不会受用户干扰）
        try:
            ctrl.click_input()
            print(f"控件 {selector} 已点击（click_input）")
            return
        except Exception:
            pass

        # 方法3：绝对坐标的 SendInput 点击（完全不受物理鼠标影响）
        try:
            rect = ctrl.rectangle()
            x = rect.left + rect.width() // 2
            y = rect.top + rect.height() // 2
            ctrl.click_input(coords=(x, y), absolute=True)
            print(f"控件 {selector} 已点击（SendInput）")
        except Exception as e:
            raise RuntimeError(f"无法点击控件: {selector}") from e

    def input(self, selector, text):
        if not self.current_window:
            raise RuntimeError("请先连接窗口")
        self.current_window.set_focus()
        ctrl = self._get_control(selector, allow_global=False)
        try:
            ctrl.set_text(text)
        except Exception:
            ctrl.click_input()
            send_keys(text)

    def wait(self, selector, timeout=5000):
        try:
            self._get_control(selector, allow_global=True, wait_seconds=timeout / 1000.0)
            return True
        except RuntimeError:
            return False

    def extract(self, selector, attribute='name'):
        control = self._get_control(selector, allow_global=True)
        if attribute in ('name', 'text', 'value'):
            return control.window_text()
        try:
            return control.element_info.properties.get(attribute, '')
        except:
            return ''