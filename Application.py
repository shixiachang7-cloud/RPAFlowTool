"""Excel 公式计算结果读取（COM 后端，驱动真实 Excel 程序）。

对外接口保持扁平函数不变，供 run_engine.py 的 excel_formula/read_value 动作调用。
内部"复用已打开的工作簿，找不到再用 Open 打开"这一段，参照的是 rpaframework
RPA.Excel.Application 模块（见 _internal/Application.py 的 `open_workbook` 方法）
的真实写法：先按路径直接索引 app.Workbooks(path) 尝试命中已打开的工作簿，命中失败
（COM 会抛异常）再退回 Workbooks.Open()。这比手写 for 循环去比较 FullName 更贴近
rpaframework 里被验证过的做法，行为上是等价的（Excel 对 Workbooks(name_or_path) 的
索引本身就是按完整路径/文件名做匹配的）。
"""
import pathlib

_excel_app = None


def get_excel_app(visible=False):
    global _excel_app
    if _excel_app is None:
        import win32com.client as win32
        _excel_app = win32.gencache.EnsureDispatch('Excel.Application')
        _excel_app.Visible = visible
    return _excel_app


def read_calculated_value(file_path, sheet_name, cell_ref):
    """用真实 Excel 打开工作簿（若已经打开则复用），读取指定单元格公式的计算结果。

    与 openpyxl 不同，这里读到的是"当前真实计算"的值，而不是文件上次保存时缓存的旧值。
    """
    resolved = pathlib.Path(file_path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Excel 文件不存在: {resolved}")
    abs_path = str(resolved)

    app = get_excel_app(visible=False)

    opened_here = False
    try:
        # 按完整路径直接索引，命中说明这个工作簿已经在当前 Excel 进程里打开，直接复用；
        # 命中失败 COM 会抛异常，这里统一用 Exception 兜底（与 rpaframework 的写法一致），
        # 退回用 Open() 真正打开文件。
        wb = app.Workbooks(abs_path)
    except Exception:
        wb = app.Workbooks.Open(abs_path)
        opened_here = True

    try:
        ws = wb.Sheets(sheet_name) if sheet_name else wb.ActiveSheet
        return ws.Range(cell_ref).Value2
    finally:
        if opened_here:
            wb.Close(SaveChanges=False)


def quit_app():
    global _excel_app
    if _excel_app is not None:
        try:
            _excel_app.Quit()
        except Exception:
            pass
        _excel_app = None


def run_macro(file_path, macro_name, macro_args=None, visible=False):
    """用真实 Excel 打开工作簿（若已经打开则复用），运行其中的 VBA 宏。

    macro_args 是一个简单参数列表（字符串/数字），按顺序透传给 `Application.Run`，
    对应 VBA Sub/Function 的形参。visible 控制运行期间 Excel 窗口是否可见——
    这里直接复用 get_excel_app 的 Visible 属性，即便 COM 进程已经存在也会随之切换。
    """
    resolved = pathlib.Path(file_path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Excel 文件不存在: {resolved}")
    abs_path = str(resolved)

    app = get_excel_app(visible=visible)
    app.Visible = visible

    opened_here = False
    try:
        wb = app.Workbooks(abs_path)
    except Exception:
        wb = app.Workbooks.Open(abs_path)
        opened_here = True

    try:
        args = macro_args or []
        return app.Run(macro_name, *args)
    finally:
        if opened_here:
            wb.Close(SaveChanges=True)


def create_pivot_table(*args, **kwargs):
    raise NotImplementedError("v2")


def export_to_pdf(*args, **kwargs):
    raise NotImplementedError("v2")
