# lite_builder.py
"""轻态搭建：纯流程编排对话框（不含浏览器/元素拾取，选择器手动填写）。

运行时不自带 RunEngine，而是把编排好的 FlowStep 列表通过 run_requested 信号
交给调用方（MainWindow）用它已有的浏览器 + RunEngine 执行。
"""
import json
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
from flow_step import FlowStep

TYPE_NAMES = {
    'click': '点击元素', 'input': '输入文本', 'extract': '提取数据',
    'wait': '等待', 'navigate': '打开网页', 'group': '板块', 'js': 'JS脚本', 'upload': '上传文件',
    'excel_file': 'Excel文件管理', 'excel_sheet': 'Excel工作表管理',
    'excel_table': 'Excel整表读写', 'excel_cell': 'Excel单元格读写',
    'excel_rowcol': 'Excel行列编辑', 'excel_style': 'Excel样式格式',
    'excel_formula': 'Excel公式', 'excel_image': 'Excel图片',
    'excel_macro': 'Excel宏',
    'email_send': '邮件自动发送',
}

TYPE_ORDER = ['navigate', 'click', 'input', 'extract', 'wait', 'js', 'upload', 'group',
              'excel_file', 'excel_sheet', 'excel_table', 'excel_cell', 'excel_rowcol',
              'excel_style', 'excel_formula', 'excel_image', 'excel_macro', 'email_send']
TYPE_TO_INDEX = {t: i for i, t in enumerate(TYPE_ORDER)}

# 步骤总览表格"所属"列：每个步骤类型归属的顶层分类标签
TYPE_TO_CATEGORY = {}
for _t in ['navigate', 'click', 'input', 'extract', 'wait', 'js', 'upload']:
    TYPE_TO_CATEGORY[_t] = 'web'
for _t in ['excel_file', 'excel_sheet', 'excel_table', 'excel_cell', 'excel_rowcol',
           'excel_style', 'excel_formula', 'excel_image', 'excel_macro']:
    TYPE_TO_CATEGORY[_t] = 'excel'
TYPE_TO_CATEGORY['email_send'] = 'email'

# 左侧功能目录树结构：分类节点无 'type' 键，叶子节点带 'type' 键对应 TYPE_NAMES/TYPE_ORDER。
# enabled=False 的节点在树中显示但整体禁用（置灰+"暂不支持"提示）。
CATEGORY_TREE = [
    {'label': 'App', 'enabled': False, 'children': []},
    {'label': 'Web 自动化', 'enabled': True, 'children': [
        {'label': TYPE_NAMES[t], 'type': t}
        for t in ['navigate', 'click', 'input', 'extract', 'wait', 'js', 'upload']
    ]},
    {'label': '办公自动化 OA', 'enabled': True, 'children': [
        {'label': 'Word', 'enabled': False, 'children': []},
        {'label': 'Excel', 'enabled': True, 'children': [
            {'label': TYPE_NAMES[t], 'type': t}
            for t in ['excel_file', 'excel_sheet', 'excel_table', 'excel_cell', 'excel_rowcol',
                      'excel_style', 'excel_formula', 'excel_image', 'excel_macro']
        ]},
        {'label': '邮件', 'enabled': True, 'children': [
            {'label': TYPE_NAMES['email_send'], 'type': 'email_send'}
        ]},
        {'label': 'PPT', 'enabled': False, 'children': []},
    ]},
]


class StepTreeItem(QTreeWidgetItem):
    def __init__(self, step: FlowStep):
        super().__init__()
        self.step_data = step
        self.update_display()

    def update_display(self, show_category=True):
        if self.step_data.type == 'group':
            self.setText(0, "板块")
            self.setText(1, self.step_data.name or "板块")
        else:
            desc = TYPE_NAMES.get(self.step_data.type, self.step_data.type)
            category = TYPE_TO_CATEGORY.get(self.step_data.type, '')
            self.setText(0, category if show_category else "")
            self.setText(1, self.step_data.name or desc)


