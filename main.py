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
from lite_builder import LiteBuilderDialog
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
                          'wait': '等待', 'navigate': '打开网页', 'js': 'JS脚本', 'upload': '上传文件',
                          'excel_file': 'Excel文件管理', 'excel_sheet': 'Excel工作表管理',
                          'excel_table': 'Excel整表读写', 'excel_cell': 'Excel单元格读写',
                          'excel_rowcol': 'Excel行列编辑', 'excel_style': 'Excel样式格式',
                          'excel_formula': 'Excel公式', 'excel_image': 'Excel图片',
                          'excel_macro': 'Excel宏', 'email_send': '邮件自动发送'}
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
        self.type_combo.addItem("Excel文件管理", "excel_file")
        self.type_combo.addItem("Excel工作表管理", "excel_sheet")
        self.type_combo.addItem("Excel整表读写", "excel_table")
        self.type_combo.addItem("Excel单元格读写", "excel_cell")
        self.type_combo.addItem("Excel行列编辑", "excel_rowcol")
        self.type_combo.addItem("Excel样式格式", "excel_style")
        self.type_combo.addItem("Excel公式", "excel_formula")
        self.type_combo.addItem("Excel图片", "excel_image")
        self.type_combo.addItem("Excel宏", "excel_macro")
        self.type_combo.addItem("邮件自动发送", "email_send")
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
        self.btn_lite_builder = QPushButton("轻态搭建")
        tree_header.addWidget(self.btn_add_step)
        tree_header.addWidget(self.btn_del_step)
        tree_header.addWidget(self.btn_add_group)
        tree_header.addWidget(self.btn_lite_builder)
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
        self.type_switch_combo.addItem("板块（分组）", "group")
        self.type_switch_combo.addItem("Excel文件管理", "excel_file")
        self.type_switch_combo.addItem("Excel工作表管理", "excel_sheet")
        self.type_switch_combo.addItem("Excel整表读写", "excel_table")
        self.type_switch_combo.addItem("Excel单元格读写", "excel_cell")
        self.type_switch_combo.addItem("Excel行列编辑", "excel_rowcol")
        self.type_switch_combo.addItem("Excel样式格式", "excel_style")
        self.type_switch_combo.addItem("Excel公式", "excel_formula")
        self.type_switch_combo.addItem("Excel图片", "excel_image")
        self.type_switch_combo.addItem("Excel宏", "excel_macro")
        self.type_switch_combo.addItem("邮件自动发送", "email_send")
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
        types = ['navigate', 'click', 'input', 'extract', 'wait', 'group', 'js', 'upload',
                 'excel_file', 'excel_sheet', 'excel_table', 'excel_cell', 'excel_rowcol',
                 'excel_style', 'excel_formula', 'excel_image', 'excel_macro', 'email_send']
        for t in types:
            self.step_controls[t] = {}

        self._create_navigate_page()
        self._create_click_page()
        self._create_input_page()
        self._create_extract_page()
        self._create_wait_page()
        self._make_js_page()          # 索引 5
        self._make_upload_page()      # 索引 6
        self._create_group_page()     # 索引 7 （移到最后）
        self._make_excel_file_page()     # 索引 8
        self._make_excel_sheet_page()    # 索引 9
        self._make_excel_table_page()    # 索引 10
        self._make_excel_cell_page()     # 索引 11
        self._make_excel_rowcol_page()   # 索引 12
        self._make_excel_style_page()    # 索引 13
        self._make_excel_formula_page()  # 索引 14
        self._make_excel_image_page()    # 索引 15
        self._make_excel_macro_page()    # 索引 16
        self._make_email_send_page()     # 索引 17

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
                elif isinstance(widget, QComboBox):
                    widget.currentIndexChanged.connect(self._on_any_control_changed)

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

    # ===================== Excel 页面辅助 =====================
    def _set_combo_data(self, combo, data):
        for i in range(combo.count()):
            if combo.itemData(i) == data:
                combo.setCurrentIndex(i)
                return
        if combo.count() > 0:
            combo.setCurrentIndex(0)

    def _browse_open_file(self, edit, caption="选择文件", filt="Excel 文件 (*.xlsx *.xlsm);;所有文件 (*)"):
        path, _ = QFileDialog.getOpenFileName(self, caption, edit.text(), filt)
        if path:
            edit.setText(path)

    def _browse_save_file(self, edit, caption="另存为", filt="Excel 文件 (*.xlsx);;所有文件 (*)"):
        path, _ = QFileDialog.getSaveFileName(self, caption, edit.text(), filt)
        if path:
            edit.setText(path)

    def _excel_path_row(self, layout, ctrls, key, label, browse='open'):
        layout.addWidget(QLabel(label))
        row = QHBoxLayout()
        edit = QLineEdit(); row.addWidget(edit)
        btn = QPushButton("浏览...")
        if browse == 'save':
            btn.clicked.connect(lambda: self._browse_save_file(edit))
        elif browse == 'image':
            btn.clicked.connect(lambda: self._browse_open_file(edit, "选择图片", "图片 (*.png *.jpg *.jpeg *.bmp *.gif);;所有文件 (*)"))
        else:
            btn.clicked.connect(lambda: self._browse_open_file(edit))
        row.addWidget(btn)
        layout.addLayout(row)
        ctrls[key] = edit
        return edit

    def _excel_page_head(self, type_key, actions):
        page = QWidget(); layout = QVBoxLayout(page)
        ctrls = self.step_controls[type_key]
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:")); layout.addWidget(name_edit)
        ctrls['name_edit'] = name_edit
        layout.addWidget(QLabel("操作:"))
        action_combo = QComboBox()
        for label, data in actions:
            action_combo.addItem(label, data)
        layout.addWidget(action_combo)
        ctrls['action_combo'] = action_combo
        return page, layout, ctrls

    def _make_excel_file_page(self):
        page, layout, ctrls = self._excel_page_head('excel_file', [
            ("新建工作簿", "new"), ("打开工作簿", "open"),
            ("保存", "save"), ("另存为", "save_as"), ("关闭", "close")])
        self._excel_path_row(layout, ctrls, 'file_path_edit', "工作簿路径:", 'open')
        self._excel_path_row(layout, ctrls, 'save_path_edit', "另存为目标路径:", 'save')
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_excel_sheet_page(self):
        page, layout, ctrls = self._excel_page_head('excel_sheet', [
            ("新增工作表", "add"), ("删除工作表", "delete"),
            ("重命名工作表", "rename"), ("切换工作表", "switch")])
        self._excel_path_row(layout, ctrls, 'file_path_edit', "工作簿路径:", 'open')
        layout.addWidget(QLabel("现有工作表名:"))
        e = QLineEdit(); layout.addWidget(e); ctrls['sheet_name_edit'] = e
        layout.addWidget(QLabel("新工作表名（新增/重命名用）:"))
        e2 = QLineEdit(); layout.addWidget(e2); ctrls['new_sheet_name_edit'] = e2
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_excel_table_page(self):
        page, layout, ctrls = self._excel_page_head('excel_table', [
            ("读取整表", "read_all"), ("写入整表", "write_all"), ("清空整表", "clear_all")])
        self._excel_path_row(layout, ctrls, 'file_path_edit', "工作簿路径:", 'open')
        layout.addWidget(QLabel("工作表名（空=活动表）:"))
        e = QLineEdit(); layout.addWidget(e); ctrls['sheet_name_edit'] = e
        layout.addWidget(QLabel("字段名（读取结果存入）:"))
        f = QLineEdit(); layout.addWidget(f); ctrls['field_edit'] = f
        layout.addWidget(QLabel("起始单元格（写入用）:"))
        sc = QLineEdit("A1"); layout.addWidget(sc); ctrls['start_cell_edit'] = sc
        layout.addWidget(QLabel("数据（JSON 二维数组，写入用）:"))
        rd = QTextEdit(); layout.addWidget(rd); ctrls['range_data_edit'] = rd
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_excel_cell_page(self):
        page, layout, ctrls = self._excel_page_head('excel_cell', [
            ("读取单元格", "read_cell"), ("写入单元格", "write_cell"),
            ("读取区域", "read_range"), ("写入区域", "write_range")])
        self._excel_path_row(layout, ctrls, 'file_path_edit', "工作簿路径:", 'open')
        layout.addWidget(QLabel("工作表名（空=活动表）:"))
        e = QLineEdit(); layout.addWidget(e); ctrls['sheet_name_edit'] = e
        layout.addWidget(QLabel("单元格/区域引用（如 A1 或 A1:C10）:"))
        cr = QLineEdit(); layout.addWidget(cr); ctrls['cell_ref_edit'] = cr
        layout.addWidget(QLabel("写入值（单元格用）:"))
        v = QLineEdit(); layout.addWidget(v); ctrls['value_edit'] = v
        layout.addWidget(QLabel("字段名（读取结果存入）:"))
        f = QLineEdit(); layout.addWidget(f); ctrls['field_edit'] = f
        layout.addWidget(QLabel("数据（JSON 二维数组，写入区域用）:"))
        rd = QTextEdit(); layout.addWidget(rd); ctrls['range_data_edit'] = rd
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_excel_rowcol_page(self):
        page, layout, ctrls = self._excel_page_head('excel_rowcol', [
            ("插入行", "insert_row"), ("删除行", "delete_row"),
            ("插入列", "insert_col"), ("删除列", "delete_col")])
        self._excel_path_row(layout, ctrls, 'file_path_edit', "工作簿路径:", 'open')
        layout.addWidget(QLabel("工作表名（空=活动表）:"))
        e = QLineEdit(); layout.addWidget(e); ctrls['sheet_name_edit'] = e
        layout.addWidget(QLabel("行号或列字母（如 3 或 C）:"))
        r = QLineEdit(); layout.addWidget(r); ctrls['ref_edit'] = r
        layout.addWidget(QLabel("数量:"))
        cs = QSpinBox(); cs.setRange(1, 1000); cs.setValue(1); layout.addWidget(cs); ctrls['count_spin'] = cs
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_excel_style_page(self):
        page, layout, ctrls = self._excel_page_head('excel_style', [
            ("设置字体", "set_font"), ("设置填充色", "set_fill_color"), ("设置边框", "set_border")])
        self._excel_path_row(layout, ctrls, 'file_path_edit', "工作簿路径:", 'open')
        layout.addWidget(QLabel("工作表名（空=活动表）:"))
        e = QLineEdit(); layout.addWidget(e); ctrls['sheet_name_edit'] = e
        layout.addWidget(QLabel("单元格/区域引用:"))
        cr = QLineEdit(); layout.addWidget(cr); ctrls['cell_ref_edit'] = cr
        layout.addWidget(QLabel("字体名:"))
        fn = QLineEdit(); layout.addWidget(fn); ctrls['font_name_edit'] = fn
        layout.addWidget(QLabel("字号（0=不修改）:"))
        fs = QSpinBox(); fs.setRange(0, 200); layout.addWidget(fs); ctrls['font_size_spin'] = fs
        fb = QCheckBox("加粗"); layout.addWidget(fb); ctrls['font_bold_chk'] = fb
        layout.addWidget(QLabel("字体颜色（十六进制，如 FF0000）:"))
        fc = QLineEdit(); layout.addWidget(fc); ctrls['font_color_edit'] = fc
        layout.addWidget(QLabel("填充色（十六进制，如 FFFF00）:"))
        flc = QLineEdit(); layout.addWidget(flc); ctrls['fill_color_edit'] = flc
        layout.addWidget(QLabel("边框样式:"))
        bc = QComboBox()
        for label, data in [("无", "none"), ("细", "thin"), ("中", "medium"), ("粗", "thick")]:
            bc.addItem(label, data)
        layout.addWidget(bc); ctrls['border_combo'] = bc
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_excel_formula_page(self):
        page, layout, ctrls = self._excel_page_head('excel_formula', [
            ("写入公式", "write_formula"), ("读取计算结果", "read_value")])
        self._excel_path_row(layout, ctrls, 'file_path_edit', "工作簿路径:", 'open')
        layout.addWidget(QLabel("工作表名（空=活动表）:"))
        e = QLineEdit(); layout.addWidget(e); ctrls['sheet_name_edit'] = e
        layout.addWidget(QLabel("单元格引用（如 B1）:"))
        cr = QLineEdit(); layout.addWidget(cr); ctrls['cell_ref_edit'] = cr
        layout.addWidget(QLabel("公式（如 =SUM(A1:A10)）:"))
        fm = QLineEdit(); layout.addWidget(fm); ctrls['formula_edit'] = fm
        layout.addWidget(QLabel("字段名（读取结果存入）:"))
        f = QLineEdit(); layout.addWidget(f); ctrls['field_edit'] = f
        layout.addWidget(QLabel("提示：读取计算结果将用本机 Excel 计算（需已安装 Excel）"))
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_excel_image_page(self):
        page, layout, ctrls = self._excel_page_head('excel_image', [
            ("插入图片", "insert_image"), ("清除全部图片", "clear_images")])
        self._excel_path_row(layout, ctrls, 'file_path_edit', "工作簿路径:", 'open')
        layout.addWidget(QLabel("工作表名（空=活动表）:"))
        e = QLineEdit(); layout.addWidget(e); ctrls['sheet_name_edit'] = e
        layout.addWidget(QLabel("锚点单元格（如 A1）:"))
        cr = QLineEdit(); layout.addWidget(cr); ctrls['cell_ref_edit'] = cr
        self._excel_path_row(layout, ctrls, 'image_path_edit', "图片路径:", 'image')
        layout.addWidget(QLabel("宽度（0=原始大小）:"))
        w = QSpinBox(); w.setRange(0, 5000); layout.addWidget(w); ctrls['width_spin'] = w
        layout.addWidget(QLabel("高度（0=原始大小）:"))
        h = QSpinBox(); h.setRange(0, 5000); layout.addWidget(h); ctrls['height_spin'] = h
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_excel_macro_page(self):
        page, layout, ctrls = self._excel_page_head('excel_macro', [
            ("运行宏", "run_macro")])
        self._excel_path_row(layout, ctrls, 'file_path_edit', "工作簿路径:", 'open')
        layout.addWidget(QLabel("宏名称（如 Module1.MacroName）:"))
        mn = QLineEdit(); layout.addWidget(mn); ctrls['macro_name_edit'] = mn
        layout.addWidget(QLabel("参数（简单参数，逗号分隔，可为空）:"))
        ma = QLineEdit(); layout.addWidget(ma); ctrls['macro_args_edit'] = ma
        mv = QCheckBox("显示Excel窗口"); layout.addWidget(mv); ctrls['macro_visible_chk'] = mv
        layout.addStretch()
        self.config_stack.addWidget(page)

    def _make_email_send_page(self):
        from PySide6.QtWidgets import QScrollArea
        ctrls = self.step_controls['email_send']

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)

        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:")); layout.addWidget(name_edit)
        ctrls['name_edit'] = name_edit
        layout.addWidget(QLabel("SMTP 服务器:"))
        server_edit = QLineEdit(); layout.addWidget(server_edit); ctrls['smtp_server_edit'] = server_edit
        layout.addWidget(QLabel("SMTP 端口:"))
        port_spin = QSpinBox(); port_spin.setRange(1, 65535); port_spin.setValue(465)
        layout.addWidget(port_spin); ctrls['smtp_port_spin'] = port_spin
        ssl_chk = QCheckBox("使用 SSL（465 端口一般勾选，587 端口用 STARTTLS 请取消勾选）")
        ssl_chk.setChecked(True)
        layout.addWidget(ssl_chk); ctrls['use_ssl_chk'] = ssl_chk
        layout.addWidget(QLabel("发件账号:"))
        account_edit = QLineEdit(); layout.addWidget(account_edit); ctrls['account_edit'] = account_edit
        layout.addWidget(QLabel("发件密码/授权码:"))
        password_edit = QLineEdit(); password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(password_edit); ctrls['password_edit'] = password_edit
        layout.addWidget(QLabel("收件人（逗号分隔，或从文件导入每行一个地址）:"))
        to_row = QHBoxLayout()
        to_edit = QLineEdit(); to_row.addWidget(to_edit); ctrls['to_edit'] = to_edit
        to_import_btn = QPushButton("从文件导入")
        def _import_recipients(te=to_edit):
            path, _ = QFileDialog.getOpenFileName(None, "选择收件人文件", "", "文本文件 (*.txt);;所有文件 (*)")
            if path:
                with open(path, encoding='utf-8', errors='replace') as f:
                    addrs = [a.strip() for line in f for a in line.split(',') if a.strip()]
                te.setText(', '.join(addrs))
        to_import_btn.clicked.connect(_import_recipients)
        to_row.addWidget(to_import_btn)
        layout.addLayout(to_row)
        layout.addWidget(QLabel("抄送（逗号分隔，可为空）:"))
        cc_edit = QLineEdit(); layout.addWidget(cc_edit); ctrls['cc_edit'] = cc_edit
        layout.addWidget(QLabel("密送（逗号分隔，可为空）:"))
        bcc_edit = QLineEdit(); layout.addWidget(bcc_edit); ctrls['bcc_edit'] = bcc_edit
        layout.addWidget(QLabel("主题（支持 {today} 等日期占位符）:"))
        subject_edit = QLineEdit(); layout.addWidget(subject_edit); ctrls['subject_edit'] = subject_edit
        layout.addWidget(QLabel("正文（HTML富文本，支持 {today} 等日期占位符）:"))
        body_edit = QTextEdit(); body_edit.setAcceptRichText(True); body_edit.setMinimumHeight(120); layout.addWidget(body_edit); ctrls['body_edit'] = body_edit
        self._excel_path_row(layout, ctrls, 'attachment_path_edit',
                              "附件路径（支持 {today}、{download:latest} 等，留空则不带附件）:", 'open')
        layout.addStretch()

        scroll_area = QScrollArea()
        scroll_area.setWidget(content_widget)
        scroll_area.setWidgetResizable(True)
        self.config_stack.addWidget(scroll_area)

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
        self.btn_lite_builder.clicked.connect(self._open_lite_builder)
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
                'excel_action': item.step_data.excel_action,
                'excel_file_path': item.step_data.excel_file_path,
                'excel_save_path': item.step_data.excel_save_path,
                'excel_sheet_name': item.step_data.excel_sheet_name,
                'excel_new_sheet_name': item.step_data.excel_new_sheet_name,
                'excel_cell_ref': item.step_data.excel_cell_ref,
                'excel_range_data': item.step_data.excel_range_data,
                'excel_row_col_ref': item.step_data.excel_row_col_ref,
                'excel_count': item.step_data.excel_count,
                'excel_font_name': item.step_data.excel_font_name,
                'excel_font_size': item.step_data.excel_font_size,
                'excel_font_bold': item.step_data.excel_font_bold,
                'excel_font_color': item.step_data.excel_font_color,
                'excel_fill_color': item.step_data.excel_fill_color,
                'excel_border_style': item.step_data.excel_border_style,
                'excel_formula_text': item.step_data.excel_formula_text,
                'excel_image_path': item.step_data.excel_image_path,
                'excel_image_width': item.step_data.excel_image_width,
                'excel_image_height': item.step_data.excel_image_height,
                'excel_macro_name': item.step_data.excel_macro_name,
                'excel_macro_args': item.step_data.excel_macro_args,
                'excel_macro_visible': item.step_data.excel_macro_visible,
                'email_smtp_server': item.step_data.email_smtp_server,
                'email_smtp_port': item.step_data.email_smtp_port,
                'email_use_ssl': item.step_data.email_use_ssl,
                'email_account': item.step_data.email_account,
                'email_password': item.step_data.email_password,
                'email_to': item.step_data.email_to,
                'email_cc': item.step_data.email_cc,
                'email_bcc': item.step_data.email_bcc,
                'email_subject': item.step_data.email_subject,
                'email_body': item.step_data.email_body,
                'email_attachment_path': item.step_data.email_attachment_path
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
            id=step_dict.get('id', 0), type=step_dict.get('type', 'click'),
            name=step_dict.get('name', ''), selector=step_dict.get('selector', ''),
            selector_type=step_dict.get('selector_type', 'xpath'),
            value=step_dict.get('value', ''), field_name=step_dict.get('field_name', ''),
            extract_attr=step_dict.get('extract_attr', 'text'), timeout=step_dict.get('timeout', 5000),
            pre_wait_seconds=step_dict.get('pre_wait_seconds', 0),
            pre_wait_element=step_dict.get('pre_wait_element', False),
            pre_wait_xpath=step_dict.get('pre_wait_xpath', ''),
            date_format=step_dict.get('date_format', ''),
            use_today=step_dict.get('use_today', True), fixed_date=step_dict.get('fixed_date', ''),
            js_code=step_dict.get('js_code', ''),
            upload_file_path=step_dict.get('upload_file_path', ''),
            excel_action=step_dict.get('excel_action', ''),
            excel_file_path=step_dict.get('excel_file_path', ''),
            excel_save_path=step_dict.get('excel_save_path', ''),
            excel_sheet_name=step_dict.get('excel_sheet_name', ''),
            excel_new_sheet_name=step_dict.get('excel_new_sheet_name', ''),
            excel_cell_ref=step_dict.get('excel_cell_ref', ''),
            excel_range_data=step_dict.get('excel_range_data', ''),
            excel_row_col_ref=step_dict.get('excel_row_col_ref', ''),
            excel_count=step_dict.get('excel_count', 1),
            excel_font_name=step_dict.get('excel_font_name', ''),
            excel_font_size=step_dict.get('excel_font_size', 0),
            excel_font_bold=step_dict.get('excel_font_bold', False),
            excel_font_color=step_dict.get('excel_font_color', ''),
            excel_fill_color=step_dict.get('excel_fill_color', ''),
            excel_border_style=step_dict.get('excel_border_style', ''),
            excel_formula_text=step_dict.get('excel_formula_text', ''),
            excel_image_path=step_dict.get('excel_image_path', ''),
            excel_image_width=step_dict.get('excel_image_width', 0),
            excel_image_height=step_dict.get('excel_image_height', 0),
            excel_macro_name=step_dict.get('excel_macro_name', ''),
            excel_macro_args=step_dict.get('excel_macro_args', ''),
            excel_macro_visible=step_dict.get('excel_macro_visible', False),
            email_smtp_server=step_dict.get('email_smtp_server', ''),
            email_smtp_port=step_dict.get('email_smtp_port', 465),
            email_use_ssl=step_dict.get('email_use_ssl', True),
            email_account=step_dict.get('email_account', ''),
            email_password=step_dict.get('email_password', ''),
            email_to=step_dict.get('email_to', ''),
            email_cc=step_dict.get('email_cc', ''),
            email_bcc=step_dict.get('email_bcc', ''),
            email_subject=step_dict.get('email_subject', ''),
            email_body=step_dict.get('email_body', ''),
            email_attachment_path=step_dict.get('email_attachment_path', '')
        )
        item = StepTreeItem(step)
        parent.addChild(item)
        for child_data in data.get('children', []):
            self._deserialize_tree_item(child_data, item)
        return item

    def _save_flow(self):
        if self.step_tree.topLevelItemCount() == 0:
            QMessageBox.warning(self, "警告", "没有步骤可保存")
            return
        filepath, _ = QFileDialog.getSaveFileName(self, "保存流程", self.current_file_path or "flow.json",
                                                  "JSON 文件 (*.json);;所有文件 (*)")
        if not filepath:
            return
        try:
            tree_data = []
            for i in range(self.step_tree.topLevelItemCount()):
                item = self.step_tree.topLevelItem(i)
                if isinstance(item, StepTreeItem):
                    tree_data.append(self._serialize_tree_item(item))
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(tree_data, f, ensure_ascii=False, indent=2)
            self.current_file_path = filepath
            self.statusBar().showMessage(f"流程已保存至 {filepath}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def _load_flow(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "加载流程", "", "JSON 文件 (*.json);;所有文件 (*)")
        if not filepath:
            return
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                tree_data = json.load(f)
            self.step_tree.clear()
            for item_data in tree_data:
                self._deserialize_tree_item(item_data, self.step_tree.invisibleRootItem())
            self.steps = self._build_flat_steps_list()
            self.current_file_path = filepath
            self.statusBar().showMessage(f"已加载流程：{filepath}")
        except Exception as e:
            QMessageBox.critical(self, "加载失败", str(e))

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
                      'wait': '等待', 'navigate': '打开网页', 'group': '板块', 'js': 'JS脚本', 'upload': '上传文件',
                      'excel_file': 'Excel文件管理', 'excel_sheet': 'Excel工作表管理',
                      'excel_table': 'Excel整表读写', 'excel_cell': 'Excel单元格读写',
                      'excel_rowcol': 'Excel行列编辑', 'excel_style': 'Excel样式格式',
                      'excel_formula': 'Excel公式', 'excel_image': 'Excel图片',
                      'excel_macro': 'Excel宏', 'email_send': '邮件自动发送'}
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
        elif node_type == 'excel_file':
            step.excel_action = 'new'
        elif node_type == 'excel_sheet':
            step.excel_action = 'add'
        elif node_type == 'excel_table':
            step.excel_action = 'read_all'
            step.excel_cell_ref = 'A1'
        elif node_type == 'excel_cell':
            step.excel_action = 'write_cell'
            step.excel_cell_ref = 'A1'
        elif node_type == 'excel_rowcol':
            step.excel_action = 'insert_row'
        elif node_type == 'excel_style':
            step.excel_action = 'set_font'
            step.excel_cell_ref = 'A1'
        elif node_type == 'excel_formula':
            step.excel_action = 'write_formula'
            step.excel_cell_ref = 'A1'
        elif node_type == 'excel_image':
            step.excel_action = 'insert_image'
            step.excel_cell_ref = 'A1'
        elif node_type == 'excel_macro':
            step.excel_action = 'run_macro'
        elif node_type == 'email_send':
            step.email_smtp_port = 465
            step.email_use_ssl = True
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

    def _add_email_step(self):
        new_id = len(self._build_flat_steps_list()) + 1
        step = FlowStep(id=new_id, type='email_send')
        count = self._get_type_count('email_send') + 1
        step.name = f"邮件自动发送{count}"
        step.email_smtp_port = 465
        step.email_use_ssl = True
        item = StepTreeItem(step)
        current = self.step_tree.currentItem()
        if current and isinstance(current, StepTreeItem):
            if current.step_data.type == 'group':
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

    def _open_lite_builder(self):
        dlg = LiteBuilderDialog(self)
        dlg.run_requested.connect(self._run_lite_builder_steps)
        dlg.sync_requested.connect(self._sync_lite_builder_steps)
        dlg.exec()

    def _sync_lite_builder_steps(self, tree_dicts):
        for item_data in tree_dicts:
            self._deserialize_tree_item(item_data, self.step_tree.invisibleRootItem())
        self.steps = self._build_flat_steps_list()

    def _run_lite_builder_steps(self, steps):
        if self.run_engine.running:
            QMessageBox.warning(self, "警告", "已有流程正在运行，请先停止")
            return
        if not steps:
            QMessageBox.warning(self, "警告", "没有可执行的流程步骤")
            return
        self.running_steps = steps
        self.run_engine.simulated_date = None
        self.btn_browse_mode.setChecked(True)
        self.pick_manager.deactivate()
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.statusBar().showMessage("轻态搭建流程运行中...")
        self.data_table.setRowCount(0)
        self.run_engine.run(steps)

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
        if step.type in self.step_controls and 'name_edit' in self.step_controls[step.type]:
            self.step_controls[step.type]['name_edit'].setText(step.name)
        if step.type == 'navigate':
            c = self.step_controls['navigate']
            c['url_edit'].setText(step.value); c['timeout_edit'].setText(str(step.timeout))
        elif step.type == 'click':
            c = self.step_controls['click']
            c['selector_edit'].setText(step.selector); c['timeout_edit'].setText(str(step.timeout))
        elif step.type == 'input':
            c = self.step_controls['input']
            c['selector_edit'].setText(step.selector); c['value_edit'].setPlainText(step.value)
        elif step.type == 'extract':
            c = self.step_controls['extract']
            c['selector_edit'].setText(step.selector); c['field_edit'].setText(step.field_name); c['attr_edit'].setText(step.extract_attr)
        elif step.type == 'wait':
            c = self.step_controls['wait']
            c['timeout_edit'].setText(str(step.timeout))
        elif step.type == 'js':
            c = self.step_controls['js']
            c['field_edit'].setText(step.field_name); c['code_edit'].setPlainText(step.js_code)
        elif step.type == 'upload':
            c = self.step_controls['upload']
            c['selector_edit'].setText(step.selector); c['path_edit'].setText(step.upload_file_path)
        elif step.type == 'excel_file':
            c = self.step_controls['excel_file']
            self._set_combo_data(c['action_combo'], step.excel_action)
            c['file_path_edit'].setText(step.excel_file_path)
            c['save_path_edit'].setText(step.excel_save_path)
        elif step.type == 'excel_sheet':
            c = self.step_controls['excel_sheet']
            self._set_combo_data(c['action_combo'], step.excel_action)
            c['file_path_edit'].setText(step.excel_file_path)
            c['sheet_name_edit'].setText(step.excel_sheet_name)
            c['new_sheet_name_edit'].setText(step.excel_new_sheet_name)
        elif step.type == 'excel_table':
            c = self.step_controls['excel_table']
            self._set_combo_data(c['action_combo'], step.excel_action)
            c['file_path_edit'].setText(step.excel_file_path)
            c['sheet_name_edit'].setText(step.excel_sheet_name)
            c['field_edit'].setText(step.field_name)
            c['start_cell_edit'].setText(step.excel_cell_ref)
            c['range_data_edit'].setPlainText(step.excel_range_data)
        elif step.type == 'excel_cell':
            c = self.step_controls['excel_cell']
            self._set_combo_data(c['action_combo'], step.excel_action)
            c['file_path_edit'].setText(step.excel_file_path)
            c['sheet_name_edit'].setText(step.excel_sheet_name)
            c['cell_ref_edit'].setText(step.excel_cell_ref)
            c['value_edit'].setText(step.value)
            c['field_edit'].setText(step.field_name)
            c['range_data_edit'].setPlainText(step.excel_range_data)
        elif step.type == 'excel_rowcol':
            c = self.step_controls['excel_rowcol']
            self._set_combo_data(c['action_combo'], step.excel_action)
            c['file_path_edit'].setText(step.excel_file_path)
            c['sheet_name_edit'].setText(step.excel_sheet_name)
            c['ref_edit'].setText(step.excel_row_col_ref)
            c['count_spin'].setValue(step.excel_count)
        elif step.type == 'excel_style':
            c = self.step_controls['excel_style']
            self._set_combo_data(c['action_combo'], step.excel_action)
            c['file_path_edit'].setText(step.excel_file_path)
            c['sheet_name_edit'].setText(step.excel_sheet_name)
            c['cell_ref_edit'].setText(step.excel_cell_ref)
            c['font_name_edit'].setText(step.excel_font_name)
            c['font_size_spin'].setValue(step.excel_font_size)
            c['font_bold_chk'].setChecked(step.excel_font_bold)
            c['font_color_edit'].setText(step.excel_font_color)
            c['fill_color_edit'].setText(step.excel_fill_color)
            self._set_combo_data(c['border_combo'], step.excel_border_style)
        elif step.type == 'excel_formula':
            c = self.step_controls['excel_formula']
            self._set_combo_data(c['action_combo'], step.excel_action)
            c['file_path_edit'].setText(step.excel_file_path)
            c['sheet_name_edit'].setText(step.excel_sheet_name)
            c['cell_ref_edit'].setText(step.excel_cell_ref)
            c['formula_edit'].setText(step.excel_formula_text)
            c['field_edit'].setText(step.field_name)
        elif step.type == 'excel_image':
            c = self.step_controls['excel_image']
            self._set_combo_data(c['action_combo'], step.excel_action)
            c['file_path_edit'].setText(step.excel_file_path)
            c['sheet_name_edit'].setText(step.excel_sheet_name)
            c['cell_ref_edit'].setText(step.excel_cell_ref)
            c['image_path_edit'].setText(step.excel_image_path)
            c['width_spin'].setValue(step.excel_image_width)
            c['height_spin'].setValue(step.excel_image_height)
        elif step.type == 'excel_macro':
            c = self.step_controls['excel_macro']
            self._set_combo_data(c['action_combo'], step.excel_action)
            c['file_path_edit'].setText(step.excel_file_path)
            c['macro_name_edit'].setText(step.excel_macro_name)
            c['macro_args_edit'].setText(step.excel_macro_args)
            c['macro_visible_chk'].setChecked(step.excel_macro_visible)
        elif step.type == 'email_send':
            c = self.step_controls['email_send']
            c['smtp_server_edit'].setText(step.email_smtp_server)
            c['smtp_port_spin'].setValue(step.email_smtp_port)
            c['use_ssl_chk'].setChecked(step.email_use_ssl)
            c['account_edit'].setText(step.email_account)
            c['password_edit'].setText(step.email_password)
            c['to_edit'].setText(step.email_to)
            c['cc_edit'].setText(step.email_cc)
            c['bcc_edit'].setText(step.email_bcc)
            c['subject_edit'].setText(step.email_subject)
            c['body_edit'].setHtml(step.email_body)
            c['attachment_path_edit'].setText(step.email_attachment_path)
        show_pre = step.type not in ('wait', 'group', 'js', 'upload',
                                     'excel_file', 'excel_sheet', 'excel_table', 'excel_cell',
                                     'excel_rowcol', 'excel_style', 'excel_formula', 'excel_image',
                                     'excel_macro', 'email_send')
        self.pre_wait_widget.setVisible(show_pre)
        if show_pre:
            self.chk_pre_wait.blockSignals(True); self.spin_pre_wait.blockSignals(True)
            self.chk_pre_wait_element.blockSignals(True); self.pre_wait_xpath_edit.blockSignals(True)
            self.chk_pre_wait.setChecked(step.pre_wait_seconds > 0)
            self.spin_pre_wait.setValue(step.pre_wait_seconds)
            self.chk_pre_wait_element.setChecked(step.pre_wait_element)
            self.pre_wait_xpath_edit.setText(step.pre_wait_xpath)
            self.chk_pre_wait.blockSignals(False); self.spin_pre_wait.blockSignals(False)
            self.chk_pre_wait_element.blockSignals(False); self.pre_wait_xpath_edit.blockSignals(False)
        type_map = {'navigate': 0, 'click': 1, 'input': 2, 'extract': 3, 'wait': 4, 'js': 5,
                    'upload': 6, 'group': 7, 'excel_file': 8, 'excel_sheet': 9, 'excel_table': 10,
                    'excel_cell': 11, 'excel_rowcol': 12, 'excel_style': 13, 'excel_formula': 14,
                    'excel_image': 15, 'excel_macro': 16, 'email_send': 17}
        idx = type_map.get(step.type, 0)
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
        for t, ctrls in self.step_controls.items():
            if ctrls.get('name_edit') is sender:
                step.name = sender.text()
                item = self.step_tree.currentItem()
                if isinstance(item, StepTreeItem):
                    item.update_display()
                return
        t = step.type
        ctrls = self.step_controls.get(t)
        if not ctrls:
            return
        if t == 'navigate':
            if sender is ctrls['url_edit']:
                step.value = ctrls['url_edit'].text()
            elif sender is ctrls['timeout_edit']:
                step.timeout = int(ctrls['timeout_edit'].text() or 3000)
        elif t == 'click':
            if sender is ctrls['selector_edit']:
                step.selector = ctrls['selector_edit'].text()
            elif sender is ctrls['timeout_edit']:
                step.timeout = int(ctrls['timeout_edit'].text() or 5000)
        elif t == 'input':
            if sender is ctrls['selector_edit']:
                step.selector = ctrls['selector_edit'].text()
            elif sender is ctrls['value_edit']:
                step.value = ctrls['value_edit'].toPlainText()
        elif t == 'extract':
            if sender is ctrls['selector_edit']:
                step.selector = ctrls['selector_edit'].text()
            elif sender is ctrls['field_edit']:
                step.field_name = ctrls['field_edit'].text()
            elif sender is ctrls['attr_edit']:
                step.extract_attr = ctrls['attr_edit'].text()
        elif t == 'wait':
            if sender is ctrls['timeout_edit']:
                step.timeout = int(ctrls['timeout_edit'].text() or 10000)
        elif t == 'js':
            if sender is ctrls['field_edit']:
                step.field_name = ctrls['field_edit'].text()
            elif sender is ctrls['code_edit']:
                step.js_code = ctrls['code_edit'].toPlainText()
        elif t == 'upload':
            if sender is ctrls['selector_edit']:
                step.selector = ctrls['selector_edit'].text()
            elif sender is ctrls['path_edit']:
                step.upload_file_path = ctrls['path_edit'].text()
        elif t == 'excel_file':
            if sender is ctrls['action_combo']:
                step.excel_action = ctrls['action_combo'].currentData()
            elif sender is ctrls['file_path_edit']:
                step.excel_file_path = ctrls['file_path_edit'].text()
            elif sender is ctrls['save_path_edit']:
                step.excel_save_path = ctrls['save_path_edit'].text()
        elif t == 'excel_sheet':
            if sender is ctrls['action_combo']:
                step.excel_action = ctrls['action_combo'].currentData()
            elif sender is ctrls['file_path_edit']:
                step.excel_file_path = ctrls['file_path_edit'].text()
            elif sender is ctrls['sheet_name_edit']:
                step.excel_sheet_name = ctrls['sheet_name_edit'].text()
            elif sender is ctrls['new_sheet_name_edit']:
                step.excel_new_sheet_name = ctrls['new_sheet_name_edit'].text()
        elif t == 'excel_table':
            if sender is ctrls['action_combo']:
                step.excel_action = ctrls['action_combo'].currentData()
            elif sender is ctrls['file_path_edit']:
                step.excel_file_path = ctrls['file_path_edit'].text()
            elif sender is ctrls['sheet_name_edit']:
                step.excel_sheet_name = ctrls['sheet_name_edit'].text()
            elif sender is ctrls['field_edit']:
                step.field_name = ctrls['field_edit'].text()
            elif sender is ctrls['start_cell_edit']:
                step.excel_cell_ref = ctrls['start_cell_edit'].text()
            elif sender is ctrls['range_data_edit']:
                step.excel_range_data = ctrls['range_data_edit'].toPlainText()
        elif t == 'excel_cell':
            if sender is ctrls['action_combo']:
                step.excel_action = ctrls['action_combo'].currentData()
            elif sender is ctrls['file_path_edit']:
                step.excel_file_path = ctrls['file_path_edit'].text()
            elif sender is ctrls['sheet_name_edit']:
                step.excel_sheet_name = ctrls['sheet_name_edit'].text()
            elif sender is ctrls['cell_ref_edit']:
                step.excel_cell_ref = ctrls['cell_ref_edit'].text()
            elif sender is ctrls['value_edit']:
                step.value = ctrls['value_edit'].text()
            elif sender is ctrls['field_edit']:
                step.field_name = ctrls['field_edit'].text()
            elif sender is ctrls['range_data_edit']:
                step.excel_range_data = ctrls['range_data_edit'].toPlainText()
        elif t == 'excel_rowcol':
            if sender is ctrls['action_combo']:
                step.excel_action = ctrls['action_combo'].currentData()
            elif sender is ctrls['file_path_edit']:
                step.excel_file_path = ctrls['file_path_edit'].text()
            elif sender is ctrls['sheet_name_edit']:
                step.excel_sheet_name = ctrls['sheet_name_edit'].text()
            elif sender is ctrls['ref_edit']:
                step.excel_row_col_ref = ctrls['ref_edit'].text()
            elif sender is ctrls['count_spin']:
                step.excel_count = ctrls['count_spin'].value()
        elif t == 'excel_style':
            if sender is ctrls['action_combo']:
                step.excel_action = ctrls['action_combo'].currentData()
            elif sender is ctrls['file_path_edit']:
                step.excel_file_path = ctrls['file_path_edit'].text()
            elif sender is ctrls['sheet_name_edit']:
                step.excel_sheet_name = ctrls['sheet_name_edit'].text()
            elif sender is ctrls['cell_ref_edit']:
                step.excel_cell_ref = ctrls['cell_ref_edit'].text()
            elif sender is ctrls['font_name_edit']:
                step.excel_font_name = ctrls['font_name_edit'].text()
            elif sender is ctrls['font_size_spin']:
                step.excel_font_size = ctrls['font_size_spin'].value()
            elif sender is ctrls['font_bold_chk']:
                step.excel_font_bold = ctrls['font_bold_chk'].isChecked()
            elif sender is ctrls['font_color_edit']:
                step.excel_font_color = ctrls['font_color_edit'].text()
            elif sender is ctrls['fill_color_edit']:
                step.excel_fill_color = ctrls['fill_color_edit'].text()
            elif sender is ctrls['border_combo']:
                step.excel_border_style = ctrls['border_combo'].currentData()
        elif t == 'excel_formula':
            if sender is ctrls['action_combo']:
                step.excel_action = ctrls['action_combo'].currentData()
            elif sender is ctrls['file_path_edit']:
                step.excel_file_path = ctrls['file_path_edit'].text()
            elif sender is ctrls['sheet_name_edit']:
                step.excel_sheet_name = ctrls['sheet_name_edit'].text()
            elif sender is ctrls['cell_ref_edit']:
                step.excel_cell_ref = ctrls['cell_ref_edit'].text()
            elif sender is ctrls['formula_edit']:
                step.excel_formula_text = ctrls['formula_edit'].text()
            elif sender is ctrls['field_edit']:
                step.field_name = ctrls['field_edit'].text()
        elif t == 'excel_image':
            if sender is ctrls['action_combo']:
                step.excel_action = ctrls['action_combo'].currentData()
            elif sender is ctrls['file_path_edit']:
                step.excel_file_path = ctrls['file_path_edit'].text()
            elif sender is ctrls['sheet_name_edit']:
                step.excel_sheet_name = ctrls['sheet_name_edit'].text()
            elif sender is ctrls['cell_ref_edit']:
                step.excel_cell_ref = ctrls['cell_ref_edit'].text()
            elif sender is ctrls['image_path_edit']:
                step.excel_image_path = ctrls['image_path_edit'].text()
            elif sender is ctrls['width_spin']:
                step.excel_image_width = ctrls['width_spin'].value()
            elif sender is ctrls['height_spin']:
                step.excel_image_height = ctrls['height_spin'].value()
        elif t == 'excel_macro':
            if sender is ctrls['action_combo']:
                step.excel_action = ctrls['action_combo'].currentData()
            elif sender is ctrls['file_path_edit']:
                step.excel_file_path = ctrls['file_path_edit'].text()
            elif sender is ctrls['macro_name_edit']:
                step.excel_macro_name = ctrls['macro_name_edit'].text()
            elif sender is ctrls['macro_args_edit']:
                step.excel_macro_args = ctrls['macro_args_edit'].text()
            elif sender is ctrls['macro_visible_chk']:
                step.excel_macro_visible = ctrls['macro_visible_chk'].isChecked()
        elif t == 'email_send':
            if sender is ctrls['smtp_server_edit']:
                step.email_smtp_server = ctrls['smtp_server_edit'].text()
            elif sender is ctrls['smtp_port_spin']:
                step.email_smtp_port = ctrls['smtp_port_spin'].value()
            elif sender is ctrls['use_ssl_chk']:
                step.email_use_ssl = ctrls['use_ssl_chk'].isChecked()
            elif sender is ctrls['account_edit']:
                step.email_account = ctrls['account_edit'].text()
            elif sender is ctrls['password_edit']:
                step.email_password = ctrls['password_edit'].text()
            elif sender is ctrls['to_edit']:
                step.email_to = ctrls['to_edit'].text()
            elif sender is ctrls['cc_edit']:
                step.email_cc = ctrls['cc_edit'].text()
            elif sender is ctrls['bcc_edit']:
                step.email_bcc = ctrls['bcc_edit'].text()
            elif sender is ctrls['subject_edit']:
                step.email_subject = ctrls['subject_edit'].text()
            elif sender is ctrls['body_edit']:
                step.email_body = ctrls['body_edit'].toHtml()
            elif sender is ctrls['attachment_path_edit']:
                step.email_attachment_path = ctrls['attachment_path_edit'].text()
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
        type_map = {0: 'navigate', 1: 'click', 2: 'input', 3: 'extract', 4: 'wait', 5: 'js',
                    6: 'upload', 7: 'group', 8: 'excel_file', 9: 'excel_sheet', 10: 'excel_table',
                    11: 'excel_cell', 12: 'excel_rowcol', 13: 'excel_style', 14: 'excel_formula',
                    15: 'excel_image', 16: 'excel_macro', 17: 'email_send'}
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
    # def _install_upload_interceptor(self):
    #     """安装文件对话框拦截器"""
    #     try:
    #         self.browser.page().chooseFiles.connect(self._on_choose_files)
    #     except:
    #         pass  # 避免重复连接

    # def _uninstall_upload_interceptor(self):
    #     """卸载文件对话框拦截器"""
    #     try:
    #         self.browser.page().chooseFiles.disconnect(self._on_choose_files)
    #     except:
    #         pass

    # def _on_choose_files(self, request):
    #     """拦截文件对话框，注入上传文件路径"""
    #     # 优先从 RunEngine 获取待上传文件
    #     pending_file = self.run_engine.get_pending_upload_file()
    #     if pending_file:
    #         urls = [QUrl.fromLocalFile(pending_file)]
    #         request.accept(urls)
    #         self.run_engine.on_upload_dialog_handled()  # 通知引擎上传完成
    #         self._uninstall_upload_interceptor()
    #     else:
    #         # 正常手动选择文件（如果用户手动点击上传）
    #         file_path, _ = QFileDialog.getOpenFileName(self, "选择文件")
    #         if file_path:
    #             request.accept([QUrl.fromLocalFile(file_path)])
    #         else:
    #             request.reject()
    #         self._uninstall_upload_interceptor()
if __name__ == "__main__":
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--remote-debugging-port=9222 --ignore-certificate-errors"
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow", type=str)
    parser.add_argument("--auto-run", action="store_true")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    if args.auto_run:
        win = MainWindow(auto_run_flow=args.flow if args.flow else None)
    else:
        win = MainWindow()
    win.show()
    sys.exit(app.exec())