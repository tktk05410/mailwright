"""界面与启动引导。"""

import warnings

# 屏蔽 jieba 的转义告警，须早于其导入
warnings.filterwarnings("ignore", category=SyntaxWarning)

import asyncio
import json
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import flet as ft
import pyperclip

from adapters.clipboard import ClipboardAdapter
from adapters.outlook_com import OutlookAdapter
from agent.orchestrator import Orchestrator
from agent.prompts import RULES_LIMIT
from agent.tools import Toolbox
from core.llm import ChatLLM
from core.logutil import log
from core.models import Message
from preprocess.clean import clean_email, parse_envelope
from rag.indexer import Embedder, KnowledgeIndex
from rag.retriever import Retriever
from rag.store import (
    load_handbook_chunks,
    load_index,
    load_style_chunks,
    read_document,
    save_index,
    sources_snapshot,
)

# 数据目录
ROOT = Path(__file__).parent
DATA = ROOT / "data"
HANDBOOK = DATA / "handbook"  # 手册
INDEX_DIR = DATA / "index"  # 索引缓存
STYLE_SAMPLES = DATA / "styles" / "samples.json"  # 内置样本
STYLE_IMPORTED = DATA / "styles" / "imported.json"  # 导入样本

# 字体族名，需与 page.fonts 一致
FONT = "Inter"
FONT_BOLD = "Inter Bold"
FONT_CN = "Noto Sans SC"
FONT_CN_BOLD = "Noto Sans SC Bold"

# 强调色
RED = "#DA291C"
RED_DARK = "#B52217"

# 窗口与间距
WIN_W, WIN_H = 1080, 760
WIN_MIN_W, WIN_MIN_H = 900, 620
PAD = 32
RADIUS = 6

# 字号与控件高度
TITLE_SIZE = 40
SECTION_SIZE = 16
BODY_SIZE = 15
LABEL_SIZE = 13
SMALL_SIZE = 12
FIELD_H = 44
BTN_H = 40

# 两套配色
PALETTE = {
    "light": {
        "page": "#F2EFE9",
        "field": "#FAF8F3",
        "text": "#141414",
        "muted": "#6B665E",
        "line": "#141414",
        "ink_bg": "#141414",
        "ink_fg": "#F2EFE9",
        "accent": "#DA291C",
    },
    "dark": {
        "page": "#141414",
        "field": "#1E1E1B",
        "text": "#F2EFE9",
        "muted": "#8E897E",
        "line": "#F2EFE9",
        "ink_bg": "#F2EFE9",
        "ink_fg": "#141414",
        "accent": "#FF6B5E",
    },
}
EMAIL_MASK = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")

# 运行期全局状态
CFG = {}  # 配置
EMB = None  # 嵌入模型
INDEX = None  # 知识库索引
RETRIEVER = None  # 检索器
LLM = None  # LLM 客户端
TOOLS = None  # 工具箱
AGENT = None  # 编排器
STARTUP_WARNINGS = []  # 启动告警


def load_env():
    """读取 .env 配置。"""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def txt(value, bold=False, size=BODY_SIZE, color=None, **kwargs):
    """创建统一文本控件。"""
    return ft.Text(
        value,
        size=size,
        color=color,
        font_family=FONT_BOLD if bold else FONT,
        font_family_fallback=[FONT_CN_BOLD if bold else FONT_CN],
        **kwargs,
    )


def status_text():
    """状态栏文案。"""
    hb = sum(1 for c in INDEX.chunks if c.kind == "handbook")
    st = sum(1 for c in INDEX.chunks if c.kind == "style")
    return f"{LLM.mode} ｜ 手册 {hb} 块 ｜ 语气样本 {st} 封 ｜ 规则 {len(CFG['rules'])} 条"


def style_chunks():
    """合并语气样本。"""
    chunks = load_style_chunks(STYLE_SAMPLES)
    if STYLE_IMPORTED.exists():
        chunks.extend(load_style_chunks(STYLE_IMPORTED, prefix="imp"))
    return chunks


def build_index(force=False):
    """构建或读取索引。"""
    srcs = sources_snapshot(HANDBOOK, extra_files=[STYLE_SAMPLES, STYLE_IMPORTED])
    cached = None if force else load_index(INDEX_DIR, EMB, sources=srcs)
    if cached is not None:
        return cached, []
    errors = []
    chunks = load_handbook_chunks(HANDBOOK, errors=errors) + style_chunks()
    index = KnowledgeIndex(EMB)
    index.rebuild(chunks)
    try:
        save_index(index, INDEX_DIR, sources=srcs)
    except Exception as ex:
        errors.append(("index", f"索引缓存写入失败：{ex}"))
    return index, errors


def bootstrap():
    """启动引导。"""
    global CFG, EMB, INDEX, RETRIEVER, LLM, TOOLS, AGENT
    load_env()

    # 补齐默认字段
    CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    CFG.setdefault("rules", [])
    CFG.setdefault("handbook_disabled", [])
    CFG.setdefault("theme", "light")

    # 加载模型与索引
    EMB = Embedder()
    INDEX, errors = build_index()
    STARTUP_WARNINGS.extend(f"{name}（{msg}）" for name, msg in errors)

    # 两种模型阈值不同
    min_cos = CFG.get("min_cos", 0.45) if EMB.model is not None else CFG.get("min_cos_hash", 0.10)
    # 仅真实模型启用单路兜底
    vector_fallback = bool(CFG.get("vector_fallback", True)) and EMB.model is not None
    RETRIEVER = Retriever(
        INDEX,
        min_cos=min_cos,
        min_rrf=CFG.get("min_rrf", 0.02),
        disabled_docs=CFG["handbook_disabled"],
        vector_fallback=vector_fallback,
    )

    # 组装智能体
    LLM = ChatLLM()
    TOOLS = Toolbox(RETRIEVER, LLM, CFG["rules"], top_k=CFG.get("top_k", 5))
    AGENT = Orchestrator(TOOLS)