class _ColumnDividerDelegate(QStyledItemDelegate):
    """在"所属"列右侧画一条竖直分隔线，配合行分隔线做出表格网格效果。"""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if index.column() == 0:
            painter.save()
            pen = QPen(QColor("#d0d0d0"))
            painter.setPen(pen)
            painter.drawLine(option.rect.topRight(), option.rect.bottomRight())
            painter.restore()


class DragSafeTreeWidget(QTreeWidget):
    def dragEnterEvent(self, event):
        dlg = self.window()
        if isinstance(dlg, LiteBuilderDialog) and event.source() is dlg.palette_tree:
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        dlg = self.window()
        if isinstance(dlg, LiteBuilderDialog) and event.source() is dlg.palette_tree:
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        dlg = self.window()
        if isinstance(dlg, LiteBuilderDialog) and event.source() is dlg.palette_tree:
            source_item = dlg.palette_tree.currentItem()
            node_type = source_item.data(0, Qt.UserRole) if source_item else None
            if not node_type or not (source_item.flags() & Qt.ItemIsEnabled):
                event.ignore()
                return
            step = dlg._make_default_step(node_type)
            item = StepTreeItem(step)
            target_item = self.itemAt(event.position().toPoint())
            dlg._insert_step_item(item, as_group=False, reference_item=target_item)
            dlg._refresh_step_categories()
            event.acceptProposedAction()
            return
        super().dropEvent(event)
        self._clean_invalid_children()
        if isinstance(dlg, LiteBuilderDialog):
            dlg.clearSelection_and_resync()
            dlg._refresh_step_categories()

    def _clean_invalid_children(self):
        self._clean_node_children(self.invisibleRootItem())

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


