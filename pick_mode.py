# pick_mode.py
from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView

PICK_ACTIVATE_JS = r"""
(function() {
    if (window.__rpa_pick_active) return;
    window.__rpa_pick_active = true;

    let overlay = document.createElement('div');
    overlay.id = '__rpa_highlight';
    overlay.style.position = 'fixed';
    overlay.style.pointerEvents = 'none';
    overlay.style.border = '2px solid #ff4444';
    overlay.style.backgroundColor = 'rgba(255,68,68,0.15)';
    overlay.style.zIndex = '2147483647';
    overlay.style.transition = 'all 0.05s ease';
    overlay.style.display = 'none';
    document.body.appendChild(overlay);

    function getXPath(element) {
        if (element === document.body) return '/html/body';
        if (element.id) return '//*[@id="' + element.id + '"]';
        let index = 1;
        let siblings = element.parentNode.childNodes;
        for (let i = 0; i < siblings.length; i++) {
            let sibling = siblings[i];
            if (sibling === element)
                return getXPath(element.parentNode) + '/' + element.tagName.toLowerCase() + '[' + index + ']';
            if (sibling.nodeType === 1 && sibling.tagName === element.tagName)
                index++;
        }
        return '';
    }

    function deduceType(el, tag) {
        if (tag === 'input' || tag === 'textarea' || tag === 'select') return 'input';
        if (tag === 'a' || tag === 'button' || el.getAttribute('role') === 'button' ||
            el.onclick || el.getAttribute('onclick') || el.getAttribute('href'))
            return 'click';
        if (el.onclick || el.getAttribute('onclick')) return 'click';
        return 'extract';
    }

    function notifyPython(xpath, text, tag, nodeType, isSingle) {
        if (typeof qt !== 'undefined' && qt.webChannelTransport) {
            qt.webChannelTransport.send(JSON.stringify({
                type: 6,
                object: 'bridge',
                method: 'pickElement',
                args: [xpath, text, tag, nodeType, !!isSingle],
                id: Date.now()
            }));
        }
    }

    let lastClick = { time: 0, xpath: '' };
    window.__rpa_clickHandler = function(e) {
        let target = e.target;
        if (!target || target === overlay) return;

        let now = Date.now();
        let xpath = getXPath(target);
        if (now - lastClick.time < 500 && lastClick.xpath === xpath) return;
        lastClick = { time: now, xpath: xpath };

        let text = '';
        let tag = target.tagName.toLowerCase();
        if (tag === 'input' || tag === 'textarea') {
            text = target.value || target.placeholder || '';
        } else {
            text = target.innerText ? target.innerText.trim().substring(0, 200) : '';
        }

        let nodeType = deduceType(target, tag);
        notifyPython(xpath, text, tag, nodeType, window.__rpa_singlePick);
    };

    window.__rpa_hoverHandler = function(e) {
        let target = e.target;
        if (!target || target === document.body || target === overlay) {
            overlay.style.display = 'none';
            return;
        }
        let rect = target.getBoundingClientRect();
        overlay.style.left = rect.left + 'px';
        overlay.style.top = rect.top + 'px';
        overlay.style.width = rect.width + 'px';
        overlay.style.height = rect.height + 'px';
        overlay.style.display = 'block';
    };

    document.addEventListener('mousemove', window.__rpa_hoverHandler, true);
    document.addEventListener('click', window.__rpa_clickHandler, true);
    console.log('RPA: 录制模式已激活');
})();
"""

PICK_DEACTIVATE_JS = r"""
(function() {
    let overlay = document.getElementById('__rpa_highlight');
    if (overlay) overlay.remove();
    document.removeEventListener('mousemove', window.__rpa_hoverHandler, true);
    document.removeEventListener('click', window.__rpa_clickHandler, true);
    delete window.__rpa_hoverHandler;
    delete window.__rpa_clickHandler;
    window.__rpa_pick_active = false;
    console.log('RPA: 录制模式已停用');
})();
"""

