# flow_step.py
from dataclasses import dataclass

@dataclass
class FlowStep:
    id: int
    type: str                     # 新增 'upload'
    name: str = ""
    selector: str = ""
    selector_type: str = "xpath"
    value: str = ""
    field_name: str = ""
    extract_attr: str = "text"
    timeout: int = 5000
    pre_wait_seconds: int = 0
    pre_wait_element: bool = False
    pre_wait_xpath: str = ""
    date_format: str = ""
    use_today: bool = True
    fixed_date: str = ""
    js_code: str = ""
    upload_file_path: str = ""    # 上传文件路径，支持占位符 {download:...}
    # Excel 相关字段（excel_file/excel_sheet/excel_table/excel_cell/excel_rowcol/excel_style/excel_formula/excel_image 共用）
    excel_action: str = ""          # 子操作，如 'open'/'write_cell'/'insert_row'
    excel_file_path: str = ""       # 工作簿路径
    excel_save_path: str = ""       # 另存为目标路径（save_as 用）
    excel_sheet_name: str = ""      # 目标/现有工作表名
    excel_new_sheet_name: str = ""  # 新工作表名（add创建名 / rename新名）
    excel_cell_ref: str = ""        # 单元格/区域引用，如 "A1" 或 "A1:C10"
    excel_range_data: str = ""      # JSON 二维数组字符串（write_all / write_range）
    excel_row_col_ref: str = ""     # 行号或列字母，如 "3" 或 "C"
    excel_count: int = 1            # 插入/删除行列数量
    excel_font_name: str = ""
    excel_font_size: int = 0        # 0 = 不修改
    excel_font_bold: bool = False
    excel_font_color: str = ""      # 十六进制，如 "FF0000"
    excel_fill_color: str = ""
    excel_border_style: str = ""    # 'none'/'thin'/'medium'/'thick'
    excel_formula_text: str = ""    # 如 "=SUM(A1:A10)"
    excel_image_path: str = ""
    excel_image_width: int = 0      # 0 = 原始大小
    excel_image_height: int = 0
    excel_macro_name: str = ""        # 宏名称（Sub/Function 名，可含模块名如 Module1.MacroName）
    excel_macro_args: str = ""        # 简单参数，逗号分隔
    excel_macro_visible: bool = False  # 运行时是否显示Excel窗口
    # 邮件自动发送相关字段
    email_smtp_server: str = ""       # SMTP 服务器地址，如 smtp.qq.com
    email_smtp_port: int = 465        # SMTP 端口，默认 465(SSL)
    email_use_ssl: bool = True        # True=SSL直连(465)，False=STARTTLS(如587)
    email_account: str = ""           # 发件账号
    email_password: str = ""          # 发件密码/授权码（明文存储于流程JSON，同现有 value 等字段一致的取舍）
    email_to: str = ""                # 收件人，逗号分隔支持多个
    email_cc: str = ""                # 抄送，逗号分隔
    email_bcc: str = ""               # 密送，逗号分隔
    email_subject: str = ""           # 邮件主题，支持 {today} 等日期占位符
    email_body: str = ""              # 纯文本正文，支持 {today} 等日期占位符
    email_attachment_path: str = ""   # 附件路径，支持 {today}/{download:...} 占位符，留空则不带附件