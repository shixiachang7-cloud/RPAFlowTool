# main.py
import sys, os, json, csv, argparse, winreg as reg, re, fnmatch
from datetime import datetime, timedelta
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineSettings, QWebEngineDownloadRequest
from flow_step import FlowStep
from pick_mode import PickModeManager
from run_engine import RunEngine
import functools

APP_NAME = "RPAFlowTool"
DEFAULT_FLOW_FILE = "auto_flow.json"
SETTINGS_FILE = os.path.join(os.path.dirname(sys.executable), "settings.json")


class StepTreeItem(QTreeWidgetItem):
    def __init__(self, step: FlowStep):
        super().__init__()
        self.step_data = step
        self.update_display()

    def update_display(self):
        if self.step_data.type == 'group':
            name = self.step_data.name if self.step_data.name else "板块"
            self.setText(0, name)
        else:
            type_names = {'click': '点击元素', 'input': '输入文本', 'extract': '提取数据',
                          'wait': '等待', 'navigate': '打开网页', 'js': 'JS脚本', 'upload': '上传文件', 'python': 'Python脚本', 
                          'desktop_focus': '窗口连接', 'desktop_click': '桌面点击', 'desktop_input': '桌面输入', 'desktop_wait': '桌面等待', 'desktop_extract': '桌面提取', 'desktop_python': '桌面Python'}
            desc = type_names.get(self.step_data.type, self.step_data.type)
            name = self.step_data.name if self.step_data.name else desc
            self.setText(0, name)


class DragSafeTreeWidget(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

    def dropEvent(self, event):
        super().dropEvent(event)
        self._clean_invalid_children()
        main_window = self.window()
        if isinstance(main_window, MainWindow):
            main_window.steps = main_window._build_flat_steps_list()
            self.clearSelection()

    def _clean_invalid_children(self):
        root = self.invisibleRootItem()
        self._clean_node_children(root)

    def _clean_node_children(self, parent_item):
        i = 0
        while i < parent_item.childCount():
            child = parent_item.child(i)
            if isinstance(child, StepTreeItem):
                if child.step_data.type != 'group' and child.childCount() > 0:
                    while child.childCount() > 0:
                        sub_child = child.takeChild(0)
                        parent_item.insertChild(i + 1, sub_child)
                    i += 1
                    continue
                if child.step_data.type == 'group':
                    self._clean_node_children(child)
            i += 1


class NodeTypeDialog(QDialog):
    def __init__(self, parent=None, allow_group=True):
        super().__init__(parent)
        self.setWindowTitle("添加节点")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItem("打开网页", "navigate")
        self.type_combo.addItem("点击元素", "click")
        self.type_combo.addItem("输入文本", "input")
        self.type_combo.addItem("提取数据", "extract")
        self.type_combo.addItem("等待", "wait")
        self.type_combo.addItem("JS脚本", "js")
        self.type_combo.addItem("上传文件", "upload")
        self.type_combo.addItem("Python脚本", "python")
        self.type_combo.addItem("窗口连接", "desktop_focus")
        self.type_combo.addItem("桌面点击", "desktop_click")
        self.type_combo.addItem("桌面输入", "desktop_input")
        self.type_combo.addItem("桌面等待", "desktop_wait")
        self.type_combo.addItem("桌面提取", "desktop_extract")
        self.type_combo.addItem("桌面Python", "desktop_python")
        if allow_group:
            self.type_combo.addItem("板块（分组）", "group")
        form.addRow("节点类型:", self.type_combo)
        layout.addLayout(form)
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def get_selected_type(self):
        return self.type_combo.currentData()


class CustomWebEnginePage(QWebEnginePage):
    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)