class LiteBuilderDialog(QDialog):
    """轻态搭建界面：纯流程编排，不含浏览器/拾取，运行时把步骤交给主窗口执行。"""

    run_requested = Signal(list)  # 传出 List[FlowStep]，由 MainWindow 用自己的 RunEngine 执行
    sync_requested = Signal(list)  # 传出顶层步骤树 dict 列表，由 MainWindow 追加进自己的 step_tree

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("轻态搭建")
        self.resize(1100, 720)
        self.current_step = None
        self.current_file_path = None
        self.step_controls = {}
        self.step_source = None  # 'pending'（左侧目录点出的草稿） / 'tree'（已在步骤列表里的节点）

        root_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)

        # 左侧：静态功能目录（App / Web自动化 / 办公自动化）
        palette_widget = QWidget()
        palette_layout = QVBoxLayout(palette_widget)
        palette_layout.setContentsMargins(4, 4, 4, 4)
        palette_layout.addWidget(QLabel("功能目录"))
        self.palette_tree = QTreeWidget()
        self.palette_tree.setHeaderHidden(True)
        self.palette_tree.setDragEnabled(True)
        self.palette_tree.setDragDropMode(QAbstractItemView.DragOnly)
        self.palette_tree.itemClicked.connect(self._on_palette_leaf_clicked)
        self._build_palette_tree()
        palette_layout.addWidget(self.palette_tree)
        splitter.addWidget(palette_widget)

        # 中间：步骤总览表格（真正会执行的、有序的步骤/分组，可上下滚动）
        tree_widget = QWidget()
        tree_layout = QVBoxLayout(tree_widget)
        tree_layout.setContentsMargins(4, 4, 4, 4)
        tree_header = QHBoxLayout()
        tree_header.addWidget(QLabel("步骤总览"))
        self.btn_del_step = QPushButton("-")
        self.btn_add_group = QPushButton("新增板块")
        tree_header.addWidget(self.btn_del_step)
        tree_header.addWidget(self.btn_add_group)
        tree_header.addStretch()
        tree_layout.addLayout(tree_header)

        self.step_tree = DragSafeTreeWidget()
        self.step_tree.setColumnCount(2)
        self.step_tree.setHeaderLabels(["所属", "自定义名称"])
        self.step_tree.setHeaderHidden(False)
        self.step_tree.setIndentation(0)
        self.step_tree.setRootIsDecorated(False)
        self.step_tree.setAlternatingRowColors(False)
        self.step_tree.setUniformRowHeights(True)
        self.step_tree.header().setSectionResizeMode(0, QHeaderView.Fixed)
        self.step_tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.step_tree.setColumnWidth(0, 70)
        self.step_tree.setStyleSheet("""
            QTreeWidget { gridline-color: #d0d0d0; }
            QTreeWidget::item { border-bottom: 1px solid #d0d0d0; padding: 6px 4px; }
        """)
        self.step_tree.setItemDelegate(_ColumnDividerDelegate(self.step_tree))
        self.step_tree.setDragDropMode(QAbstractItemView.DragDrop)
        self.step_tree.setDefaultDropAction(Qt.MoveAction)
        self.step_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.step_tree.setDropIndicatorShown(True)
        self.step_tree.currentItemChanged.connect(self._on_tree_selected)
        tree_layout.addWidget(self.step_tree)
        splitter.addWidget(tree_widget)

        # 右侧：节点配置表单
        config_widget = QWidget()
        config_layout = QVBoxLayout(config_widget)
        config_layout.setContentsMargins(4, 4, 4, 4)
        self.config_title_label = QLabel("请选择左侧功能或已有步骤")
        config_layout.addWidget(self.config_title_label)

        self.config_stack = QStackedWidget()
        self._create_config_pages()
        config_layout.addWidget(self.config_stack)

        self.pre_wait_widget = QWidget()
        pre_layout = QVBoxLayout(self.pre_wait_widget)
        self._add_pre_wait_controls(pre_layout)
        config_layout.addWidget(self.pre_wait_widget)
        self.pre_wait_widget.setVisible(False)

        self.btn_commit_step = QPushButton("+ 新增步骤")
        self.btn_commit_step.setVisible(False)
        config_layout.addWidget(self.btn_commit_step)

        splitter.addWidget(config_widget)
        splitter.setSizes([220, 380, 500])
        root_layout.addWidget(splitter)

        # 底部：保存/加载/运行
        bottom_bar = QHBoxLayout()
        self.btn_save = QPushButton("保存流程")
        self.btn_load_flow = QPushButton("加载流程")
        self.btn_sync = QPushButton("同步到主界面")
        self.btn_run = QPushButton("▶ 运行")
        bottom_bar.addWidget(self.btn_save)
        bottom_bar.addWidget(self.btn_load_flow)
        bottom_bar.addStretch()
        bottom_bar.addWidget(self.btn_sync)
        bottom_bar.addWidget(self.btn_run)
        root_layout.addLayout(bottom_bar)

        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(self.reject)
        btn_box.accepted.connect(self.accept)
        root_layout.addWidget(btn_box)

        self.btn_add_group.clicked.connect(lambda: self._add_step(True))
        self.btn_del_step.clicked.connect(self._delete_step)
        self.btn_commit_step.clicked.connect(self._on_commit_step_clicked)
        self.btn_save.clicked.connect(self._save_flow)
        self.btn_load_flow.clicked.connect(self._load_flow)
        self.btn_sync.clicked.connect(self._on_sync_clicked)
        self.btn_run.clicked.connect(self._on_run_clicked)

    # ===================== 左侧功能目录 =====================
    def _build_palette_tree(self):
        self.palette_tree.clear()

        def add_nodes(parent_item, nodes):
            for node in nodes:
                item = QTreeWidgetItem([node['label']])
                if 'type' in node:
                    item.setData(0, Qt.UserRole, node['type'])
                    if node.get('enabled', True):
                        item.setFlags(item.flags() | Qt.ItemIsDragEnabled)
                if not node.get('enabled', True):
                    item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                    item.setToolTip(0, "暂不支持")
                if parent_item is None:
                    self.palette_tree.addTopLevelItem(item)
                else:
                    parent_item.addChild(item)
                if node.get('children'):
                    add_nodes(item, node['children'])

        add_nodes(None, CATEGORY_TREE)
        self.palette_tree.expandAll()

    def _on_palette_leaf_clicked(self, item, column):
        node_type = item.data(0, Qt.UserRole)
        if not node_type or not (item.flags() & Qt.ItemIsEnabled):
            return
        step = self._make_default_step(node_type)
        self.current_step = step
        self.step_source = 'pending'
        self.config_title_label.setText(f"新增：{TYPE_NAMES.get(node_type, node_type)}（尚未加入步骤列表）")
        self._populate_controls(step)
        self.btn_commit_step.setVisible(True)

    # ===================== 树同步 =====================
    def clearSelection_and_resync(self):
        self.step_tree.clearSelection()

    def _refresh_step_categories(self):
        """按可见行顺序刷新"所属"列：同一分类的连续行只在第一行显示，跨"板块"行重置。"""
        last_category = None

        def walk(item):
            nonlocal last_category
            if not isinstance(item, StepTreeItem):
                return
            if item.step_data.type == 'group':
                item.update_display()
                last_category = None
                for i in range(item.childCount()):
                    walk(item.child(i))
                return
            category = TYPE_TO_CATEGORY.get(item.step_data.type, '')
            show_category = category != last_category
            item.update_display(show_category=show_category)
            last_category = category

        for i in range(self.step_tree.topLevelItemCount()):
            walk(self.step_tree.topLevelItem(i))

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

    def _get_type_count(self, type_str):
        return sum(1 for s in self._build_flat_steps_list() if s.type == type_str)

    def _make_default_step(self, node_type):
        new_id = len(self._build_flat_steps_list()) + 1
        step = FlowStep(id=new_id, type=node_type)
        base_name = TYPE_NAMES.get(node_type, '步骤')
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
        return step

    def _insert_step_item(self, item, as_group, reference_item=None):
        current = reference_item if reference_item is not None else self.step_tree.currentItem()
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

    def _add_step(self, as_group=True):
        step = self._make_default_step('group')
        item = StepTreeItem(step)
        self._insert_step_item(item, as_group=True)
        self._refresh_step_categories()

    def _on_commit_step_clicked(self):
        if self.step_source != 'pending' or self.current_step is None:
            return
        step = self.current_step
        step.id = len(self._build_flat_steps_list()) + 1
        item = StepTreeItem(step)
        self._insert_step_item(item, as_group=False)
        self.btn_commit_step.setVisible(False)
        self._refresh_step_categories()

    def _delete_step(self):
        item = self.step_tree.currentItem()
        if not item:
            return
        parent = item.parent() or self.step_tree.invisibleRootItem()
        parent.removeChild(item)
        self.config_stack.setCurrentIndex(0)
        self._refresh_step_categories()

    def _on_tree_selected(self, current, previous):
        if current is None or not isinstance(current, StepTreeItem):
            self.current_step = None
            self.step_source = None
            self.config_stack.setCurrentIndex(0)
            self.config_title_label.setText("请选择左侧功能或已有步骤")
            self.pre_wait_widget.setVisible(False)
            self.btn_commit_step.setVisible(False)
            return
        step = current.step_data
        self.current_step = step
        self.step_source = 'tree'
        self.btn_commit_step.setVisible(False)
        if step.type == 'group':
            self.config_title_label.setText(f"板块：{step.name or '板块'}")
        else:
            self.config_title_label.setText(f"当前配置：{TYPE_NAMES.get(step.type, step.type)}")
        self._populate_controls(step)

    # ===================== 配置页创建 =====================
    def _create_config_pages(self):
        for t in TYPE_ORDER:
            self.step_controls[t] = {}

        self._create_navigate_page()
        self._create_click_page()
        self._create_input_page()
        self._create_extract_page()
        self._create_wait_page()
        self._make_js_page()
        self._make_upload_page()
        self._create_group_page()
        self._make_excel_file_page()
        self._make_excel_sheet_page()
        self._make_excel_table_page()
        self._make_excel_cell_page()
        self._make_excel_rowcol_page()
        self._make_excel_style_page()
        self._make_excel_formula_page()
        self._make_excel_image_page()
        self._make_excel_macro_page()
        self._make_email_send_page()

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
        page = QWidget(); layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:")); layout.addWidget(name_edit)
        self.step_controls['navigate']['name_edit'] = name_edit
        url_edit = QLineEdit()
        layout.addWidget(QLabel("网址:")); layout.addWidget(url_edit)
        timeout_edit = QLineEdit("3000")
        layout.addWidget(QLabel("超时(ms):")); layout.addWidget(timeout_edit)
        layout.addStretch()
        self.config_stack.addWidget(page)
        self.step_controls['navigate']['url_edit'] = url_edit
        self.step_controls['navigate']['timeout_edit'] = timeout_edit

    def _create_click_page(self):
        page = QWidget(); layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:")); layout.addWidget(name_edit)
        self.step_controls['click']['name_edit'] = name_edit
        sel_edit = QLineEdit()
        layout.addWidget(QLabel("XPath路径:")); layout.addWidget(sel_edit)
        timeout_edit = QLineEdit("5000")
        layout.addWidget(QLabel("超时(ms):")); layout.addWidget(timeout_edit)
        layout.addStretch()
        self.config_stack.addWidget(page)
        self.step_controls['click']['selector_edit'] = sel_edit
        self.step_controls['click']['timeout_edit'] = timeout_edit

    def _create_input_page(self):
        page = QWidget(); layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:")); layout.addWidget(name_edit)
        self.step_controls['input']['name_edit'] = name_edit
        sel_edit = QLineEdit()
        layout.addWidget(QLabel("XPath路径:")); layout.addWidget(sel_edit)
        value_edit = QTextEdit()
        layout.addWidget(QLabel("输入文本（支持 {today}、{today-N}）:")); layout.addWidget(value_edit)
        layout.addStretch()
        self.config_stack.addWidget(page)
        self.step_controls['input']['selector_edit'] = sel_edit
        self.step_controls['input']['value_edit'] = value_edit

    def _create_extract_page(self):
        page = QWidget(); layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:")); layout.addWidget(name_edit)
        self.step_controls['extract']['name_edit'] = name_edit
        sel_edit = QLineEdit()
        layout.addWidget(QLabel("XPath路径:")); layout.addWidget(sel_edit)
        field_edit = QLineEdit()
        layout.addWidget(QLabel("字段名:")); layout.addWidget(field_edit)
        attr_edit = QLineEdit("text")
        layout.addWidget(QLabel("提取属性:")); layout.addWidget(attr_edit)
        layout.addStretch()
        self.config_stack.addWidget(page)
        self.step_controls['extract']['selector_edit'] = sel_edit
        self.step_controls['extract']['field_edit'] = field_edit
        self.step_controls['extract']['attr_edit'] = attr_edit

    def _create_wait_page(self):
        page = QWidget(); layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:")); layout.addWidget(name_edit)
        self.step_controls['wait']['name_edit'] = name_edit
        timeout_edit = QLineEdit("10000")
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
        page = QWidget(); layout = QVBoxLayout(page)
        name_edit = QLineEdit()
        layout.addWidget(QLabel("名称:")); layout.addWidget(name_edit)
        self.step_controls['upload']['name_edit'] = name_edit
        sel_edit = QLineEdit()
        layout.addWidget(QLabel("目标元素 XPath:")); layout.addWidget(sel_edit)
        self.step_controls['upload']['selector_edit'] = sel_edit
        path_edit = QLineEdit()
        layout.addWidget(QLabel("文件路径（支持 {today}、{download:latest} 等）:"))
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
        page = QWidget(); layout = QVBoxLayout(page)
        ctrls = self.step_controls['email_send']
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
        body_edit = QTextEdit(); body_edit.setAcceptRichText(True); layout.addWidget(body_edit); ctrls['body_edit'] = body_edit
        self._excel_path_row(layout, ctrls, 'attachment_path_edit',
                              "附件路径（支持 {today}、{download:latest} 等，留空则不带附件）:", 'open')
        layout.addStretch()
        self.config_stack.addWidget(page)

    # ===================== 执行前等待控件 =====================
    def _add_pre_wait_controls(self, layout):
        self.chk_pre_wait = QCheckBox("执行前等待(秒):")
        self.spin_pre_wait = QSpinBox(); self.spin_pre_wait.setRange(0, 300)
        row = QHBoxLayout(); row.addWidget(self.chk_pre_wait); row.addWidget(self.spin_pre_wait)
        layout.addLayout(row)
        self.chk_pre_wait_element = QCheckBox("等待指定元素出现")
        self.pre_wait_xpath_edit = QLineEdit()
        pre_row = QHBoxLayout(); pre_row.addWidget(self.chk_pre_wait_element); pre_row.addWidget(self.pre_wait_xpath_edit)
        layout.addLayout(pre_row)
        self.chk_pre_wait.toggled.connect(self._on_pre_wait_toggled)
        self.chk_pre_wait_element.toggled.connect(self._on_pre_wait_element_toggled)
        self.chk_pre_wait.toggled.connect(self._on_any_control_changed)
        self.spin_pre_wait.valueChanged.connect(self._on_any_control_changed)
        self.chk_pre_wait_element.toggled.connect(self._on_any_control_changed)
        self.pre_wait_xpath_edit.textChanged.connect(self._on_any_control_changed)
        self.chk_pre_wait_element.setVisible(False)
        self.pre_wait_xpath_edit.setVisible(False)
        self.spin_pre_wait.setEnabled(False)

    def _on_pre_wait_toggled(self, checked):
        self.spin_pre_wait.setEnabled(checked)
        self.chk_pre_wait_element.setVisible(checked)
        self.pre_wait_xpath_edit.setVisible(checked and self.chk_pre_wait_element.isChecked())
        if not checked:
            self.chk_pre_wait_element.setChecked(False)

    def _on_pre_wait_element_toggled(self, checked):
        self.pre_wait_xpath_edit.setVisible(checked)

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
        self.config_stack.setCurrentIndex(TYPE_TO_INDEX.get(step.type, 0))
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
                if isinstance(item, StepTreeItem) and item.step_data is step:
                    self._refresh_step_categories()
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
        if isinstance(item, StepTreeItem) and item.step_data is step:
            self._refresh_step_categories()

    # ===================== 保存/加载/运行 =====================
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
            QMessageBox.information(self, "已保存", f"流程已保存至 {filepath}")
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
            self.current_file_path = filepath
            self._refresh_step_categories()
            QMessageBox.information(self, "已加载", f"已加载流程：{filepath}")
        except Exception as e:
            QMessageBox.critical(self, "加载失败", str(e))

    def _on_run_clicked(self):
        steps = self._build_flat_steps_list()
        if not steps:
            QMessageBox.warning(self, "警告", "没有可运行的步骤")
            return
        self.run_requested.emit(steps)
        self.accept()

    def _on_sync_clicked(self):
        if self.step_tree.topLevelItemCount() == 0:
            QMessageBox.warning(self, "警告", "没有可同步的步骤")
            return
        tree_data = []
        for i in range(self.step_tree.topLevelItemCount()):
            item = self.step_tree.topLevelItem(i)
            if isinstance(item, StepTreeItem):
                tree_data.append(self._serialize_tree_item(item))
        self.sync_requested.emit(tree_data)
        self.accept()