SINGLE_PICK_ACTIVATE_JS = r"""
(function() {
    if (window.__rpa_pick_active) {
        console.warn('RPA: 全局录制模式已激活，单次拾取不可用');
        return;
    }
    if (window.__rpa_singlePick) return;
    window.__rpa_singlePick = true;

    if (!document.getElementById('__rpa_highlight')) {
        let overlay = document.createElement('div');
        overlay.id = '__rpa_highlight';
        overlay.style.position = 'fixed';
        overlay.style.pointerEvents = 'none';
        overlay.style.border = '2px solid #ff4444';
        overlay.style.backgroundColor = 'rgba(255,68,68,0.15)';
        overlay.style.zIndex = '2147483647';
        overlay.style.transition = 'all 0.05s ease';
        overlay.style.display = 'none';
        document.body.appendChild(overlay);
    }

    function getXPath(element) {
        if (element === document.body) return '/html/body';
        if (element.id) return '//*[@id="' + element.id + '"]';
        let index = 1;
        let siblings = element.parentNode.childNodes;
        for (let i = 0; i < siblings.length; i++) {
            let sibling = siblings[i];
            if (sibling === element)
                return getXPath(element.parentNode) + '/' + element.tagName.toLowerCase() + '[' + index + ']';
            if (sibling.nodeType === 1 && sibling.tagName === element.tagName)
                index++;
        }
        return '';
    }

    window.__rpa_singleHoverHandler = function(e) {
        let target = e.target;
        let overlay = document.getElementById('__rpa_highlight');
        if (!target || target === document.body || target === overlay) {
            overlay.style.display = 'none';
            return;
        }
        let rect = target.getBoundingClientRect();
        overlay.style.left = rect.left + 'px';
        overlay.style.top = rect.top + 'px';
        overlay.style.width = rect.width + 'px';
        overlay.style.height = rect.height + 'px';
        overlay.style.display = 'block';
    };

    window.__rpa_singleClickHandler = function(e) {
        e.preventDefault();
        e.stopPropagation();
        let target = e.target;
        if (!target || target === document.getElementById('__rpa_highlight')) return;

        let xpath = getXPath(target);
        if (typeof qt !== 'undefined' && qt.webChannelTransport) {
            qt.webChannelTransport.send(JSON.stringify({
                type: 6,
                object: 'bridge',
                method: 'singlePickElement',
                args: [xpath],
                id: Date.now()
            }));
        }

        // 自动移除单次拾取
        window.__rpa_singlePick = false;
        document.removeEventListener('click', window.__rpa_singleClickHandler, true);
        document.removeEventListener('mousemove', window.__rpa_singleHoverHandler, true);
        delete window.__rpa_singleClickHandler;
        delete window.__rpa_singleHoverHandler;
        let overlay = document.getElementById('__rpa_highlight');
        if (overlay) overlay.remove();
    };

    document.addEventListener('mousemove', window.__rpa_singleHoverHandler, true);
    document.addEventListener('click', window.__rpa_singleClickHandler, true);
    console.log('RPA: 单次拾取模式已激活');
})();
"""

SINGLE_PICK_DEACTIVATE_JS = r"""
(function() {
    document.removeEventListener('click', window.__rpa_singleClickHandler, true);
    document.removeEventListener('mousemove', window.__rpa_singleHoverHandler, true);
    delete window.__rpa_singleClickHandler;
    delete window.__rpa_singleHoverHandler;
    let overlay = document.getElementById('__rpa_highlight');
    if (overlay) overlay.remove();
    window.__rpa_singlePick = false;
    console.log('RPA: 单次拾取已停用');
})();
"""


class PickBridge(QObject):
    element_picked = Signal(str, str, str, str)    # xpath, text, tag, node_type
    single_picked = Signal(str)

    @Slot(str, str, str, str, bool)
    def pickElement(self, xpath, text, tag_name, node_type, is_single):
        if is_single:
            self.single_picked.emit(xpath)
        else:
            self.element_picked.emit(xpath, text, tag_name, node_type)

    @Slot(str)
    def singlePickElement(self, xpath):
        self.single_picked.emit(xpath)


class PickModeManager:
    def __init__(self, webview: QWebEngineView):
        self.webview = webview
        self.bridge = PickBridge()
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.webview.page().setWebChannel(self.channel)
        self.active = False
        self.single_active = False
        self.webview.page().loadFinished.connect(self._on_page_loaded)

    def _on_page_loaded(self, ok):
        if ok:
            if self.active:
                self.webview.page().runJavaScript(PICK_ACTIVATE_JS)
            elif self.single_active:
                self.webview.page().runJavaScript(SINGLE_PICK_ACTIVATE_JS)

    def activate(self):
        if self.active:
            return
        self.active = True
        self.single_active = False
        self.webview.page().runJavaScript(PICK_ACTIVATE_JS)

    def deactivate(self):
        if not self.active:
            return
        self.active = False
        self.webview.page().runJavaScript(PICK_DEACTIVATE_JS)

    def activate_single_pick(self):
        if self.active:
            print("全局录制模式已激活，单次拾取不可用")
            return False
        if self.single_active:
            return False
        self.single_active = True
        self.webview.page().runJavaScript(SINGLE_PICK_ACTIVATE_JS)
        return True

    def deactivate_single_pick(self):
        if not self.single_active:
            return
        self.single_active = False
        self.webview.page().runJavaScript(SINGLE_PICK_DEACTIVATE_JS)