class MainWindow(QMainWindow):
    def __init__(self, auto_run_flow=None):
        super().__init__()
        self.setWindowTitle("RPA 网页自动化工具")
        self.resize(1400, 900)
        self.current_step = None
        self.running_steps = []
        self._single_pick_source = 'click'
        self.current_file_path = None
        self.default_flow_path = os.path.join(os.path.dirname(sys.executable), DEFAULT_FLOW_FILE)

        self._load_settings()
        self._create_toolbar()

        main_splitter = QSplitter(Qt.Vertical)
        top_splitter = QSplitter(Qt.Horizontal)

        # 浏览器
        profile = QWebEngineProfile(self)
        page = CustomWebEnginePage(profile, self)
        self.browser = QWebEngineView()
        self.browser.setPage(page)
        self.browser.setUrl(QUrl("about:blank"))
        top_splitter.addWidget(self.browser)

        profile.downloadRequested.connect(self._on_download_requested)

        self.pick_manager = PickModeManager(self.browser)
        self.pick_manager.bridge.element_picked.connect(lambda x, t, tag, nt: self._on_element_picked(x, t, tag, nt))
        self.pick_manager.bridge.single_picked.connect(self._on_single_picked)

        self.run_engine = RunEngine(self.browser)
        self.run_engine.step_started.connect(self._on_run_step_started)
        self.run_engine.step_finished.connect(self._on_run_step_finished)
        self.run_engine.finished.connect(self._on_run_finished)
        self.run_engine.error_occurred.connect(self._on_run_error)
        self.run_engine.stopped.connect(self._on_run_stopped)

        # 右侧面板
        right_splitter = QSplitter(Qt.Vertical)
        tree_widget = QWidget()
        tree_layout = QVBoxLayout(tree_widget)
        tree_layout.setContentsMargins(4, 4, 4, 4)
        tree_header = QHBoxLayout()
        tree_header.addWidget(QLabel("流程步骤"))
        self.btn_add_step = QPushButton("+")
        self.btn_del_step = QPushButton("-")
        self.btn_add_group = QPushButton("新增板块")
        tree_header.addWidget(self.btn_add_step)
        tree_header.addWidget(self.btn_del_step)
        tree_header.addWidget(self.btn_add_group)
        tree_header.addStretch()
        tree_layout.addLayout(tree_header)

        self.step_tree = DragSafeTreeWidget()
        self.step_tree.setHeaderHidden(True)
        self.step_tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.step_tree.setDefaultDropAction(Qt.MoveAction)
        self.step_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.step_tree.setDropIndicatorShown(True)
        self.step_tree.currentItemChanged.connect(self._on_tree_selected)
        tree_layout.addWidget(self.step_tree)
        right_splitter.addWidget(tree_widget)

        # 配置面板
        config_widget = QWidget()
        config_layout = QVBoxLayout(config_widget)
        config_layout.setContentsMargins(4, 4, 4, 4)
        config_header = QHBoxLayout()
        config_header.addWidget(QLabel("节点配置"))
        self.type_switch_combo = QComboBox()
        self.type_switch_combo.addItem("打开网页", "navigate")
        self.type_switch_combo.addItem("点击元素", "click")
        self.type_switch_combo.addItem("输入文本", "input")
        self.type_switch_combo.addItem("提取数据", "extract")
        self.type_switch_combo.addItem("等待", "wait")
        self.type_switch_combo.addItem("JS脚本", "js")
        self.type_switch_combo.addItem("上传文件", "upload")
        self.type_switch_combo.addItem("Python脚本", "python")
        self.type_switch_combo.addItem("窗口连接", "desktop_focus")
        self.type_switch_combo.addItem("桌面点击", "desktop_click")
        self.type_switch_combo.addItem("桌面输入", "desktop_input")
        self.type_switch_combo.addItem("桌面等待", "desktop_wait")
        self.type_switch_combo.addItem("桌面提取", "desktop_extract")
        self.type_switch_combo.addItem("桌面Python", "desktop_python")
        self.type_switch_combo.addItem("板块（分组）", "group")
        self.type_switch_combo.currentIndexChanged.connect(self._on_type_switched)
        config_header.addWidget(self.type_switch_combo)
        config_layout.addLayout(config_header)

        self.config_stack = QStackedWidget()
        self.step_controls = {}
        self._create_config_pages()
        config_layout.addWidget(self.config_stack)

        self.pre_wait_widget = QWidget()
        pre_layout = QVBoxLayout(self.pre_wait_widget)
        self._add_pre_wait_controls(pre_layout)
        config_layout.addWidget(self.pre_wait_widget)
        self.pre_wait_widget.setVisible(False)

        right_splitter.addWidget(config_widget)
        right_splitter.setSizes([300, 300])
        top_splitter.addWidget(right_splitter)
        top_splitter.setSizes([900, 500])
        main_splitter.addWidget(top_splitter)

        # 底部区域
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(4, 4, 4, 4)

        # 表格
        table_header = QHBoxLayout()
        table_header.addWidget(QLabel("采集字段列表"))
        self.btn_clear_table = QPushButton("清空表格")
        self.btn_export_csv = QPushButton("导出CSV")
        table_header.addWidget(self.btn_clear_table)
        table_header.addWidget(self.btn_export_csv)
        table_header.addStretch()
        bottom_layout.addLayout(table_header)
        self.data_table = QTableWidget(0, 3)
        self.data_table.setHorizontalHeaderLabels(["字段名", "示例值", "XPath路径"])
        self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        bottom_layout.addWidget(self.data_table)

        # 定时控制
        loop_group = QGroupBox("定时控制")
        loop_layout = QHBoxLayout(loop_group)
        self.chk_loop = QCheckBox("间隔循环")
        self.chk_loop.setChecked(False)
        loop_layout.addWidget(self.chk_loop)
        loop_layout.addWidget(QLabel("间隔:"))
        self.spin_h = QSpinBox(); self.spin_h.setRange(0, 99); self.spin_h.setSuffix(" 时")
        self.spin_m = QSpinBox(); self.spin_m.setRange(0, 59); self.spin_m.setSuffix(" 分")
        self.spin_s = QSpinBox(); self.spin_s.setRange(0, 59); self.spin_s.setSuffix(" 秒")
        loop_layout.addWidget(self.spin_h); loop_layout.addWidget(self.spin_m); loop_layout.addWidget(self.spin_s)
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine); sep.setFrameShadow(QFrame.Sunken)
        loop_layout.addWidget(sep)
        self.chk_daily = QCheckBox("每日定点")
        self.chk_daily.setChecked(False)
        loop_layout.addWidget(self.chk_daily)
        self.time_daily = QTimeEdit()
        self.time_daily.setDisplayFormat("HH:mm")
        loop_layout.addWidget(self.time_daily)
        self.btn_stop_loop = QPushButton("停止定时")
        self.btn_stop_loop.setEnabled(False)
        loop_layout.addWidget(self.btn_stop_loop)
        loop_layout.addStretch()
        bottom_layout.addWidget(loop_group)

        # 日期遍历控制
        traverse_group = QGroupBox("日期遍历")
        traverse_layout = QHBoxLayout(traverse_group)
        traverse_layout.addWidget(QLabel("从:"))
        self.traverse_start_date = QDateEdit()
        self.traverse_start_date.setCalendarPopup(True)
        self.traverse_start_date.setDate(QDate.currentDate().addMonths(-1))
        traverse_layout.addWidget(self.traverse_start_date)
        traverse_layout.addWidget(QLabel("至:"))
        self.traverse_end_date = QDateEdit()
        self.traverse_end_date.setCalendarPopup(True)
        self.traverse_end_date.setDate(QDate.currentDate())
        traverse_layout.addWidget(self.traverse_end_date)
        self.btn_start_traverse = QPushButton("开始遍历")
        self.btn_stop_traverse = QPushButton("停止遍历")
        self.btn_stop_traverse.setEnabled(False)
        traverse_layout.addWidget(self.btn_start_traverse)
        traverse_layout.addWidget(self.btn_stop_traverse)
        traverse_layout.addStretch()
        bottom_layout.addWidget(traverse_group)

        # 下载管理
        dload_group = QGroupBox("下载管理")
        dl_layout = QHBoxLayout(dload_group)
        dl_layout.addWidget(QLabel("保存到:"))
        self.dl_dir_edit = QLineEdit(self.settings.get("download_dir", os.path.join(os.path.dirname(sys.executable), "downloads")))
        self.dl_dir_edit.setReadOnly(True)
        dl_layout.addWidget(self.dl_dir_edit)
        self.btn_dl_dir = QPushButton("更改...")
        dl_layout.addWidget(self.btn_dl_dir)
        self.chk_date_subdir = QCheckBox("日期子目录")
        self.chk_date_subdir.setChecked(self.settings.get("date_subdir", False))
        dl_layout.addWidget(self.chk_date_subdir)
        self.cmb_date_mode = QComboBox()
        self.cmb_date_mode.addItems(["当天日期", "指定日期"])
        self.cmb_date_mode.currentIndexChanged.connect(self._on_date_mode_changed)
        dl_layout.addWidget(self.cmb_date_mode)
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.fromString(self.settings.get("fixed_date", QDate.currentDate().toString("yyyyMMdd")), "yyyyMMdd"))
        if self.settings.get("date_mode", 0) == 1:
            self.cmb_date_mode.setCurrentIndex(1)
        else:
            self.cmb_date_mode.setCurrentIndex(0)
        self.date_edit.setVisible(self.cmb_date_mode.currentIndex() == 1)
        dl_layout.addWidget(self.date_edit)
        bottom_layout.addWidget(dload_group)

        main_splitter.addWidget(bottom_widget)
        main_splitter.setSizes([700, 200])
        self.setCentralWidget(main_splitter)

        self.setWindowFlags(Qt.Window | Qt.WindowMinimizeButtonHint | Qt.CustomizeWindowHint)
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        self.tray_icon.setToolTip("RPA 网页自动化工具")
        tray_menu = QMenu()
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)

        self.installEventFilter(self)

        self.loop_timer = QTimer(self)
        self.loop_timer.timeout.connect(self._loop_trigger)
        self.daily_timer = QTimer(self)
        self.daily_timer.timeout.connect(self._daily_trigger)
        self.daily_timer.start(1000)

        self._connect_signals()

        self.single_pick_buttons = []
        for t in ['click', 'input', 'extract']:
            if 'pick_btn' in self.step_controls[t]:
                self.single_pick_buttons.append(self.step_controls[t]['pick_btn'])

        # 遍历状态
        self._traverse_active = False
        self._traverse_paused = False
        self._traverse_current_date = None
        self._traverse_end_date = None
        self._paused_steps = []
        self._paused_full_steps = []
        self._paused_step_index = 0

        # ===================== 恢复持久化控件状态 =====================
        self.chk_daily.setChecked(self.settings.get("daily_enabled", False))
        self.time_daily.setTime(QTime.fromString(self.settings.get("daily_time", "08:00"), "HH:mm"))
        self.chk_loop.setChecked(self.settings.get("loop_enabled", False))
        self.spin_h.setValue(self.settings.get("loop_h", 0))
        self.spin_m.setValue(self.settings.get("loop_m", 0))
        self.spin_s.setValue(self.settings.get("loop_s", 0))
        self.statusBar().messageChanged.connect(self._log_to_file)
        if auto_run_flow:
            QTimer.singleShot(1000, lambda: self._auto_start(auto_run_flow))
        elif getattr(args, 'auto_run', False) and os.path.exists(self.default_flow_path):
            QTimer.singleShot(1000, lambda: self._auto_start(self.default_flow_path))

    # ===================== 设置相关 =====================
    def _load_settings(self):
        self.settings = {
            "download_dir": os.path.join(os.path.dirname(sys.executable), "downloads"),
            "date_subdir": False,
            "date_mode": 0,
            "fixed_date": QDate.currentDate().toString("yyyyMMdd"),
            "daily_enabled": False,
            "daily_time": "08:00",
            "loop_enabled": False,
            "loop_h": 0, "loop_m": 0, "loop_s": 0
        }
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                self.settings.update(json.load(f))

    def _save_settings(self):
        self.settings["download_dir"] = self.dl_dir_edit.text()
        self.settings["date_subdir"] = self.chk_date_subdir.isChecked()
        self.settings["date_mode"] = self.cmb_date_mode.currentIndex()
        self.settings["fixed_date"] = self.date_edit.date().toString("yyyyMMdd")
        self.settings["daily_enabled"] = self.chk_daily.isChecked()
        self.settings["daily_time"] = self.time_daily.time().toString("HH:mm")
        self.settings["loop_enabled"] = self.chk_loop.isChecked()
        self.settings["loop_h"] = self.spin_h.value()
        self.settings["loop_m"] = self.spin_m.value()
        self.settings["loop_s"] = self.spin_s.value()
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.settings, f, indent=2)

    def _on_date_mode_changed(self, idx):
        self.date_edit.setVisible(idx == 1)

    # ===================== 配置页创建 =====================
    def _create_config_pages(self):
        types = ['navigate', 'click', 'input', 'extract', 'wait',  'js', 'upload', 'python',
                'desktop_focus', 'desktop_click', 'desktop_input', 'desktop_wait', 'desktop_extract', 'desktop_python','group']
        for t in types:
            self.step_controls[t] = {}

        self._create_navigate_page()
        self._create_click_page()
        self._create_input_page()
        self._create_extract_page()
        self._create_wait_page()
        self._make_js_page()          # 索引 5
        self._make_upload_page()      # 索引 6
        self._make_python_page()      # 索引 7
        self._make_desktop_focus_page()
        self._make_desktop_click_page()
        self._make_desktop_input_page()
        self._make_desktop_wait_page()
        self._make_desktop_extract_page()
        self._make_desktop_python_page()
        self._create_group_page()     # 索引 14 （移到最后）

        for t, ctrls in self.step_controls.items():
            for name, widget in ctrls.items():
                if isinstance(widget, QLineEdit):
                    widget.textChanged.connect(self._on_any_control_changed)
                elif isinstance(widget, QTextEdit):
                    widget.textChanged.connect(self._on_any_control_changed)
                elif isinstance(widget, QCheckBox):
                    widget.toggled.connect(self._on_any_control_changed)
                elif isinstance(widget, QSpinBox):
                    widget.valueChanged.connect(self._on_any_control_changed)

    def _create_navigate_page(self):
        url_edit = QLineEdit()
        timeout_edit = QLineEdit("3000")
        use_current_btn = QPushButton("使用当前网址")
        use_current_btn.clicked.connect(lambda: self._use_current_url_for('navigate'))
        page = QWidget(); layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:")); layout.addWidget(name_edit)
        self.step_controls['navigate']['name_edit'] = name_edit
        layout.addWidget(QLabel("网址:")); layout.addWidget(url_edit); layout.addWidget(use_current_btn)
        layout.addWidget(QLabel("超时(ms):")); layout.addWidget(timeout_edit)
        layout.addStretch()
        self.config_stack.addWidget(page)
        self.step_controls['navigate']['url_edit'] = url_edit
        self.step_controls['navigate']['timeout_edit'] = timeout_edit

    def _create_click_page(self):
        sel_edit = QLineEdit(); pick_btn = QPushButton("🎯")
        pick_btn.clicked.connect(lambda: self._start_single_pick('click'))
        timeout_edit = QLineEdit("5000")
        page = QWidget(); layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:")); layout.addWidget(name_edit)
        self.step_controls['click']['name_edit'] = name_edit
        sel_layout = QHBoxLayout(); sel_layout.addWidget(QLabel("XPath路径:")); sel_layout.addWidget(sel_edit); sel_layout.addWidget(pick_btn)
        layout.addLayout(sel_layout)
        layout.addWidget(QLabel("超时(ms):")); layout.addWidget(timeout_edit)
        layout.addStretch()
        self.config_stack.addWidget(page)
        self.step_controls['click']['selector_edit'] = sel_edit
        self.step_controls['click']['timeout_edit'] = timeout_edit
        self.step_controls['click']['pick_btn'] = pick_btn

    def _create_input_page(self):
        sel_edit = QLineEdit(); pick_btn = QPushButton("🎯")
        pick_btn.clicked.connect(lambda: self._start_single_pick('input'))
        value_edit = QTextEdit()
        insert_date_btn = QPushButton("插入日期"); insert_date_btn.clicked.connect(self._insert_date_to_input)
        page = QWidget(); layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:")); layout.addWidget(name_edit)
        self.step_controls['input']['name_edit'] = name_edit
        sel_layout = QHBoxLayout(); sel_layout.addWidget(QLabel("XPath路径:")); sel_layout.addWidget(sel_edit); sel_layout.addWidget(pick_btn)
        layout.addLayout(sel_layout)
        layout.addWidget(QLabel("输入文本:")); layout.addWidget(value_edit)
        date_row = QHBoxLayout(); date_row.addWidget(insert_date_btn); date_row.addStretch()
        layout.addLayout(date_row)
        layout.addStretch()
        self.config_stack.addWidget(page)
        self.step_controls['input']['selector_edit'] = sel_edit
        self.step_controls['input']['pick_btn'] = pick_btn
        self.step_controls['input']['value_edit'] = value_edit

    def _create_extract_page(self):
        sel_edit = QLineEdit(); pick_btn = QPushButton("🎯")
        pick_btn.clicked.connect(lambda: self._start_single_pick('extract'))
        field_edit = QLineEdit(); attr_edit = QLineEdit("text")
        page = QWidget(); layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:")); layout.addWidget(name_edit)
        self.step_controls['extract']['name_edit'] = name_edit
        sel_layout = QHBoxLayout(); sel_layout.addWidget(QLabel("XPath路径:")); sel_layout.addWidget(sel_edit); sel_layout.addWidget(pick_btn)
        layout.addLayout(sel_layout)
        layout.addWidget(QLabel("字段名:")); layout.addWidget(field_edit)
        layout.addWidget(QLabel("提取属性:")); layout.addWidget(attr_edit)
        layout.addStretch()
        self.config_stack.addWidget(page)
        self.step_controls['extract']['selector_edit'] = sel_edit
        self.step_controls['extract']['pick_btn'] = pick_btn
        self.step_controls['extract']['field_edit'] = field_edit
        self.step_controls['extract']['attr_edit'] = attr_edit

    def _create_wait_page(self):
        timeout_edit = QLineEdit("10000")
        page = QWidget(); layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:")); layout.addWidget(name_edit)
        self.step_controls['wait']['name_edit'] = name_edit
        layout.addWidget(QLabel("超时时间(ms):")); layout.addWidget(timeout_edit)
        layout.addStretch()
        self.config_stack.addWidget(page)
        self.step_controls['wait']['timeout_edit'] = timeout_edit

    def _create_group_page(self):
        page = QWidget(); layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:")); layout.addWidget(name_edit)
        self.step_controls['group']['name_edit'] = name_edit
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_js_page(self):
        page = QWidget(); layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:")); layout.addWidget(name_edit)
        self.step_controls['js']['name_edit'] = name_edit
        layout.addWidget(QLabel("字段名（可选，用于提取返回值）:"))
        field_edit = QLineEdit(); layout.addWidget(field_edit)
        self.step_controls['js']['field_edit'] = field_edit
        layout.addWidget(QLabel("JavaScript 代码:"))
        code_edit = QTextEdit(); layout.addWidget(code_edit)
        self.step_controls['js']['code_edit'] = code_edit
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_upload_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:"))
        layout.addWidget(name_edit)
        self.step_controls['upload'] = {'name_edit': name_edit}

        sel_layout = QHBoxLayout()
        sel_layout.addWidget(QLabel("目标元素 XPath:"))
        sel_edit = QLineEdit()
        sel_layout.addWidget(sel_edit)
        pick_btn = QPushButton("🎯")
        pick_btn.clicked.connect(lambda: self._start_single_pick('upload'))
        sel_layout.addWidget(pick_btn)
        layout.addLayout(sel_layout)
        self.step_controls['upload']['selector_edit'] = sel_edit
        self.step_controls['upload']['pick_btn'] = pick_btn

        layout.addWidget(QLabel("文件路径（支持 {today}、{download:latest} 等）:"))
        path_edit = QLineEdit()
        layout.addWidget(path_edit)
        self.step_controls['upload']['path_edit'] = path_edit

        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_python_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:"))
        layout.addWidget(name_edit)
        self.step_controls['python'] = {'name_edit': name_edit}

        layout.addWidget(QLabel("Python 代码:"))
        code_edit = QTextEdit()
        layout.addWidget(code_edit)
        self.step_controls['python']['code_edit'] = code_edit

        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_desktop_focus_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:"))
        layout.addWidget(name_edit)
        self.step_controls['desktop_focus'] = {'name_edit': name_edit}
        layout.addWidget(QLabel("窗口标题/进程:"))
        title_edit = QLineEdit()
        layout.addWidget(title_edit)
        self.step_controls['desktop_focus']['title_edit'] = title_edit
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_desktop_click_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:"))
        layout.addWidget(name_edit)
        self.step_controls['desktop_click'] = {'name_edit': name_edit}
        layout.addWidget(QLabel("选择器:"))
        sel_edit = QLineEdit()
        layout.addWidget(sel_edit)
        self.step_controls['desktop_click']['selector_edit'] = sel_edit
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_desktop_input_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:"))
        layout.addWidget(name_edit)
        self.step_controls['desktop_input'] = {'name_edit': name_edit}
        layout.addWidget(QLabel("选择器:"))
        sel_edit = QLineEdit()
        layout.addWidget(sel_edit)
        self.step_controls['desktop_input']['selector_edit'] = sel_edit
        layout.addWidget(QLabel("输入文本:"))
        value_edit = QTextEdit()
        layout.addWidget(value_edit)
        self.step_controls['desktop_input']['value_edit'] = value_edit
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_desktop_wait_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:"))
        layout.addWidget(name_edit)
        self.step_controls['desktop_wait'] = {'name_edit': name_edit}
        layout.addWidget(QLabel("选择器:"))
        sel_edit = QLineEdit()
        layout.addWidget(sel_edit)
        self.step_controls['desktop_wait']['selector_edit'] = sel_edit
        layout.addWidget(QLabel("超时(ms):"))
        timeout_edit = QLineEdit("5000")
        layout.addWidget(timeout_edit)
        self.step_controls['desktop_wait']['timeout_edit'] = timeout_edit
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_desktop_extract_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:"))
        layout.addWidget(name_edit)
        self.step_controls['desktop_extract'] = {'name_edit': name_edit}
        layout.addWidget(QLabel("选择器:"))
        sel_edit = QLineEdit()
        layout.addWidget(sel_edit)
        self.step_controls['desktop_extract']['selector_edit'] = sel_edit
        layout.addWidget(QLabel("字段名:"))
        field_edit = QLineEdit()
        layout.addWidget(field_edit)
        self.step_controls['desktop_extract']['field_edit'] = field_edit
        layout.addWidget(QLabel("提取属性:"))
        attr_edit = QLineEdit("name")
        layout.addWidget(attr_edit)
        self.step_controls['desktop_extract']['attr_edit'] = attr_edit
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_desktop_python_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:"))
        layout.addWidget(name_edit)
        self.step_controls['desktop_python'] = {'name_edit': name_edit}
        layout.addWidget(QLabel("Python 代码:"))
        code_edit = QTextEdit()
        layout.addWidget(code_edit)
        self.step_controls['desktop_python']['code_edit'] = code_edit
        layout.addStretch()
        self.config_stack.addWidget(page)

    # ===================== 预等待控件 =====================
    def _add_pre_wait_controls(self, layout):
        self.chk_pre_wait = QCheckBox("执行前等待(秒):")
        self.spin_pre_wait = QSpinBox(); self.spin_pre_wait.setRange(0, 300)
        row = QHBoxLayout(); row.addWidget(self.chk_pre_wait); row.addWidget(self.spin_pre_wait)
        layout.addLayout(row)

        self.chk_pre_wait_element = QCheckBox("等待指定元素出现")
        self.pre_wait_xpath_edit = QLineEdit()
        self.pre_wait_xpath_pick_btn = QPushButton("🎯")
        self.pre_wait_xpath_pick_btn.clicked.connect(lambda: self._start_single_pick('pre_wait'))
        pre_row = QHBoxLayout(); pre_row.addWidget(self.chk_pre_wait_element); pre_row.addWidget(self.pre_wait_xpath_edit); pre_row.addWidget(self.pre_wait_xpath_pick_btn)
        layout.addLayout(pre_row)

        self.chk_pre_wait.toggled.connect(self._on_pre_wait_toggled)
        self.chk_pre_wait_element.toggled.connect(self._on_pre_wait_element_toggled)
        # 值变化连接到数据更新
        self.chk_pre_wait.toggled.connect(self._on_any_control_changed)
        self.spin_pre_wait.valueChanged.connect(self._on_any_control_changed)
        self.chk_pre_wait_element.toggled.connect(self._on_any_control_changed)
        self.pre_wait_xpath_edit.textChanged.connect(self._on_any_control_changed)

        self.chk_pre_wait_element.setVisible(False)
        self.pre_wait_xpath_edit.setVisible(False)
        self.pre_wait_xpath_pick_btn.setVisible(False)
        self.spin_pre_wait.setEnabled(False)

    def _on_pre_wait_toggled(self, checked):
        self.spin_pre_wait.setEnabled(checked)
        self.chk_pre_wait_element.setVisible(checked)
        self.pre_wait_xpath_edit.setVisible(checked and self.chk_pre_wait_element.isChecked())
        self.pre_wait_xpath_pick_btn.setVisible(checked and self.chk_pre_wait_element.isChecked())
        if not checked:
            self.chk_pre_wait_element.setChecked(False)

    def _on_pre_wait_element_toggled(self, checked):
        self.pre_wait_xpath_edit.setVisible(checked)
        self.pre_wait_xpath_pick_btn.setVisible(checked)

    # ===================== 信号连接 =====================
    def _connect_signals(self):
        self.btn_load.clicked.connect(self._load_url)
        self.url_input.returnPressed.connect(self._load_url)
        self.btn_add_step.clicked.connect(lambda: self._add_step(False))
        self.btn_add_group.clicked.connect(lambda: self._add_step(True))
        self.btn_del_step.clicked.connect(self._delete_step)
        self.btn_run.clicked.connect(lambda: self._run_flow(from_beginning=False))
        self.btn_stop.clicked.connect(self._stop_flow)
        self.btn_save.clicked.connect(self._save_flow)
        self.btn_load_flow.clicked.connect(self._load_flow)
        self.btn_set_default.clicked.connect(self._save_default_flow)
        self.btn_clear_table.clicked.connect(self._clear_table)
        self.btn_export_csv.clicked.connect(self._export_csv)
        self.delay_slider.valueChanged.connect(self._on_delay_changed)
        self.btn_dl_dir.clicked.connect(self._choose_download_dir)
        self.btn_stop_loop.clicked.connect(self._stop_all_timers)
        self.chk_daily.toggled.connect(self._on_daily_toggled)
        self.chk_loop.toggled.connect(self._on_loop_toggled)
        self.btn_start_traverse.clicked.connect(self._start_traverse)
        self.btn_stop_traverse.clicked.connect(self._stop_traverse)
        self.chk_date_subdir.toggled.connect(self._save_settings)

    # ===================== 工具栏 =====================
    def _create_toolbar(self):
        toolbar = QToolBar("主工具栏")
        toolbar.setIconSize(QSize(16, 16))
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        self.url_input = QLineEdit(); self.url_input.setPlaceholderText("输入网址..."); self.url_input.setFixedWidth(350)
        toolbar.addWidget(self.url_input)
        self.btn_load = QPushButton("加载"); toolbar.addWidget(self.btn_load)
        toolbar.addSeparator()
        self.btn_browse_mode = QPushButton("浏览模式"); self.btn_browse_mode.setCheckable(True); self.btn_browse_mode.setChecked(True)
        toolbar.addWidget(self.btn_browse_mode)
        self.btn_pick_mode = QPushButton("拾取模式"); self.btn_pick_mode.setCheckable(True)
        toolbar.addWidget(self.btn_pick_mode)
        self.mode_group = QButtonGroup(self); self.mode_group.addButton(self.btn_browse_mode, 0); self.mode_group.addButton(self.btn_pick_mode, 1)
        self.mode_group.buttonClicked.connect(self._on_mode_changed)
        toolbar.addSeparator()
        self.btn_save = QPushButton("保存流程"); toolbar.addWidget(self.btn_save)
        self.btn_load_flow = QPushButton("加载流程"); toolbar.addWidget(self.btn_load_flow)
        self.btn_set_default = QPushButton("设为默认"); toolbar.addWidget(self.btn_set_default)
        toolbar.addSeparator()
        self.btn_run = QPushButton("▶ 运行"); toolbar.addWidget(self.btn_run)
        self.btn_stop = QPushButton("停止"); self.btn_stop.setEnabled(False); toolbar.addWidget(self.btn_stop)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel("步骤延迟:"))
        self.delay_slider = QSlider(Qt.Horizontal); self.delay_slider.setRange(0, 5000); self.delay_slider.setValue(1000)
        self.delay_slider.setFixedWidth(120); toolbar.addWidget(self.delay_slider)
        self.delay_label = QLabel("1000 ms"); toolbar.addWidget(self.delay_label)
        toolbar.addSeparator()
        self.chk_autostart = QCheckBox("开机自启"); self.chk_autostart.setChecked(self._get_autostart_status()); self.chk_autostart.toggled.connect(self._set_autostart)
        toolbar.addWidget(self.chk_autostart)

    def _log_to_file(self, msg):
        """将状态栏消息写入 logs/年/年月/年月日.txt，并添加时间戳"""
        if not msg:
            return
        try:
            now = datetime.now()
            # 时间戳格式：2026-07-26 14:30:25.123
            timestamp = now.strftime('%Y-%m-%d %H:%M:%S.') + f"{now.microsecond // 1000:03d}"
            line = f"[{timestamp}] {msg}\n"

            # 日志根目录：与 exe 同级的 logs 文件夹
            base_dir = os.path.join(os.path.dirname(sys.executable), "logs")
            year = now.strftime("%Y")
            month = now.strftime("%Y%m")
            day = now.strftime("%Y%m%d")

            log_dir = os.path.join(base_dir, year, month)
            os.makedirs(log_dir, exist_ok=True)

            log_path = os.path.join(log_dir, f"{day}.txt")
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(line)
        except Exception:
            pass  # 静默处理，避免日志记录影响主流程


    # ===================== 步骤存取 =====================
    def _build_flat_steps_list(self):
        steps = []
        for i in range(self.step_tree.topLevelItemCount()):
            item = self.step_tree.topLevelItem(i)
            if isinstance(item, StepTreeItem):
                if item.step_data.type == 'group':
                    for j in range(item.childCount()):
                        child = item.child(j)
                        if isinstance(child, StepTreeItem):
                            steps.append(child.step_data)
                else:
                    steps.append(item.step_data)
        return steps

    def _serialize_tree_item(self, item):
        data = {
            'step': {
                'id': item.step_data.id, 'type': item.step_data.type, 'name': item.step_data.name,
                'selector': item.step_data.selector, 'selector_type': item.step_data.selector_type,
                'value': item.step_data.value, 'field_name': item.step_data.field_name,
                'extract_attr': item.step_data.extract_attr, 'timeout': item.step_data.timeout,
                'pre_wait_seconds': item.step_data.pre_wait_seconds,
                'pre_wait_element': item.step_data.pre_wait_element,
                'pre_wait_xpath': item.step_data.pre_wait_xpath,
                'date_format': item.step_data.date_format, 'use_today': item.step_data.use_today,
                'fixed_date': item.step_data.fixed_date,
                'js_code': item.step_data.js_code,
                'upload_file_path': item.step_data.upload_file_path,
                'python_code': item.step_data.python_code
            },
            'children': []
        }
        for i in range(item.childCount()):
            child = item.child(i)
            if isinstance(child, StepTreeItem):
                data['children'].append(self._serialize_tree_item(child))
        return data

    def _deserialize_tree_item(self, data, parent):
        step_dict = data['step']
        step = FlowStep(
            id=step_dict.get('id', 0),
            type=step_dict.get('type', 'click'),
            name=step_dict.get('name', ''),
            selector=step_dict.get('selector', ''),
            selector_type=step_dict.get('selector_type', 'xpath'),
            value=step_dict.get('value', ''),
            field_name=step_dict.get('field_name', ''),          
            extract_attr=step_dict.get('extract_attr', 'text'),
            timeout=step_dict.get('timeout', 5000),
            pre_wait_seconds=step_dict.get('pre_wait_seconds', 0),
            pre_wait_element=step_dict.get('pre_wait_element', False),
            pre_wait_xpath=step_dict.get('pre_wait_xpath', ''),
            date_format=step_dict.get('date_format', ''),
            use_today=step_dict.get('use_today', True),
            fixed_date=step_dict.get('fixed_date', ''),
            js_code=step_dict.get('js_code', ''),
            upload_file_path=step_dict.get('upload_file_path', ''),
            python_code=step_dict.get('python_code', '')        
        )
        item = StepTreeItem(step)
        parent.addChild(item)
        for child_data in data.get('children', []):
            self._deserialize_tree_item(child_data, item)
        return item

    def _save_flow(self):

        print("进入保存流程")

        from PySide6.QtWidgets import QFileDialog

        dialog = QFileDialog(self)

        dialog.setWindowTitle(
            "保存流程"
        )

        dialog.setAcceptMode(
            QFileDialog.AcceptSave
        )

        dialog.setNameFilter(
            "JSON 文件 (*.json)"
        )


        # 关键修改：
        # 禁止 Windows 原生保存框

        dialog.setOption(
            QFileDialog.DontUseNativeDialog,
            True
        )


        def on_finished(result):

            print(
                "保存窗口关闭:",
                result
            )


            if result:

                files = dialog.selectedFiles()


                if files:

                    filepath = files[0]


                    if not filepath.lower().endswith(".json"):

                        filepath += ".json"


                    print(
                        "保存路径:",
                        filepath
                    )


                    try:

                        import json


                        tree_data = []


                        for i in range(
                            self.step_tree.topLevelItemCount()
                        ):

                            item = (
                                self.step_tree.topLevelItem(i)
                            )


                            if isinstance(
                                item,
                                StepTreeItem
                            ):

                                tree_data.append(
                                    self._serialize_tree_item(
                                        item
                                    )
                                )


                        with open(
                            filepath,
                            "w",
                            encoding="utf-8"
                        ) as f:

                            json.dump(
                                tree_data,
                                f,
                                ensure_ascii=False,
                                indent=2
                            )


                        QMessageBox.information(
                            self,
                            "保存成功",
                            "流程保存成功"
                        )


                    except Exception as e:

                        import traceback

                        traceback.print_exc()


                        QMessageBox.critical(
                            self,
                            "保存失败",
                            str(e)
                        )


            dialog.deleteLater()



        dialog.finished.connect(
            on_finished
        )


        # 不进入第二事件循环
        dialog.open()

    def _load_flow(self):

        print("进入加载流程")

        from PySide6.QtWidgets import QFileDialog

        dialog = QFileDialog(self)

        dialog.setWindowTitle("加载流程")

        dialog.setFileMode(
            QFileDialog.ExistingFile
        )

        dialog.setNameFilter(
            "JSON 文件 (*.json)"
        )

        # 关键修改：
        # 不使用 Windows 原生文件框
        dialog.setOption(
            QFileDialog.DontUseNativeDialog,
            True
        )

        def on_finished(result):

            print("加载窗口关闭:", result)

            if result:

                files = dialog.selectedFiles()

                if files:

                    filepath = files[0]

                    print(
                        "加载文件:",
                        filepath
                    )

                    try:

                        import json

                        with open(
                            filepath,
                            "r",
                            encoding="utf-8"
                        ) as f:

                            tree_data = json.load(f)


                        if not isinstance(tree_data, list):

                            raise ValueError(
                                "流程文件格式错误"
                            )


                        # 清理状态

                        self.current_step = None

                        self.running_steps.clear()


                        self.step_tree.blockSignals(True)

                        self.step_tree.clear()


                        for item_data in tree_data:

                            self._deserialize_tree_item(
                                item_data,
                                self.step_tree.invisibleRootItem()
                            )


                        self.step_tree.blockSignals(False)


                        self.steps = (
                            self._build_flat_steps_list()
                        )


                        print(
                            "加载完成，步骤数量:",
                            len(self.steps)
                        )


                    except Exception as e:

                        import traceback

                        traceback.print_exc()

                        QMessageBox.critical(
                            self,
                            "加载失败",
                            str(e)
                        )


            dialog.deleteLater()


        dialog.finished.connect(
            on_finished
        )


        # 不阻塞 Qt 主事件循环
        dialog.open()

    def _save_default_flow(self):
        if self.step_tree.topLevelItemCount() == 0:
            QMessageBox.warning(self, "警告", "没有步骤可保存")
            return
        try:
            tree_data = []
            for i in range(self.step_tree.topLevelItemCount()):
                item = self.step_tree.topLevelItem(i)
                if isinstance(item, StepTreeItem):
                    tree_data.append(self._serialize_tree_item(item))
            with open(self.default_flow_path, 'w', encoding='utf-8') as f:
                json.dump(tree_data, f, ensure_ascii=False, indent=2)
            self.statusBar().showMessage(f"已保存为默认流程：{self.default_flow_path}")
        except Exception as e:
            QMessageBox.critical(self, "保存默认流程失败", str(e))

    def _auto_start(self, flow_path):
        try:
            with open(flow_path, 'r', encoding='utf-8') as f:
                tree_data = json.load(f)
            self.step_tree.clear()
            for item_data in tree_data:
                self._deserialize_tree_item(item_data, self.step_tree.invisibleRootItem())
            self.steps = self._build_flat_steps_list()
            self.statusBar().showMessage(f"已自动加载流程：{flow_path}")
            self.btn_browse_mode.setChecked(True)
            self.pick_manager.deactivate()
            QTimer.singleShot(800, lambda: self._run_flow(from_beginning=True))
        except Exception as e:
            QMessageBox.critical(self, "自动启动失败", str(e))

    # ===================== 单次拾取 =====================
    def _set_single_pick_active(self, active):
        style = "QPushButton { background-color: #4285f4; color: white; }" if active else ""
        for btn in self.single_pick_buttons:
            btn.setStyleSheet(style); btn.setDown(active)
        self.statusBar().showMessage("单次拾取模式..." if active else "就绪")

    def _stop_single_pick(self):
        if self.pick_manager.single_active:
            self.pick_manager.deactivate_single_pick()
            self._set_single_pick_active(False)

    def _start_single_pick(self, source):
        if self.pick_manager.active:
            QMessageBox.warning(self, "提示", "请先切换到浏览模式再使用选择器拾取")
            return
        if self.pick_manager.single_active:
            self._stop_single_pick()
            return
        self._single_pick_source = source
        success = self.pick_manager.activate_single_pick()
        if success:
            self._set_single_pick_active(True)
        else:
            self.statusBar().showMessage("单次拾取启动失败")

    def _on_single_picked(self, xpath):
        source = self._single_pick_source
        if source == 'pre_wait':
            self.pre_wait_xpath_edit.setText(xpath)
        elif source in self.step_controls:
            ctrls = self.step_controls[source]
            if 'selector_edit' in ctrls:
                ctrls['selector_edit'].setText(xpath)
        self._stop_single_pick()
        self.statusBar().showMessage(f"已填入 XPath: {xpath}", 3000)

    # ===================== 运行控制 =====================
    def _run_flow(self, from_beginning=False):
        if self.run_engine.running:
            return

        # 遍历暂停恢复逻辑
        if self._traverse_active and self._traverse_paused:
            current_item = self.step_tree.currentItem()
            if isinstance(current_item, StepTreeItem) and current_item.step_data.type != 'group':
                try:
                    start_index = self._paused_full_steps.index(current_item.step_data)
                except ValueError:
                    start_index = 0
                steps_to_run = self._paused_full_steps[start_index:]
            elif isinstance(current_item, StepTreeItem) and current_item.step_data.type == 'group':
                if current_item.childCount() == 0:
                    QMessageBox.warning(self, "警告", "板块内没有可执行的步骤")
                    return
                first_child = current_item.child(0)
                if isinstance(first_child, StepTreeItem):
                    try:
                        start_index = self._paused_full_steps.index(first_child.step_data)
                    except ValueError:
                        start_index = 0
                else:
                    start_index = 0
                steps_to_run = self._paused_full_steps[start_index:]
            else:
                steps_to_run = self._paused_steps

            self._traverse_paused = False
            self.running_steps = steps_to_run
            self.btn_browse_mode.setChecked(True)
            self.pick_manager.deactivate()
            self.btn_run.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.statusBar().showMessage("继续执行...")
            self.run_engine.run(steps_to_run)
            return

        flat_steps = self._build_flat_steps_list()
        if not flat_steps:
            QMessageBox.warning(self, "警告", "没有可执行的流程步骤")
            return

        if self._traverse_active or from_beginning:
            start_index = 0
        else:
            start_index = 0
            current_item = self.step_tree.currentItem()
            if isinstance(current_item, StepTreeItem):
                if current_item.step_data.type == 'group':
                    if current_item.childCount() == 0:
                        QMessageBox.warning(self, "警告", "板块内没有可执行的步骤")
                        return
                    first_child = current_item.child(0)
                    if isinstance(first_child, StepTreeItem):
                        try:
                            start_index = flat_steps.index(first_child.step_data)
                        except ValueError:
                            pass
                else:
                    try:
                        start_index = flat_steps.index(current_item.step_data)
                    except ValueError:
                        pass

        steps_to_run = flat_steps[start_index:]
        self.running_steps = steps_to_run

        if not self._traverse_active:
            self.run_engine.simulated_date = None

        self.btn_browse_mode.setChecked(True)
        self.pick_manager.deactivate()

        if self._traverse_active:
            self.btn_run.setEnabled(False)
            self.btn_stop.setEnabled(True)
        else:
            self.btn_run.setEnabled(False)
            self.btn_stop.setEnabled(True)

        self.statusBar().showMessage(f"流程运行中（从第{start_index+1}步）...")
        self.data_table.setRowCount(0)
        self.run_engine.run(steps_to_run)

        if not self._traverse_active:
            if self.chk_loop.isChecked():
                self._start_loop()
            if self.chk_daily.isChecked():
                self._start_daily_check()

    def _stop_flow(self):
        # 遍历暂停：保存当天完整步骤列表和剩余步骤
        if self._traverse_active and not self._traverse_paused:
            self._paused_full_steps = self._build_flat_steps_list()
            remaining = self.run_engine.steps[self.run_engine.current_index:]
            self._paused_steps = remaining
            self._paused_step_index = self.run_engine.current_index
            self._traverse_paused = True
            self.run_engine.stop()
            self.running_steps = []
            self.btn_run.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.statusBar().showMessage("遍历已暂停，可选中步骤后继续运行")
            return

        self.run_engine.stop()
        self.running_steps = []
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.statusBar().showMessage("流程已停止")

    def _on_run_step_started(self, index):
        if self.running_steps and 0 <= index < len(self.running_steps):
            step = self.running_steps[index]
            step_name = step.name if step.name else step.type
            total = len(self.running_steps)
            msg = f"执行：{step_name} ({index+1}/{total})"
            if self._traverse_active and self._traverse_current_date:
                msg = f"[{self._traverse_current_date.strftime('%Y-%m-%d')}] {msg}"
            self.statusBar().showMessage(msg)

    def _on_run_finished(self, extracted_data):
        self.statusBar().showMessage("流程运行完毕")
        self.running_steps = []
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.data_table.setRowCount(0)
        for row, item in enumerate(extracted_data):
            self.data_table.insertRow(row)
            self.data_table.setItem(row, 0, QTableWidgetItem(item['field']))
            self.data_table.setItem(row, 1, QTableWidgetItem(item['value']))
            self.data_table.setItem(row, 2, QTableWidgetItem(item['selector']))
        if self._traverse_active:
            if self._traverse_paused:
                return
            self._paused_steps = []
            self._traverse_next()
        # 清除下载历史
        self.run_engine.download_history.clear()

    def _on_run_step_finished(self, index):
        pass

    def _on_run_error(self, msg):
        self.statusBar().showMessage(msg)

    def _on_run_stopped(self):
        self.running_steps = []
        if self._traverse_active and not self._traverse_paused:
            self._paused_full_steps = self._build_flat_steps_list()
            remaining = self.run_engine.steps[self.run_engine.current_index:]
            self._paused_steps = remaining
            self._paused_step_index = self.run_engine.current_index
            self._traverse_paused = True
            self.btn_run.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.statusBar().showMessage("遍历已暂停，可选中步骤后继续运行")
            return

        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.statusBar().showMessage("流程已停止")

    # ===================== 遍历控制 =====================
    def _start_traverse(self):
        if self.run_engine.running or self._traverse_active:
            return
        if not self._build_flat_steps_list():
            QMessageBox.warning(self, "警告", "没有可执行的流程步骤")
            return
        start = self.traverse_start_date.date().toPython()
        end = self.traverse_end_date.date().toPython()
        if start > end:
            QMessageBox.warning(self, "警告", "起始日期不能晚于结束日期")
            return
        self.chk_loop.setChecked(False)
        self.chk_daily.setChecked(False)
        self._stop_all_timers()
        self._traverse_active = True
        self._traverse_paused = False
        self._traverse_current_date = start
        self._traverse_end_date = end
        self.btn_start_traverse.setEnabled(False)
        self.btn_stop_traverse.setEnabled(True)
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._run_traverse_day()

    def _run_traverse_day(self):
        if not self._traverse_active:
            return
        self.run_engine.simulated_date = self._traverse_current_date
        self.statusBar().showMessage(f"遍历日期: {self._traverse_current_date.strftime('%Y-%m-%d')}")
        self._run_flow(from_beginning=True)

    def _traverse_next(self):
        if not self._traverse_active or self._traverse_paused:
            return
        if self.run_engine.running:
            QTimer.singleShot(100, self._traverse_next)
            return
        if self._traverse_current_date >= self._traverse_end_date:
            self._stop_traverse()
            return
        self._traverse_current_date += timedelta(days=1)
        self._run_traverse_day()

    def _stop_traverse(self):
        self._traverse_active = False
        self._traverse_paused = False
        self._paused_steps = []
        self._paused_full_steps = []
        self._paused_step_index = 0
        self.run_engine.simulated_date = None
        self.btn_start_traverse.setEnabled(True)
        self.btn_stop_traverse.setEnabled(False)
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.statusBar().showMessage("遍历已停止")
        if self.run_engine.running:
            self.run_engine.stop()

    # ===================== 定时控制 =====================
    def _start_loop(self):
        total_ms = (self.spin_h.value() * 3600 + self.spin_m.value() * 60 + self.spin_s.value()) * 1000
        if total_ms < 1000:
            total_ms = 1000
        self.loop_timer.stop()
        self.loop_timer.start(total_ms)
        self.btn_stop_loop.setEnabled(True)
        self.statusBar().showMessage(f"间隔循环已启动（{total_ms//1000}秒）")

    def _start_daily_check(self):
        self.daily_timer.start(1000)
        self.btn_stop_loop.setEnabled(True)

    def _loop_trigger(self):
        if self.run_engine.running or self._traverse_active:
            return
        self._run_flow(from_beginning=True)

    def _daily_trigger(self):
        if not self.chk_daily.isChecked() or self._traverse_active:
            return
        now = QTime.currentTime()
        target = self.time_daily.time()
        if now.hour() == target.hour() and now.minute() == target.minute() and now.second() == 0:
            if not self.run_engine.running:
                self._run_flow(from_beginning=True)

    def _stop_all_timers(self):
        self.loop_timer.stop()
        self.daily_timer.stop()
        self.chk_loop.setChecked(False)
        self.chk_daily.setChecked(False)
        self.btn_stop_loop.setEnabled(False)
        self.statusBar().showMessage("所有定时已停止")

    def _on_loop_toggled(self, checked):
        if checked:
            self.chk_daily.setChecked(False)
        if not checked:
            self.loop_timer.stop()

    def _on_daily_toggled(self, checked):
        if checked:
            self.chk_loop.setChecked(False)

    # ===================== 下载管理（含记录历史）=====================
    def _on_download_requested(self, download):
        base_dir = self.dl_dir_edit.text()
        if self.chk_date_subdir.isChecked() or self._traverse_active:
            if self._traverse_active and self.run_engine.simulated_date:
                date_str = self.run_engine.simulated_date.strftime("%Y%m%d")
            elif self.cmb_date_mode.currentIndex() == 0:
                date_str = QDate.currentDate().toString("yyyyMMdd")
            else:
                date_str = self.date_edit.date().toString("yyyyMMdd")
            path = os.path.join(base_dir, date_str)
            os.makedirs(path, exist_ok=True)
        else:
            path = base_dir
        download.setDownloadDirectory(path)
        download.accept()
        download.isFinishedChanged.connect(functools.partial(self._on_download_finished, download))
   
    def _on_download_finished(self, download):
        if download.isFinished():
            final_path = os.path.join(download.downloadDirectory(), download.downloadFileName())
            if os.path.exists(final_path):
                self.run_engine.download_history.append(final_path)

    def _choose_download_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择下载目录", self.dl_dir_edit.text())
        if dir_path:
            self.dl_dir_edit.setText(dir_path)
            self._save_settings()

    # ===================== 日期插入 =====================
    def _insert_date_to_input(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("选择日期")
        layout = QVBoxLayout(dlg)
        cal = QCalendarWidget()
        layout.addWidget(cal)
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)
        if dlg.exec() == QDialog.Accepted:
            date = cal.selectedDate()
            formatted = date.toString("M/d/yyyy")
            self.step_controls['input']['value_edit'].insertPlainText(formatted)

    # ===================== 其他操作 =====================
    def _load_url(self):
        url = self.url_input.text().strip()
        if not url:
            return
        if not url.startswith('http'):
            url = 'https://' + url
        self.browser.setUrl(QUrl(url))
        self.statusBar().showMessage(f"正在加载: {url}")

    def _use_current_url_for(self, type_key):
        ctrls = self.step_controls.get(type_key)
        if ctrls and 'url_edit' in ctrls:
            ctrls['url_edit'].setText(self.browser.url().toString())

    def _get_type_count(self, type_str):
        return sum(1 for s in self._build_flat_steps_list() if s.type == type_str)

    def _add_step(self, as_group=False):
        if as_group:
            node_type = 'group'
        else:
            dlg = NodeTypeDialog(self, allow_group=False)
            if dlg.exec() != QDialog.Accepted:
                return
            node_type = dlg.get_selected_type()
        new_id = len(self._build_flat_steps_list()) + 1
        step = FlowStep(id=new_id, type=node_type)
        type_names = {'click': '点击元素', 'input': '输入文本', 'extract': '提取数据',
                      'wait': '等待', 'navigate': '打开网页', 'group': '板块', 'js': 'JS脚本', 'upload': '上传文件', 'python': 'Python脚本',
                      'desktop_focus': '窗口连接', 'desktop_click': '桌面点击','desktop_input': '桌面输入', 'desktop_wait': '桌面等待','desktop_extract': '桌面提取', 'desktop_python': '桌面Python'}
        base_name = type_names.get(node_type, '步骤')
        if node_type != 'group':
            count = self._get_type_count(node_type) + 1
            step.name = f"{base_name}{count}"
        else:
            step.name = base_name
        if node_type == 'extract':
            step.field_name = f"字段{new_id}"
            step.extract_attr = "text"
        elif node_type == 'wait':
            step.timeout = 10000
        elif node_type == 'navigate':
            step.timeout = 3000
        item = StepTreeItem(step)
        current = self.step_tree.currentItem()
        if current and isinstance(current, StepTreeItem):
            if current.step_data.type == 'group' and not as_group:
                current.insertChild(current.childCount(), item)
            else:
                parent = current.parent()
                if parent is None:
                    root = self.step_tree.invisibleRootItem()
                    idx = root.indexOfChild(current) + 1
                    root.insertChild(idx, item)
                else:
                    idx = parent.indexOfChild(current) + 1
                    parent.insertChild(idx, item)
        else:
            self.step_tree.addTopLevelItem(item)
        self.step_tree.setCurrentItem(item)
        self.steps = self._build_flat_steps_list()

    def _delete_step(self):
        item = self.step_tree.currentItem()
        if not item:
            return
        parent = item.parent() or self.step_tree.invisibleRootItem()
        parent.removeChild(item)
        self.steps = self._build_flat_steps_list()
        self.config_stack.setCurrentIndex(0)

    def _on_tree_selected(self, current, previous):
        if current is None or not isinstance(current, StepTreeItem):
            self.current_step = None
            self.type_switch_combo.setEnabled(False)
            self.config_stack.setCurrentIndex(0)
            self.pre_wait_widget.setVisible(False)
            return
        step = current.step_data
        self.current_step = step
        self.type_switch_combo.setEnabled(True)
        self._populate_controls(step)

    def _block_all_controls(self, block):
        for t, ctrls in self.step_controls.items():
            for w in ctrls.values():
                if hasattr(w, 'blockSignals'):
                    w.blockSignals(block)
        for w in [self.chk_pre_wait, self.spin_pre_wait, self.chk_pre_wait_element, self.pre_wait_xpath_edit]:
            if w:
                w.blockSignals(block)
    def _populate_controls(self, step):
        self._block_all_controls(True)
        t = step.type

        ctrls = self.step_controls.get(t)
        if ctrls is None:
            print(f"[警告] 未知步骤类型 {t}，无法填充配置页")
            self._block_all_controls(False)
            return

        # 统一设置名称（所有类型都有 name_edit）
        if 'name_edit' in ctrls:
            ctrls['name_edit'].setText(step.name if step.name else '')

        # 按类型填充具体字段
        if t == 'navigate':
            if 'url_edit' in ctrls: ctrls['url_edit'].setText(step.value or '')
            if 'timeout_edit' in ctrls: ctrls['timeout_edit'].setText(str(step.timeout))
        elif t == 'click':
            if 'selector_edit' in ctrls: ctrls['selector_edit'].setText(step.selector or '')
            if 'timeout_edit' in ctrls: ctrls['timeout_edit'].setText(str(step.timeout))
        elif t == 'input':
            if 'selector_edit' in ctrls: ctrls['selector_edit'].setText(step.selector or '')
            if 'value_edit' in ctrls: ctrls['value_edit'].setPlainText(step.value or '')
        elif t == 'extract':
            if 'selector_edit' in ctrls: ctrls['selector_edit'].setText(step.selector or '')
            if 'field_edit' in ctrls: ctrls['field_edit'].setText(step.field_name or '')
            if 'attr_edit' in ctrls: ctrls['attr_edit'].setText(step.extract_attr or '')
        elif t == 'wait':
            if 'timeout_edit' in ctrls: ctrls['timeout_edit'].setText(str(step.timeout))
        elif t == 'js':
            if 'field_edit' in ctrls: ctrls['field_edit'].setText(step.field_name or '')
            if 'code_edit' in ctrls: ctrls['code_edit'].setPlainText(step.js_code or '')
        elif t == 'upload':
            if 'selector_edit' in ctrls: ctrls['selector_edit'].setText(step.selector or '')
            if 'path_edit' in ctrls: ctrls['path_edit'].setText(step.upload_file_path or '')
        elif t == 'python':
            if 'code_edit' in ctrls: ctrls['code_edit'].setPlainText(step.python_code or '')
        elif t == 'desktop_focus':
            if 'title_edit' in ctrls: ctrls['title_edit'].setText(step.value or '')
        elif t == 'desktop_click':
            if 'selector_edit' in ctrls: ctrls['selector_edit'].setText(step.selector or '')
        elif t == 'desktop_input':
            if 'selector_edit' in ctrls: ctrls['selector_edit'].setText(step.selector or '')
            if 'value_edit' in ctrls: ctrls['value_edit'].setPlainText(step.value or '')
        elif t == 'desktop_wait':
            if 'selector_edit' in ctrls: ctrls['selector_edit'].setText(step.selector or '')
            if 'timeout_edit' in ctrls: ctrls['timeout_edit'].setText(str(step.timeout))
        elif t == 'desktop_extract':
            if 'selector_edit' in ctrls: ctrls['selector_edit'].setText(step.selector or '')
            if 'field_edit' in ctrls: ctrls['field_edit'].setText(step.field_name or '')
            if 'attr_edit' in ctrls: ctrls['attr_edit'].setText(step.extract_attr or '')
        elif t == 'desktop_python':
            if 'code_edit' in ctrls: ctrls['code_edit'].setPlainText(step.python_code or '')

        # 预等待控件（桌面节点不显示）
        show_pre = t not in ('wait', 'group', 'js', 'upload', 'python',
                            'desktop_focus', 'desktop_click', 'desktop_input',
                            'desktop_wait', 'desktop_extract', 'desktop_python')
        self.pre_wait_widget.setVisible(show_pre)
        if show_pre and hasattr(self, 'chk_pre_wait'):
            self.chk_pre_wait.blockSignals(True)
            self.spin_pre_wait.blockSignals(True)
            self.chk_pre_wait_element.blockSignals(True)
            self.pre_wait_xpath_edit.blockSignals(True)
            self.chk_pre_wait.setChecked(step.pre_wait_seconds > 0)
            self.spin_pre_wait.setValue(step.pre_wait_seconds)
            self.chk_pre_wait_element.setChecked(step.pre_wait_element)
            self.pre_wait_xpath_edit.setText(step.pre_wait_xpath or '')
            self.chk_pre_wait.blockSignals(False)
            self.spin_pre_wait.blockSignals(False)
            self.chk_pre_wait_element.blockSignals(False)
            self.pre_wait_xpath_edit.blockSignals(False)

        # 设置页面索引和下拉框
        type_map = {'navigate': 0, 'click': 1, 'input': 2, 'extract': 3, 'wait': 4,
                    'js': 5, 'upload': 6, 'python': 7, 'desktop_focus': 8,
                    'desktop_click': 9, 'desktop_input': 10, 'desktop_wait': 11,
                    'desktop_extract': 12, 'desktop_python': 13, 'group': 14}
        idx = type_map.get(t, 0)
        self.config_stack.setCurrentIndex(idx)
        self.type_switch_combo.blockSignals(True)
        self.type_switch_combo.setCurrentIndex(idx)
        self.type_switch_combo.blockSignals(False)
        self._block_all_controls(False)

    def _on_any_control_changed(self, *args):
        if not self.current_step:
            return
        step = self.current_step
        sender = self.sender()

        # 处理名称编辑框（所有节点共用）
        for t, ctrls in self.step_controls.items():
            if 'name_edit' in ctrls and ctrls['name_edit'] is sender:
                step.name = sender.text()
                item = self.step_tree.currentItem()
                if isinstance(item, StepTreeItem):
                    item.update_display()
                return

        t = step.type
        ctrls = self.step_controls.get(t)
        if not ctrls:
            return

        # ---- 安全访问控件 ----
        def safe_get(ctrl_name, is_text=False):
            """获取控件，如果不存在则返回 None，并屏蔽信号"""
            w = ctrls.get(ctrl_name)
            if w is None:
                return None
            return w

        if t == 'navigate':
            w = safe_get('url_edit')
            if w and sender is w: step.value = w.text()
            w = safe_get('timeout_edit')
            if w and sender is w: step.timeout = int(w.text() or 3000)
        elif t == 'click':
            w = safe_get('selector_edit')
            if w and sender is w: step.selector = w.text()
            w = safe_get('timeout_edit')
            if w and sender is w: step.timeout = int(w.text() or 5000)
        elif t == 'input':
            w = safe_get('selector_edit')
            if w and sender is w: step.selector = w.text()
            w = safe_get('value_edit')
            if w and sender is w: step.value = w.toPlainText()
        elif t == 'extract':
            w = safe_get('selector_edit')
            if w and sender is w: step.selector = w.text()
            w = safe_get('field_edit')
            if w and sender is w: step.field_name = w.text()
            w = safe_get('attr_edit')
            if w and sender is w: step.extract_attr = w.text()
        elif t == 'wait':
            w = safe_get('timeout_edit')
            if w and sender is w: step.timeout = int(w.text() or 10000)
        elif t == 'js':
            w = safe_get('field_edit')
            if w and sender is w: step.field_name = w.text()
            w = safe_get('code_edit')
            if w and sender is w: step.js_code = w.toPlainText()
        elif t == 'upload':
            w = safe_get('selector_edit')
            if w and sender is w: step.selector = w.text()
            w = safe_get('path_edit')
            if w and sender is w: step.upload_file_path = w.text()
        elif t == 'python':
            w = safe_get('code_edit')
            if w and sender is w: step.python_code = w.toPlainText()
        elif t == 'desktop_focus':
            w = safe_get('title_edit')
            if w and sender is w: step.value = w.text()
        elif t == 'desktop_click':
            w = safe_get('selector_edit')
            if w and sender is w: step.selector = w.text()
        elif t == 'desktop_input':
            w = safe_get('selector_edit')
            if w and sender is w: step.selector = w.text()
            w = safe_get('value_edit')
            if w and sender is w: step.value = w.toPlainText()
        elif t == 'desktop_wait':
            w = safe_get('selector_edit')
            if w and sender is w: step.selector = w.text()
            w = safe_get('timeout_edit')
            if w and sender is w: step.timeout = int(w.text() or 5000)
        elif t == 'desktop_extract':
            w = safe_get('selector_edit')
            if w and sender is w: step.selector = w.text()
            w = safe_get('field_edit')
            if w and sender is w: step.field_name = w.text()
            w = safe_get('attr_edit')
            if w and sender is w: step.extract_attr = w.text()
        elif t == 'desktop_python':
            w = safe_get('code_edit')
            if w and sender is w: step.python_code = w.toPlainText()

        # 预等待控件
        if sender in (self.chk_pre_wait, self.spin_pre_wait, self.chk_pre_wait_element, self.pre_wait_xpath_edit):
            step.pre_wait_seconds = self.spin_pre_wait.value() if self.chk_pre_wait.isChecked() else 0
            step.pre_wait_element = self.chk_pre_wait_element.isChecked() and self.chk_pre_wait.isChecked()
            step.pre_wait_xpath = self.pre_wait_xpath_edit.text() if step.pre_wait_element else ""

        item = self.step_tree.currentItem()
        if isinstance(item, StepTreeItem):
            item.update_display()
            
    def _on_type_switched(self, index):
        if not self.current_step:
            return
        type_map = {0: 'navigate', 1: 'click', 2: 'input', 3: 'extract', 4: 'wait', 5: 'js', 6: 'upload', 7: 'python', 8: 'desktop_focus', 9: 'desktop_click', 10: 'desktop_input', 11: 'desktop_wait', 12: 'desktop_extract', 13: 'desktop_python', 14: 'group'}
        new_type = type_map.get(index, 'click')
        self.current_step.type = new_type
        self._populate_controls(self.current_step)

    # ===================== 窗口事件 =====================
    def eventFilter(self, obj, event):
        if obj == self and event.type() == QEvent.WindowStateChange:
            if self.isMinimized():
                self.hide()
                self.tray_icon.show()
                return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        event.ignore()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.showNormal()
            self.activateWindow()
            self.tray_icon.hide()

    # ===================== 开机自启 =====================
    def _get_autostart_status(self):
        try:
            key = reg.OpenKey(reg.HKEY_CURRENT_USER,
                              r"Software\Microsoft\Windows\CurrentVersion\Run", 0, reg.KEY_READ)
            value, _ = reg.QueryValueEx(key, APP_NAME)
            reg.CloseKey(key)
            return value == self._startup_command()
        except FileNotFoundError:
            return False

    def _startup_command(self):
        return f'"{sys.executable}" --flow "{self.default_flow_path}" --auto-run'

    def _set_autostart(self, enabled):
        key = reg.OpenKey(reg.HKEY_CURRENT_USER,
                          r"Software\Microsoft\Windows\CurrentVersion\Run", 0, reg.KEY_SET_VALUE)
        if enabled:
            reg.SetValueEx(key, APP_NAME, 0, reg.REG_SZ, self._startup_command())
            self.statusBar().showMessage("已设置开机自启")
        else:
            try:
                reg.DeleteValue(key, APP_NAME)
                self.statusBar().showMessage("已取消开机自启")
            except FileNotFoundError:
                pass
        reg.CloseKey(key)

    def _on_delay_changed(self, value):
        self.delay_label.setText(f"{value} ms")
        self.run_engine.base_delay = value

    def _clear_table(self):
        self.data_table.setRowCount(0)

    def _export_csv(self):
        if self.data_table.rowCount() == 0:
            QMessageBox.information(self, "提示", "表格中没有数据")
            return
        filepath, _ = QFileDialog.getSaveFileName(self, "导出CSV", "data.csv", "CSV 文件 (*.csv);;所有文件 (*)")
        if not filepath:
            return
        try:
            with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow([self.data_table.horizontalHeaderItem(c).text() for c in range(3)])
                for r in range(self.data_table.rowCount()):
                    writer.writerow([self.data_table.item(r, c).text() if self.data_table.item(r, c) else '' for c in range(3)])
            self.statusBar().showMessage(f"数据已导出至 {filepath}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_element_picked(self, xpath, text, tag_name, node_type):
        new_id = len(self._build_flat_steps_list()) + 1
        step = FlowStep(id=new_id, type=node_type, selector=xpath)
        if node_type == 'input':
            step.value = text
        elif node_type == 'extract':
            step.field_name = f"字段{new_id}"
            step.extract_attr = "text"
            self._extract_data_immediately(step)
        item = StepTreeItem(step)
        self.step_tree.addTopLevelItem(item)
        self.step_tree.setCurrentItem(item)
        self.steps = self._build_flat_steps_list()
        self.statusBar().showMessage(f"已添加 {node_type} 节点")

    def _extract_data_immediately(self, step):
        xpath = step.selector.replace('\\', '\\\\').replace('`', '\\`')
        attr = step.extract_attr or 'text'
        js = f"""(function() {{
            let el = document.evaluate(`{xpath}`, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            if (el) {{
                let val = ('{attr}' === 'text') ? (el.innerText || el.textContent || '') : (el.getAttribute('{attr}') || '');
                return val.trim();
            }}
            return null;
        }})();"""
        self.browser.page().runJavaScript(js, lambda r, s=step: self._on_immediate_extract(r, s))

    def _on_immediate_extract(self, result, step):
        if result is None:
            self.statusBar().showMessage(f"即时提取失败：未找到元素 {step.selector[:50]}")
            return
        row = self.data_table.rowCount()
        self.data_table.insertRow(row)
        self.data_table.setItem(row, 0, QTableWidgetItem(step.field_name))
        self.data_table.setItem(row, 1, QTableWidgetItem(result))
        self.data_table.setItem(row, 2, QTableWidgetItem(step.selector))

    def _on_mode_changed(self, button):
        if button == self.btn_browse_mode:
            self.pick_manager.deactivate()
            self._stop_single_pick()
            self.statusBar().showMessage("浏览模式")
        else:
            self.pick_manager.activate()
            self.statusBar().showMessage("录制模式：点击页面元素自动添加步骤")

    # # ===================== 上传拦截管理（用于鼠标点击方案） =====================
    def _install_upload_interceptor(self):
        try:
            self.browser.page().chooseFiles.connect(self._on_choose_files)
        except:
            pass

    def _uninstall_upload_interceptor(self):
        try:
            self.browser.page().chooseFiles.disconnect(self._on_choose_files)
        except:
            pass

    def _on_choose_files(self, request):
        pending_file = self.run_engine.pending_upload_file
        if pending_file:
            urls = [QUrl.fromLocalFile(pending_file)]
            request.accept(urls)
            self.run_engine._on_upload_dialog_handled(True)
        else:
            request.reject()
        self._uninstall_upload_interceptor()
        
if __name__ == "__main__":

    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--remote-debugging-port=9222 --ignore-certificate-errors"
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow", type=str)
    parser.add_argument("--auto-run", action="store_true")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_DontUseNativeDialogs, True)   # 关键：禁用原生文件对话框
    if args.auto_run:
        win = MainWindow(auto_run_flow=args.flow if args.flow else None)
    else:
        win = MainWindow()
    win.show()
    sys.exit(app.exec())