class App:
    """主界面与设置页。"""

    def __init__(self, page: ft.Page):
        """装配界面。"""
        self.page = page
        self.state = {"mode": CFG.get("theme", "light"), "source": "剪贴板", "msg": None}
        self._ghosts = []
        self._main_labels = []
        self._muted_labels = []
        self._accent_texts = []
        self._settings_lines = []

        self.picker = ft.FilePicker()
        page.services.append(self.picker)

        self._build_widgets()
        self._build_main_view()
        self._build_settings_view()
        self.refresh_settings()
        self.paint()

    # ---------- 组件 ----------

    def _ghost(self, label, on_click, width):
        """创建描边按钮。"""
        button = ft.Button(label, on_click=on_click, width=width, height=BTN_H)
        self._ghosts.append(button)
        return button

    def _label_row(self, num, label):
        """编号标题行。"""
        return ft.Row([num, label], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _build_widgets(self):
        """创建全部控件。"""
        self.title = txt("Mailwright", bold=True, size=TITLE_SIZE)
        self.dot = ft.Container(
            width=12, height=12, bgcolor=RED, margin=ft.Margin.only(left=10, bottom=7)
        )
        self.subtitle = txt(status_text(), size=LABEL_SIZE)
        self.btn_settings = self._ghost("设置", self.show_settings, 92)
        self.btn_back = self._ghost("返回", self.show_main, 92)

        self.num_main = [txt(f"0{i}", bold=True, size=LABEL_SIZE) for i in range(1, 7)]
        self.num_set = [txt(f"0{i}", bold=True, size=LABEL_SIZE) for i in range(1, 6)]
        self._accent_texts.extend(self.num_main + self.num_set)

        def label(text):
            widget = txt(text, bold=True, size=LABEL_SIZE)
            self._main_labels.append(widget)
            return widget

        self.lbl_src = label("来源")
        self.lbl_mail = label("邮件正文")
        self.lbl_cited = label("引用条款")
        self.lbl_sum = label("摘要")
        self.lbl_tags = label("标签")
        self.lbl_draft = label("回复草稿")

        self.seg_clip = ft.Button(
            "剪贴板", on_click=lambda e: self.set_source("剪贴板"), width=132, height=BTN_H
        )
        self.seg_out = ft.Button(
            "Outlook 选中", on_click=lambda e: self.set_source("Outlook 选中"), width=168, height=BTN_H
        )

        self.email_box = ft.TextField(
            multiline=True,
            fit_parent_size=True,
            text_vertical_align=-1.0,
            hint_text="从剪贴板或 Outlook 读取，也可直接粘贴邮件正文",
        )
        self.summary = ft.TextField(
            read_only=True,
            multiline=True,
            min_lines=2,
            max_lines=3,
            hint_text="生成后显示邮件摘要",
        )
        self.tags = ft.TextField(read_only=True, hint_text="生成后显示分类标签")
        self.cited = ft.TextField(
            read_only=True,
            multiline=True,
            fit_parent_size=True,
            text_vertical_align=-1.0,
            hint_text="生成后显示命中的手册条款",
        )
        self.draft = ft.TextField(
            multiline=True,
            fit_parent_size=True,
            text_vertical_align=-1.0,
            hint_text="① 粘贴或读取邮件正文 → ② 点「生成回复」，草稿会显示在这里",
        )

        self.status_word = txt("状态 —", size=SMALL_SIZE)
        self.chip = txt("待生成", bold=True, size=LABEL_SIZE)
        self.conf = txt("置信度 —", size=SMALL_SIZE)
        self.gen = txt("", size=SMALL_SIZE)

        self.btn_read = self._ghost("读取邮件", self.on_read, 110)
        self.btn_copy = self._ghost("复制草稿", self.on_copy, 110)
        self.btn_primary = ft.Button(
            "生成回复", on_click=self.on_generate, width=150, height=BTN_H + 2
        )

        # 设置页
        self.settings_title = txt("设置", bold=True, size=TITLE_SIZE)
        self.settings_dot = ft.Container(
            width=12, height=12, bgcolor=RED, margin=ft.Margin.only(left=10, bottom=7)
        )
        self.sec_titles = [
            txt(name, bold=True, size=SECTION_SIZE)
            for name in ("外观", "模型 API", "手册管理", "语气管理", "规则管理")
        ]
        self._main_labels.extend(self.sec_titles)

        self.lbl_key = txt("API Key", size=SMALL_SIZE)
        self.lbl_base = txt("Base URL", size=SMALL_SIZE)
        self.lbl_model = txt("模型名", size=SMALL_SIZE)
        self._muted_labels.extend([self.lbl_key, self.lbl_base, self.lbl_model])
        self.api_status = txt("", size=SMALL_SIZE)
        self.style_status = txt("", size=SMALL_SIZE)
        self._muted_labels.extend([self.api_status, self.style_status])
        self.dark_label = txt("深色模式", size=BODY_SIZE)

        self.api_key = ft.TextField(password=True, can_reveal_password=True, height=FIELD_H)
        self.base_url = ft.TextField(height=FIELD_H)
        self.model_field = ft.TextField(height=FIELD_H)
        self.rule_field = ft.TextField(
            hint_text="输入新规则后点击添加", expand=True, height=FIELD_H
        )

        self.btn_save_api = self._ghost("保存并重连", self.on_save_api, 130)
        self.btn_upload = self._ghost("上传手册", self.on_pick, 130)
        self.btn_import = self._ghost("导入语气", self.on_import, 130)
        self.btn_clear_style = self._ghost("清除导入", self.ask_style_clear, 120)
        self.btn_rule_add = self._ghost("添加", self.on_rule_add, 80)
        self.btn_rebuild = self._ghost("重建索引", self.on_rebuild, 130)

        self.handbook_list = ft.Column([], spacing=10)
        self.rules_list = ft.Column([], spacing=10)

        self.theme_knob = ft.Container(width=16, height=16, border_radius=3)
        self.theme_row = ft.Row([self.theme_knob], spacing=0)
        self.theme_box = ft.Container(
            content=self.theme_row,
            width=46,
            height=26,
            padding=4,
            border_radius=RADIUS,
            on_click=self.on_toggle_theme,
        )

        self.fields = [
            self.email_box,
            self.summary,
            self.tags,
            self.cited,
            self.draft,
            self.api_key,
            self.base_url,
            self.model_field,
            self.rule_field,
        ]

    # ---------- 视图 ----------

    def _build_main_view(self):
        """构建主页面。"""
        header = ft.Container(
            padding=ft.Padding.only(left=PAD, right=PAD, top=22, bottom=16),
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Row(
                                [self.title, self.dot],
                                spacing=10,
                                vertical_alignment=ft.CrossAxisAlignment.END,
                            ),
                            ft.Container(self.subtitle, margin=ft.Margin.only(top=8)),
                        ],
                        spacing=0,
                        expand=True,
                    ),
                    ft.Row(
                        [self.btn_settings],
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        )

        left = ft.Container(
            expand=5,
            content=ft.Column(
                [
                    self._label_row(self.num_main[0], self.lbl_src),
                    ft.Container(
                        ft.Row([self.seg_clip, self.seg_out], spacing=10),
                        margin=ft.Margin.only(top=12),
                    ),
                    ft.Container(
                        self._label_row(self.num_main[1], self.lbl_mail),
                        margin=ft.Margin.only(top=26),
                    ),
                    ft.Container(self.email_box, expand=3, margin=ft.Margin.only(top=10)),
                    ft.Container(
                        self._label_row(self.num_main[2], self.lbl_cited),
                        margin=ft.Margin.only(top=26),
                    ),
                    ft.Container(self.cited, expand=2, margin=ft.Margin.only(top=10)),
                ],
                spacing=0,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )

        right = ft.Container(
            expand=7,
            content=ft.Column(
                [
                    self._label_row(self.num_main[3], self.lbl_sum),
                    ft.Container(self.summary, margin=ft.Margin.only(top=10)),
                    ft.Container(
                        self._label_row(self.num_main[4], self.lbl_tags),
                        margin=ft.Margin.only(top=22),
                    ),
                    ft.Container(self.tags, margin=ft.Margin.only(top=10)),
                    ft.Container(
                        self._label_row(self.num_main[5], self.lbl_draft),
                        margin=ft.Margin.only(top=22),
                    ),
                    ft.Container(self.draft, expand=1, margin=ft.Margin.only(top=10)),
                ],
                spacing=0,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )

        self.vline = ft.Container(width=1)
        self.hair_top = ft.Container(height=1)
        self.hair_bottom = ft.Container(height=1)

        body = ft.Container(
            expand=True,
            padding=ft.Padding.only(left=PAD, right=PAD, top=26, bottom=18),
            content=ft.Row(
                [left, self.vline, right],
                spacing=32,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )

        footer = ft.Container(
            padding=ft.Padding.only(left=PAD, right=PAD, top=16, bottom=22),
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Row([self.status_word, self.chip], spacing=10),
                            ft.Row([self.conf, self.gen], spacing=22),
                        ],
                        spacing=6,
                    ),
                    ft.Row(
                        [self.btn_read, self.btn_copy, self.btn_primary],
                        spacing=12,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        self.main_view = ft.Column(
            [header, self.hair_top, body, self.hair_bottom, footer],
            spacing=0,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self.stage = ft.Container(content=self.main_view, expand=True)
        self.root = ft.Container(content=self.stage, expand=True)

    def _section(self, idx, title, controls, top=0):
        """设置页分区。"""
        line = ft.Container(height=1, margin=ft.Margin.only(top=12))
        self._settings_lines.append(line)
        return ft.Container(
            margin=ft.Margin.only(top=top),
            content=ft.Column(
                [ft.Row([self.num_set[idx], title], spacing=10), line, *controls],
                spacing=0,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )

    def _build_settings_view(self):
        """构建设置页。"""
        header = ft.Container(
            padding=ft.Padding.only(left=PAD, right=PAD, top=22, bottom=16),
            content=ft.Row(
                [
                    ft.Row(
                        [self.settings_title, self.settings_dot],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    self.btn_back,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        )

        sections = ft.Column(
            [
                self._section(
                    0,
                    self.sec_titles[0],
                    [
                        ft.Row(
                            [self.dark_label, self.theme_box],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            margin=ft.Margin.only(top=16),
                        )
                    ],
                ),
                self._section(
                    1,
                    self.sec_titles[1],
                    [
                        ft.Container(self.lbl_key, margin=ft.Margin.only(top=18)),
                        ft.Container(self.api_key, margin=ft.Margin.only(top=6)),
                        ft.Container(self.lbl_base, margin=ft.Margin.only(top=14)),
                        ft.Container(self.base_url, margin=ft.Margin.only(top=6)),
                        ft.Container(self.lbl_model, margin=ft.Margin.only(top=14)),
                        ft.Container(self.model_field, margin=ft.Margin.only(top=6)),
                        ft.Row(
                            [self.btn_save_api, self.api_status],
                            spacing=14,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            margin=ft.Margin.only(top=16),
                        ),
                    ],
                    top=34,
                ),
                self._section(
                    2,
                    self.sec_titles[2],
                    [
                        ft.Container(self.handbook_list, margin=ft.Margin.only(top=16)),
                        ft.Row([self.btn_upload], margin=ft.Margin.only(top=14)),
                    ],
                    top=34,
                ),
                self._section(
                    3,
                    self.sec_titles[3],
                    [
                        ft.Container(self.style_status, margin=ft.Margin.only(top=16)),
                        ft.Row(
                            [self.btn_import, self.btn_clear_style],
                            spacing=10,
                            margin=ft.Margin.only(top=14),
                        ),
                    ],
                    top=34,
                ),
                self._section(
                    4,
                    self.sec_titles[4],
                    [
                        ft.Container(self.rules_list, margin=ft.Margin.only(top=16)),
                        ft.Row(
                            [self.rule_field, self.btn_rule_add],
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            margin=ft.Margin.only(top=14),
                        ),
                        ft.Row([self.btn_rebuild], margin=ft.Margin.only(top=30, bottom=10)),
                    ],
                    top=34,
                ),
            ],
            spacing=0,
            width=780,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        self.settings_rule = ft.Container(height=1)
        body = ft.Container(
            expand=True,
            padding=ft.Padding.only(left=PAD, right=PAD, top=8, bottom=24),
            content=ft.Column(
                [ft.Row([sections], alignment=ft.MainAxisAlignment.CENTER)],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
                spacing=0,
            ),
        )
        self.settings_view = ft.Column(
            [header, self.settings_rule, body],
            spacing=0,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    # ---------- 配色 ----------

    def paint(self):
        """按主题重刷配色。"""
        p = PALETTE[self.state["mode"]]
        self.page.bgcolor = p["page"]
        self.root.bgcolor = p["page"]

        for widget in (self.title, self.settings_title, self.dark_label):
            widget.color = p["text"]
        self.subtitle.color = p["muted"]
        for widget in self._main_labels:
            widget.color = p["text"]
        for widget in self._muted_labels:
            widget.color = p["muted"]
        for widget in self._accent_texts:
            widget.color = p["accent"]
        for widget in (self.status_word, self.conf, self.gen):
            widget.color = p["muted"]
        self.chip.color = p["text"]

        for line in [
            self.hair_top,
            self.hair_bottom,
            self.vline,
            self.settings_rule,
            *self._settings_lines,
        ]:
            line.bgcolor = p["line"]

        for field in self.fields:
            field.bgcolor = p["field"]
            field.filled = False
            field.focused_bgcolor = p["field"]
            field.cursor_color = p["text"]
            field.text_style = ft.TextStyle(
                size=BODY_SIZE, color=p["text"], font_family=FONT, font_family_fallback=[FONT_CN]
            )
            field.hint_style = ft.TextStyle(
                size=BODY_SIZE, color=p["muted"], font_family=FONT, font_family_fallback=[FONT_CN]
            )
            field.content_padding = ft.Padding.symmetric(horizontal=12, vertical=10)
            field.border = {
                ft.ControlState.DEFAULT: ft.OutlineInputBorder(
                    border_radius=RADIUS, side=ft.BorderSide(1, p["line"])
                ),
                ft.ControlState.FOCUSED: ft.OutlineInputBorder(
                    border_radius=RADIUS, side=ft.BorderSide(1, p["accent"])
                ),
            }
        self.cited.text_style = ft.TextStyle(
            size=LABEL_SIZE, color=p["text"], font_family=FONT, font_family_fallback=[FONT_CN]
        )

        for seg, value in ((self.seg_clip, "剪贴板"), (self.seg_out, "Outlook 选中")):
            active = value == self.state["source"]
            seg.style = ft.ButtonStyle(
                bgcolor=p["ink_bg"] if active else p["page"],
                color=p["ink_fg"] if active else p["text"],
                side=ft.BorderSide(1, p["ink_bg"] if active else p["line"]),
                shape=ft.RoundedRectangleBorder(radius=RADIUS),
                elevation=0,
                text_style=ft.TextStyle(
                    font_family=FONT, size=BODY_SIZE, font_family_fallback=[FONT_CN]
                ),
            )

        self.btn_primary.style = ft.ButtonStyle(
            bgcolor={ft.ControlState.DEFAULT: RED, ft.ControlState.HOVERED: RED_DARK},
            color="#FFFFFF",
            shape=ft.RoundedRectangleBorder(radius=RADIUS),
            elevation=0,
            text_style=ft.TextStyle(
                font_family=FONT, size=BODY_SIZE, font_family_fallback=[FONT_CN]
            ),
        )

        for button in self._ghosts:
            button.style = ft.ButtonStyle(
                bgcolor={ft.ControlState.DEFAULT: p["page"], ft.ControlState.HOVERED: p["ink_bg"]},
                color={ft.ControlState.DEFAULT: p["text"], ft.ControlState.HOVERED: p["ink_fg"]},
                side=ft.BorderSide(1, p["line"]),
                shape=ft.RoundedRectangleBorder(radius=RADIUS),
                elevation=0,
                text_style=ft.TextStyle(
                    font_family=FONT, size=LABEL_SIZE, font_family_fallback=[FONT_CN]
                ),
            )

        on = self.state["mode"] == "dark"
        self.theme_knob.bgcolor = p["accent"] if on else None
        self.theme_knob.border = None if on else ft.Border.all(1, p["muted"])
        self.theme_row.alignment = (
            ft.MainAxisAlignment.END if on else ft.MainAxisAlignment.START
        )
        self.theme_box.border = ft.Border.all(1, p["line"])

    # ---------- 设置页数据 ----------

    def _mini_style(self, p):
        """列表小按钮样式。"""
        return ft.ButtonStyle(
            bgcolor={ft.ControlState.DEFAULT: p["page"], ft.ControlState.HOVERED: p["ink_bg"]},
            color={ft.ControlState.DEFAULT: p["text"], ft.ControlState.HOVERED: p["ink_fg"]},
            side=ft.BorderSide(1, p["line"]),
            shape=ft.RoundedRectangleBorder(radius=0),
            padding=ft.Padding.symmetric(horizontal=10),
            elevation=0,
            text_style=ft.TextStyle(
                size=LABEL_SIZE, font_family=FONT, font_family_fallback=[FONT_CN]
            ),
        )

    def refresh_settings(self):
        """刷新设置页数据。"""
        self.api_key.value = os.environ.get("LLM_API_KEY", "")
        self.base_url.value = os.environ.get("LLM_BASE_URL", "")
        self.model_field.value = os.environ.get("LLM_MODEL", "")
        self.api_status.value = f"当前：{LLM.mode} · {LLM.model}"

        p = PALETTE[self.state["mode"]]
        files = (
            sorted(x.name for x in HANDBOOK.iterdir() if x.is_file())
            if HANDBOOK.exists()
            else []
        )
        disabled = set(CFG["handbook_disabled"])
        self.handbook_list.controls = [
            ft.Row(
                [
                    txt(name, size=BODY_SIZE, expand=True),
                    self._toggle(
                        name not in disabled,
                        lambda v, n=name: self.on_handbook_toggle(n, v),
                    ),
                    ft.Button(
                        "删除",
                        width=56,
                        height=30,
                        style=self._mini_style(p),
                        on_click=lambda e, n=name: self.ask_handbook_del(n),
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            for name in files
        ] or [txt("（暂无手册文件）", size=BODY_SIZE, color=p["muted"])]

        self.style_status.value = (
            f"内置样本 {self._count_json(STYLE_SAMPLES)} 封 ｜ "
            f"已导入 {self._count_json(STYLE_IMPORTED)} 封"
        )

        self.rules_list.controls = [
            ft.Row(
                [
                    txt(rule, size=BODY_SIZE, expand=True),
                    ft.Button(
                        "删除",
                        width=56,
                        height=30,
                        style=self._mini_style(p),
                        on_click=lambda e, i=i: self.on_rule_del(i),
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            for i, rule in enumerate(CFG["rules"])
        ] or [txt("（暂无规则）", size=BODY_SIZE, color=p["muted"])]

    @staticmethod
    def _count_json(path):
        """数 JSON 条数。"""
        if not Path(path).exists():
            return 0
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return len(data) if isinstance(data, list) else 0
        except Exception:
            return 0

    # ---------- 交互 ----------

    def toast(self, message, error=False):
        """底部提示条。"""
        p = PALETTE[self.state["mode"]]
        mark = "✕ " if error else "✓ "
        self.page.show_dialog(
            ft.SnackBar(
                txt(
                    f"{mark}{message}",
                    size=LABEL_SIZE,
                    color="#FFFFFF" if error else p["ink_fg"],
                ),
                bgcolor=RED if error else p["ink_bg"],
                duration=4000,
            )
        )

    def save_config(self):
        """写回 config.json。"""
        (ROOT / "config.json").write_text(
            json.dumps(CFG, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def set_source(self, value):
        """切换来源。"""
        self.state["source"] = value
        self.paint()
        self.page.update()

    def show_main(self, e=None):
        """返回主页面。"""
        self.stage.content = self.main_view
        self.paint()
        self.page.update()

    def show_settings(self, e=None):
        """进入设置页。"""
        self.refresh_settings()
        self.stage.content = self.settings_view
        self.paint()
        self.page.update()

    def on_toggle_theme(self, e):
        """切换深浅色。"""
        self.state["mode"] = "dark" if self.state["mode"] == "light" else "light"
        self.page.theme_mode = (
            ft.ThemeMode.DARK if self.state["mode"] == "dark" else ft.ThemeMode.LIGHT
        )
        CFG["theme"] = self.state["mode"]
        try:
            self.save_config()
        except Exception:
            pass
        self.refresh_settings()
        self.paint()
        self.page.update()

    def _toggle(self, value, on_flip):
        """手绘开关。"""
        knob = ft.Container(width=16, height=16, border_radius=3)
        row = ft.Row([knob], spacing=0)
        box = ft.Container(content=row, width=46, height=26, padding=4, border_radius=RADIUS)
        current = {"value": value}

        def redraw():
            p = PALETTE[self.state["mode"]]
            knob.bgcolor = p["accent"] if current["value"] else None
            knob.border = None if current["value"] else ft.Border.all(1, p["muted"])
            row.alignment = (
                ft.MainAxisAlignment.END if current["value"] else ft.MainAxisAlignment.START
            )
            box.border = ft.Border.all(1, p["line"])

        def flip(_):
            current["value"] = not current["value"]
            on_flip(current["value"])
            redraw()
            self.page.update()

        def hover(e):
            p = PALETTE[self.state["mode"]]
            active = str(getattr(e, "data", "")).lower() in ("true", "1")
            box.border = ft.Border.all(1, p["accent"] if active else p["line"])
            box.update()

        box.on_click = flip
        box.on_hover = hover
        redraw()
        return box

    def on_read(self, e):
        """读取邮件。"""
        if self.btn_read.disabled:
            return
        self.btn_read.disabled = True
        self.status_word.value = "状态 读取中…"
        self.page.update()
        self.page.run_thread(self.do_read)

    def do_read(self):
        """后台读取邮件。"""
        adapter = None
        msg, note, error = None, "", ""
        try:
            adapter = (
                OutlookAdapter() if self.state["source"] == "Outlook 选中" else ClipboardAdapter()
            )
            msg = adapter.current()
            note = getattr(adapter, "note", "")
        except Exception as ex:
            hint = "，可改用「剪贴板」来源" if self.state["source"] == "Outlook 选中" else ""
            error = f"{ex}{hint}"
            log(f"读取邮件失败：{error}")
        finally:
            # 释放 COM
            if adapter is not None and hasattr(adapter, "close"):
                adapter.close()
        self.btn_read.disabled = False
        if error:
            self.status_word.value = "状态 —"
            self.toast(error, error=True)
            self.page.update()
            return
        self.state["msg"] = msg
        self.email_box.value = f"发件人：{msg.sender}\n主题：{msg.subject}\n\n{msg.body_text}"
        self.status_word.value = "状态 已读取"
        self.page.update()
        if note:
            self.toast(note)

    # ---------- 生成 ----------

    @staticmethod
    def _clause_label(clause):
        """条款显示名。"""
        if clause.get("clause_no"):
            return f"第 {clause['clause_no']} 条"
        path = (clause.get("section_path") or "").strip()
        return path.split("/")[-1] if path else "前言"

    def _format_clauses(self, clauses):
        """拼接条款文本。"""
        lines = []
        for clause in clauses:
            label = self._clause_label(clause)
            path = (clause.get("section_path") or "").strip()
            prefix = f"{label}（{path}）" if path and path != label else label
            lines.append(f"{prefix}：{clause.get('text', '')}")
        return "\n".join(lines)

    def run_generate(self):
        """生成回复主流程。"""
        try:
            raw = self.email_box.value or ""
            body = clean_email(raw)
            if not body.strip():
                self.gen.value = ""
                self.toast("请先读取或粘贴邮件内容", error=True)
                return

            # 以文本框为准
            sender, subject = parse_envelope(raw)
            old = self.state["msg"]
            msg = Message(
                id=old.id if old else uuid.uuid4().hex,
                source=old.source if old else "manual",
                sender=sender or (old.sender if old else "未知"),
                recipients=old.recipients if old else [],
                subject=subject
                or (old.subject if old else body.splitlines()[0][:30]),
                body_text=body,
                received_at=old.received_at if old else datetime.now(),
            )
            result = AGENT.run(msg)

            self.summary.value = result.get("summary", "")
            self.tags.value = "、".join(result.get("labels", [])) or "其他"

            p = PALETTE[self.state["mode"]]
            if result.get("offline"):
                self.chip.value = "离线演示 · 未调用模型"
                self.chip.color = p["accent"]
            elif result.get("engine_error"):
                self.chip.value = "已降级 · 本地演示"
                self.chip.color = p["accent"]
            else:
                self.chip.value = "需回复" if result.get("need_reply") else "无需回复"
                self.chip.color = p["text"]

            self.conf.value = f"置信度 {result.get('confidence', 0.0):.2f}"
            self.draft.value = result.get("draft", "")
            clauses = result.get("clauses", [])
            self.cited.value = self._format_clauses(clauses) or "（无命中条款）"

            # 校验引用是否存在
            valid = {c["clause_no"] for c in clauses if c["clause_no"]}
            cited = [str(n) for n in (result.get("cited_clauses") or [])]
            fake = sorted({n for n in cited if n not in valid})
            if fake:
                self.toast(f"注意：模型引用了不存在的条款 {'、'.join(fake)}，已忽略", error=True)

            self.gen.value = f"生成于 {datetime.now().strftime('%H:%M:%S')}"
            self.page.update()
            if result.get("engine_error"):
                self.toast(result["engine_error"], error=True)
        except Exception as ex:
            self.gen.value = "生成失败"
            log(f"生成失败：{ex}")
            try:
                self.page.update()
            except Exception:
                pass
            self.toast(f"生成失败：{ex}", error=True)
        finally:
            self.btn_primary.disabled = False
            try:
                self.page.update()
            except Exception:
                pass

    def on_generate(self, e):
        """点击生成。"""
        if self.btn_primary.disabled:
            return
        self.btn_primary.disabled = True
        self.gen.value = "生成中…"
        self.page.update()
        self.page.run_thread(self.run_generate)

    def on_copy(self, e):
        """复制草稿。"""
        text = self.draft.value or ""
        if not text.strip():
            self.toast("草稿为空，请先生成回复", error=True)
            return
        pyperclip.copy(text)
        self.toast("草稿已复制，请自行粘贴发送")

    # ---------- 索引与手册 ----------

    def do_rebuild(self):
        """重建索引。"""
        global INDEX
        try:
            new_index, errors = build_index(force=True)
        except Exception as ex:
            log(f"索引重建失败：{ex}")
            self.toast(f"索引重建失败：{ex}", error=True)
            return
        INDEX = new_index
        RETRIEVER.index = INDEX
        self.subtitle.value = status_text()
        self.page.update()
        if errors:
            detail = "；".join(f"{name}（{msg}）" for name, msg in errors)
            self.toast(f"重建完成，但部分手册被跳过：{detail}", error=True)
        else:
            self.toast("索引已重建")

    def on_rebuild(self, e):
        """点重建索引。"""
        self.toast("索引重建中…")
        self.page.run_thread(self.do_rebuild)

    @staticmethod
    def _unique_target(source: Path) -> Path:
        """生成不重名路径。"""
        suffix = source.suffix.lower() or ".md"
        target = HANDBOOK / f"{source.stem}{suffix}"
        index = 2
        while target.exists():
            target = HANDBOOK / f"{source.stem} ({index}){suffix}"
            index += 1
        return target

    async def on_pick(self, e):
        """上传手册。"""
        files = await self.picker.pick_files(
            dialog_title="选择手册文件",
            allow_multiple=False,
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["md", "txt", "pdf", "docx"],
        )
        if not files:
            return
        selected = files[0]
        if not selected.path:
            self.toast(f"当前模式无法获取文件路径：{selected.name}", error=True)
            return
        source = Path(selected.path)
        target = None
        try:
            HANDBOOK.mkdir(parents=True, exist_ok=True)
            target = self._unique_target(source)
            shutil.copy2(source, target)
            # 先试读，失败则删除
            if not read_document(target).strip():
                raise ValueError("没有可提取的文字（扫描件？）")
        except Exception as ex:
            if target is not None:
                try:
                    target.unlink(missing_ok=True)
                except Exception:
                    pass
            self.toast(f"导入失败：{ex}", error=True)
            return
        self.toast(f"已导入 {target.name}（仅保存在本机），正在重建索引…")
        self.refresh_settings()
        self.page.update()
        self.page.run_thread(self.do_rebuild)

    def on_handbook_del(self, name):
        """删除手册并重建。"""
        target = HANDBOOK / name
        try:
            if target.exists():
                target.unlink()
        except Exception as ex:
            self.toast(f"删除失败：{ex}", error=True)
            return
        CFG["handbook_disabled"] = [n for n in CFG["handbook_disabled"] if n != name]
        RETRIEVER.disabled_docs = set(CFG["handbook_disabled"])
        self.save_config()
        self.refresh_settings()
        self.page.update()
        self.page.run_thread(self.do_rebuild)

    def ask_handbook_del(self, name):
        """删除确认框。"""
        def close(_):
            self.page.pop_dialog()

        def confirm(_):
            self.page.pop_dialog()
            self.on_handbook_del(name)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=txt("确认删除", bold=True, size=18),
                content=txt(f"将删除 data/handbook/{name}，不可恢复。", size=BODY_SIZE),
                actions=[
                    ft.TextButton("取消", on_click=close),
                    ft.TextButton("删除", on_click=confirm),
                ],
            )
        )

    def on_handbook_toggle(self, name, value):
        """启用或停用手册。"""
        disabled = set(CFG["handbook_disabled"])
        if value:
            disabled.discard(name)
        else:
            disabled.add(name)
        CFG["handbook_disabled"] = sorted(disabled)
        RETRIEVER.disabled_docs = set(CFG["handbook_disabled"])
        self.save_config()

    def on_style_clear(self):
        """清除导入样本。"""
        try:
            if STYLE_IMPORTED.exists():
                STYLE_IMPORTED.unlink()
        except Exception as ex:
            self.toast(f"清除失败：{ex}", error=True)
            return
        self.refresh_settings()
        self.page.update()
        self.page.run_thread(self.do_rebuild)

    def ask_style_clear(self, e):
        """清除确认框。"""
        def close(_):
            self.page.pop_dialog()

        def confirm(_):
            self.page.pop_dialog()
            self.on_style_clear()

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=txt("确认清除", bold=True, size=18),
                content=txt("将删除已导入的语气样本（data/styles/imported.json），不可恢复。", size=BODY_SIZE),
                actions=[
                    ft.TextButton("取消", on_click=close),
                    ft.TextButton("清除", on_click=confirm),
                ],
            )
        )

    # ---------- 语气导入 ----------

    def do_import(self):
        """导入 Outlook 语气样本。"""
        try:
            adapter = OutlookAdapter()
            try:
                samples = adapter.sent_samples(limit=200)
            finally:
                adapter.close()
        except Exception as ex:
            log(f"导入语气失败：{ex}")
            self.toast(str(ex), error=True)
            return
        items = [
            {
                "to": EMAIL_MASK.sub("***@***", m.recipients[0] if m.recipients else ""),
                "subject": EMAIL_MASK.sub("***@***", m.subject),
                "body": EMAIL_MASK.sub("***@***", m.body_text),
                "date": m.received_at.strftime("%Y-%m-%d"),
            }
            for m in samples
            if len(m.body_text) >= 20
        ]
        if not items:
            self.toast("未找到可用的已发送邮件", error=True)
            return
        STYLE_IMPORTED.write_text(
            json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        self.do_rebuild()
        self.toast(f"已导入 {len(items)} 封历史邮件（已脱敏，仅保存在本机）")

    def on_import(self, e):
        """点导入语气。"""
        self.toast("正在读取 Outlook 已发送邮件…")
        self.page.run_thread(self.do_import)

    # ---------- 模型 ----------

    def rebuild_llm(self):
        """重建 LLM 链路。"""
        global LLM, TOOLS, AGENT
        LLM = ChatLLM()
        TOOLS = Toolbox(RETRIEVER, LLM, CFG["rules"], top_k=CFG.get("top_k", 5))
        AGENT = Orchestrator(TOOLS)
        self.subtitle.value = status_text()

    def on_save_api(self, e):
        """保存前测试连通。"""
        key = (self.api_key.value or "").strip()
        base = (self.base_url.value or "").strip()
        model = (self.model_field.value or "").strip()
        if not key or not base or not model:
            self.toast("API Key、Base URL 与模型名均不能为空", error=True)
            return
        self.btn_save_api.disabled = True
        self.api_status.value = "正在测试连通…"
        self.page.update()
        self.page.run_thread(lambda: self.save_api_worker(key, base, model))

    def save_api_worker(self, key, base, model):
        """后台保存 API 配置。"""
        old = (
            os.environ.get("LLM_API_KEY", ""),
            os.environ.get("LLM_BASE_URL", ""),
            os.environ.get("LLM_MODEL", ""),
        )
        os.environ["LLM_API_KEY"] = key
        os.environ["LLM_BASE_URL"] = base
        os.environ["LLM_MODEL"] = model
        try:
            ChatLLM().ping()
        except Exception as ex:
            # 失败则回滚
            os.environ["LLM_API_KEY"], os.environ["LLM_BASE_URL"], os.environ["LLM_MODEL"] = old
            self.rebuild_llm()
            self.api_status.value = f"当前：{LLM.mode} · {LLM.model}"
            self.btn_save_api.disabled = False
            self.page.update()
            self.toast(f"连通测试失败，未保存：{ex}", error=True)
            return

        env_path = ROOT / ".env"
        kept = []
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                k = line.split("=", 1)[0].strip() if "=" in line else ""
                if k not in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
                    kept.append(line)
        kept += [f"LLM_API_KEY={key}", f"LLM_BASE_URL={base}", f"LLM_MODEL={model}"]
        env_path.write_text("\n".join(kept) + "\n", encoding="utf-8")

        self.rebuild_llm()
        self.refresh_settings()
        self.btn_save_api.disabled = False
        self.page.update()
        self.toast(f"已保存并重连，当前 {LLM.mode}")

    # ---------- 规则 ----------

    def on_rule_add(self, e):
        """新增规则。"""
        value = (self.rule_field.value or "").strip()
        if not value:
            self.toast("请输入规则内容", error=True)
            return
        if value in CFG["rules"]:
            self.toast("该规则已存在", error=True)
            return
        if len(CFG["rules"]) >= RULES_LIMIT:
            self.toast(f"规则最多 {RULES_LIMIT} 条，请先删除一条", error=True)
            return
        CFG["rules"].append(value)
        self.rule_field.value = ""
        self.save_config()
        self.refresh_settings()
        self.subtitle.value = status_text()
        self.page.update()
        self.toast("规则已添加")

    def on_rule_del(self, index):
        """删除规则。"""
        if 0 <= index < len(CFG["rules"]):
            CFG["rules"].pop(index)
        self.save_config()
        self.refresh_settings()
        self.subtitle.value = status_text()
        self.page.update()
        self.toast("规则已删除")


async def main(page: ft.Page):
    """应用入口。"""
    page.title = "Mailwright"
    page.window.width = WIN_W
    page.window.height = WIN_H
    page.window.min_width = WIN_MIN_W
    page.window.min_height = WIN_MIN_H
    page.window.maximizable = True
    page.padding = 0
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
    page.vertical_alignment = ft.CrossAxisAlignment.STRETCH
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(font_family=FONT)
    page.dark_theme = ft.Theme(font_family=FONT)
    page.fonts = {
        FONT: "fonts/Inter-Regular.otf",
        FONT_BOLD: "fonts/Inter-Bold.otf",
        FONT_CN: "fonts/NotoSansSC-Regular.otf",
        FONT_CN_BOLD: "fonts/NotoSansSC-Bold.otf",
    }
    page.bgcolor = PALETTE["light"]["page"]

    # 加载页
    splash = ft.Container(
        expand=True,
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            [
                txt("Mailwright", bold=True, size=TITLE_SIZE),
                ft.ProgressRing(width=20, height=20, stroke_width=2, color=RED),
                txt("正在加载嵌入模型与索引…", size=LABEL_SIZE, color=PALETTE["light"]["muted"]),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=18,
        ),
    )
    page.add(splash)
    page.update()
    await asyncio.sleep(0.05)

    try:
        # 后台加载
        await asyncio.to_thread(bootstrap)
    except Exception as ex:
        splash.content = ft.Column(
            [
                txt("启动失败", bold=True, size=24, color=RED),
                txt(str(ex), size=BODY_SIZE),
                txt("请检查 config.json 是否完整、data/ 目录是否存在。", size=LABEL_SIZE),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=12,
        )
        page.update()
        return

    page.theme_mode = ft.ThemeMode.DARK if CFG.get("theme") == "dark" else ft.ThemeMode.LIGHT
    # 换成主界面
    app = App(page)
    page.controls.clear()
    page.add(app.root)
    page.update()
    await page.window.center()

    if STARTUP_WARNINGS:
        app.toast("部分手册被跳过：" + "；".join(STARTUP_WARNINGS), error=True)


if __name__ == "__main__":
    ft.run(main, assets_dir=str(ROOT / "assets"))
