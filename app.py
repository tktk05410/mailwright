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
from agent.tools import Toolbox
from core.llm import ChatLLM
from core.models import Message
from preprocess.clean import clean_email
from rag.indexer import Embedder, KnowledgeIndex
from rag.retriever import Retriever
from rag.store import (
    load_handbook_chunks,
    load_index,
    load_style_chunks,
    save_index,
    sources_snapshot,
)

ROOT = Path(__file__).parent
DATA = ROOT / "data"

FONT = "Inter"
FONT_BOLD = "Inter Bold"
FONT_FILE = "fonts/Inter-Regular.otf"
FONT_BOLD_FILE = "fonts/Inter-Bold.otf"
FONT_CN = "Noto Sans SC"
FONT_CN_BOLD = "Noto Sans SC Bold"
FONT_CN_FILE = "fonts/NotoSansSC-Regular.otf"
FONT_CN_BOLD_FILE = "fonts/NotoSansSC-Bold.otf"

RED = "#DA291C"
RED_DARK = "#B52217"
BASE_W = 640
BASE_H = 480
HEADER_H = 108
SETTINGS_HEADER_H = 76
BODY_H = 292
FOOTER_H = 78
EMAIL_BOX_HEIGHT = 112
DRAFT_HEIGHT = 116
CITED_HEIGHT = 46
FIELD_H = 34
SEG_H = 26
SPAN_2 = 92
SPAN_3 = 142
LEFT_W = 242
RIGHT_W = 342

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


def load_env():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


load_env()
config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
config.setdefault("handbook_disabled", [])
EMB = Embedder()


STYLE_SAMPLES = DATA / "styles" / "samples.json"
STYLE_IMPORTED = DATA / "styles" / "imported.json"


def style_chunks():
    chunks = load_style_chunks(STYLE_SAMPLES)
    if STYLE_IMPORTED.exists():
        chunks.extend(load_style_chunks(STYLE_IMPORTED, prefix="imp"))
    return chunks


def build_index(force=False):
    srcs = sources_snapshot(DATA / "handbook", extra_files=[STYLE_SAMPLES, STYLE_IMPORTED])
    cached = None if force else load_index(DATA / "index", EMB, sources=srcs)
    if cached is not None:
        return cached
    chunks = load_handbook_chunks(DATA / "handbook") + style_chunks()
    idx = KnowledgeIndex(EMB)
    idx.rebuild(chunks)
    save_index(idx, DATA / "index", sources=srcs)
    return idx


index = build_index()
retriever = Retriever(
    index,
    min_cos=config.get("min_cos", 0.45),
    min_rrf=config.get("min_rrf", 0.01),
    disabled_docs=config["handbook_disabled"],
)
llm = ChatLLM()
tools = Toolbox(
    retriever,
    llm,
    config["rules"],
    config["blocklist"],
    top_k=config.get("top_k", 5),
)
agent = Orchestrator(tools)


def status_text():
    hb = sum(1 for c in index.chunks if c.kind == "handbook")
    st = sum(1 for c in index.chunks if c.kind == "style")
    return f"{llm.mode} ｜ 手册 {hb} 块 ｜ 语气样本 {st} 封 ｜ 规则 {len(config['rules'])} 条"


async def main(page: ft.Page):
    page.title = "Mailwright"
    page.window.width = BASE_W * 1.5
    page.window.height = BASE_H * 1.5
    page.window.min_width = 560
    page.window.min_height = BASE_H
    page.window.maximizable = True
    page.padding = 0
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(font_family=FONT)
    page.dark_theme = ft.Theme(font_family=FONT)
    page.fonts = {
        FONT: FONT_FILE,
        FONT_BOLD: FONT_BOLD_FILE,
        FONT_CN: FONT_CN_FILE,
        FONT_CN_BOLD: FONT_CN_BOLD_FILE,
    }

    state = {"mode": config.get("theme", "light"), "source": "剪贴板", "msg": None, "scale": 1.0}
    page.theme_mode = ft.ThemeMode.DARK if state["mode"] == "dark" else ft.ThemeMode.LIGHT

    def txt(value, bold=False, **kwargs):
        return ft.Text(
            value,
            font_family=FONT_BOLD if bold else FONT,
            font_family_fallback=[FONT_CN_BOLD if bold else FONT_CN],
            **kwargs,
        )

    def toast(msg, error=False):
        p = PALETTE[state["mode"]]
        mark = "✕ " if error else "✓ "
        page.show_dialog(
            ft.SnackBar(
                txt(f"{mark}{msg}", color=p["ink_fg"], size=12),
                bgcolor=RED if error else p["ink_bg"],
                duration=4000,
            )
        )

    def set_source(value):
        state["source"] = value
        paint()
        page.update()

    def on_toggle(e):
        state["mode"] = "dark" if state["mode"] == "light" else "light"
        page.theme_mode = ft.ThemeMode.DARK if state["mode"] == "dark" else ft.ThemeMode.LIGHT
        config["theme"] = state["mode"]
        save_config()
        paint()
        refresh_settings()
        page.update()

    def on_read(e):
        adapter = None
        try:
            adapter = OutlookAdapter() if state["source"] == "Outlook 选中" else ClipboardAdapter()
            msg = adapter.current()
        except Exception as ex:
            hint = "，可改用「剪贴板」来源" if state["source"] == "Outlook 选中" else ""
            toast(f"{ex}{hint}", error=True)
            return
        finally:
            if adapter is not None and hasattr(adapter, "close"):
                adapter.close()
        state["msg"] = msg
        email_box.value = f"发件人：{msg.sender}\n主题：{msg.subject}\n\n{msg.body_text}"
        page.update()

    def run_generate():
        try:
            email_text = clean_email(email_box.value or "")
            if not email_text.strip():
                gen.value = ""
                toast("请先读取或粘贴邮件内容", error=True)
                return
            old_msg = state["msg"]
            subject = old_msg.subject if old_msg else email_text.splitlines()[0][:30]
            msg = Message(
                id=old_msg.id if old_msg else uuid.uuid4().hex,
                source=old_msg.source if old_msg else "manual",
                sender=old_msg.sender if old_msg else "未知",
                recipients=old_msg.recipients if old_msg else [],
                subject=subject,
                body_text=email_text,
                received_at=old_msg.received_at if old_msg else datetime.now(),
            )
            result = agent.run(msg)
            if result.get("blocked"):
                summary.value = ""
                labels.value = ""
                draft.value = ""
                cited.value = ""
                conf.value = "置信度 —"
                chip_text.value = f"已拦截 · {result['sender']}"
                chip_text.color = PALETTE[state["mode"]]["accent"]
                gen.value = ""
                page.update()
                toast(f"发件人 {result['sender']} 命中黑名单，已拦截", error=True)
                return
            summary.value = result.get("summary", "")
            labels.value = "、".join(result.get("labels", [])) or "其他"
            if not llm.online:
                chip_text.value = "离线演示 · 未调用模型"
                chip_text.color = PALETTE[state["mode"]]["accent"]
            else:
                chip_text.value = "需回复" if result.get("need_reply") else "无需回复"
                chip_text.color = PALETTE[state["mode"]]["text"]
            conf.value = f"置信度 {result.get('confidence', 0.0):.2f}"
            draft.value = result.get("draft", "")
            valid = {c["clause_no"] for c in result.get("clauses", []) if c["clause_no"]}
            cited_raw = [str(n) for n in (result.get("cited_clauses") or [])]
            fake = sorted({n for n in cited_raw if n not in valid})
            if fake:
                toast(f"注意：模型引用了不存在的条款 {'、'.join(fake)}，已忽略", error=True)
            cited.value = "\n".join(
                f"第 {c['clause_no']} 条（{c['section_path']}）：{c['text']}"
                for c in result.get("clauses", [])
                if c["clause_no"]
            ) or "（无命中条款）"
            gen.value = f"生成于 {datetime.now().strftime('%H:%M:%S')}"
            page.update()
            if result.get("engine_error"):
                toast(result["engine_error"], error=True)
        except Exception as ex:
            gen.value = "生成失败"
            try:
                page.update()
            except Exception:
                pass
            toast(f"生成失败：{ex}", error=True)
        finally:
            btn_primary.disabled = False
            try:
                page.update()
            except Exception:
                pass

    def on_generate(e):
        if btn_primary.disabled:
            return
        btn_primary.disabled = True
        gen.value = "生成中…"
        page.update()
        page.run_thread(run_generate)

    def on_copy(e):
        pyperclip.copy(draft.value or "")
        toast("草稿已复制，请自行粘贴发送")

    def do_rebuild():
        global index
        index = build_index(force=True)
        retriever.index = index
        subtitle.value = status_text()
        toast("索引已重建")
        page.update()

    def on_rebuild(e):
        toast("索引重建中…")
        page.run_thread(do_rebuild)

    def do_import():
        try:
            adapter = OutlookAdapter()
            try:
                samples = adapter.sent_samples(limit=200)
            finally:
                adapter.close()
        except Exception as ex:
            toast(str(ex), error=True)
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
            toast("未找到可用的已发送邮件", error=True)
            return
        STYLE_IMPORTED.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        do_rebuild()
        toast(f"已导入 {len(items)} 封历史邮件（已脱敏，仅保存在本机）")

    def on_import(e):
        toast("正在读取 Outlook 已发送邮件…")
        page.run_thread(do_import)

    async def on_pick(e):
        files = await picker.pick_files(
            dialog_title="选择手册文件",
            allow_multiple=False,
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["md", "txt", "pdf", "docx"],
        )
        if not files:
            return
        f = files[0]
        if not f.path:
            toast(f"当前模式无法获取文件路径：{f.name}", error=True)
            return
        try:
            suffix = Path(f.path).suffix.lower() or ".md"
            target = DATA / "handbook" / f"{Path(f.name).stem}{suffix}"
            shutil.copy(Path(f.path), target)
        except Exception as ex:
            toast(f"复制失败：{ex}", error=True)
            return
        toast(f"已导入 {f.name}（仅保存在本机），正在重建索引…")
        refresh_settings()
        page.update()
        page.run_thread(do_rebuild)

    def rebuild_llm():
        global llm, tools, agent
        llm = ChatLLM()
        tools = Toolbox(retriever, llm, config["rules"], set(config["blocklist"]))
        agent = Orchestrator(tools)
        subtitle.value = status_text()

    def save_config():
        (ROOT / "config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def on_save_api(e):
        key = api_key_field.value.strip()
        base = base_url_field.value.strip()
        model = model_field.value.strip()
        if not key or not base or not model:
            toast("API Key、Base URL 与模型名均不能为空", error=True)
            return
        btn_save_api.disabled = True
        api_status.value = "正在测试连通…"
        page.update()
        page.run_thread(lambda: save_api_worker(key, base, model))

    def save_api_worker(key, base, model):
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
            os.environ["LLM_API_KEY"], os.environ["LLM_BASE_URL"], os.environ["LLM_MODEL"] = old
            rebuild_llm()
            api_status.value = f"当前：{llm.mode} · {llm.model}"
            btn_save_api.disabled = False
            page.update()
            toast(f"连通测试失败，未保存：{ex}", error=True)
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
        rebuild_llm()
        refresh_settings()
        btn_save_api.disabled = False
        page.update()
        toast(f"已保存并重连，当前 {llm.mode}")

    def on_rule_add(e):
        value = (rule_field.value or "").strip()
        if not value:
            toast("请输入规则内容", error=True)
            return
        config["rules"].append(value)
        rule_field.value = ""
        save_config()
        refresh_settings()
        subtitle.value = status_text()
        page.update()
        toast("规则已添加")

    def on_rule_del(i):
        config["rules"].pop(i)
        save_config()
        refresh_settings()
        subtitle.value = status_text()
        page.update()
        toast("规则已删除")

    def on_blocklist_save(e):
        raw = blocklist_field.value or ""
        items = [x.strip() for x in raw.replace("，", ",").replace("、", ",").replace("\n", ",").split(",")]
        filtered = [x for x in items if x]
        config["blocklist"].clear()
        config["blocklist"].extend(filtered)
        save_config()
        refresh_settings()
        page.update()
        toast("黑名单已保存")

    def on_handbook_del(name):
        target = DATA / "handbook" / name
        if target.exists():
            target.unlink()
        config["handbook_disabled"] = [n for n in config["handbook_disabled"] if n != name]
        retriever.disabled_docs = set(config["handbook_disabled"])
        save_config()
        refresh_settings()
        page.update()
        page.run_thread(do_rebuild)

    def confirm_handbook_del(e, name):
        page.pop_dialog()
        on_handbook_del(name)

    def ask_handbook_del(e, name):
        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=txt("确认删除", bold=True, size=14, height=18),
                content=txt(f"将删除 data/handbook/{name}，不可恢复。", size=12, height=16),
                actions=[
                    ft.TextButton("取消", on_click=lambda e2: page.pop_dialog()),
                    ft.TextButton("删除", on_click=lambda e2: confirm_handbook_del(e2, name)),
                ],
            )
        )

    def on_handbook_toggle(name, value):
        disabled = set(config["handbook_disabled"])
        if value:
            disabled.discard(name)
        else:
            disabled.add(name)
        config["handbook_disabled"] = sorted(disabled)
        retriever.disabled_docs = set(config["handbook_disabled"])
        save_config()

    def on_style_clear(e):
        if STYLE_IMPORTED.exists():
            STYLE_IMPORTED.unlink()
        refresh_settings()
        page.update()
        page.run_thread(do_rebuild)

    def confirm_style_clear(e):
        page.pop_dialog()
        on_style_clear(e)

    def ask_style_clear(e):
        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=txt("确认清除", bold=True, size=14, height=18),
                content=txt("将删除已导入的语气样本（data/styles/imported.json），不可恢复。", size=12, height=16),
                actions=[
                    ft.TextButton("取消", on_click=lambda e2: page.pop_dialog()),
                    ft.TextButton("清除", on_click=lambda e2: confirm_style_clear(e2)),
                ],
            )
        )

    def make_toggle(value, on_flip):
        knob = ft.Container(width=12, height=12)
        row = ft.Row([knob], spacing=0)
        box = ft.Container(content=row, width=36, height=18, padding=3)
        cur = {"value": value}

        def redraw():
            p = PALETTE[state["mode"]]
            knob.bgcolor = p["accent"] if cur["value"] else None
            knob.border = None if cur["value"] else ft.Border.all(1, p["muted"])
            row.alignment = ft.MainAxisAlignment.END if cur["value"] else ft.MainAxisAlignment.START
            box.border = ft.Border.all(1, p["line"])

        def flip(e):
            cur["value"] = not cur["value"]
            on_flip(cur["value"])
            redraw()
            page.update()

        box.on_click = flip
        redraw()
        return box

    def mini_style(p):
        return ft.ButtonStyle(
            bgcolor={
                ft.ControlState.DEFAULT: p["page"],
                ft.ControlState.HOVERED: p["ink_bg"],
            },
            color={
                ft.ControlState.DEFAULT: p["text"],
                ft.ControlState.HOVERED: p["ink_fg"],
            },
            side=ft.BorderSide(1, p["line"]),
            shape=ft.RoundedRectangleBorder(radius=0),
            padding=ft.Padding.symmetric(horizontal=8),
            text_style=ft.TextStyle(
                size=10, font_family=FONT, font_family_fallback=[FONT_CN]
            ),
        )

    title = txt("Mailwright", bold=True, size=42, height=56)
    title_dot = ft.Container(width=10, height=10, bgcolor=RED, margin=ft.Margin.only(left=8, bottom=8))
    subtitle = txt(status_text(), size=9, height=12)
    btn_settings = ft.Button("设置", on_click=lambda e: show_settings(e), width=SPAN_2, height=30)

    n01 = txt("01", bold=True, size=10, height=14, color=RED)
    n02 = txt("02", bold=True, size=10, height=14, color=RED)
    n03 = txt("03", bold=True, size=10, height=14, color=RED)
    n04 = txt("04", bold=True, size=10, height=14, color=RED)
    n05 = txt("05", bold=True, size=10, height=14, color=RED)
    n06 = txt("06", bold=True, size=10, height=14, color=RED)
    SETTING_NUMS = [txt(f"0{i}", bold=True, size=10, height=14, color=RED) for i in range(1, 6)]
    accent_texts = (n01, n02, n03, n04, n05, n06, *SETTING_NUMS)

    lbl_src = txt("来源", bold=True, size=10, height=14)
    lbl_mail = txt("邮件正文", bold=True, size=10, height=14)
    lbl_cited = txt("引用条款", bold=True, size=10, height=14)
    lbl_sum = txt("摘要", bold=True, size=10, height=14)
    lbl_tags = txt("标签", bold=True, size=10, height=14)
    lbl_draft = txt("回复草稿", bold=True, size=10, height=14)

    seg_clip = ft.Button("剪贴板", on_click=lambda e: set_source("剪贴板"), width=SPAN_2, height=SEG_H)
    seg_out = ft.Button("Outlook 选中", on_click=lambda e: set_source("Outlook 选中"), width=SPAN_3, height=SEG_H)

    email_box = ft.TextField(
        multiline=True,
        fit_parent_size=True,
        text_vertical_align=-1.0,
        hint_text="从剪贴板或 Outlook 读取，也可直接粘贴邮件正文",
    )
    summary = ft.TextField(read_only=True, height=FIELD_H, hint_text="生成后显示邮件摘要")
    labels = ft.TextField(read_only=True, height=FIELD_H, hint_text="生成后显示分类标签")
    draft = ft.TextField(
        multiline=True,
        fit_parent_size=True,
        text_vertical_align=-1.0,
        hint_text="① 粘贴或读取邮件正文 → ② 点「生成回复」，草稿将显示在这里",
    )
    cited = ft.TextField(
        multiline=True,
        read_only=True,
        min_lines=2,
        max_lines=2,
        height=CITED_HEIGHT,
        hint_text="生成后显示命中的手册条款",
    )

    status_word = txt("状态 —", size=9, height=12)
    chip_text = txt("待生成", bold=True, size=10, height=14)
    conf = txt("置信度 —", size=9, height=12)
    gen = txt("", size=9, height=12)

    btn_primary = ft.Button("生成回复", on_click=on_generate, width=SPAN_3, height=32)
    btn_read = ft.Button("读取邮件", on_click=on_read, width=SPAN_2, height=32)
    btn_copy = ft.Button("复制草稿", on_click=on_copy, width=SPAN_2, height=32)

    settings_title = txt("设置", bold=True, size=42, height=52)
    settings_dot = ft.Container(width=10, height=10, bgcolor=RED, margin=ft.Margin.only(left=8, bottom=8))
    btn_back = ft.Button("返回", on_click=lambda e: show_main(e), width=SPAN_2, height=30)

    sec_look = txt("外观", bold=True, size=13, height=16)
    sec_api = txt("模型 API", bold=True, size=13, height=16)
    sec_book = txt("手册管理", bold=True, size=13, height=16)
    sec_style = txt("语气管理", bold=True, size=13, height=16)
    sec_rules = txt("规则管理", bold=True, size=13, height=16)
    settings_secs = (sec_look, sec_api, sec_book, sec_style, sec_rules)

    lbl_key = txt("API Key", size=10, height=12)
    lbl_base = txt("Base URL", size=10, height=12)
    lbl_model = txt("模型名", size=10, height=12)
    lbl_block = txt("黑名单", size=10, height=12)
    api_status = txt("", size=9, height=12)
    style_status = txt("", size=9, height=12)
    dark_label = txt("深色模式", size=12, height=16)

    api_key_field = ft.TextField(password=True, can_reveal_password=True, height=FIELD_H)
    base_url_field = ft.TextField(height=FIELD_H)
    model_field = ft.TextField(height=FIELD_H)
    btn_save_api = ft.Button("保存并重连", on_click=on_save_api, width=SPAN_3, height=32)

    handbook_list = ft.Column([], spacing=6)
    btn_upload = ft.Button("上传手册", on_click=on_pick, width=SPAN_3, height=32)
    btn_import = ft.Button("导入语气", on_click=on_import, width=SPAN_3, height=32)
    btn_style_clear = ft.Button("清除导入", on_click=ask_style_clear, width=SPAN_2, height=32)

    rules_list = ft.Column([], spacing=6)
    rule_field = ft.TextField(hint_text="输入新规则后点击添加", expand=True, height=FIELD_H)
    btn_rule_add = ft.Button("添加", on_click=on_rule_add, width=SPAN_2, height=32)
    blocklist_field = ft.TextField(hint_text="多个地址用逗号分隔", height=FIELD_H)
    btn_blocklist_save = ft.Button("保存黑名单", on_click=on_blocklist_save, width=SPAN_3, height=32)
    btn_rebuild = ft.Button("重建索引", on_click=on_rebuild, width=SPAN_3, height=32)

    theme_knob = ft.Container(width=12, height=12)
    theme_row = ft.Row([theme_knob], spacing=0)
    theme_box = ft.Container(content=theme_row, width=36, height=18, padding=3, on_click=on_toggle)

    def paint_theme_toggle(p):
        on = state["mode"] == "dark"
        theme_knob.bgcolor = p["accent"] if on else None
        theme_knob.border = None if on else ft.Border.all(1, p["muted"])
        theme_row.alignment = ft.MainAxisAlignment.END if on else ft.MainAxisAlignment.START
        theme_box.border = ft.Border.all(1, p["line"])

    ghosts = [
        btn_settings,
        btn_back,
        btn_read,
        btn_copy,
        btn_save_api,
        btn_upload,
        btn_import,
        btn_style_clear,
        btn_rule_add,
        btn_blocklist_save,
        btn_rebuild,
    ]
    all_fields = (
        email_box,
        summary,
        labels,
        draft,
        cited,
        api_key_field,
        base_url_field,
        model_field,
        rule_field,
        blocklist_field,
    )
    zone_labels = (lbl_src, lbl_mail, lbl_cited, lbl_sum, lbl_tags, lbl_draft)
    muted_labels = (lbl_key, lbl_base, lbl_model, lbl_block, api_status, style_status)

    rule_top = ft.Container(height=1)
    rule_bottom = ft.Container(height=1)
    divider = ft.Container(width=1)

    def paint():
        p = PALETTE[state["mode"]]
        page.bgcolor = p["page"]
        stage.bgcolor = p["page"]

        title.color = p["text"]
        settings_title.color = p["text"]
        subtitle.color = p["muted"]
        dark_label.color = p["text"]
        chip_text.color = p["text"]
        for t in zone_labels:
            t.color = p["text"]
        for t in muted_labels:
            t.color = p["muted"]
        for t in settings_secs:
            t.color = p["text"]
        for t in (status_word, conf, gen):
            t.color = p["muted"]
        for t in accent_texts:
            t.color = p["accent"]

        for c in (rule_top, rule_bottom, divider):
            c.bgcolor = p["line"]
        for c in settings_lines:
            c.bgcolor = p["line"]

        for f in all_fields:
            f.bgcolor = p["field"]
            f.cursor_color = p["text"]
            f.text_style = ft.TextStyle(
                size=12, color=p["text"], font_family=FONT, font_family_fallback=[FONT_CN]
            )
            f.hint_style = ft.TextStyle(
                size=12, color=p["muted"], font_family=FONT, font_family_fallback=[FONT_CN]
            )
            f.content_padding = ft.Padding.symmetric(horizontal=10, vertical=8)
            f.border = {
                ft.ControlState.DEFAULT: ft.OutlineInputBorder(
                    border_radius=0, side=ft.BorderSide(1, p["line"])
                ),
                ft.ControlState.FOCUSED: ft.OutlineInputBorder(
                    border_radius=0, side=ft.BorderSide(1, RED)
                ),
            }
        cited.text_style = ft.TextStyle(
            size=11, color=p["text"], font_family=FONT, font_family_fallback=[FONT_CN]
        )

        for seg, value in ((seg_clip, "剪贴板"), (seg_out, "Outlook 选中")):
            if value == state["source"]:
                seg.style = ft.ButtonStyle(
                    bgcolor=p["ink_bg"],
                    color=p["ink_fg"],
                    side=ft.BorderSide(1, p["ink_bg"]),
                    shape=ft.RoundedRectangleBorder(radius=0),
                    text_style=ft.TextStyle(
                        font_family=FONT, size=11, font_family_fallback=[FONT_CN]
                    ),
                )
            else:
                seg.style = ft.ButtonStyle(
                    bgcolor=p["page"],
                    color=p["text"],
                    side=ft.BorderSide(1, p["line"]),
                    shape=ft.RoundedRectangleBorder(radius=0),
                    text_style=ft.TextStyle(
                        font_family=FONT, size=11, font_family_fallback=[FONT_CN]
                    ),
                )

        btn_primary.style = ft.ButtonStyle(
            bgcolor={
                ft.ControlState.DEFAULT: RED,
                ft.ControlState.HOVERED: RED_DARK,
            },
            color="#FFFFFF",
            shape=ft.RoundedRectangleBorder(radius=0),
            text_style=ft.TextStyle(
                font_family=FONT, size=12, font_family_fallback=[FONT_CN]
            ),
        )
        for b in ghosts:
            b.style = ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.DEFAULT: p["page"],
                    ft.ControlState.HOVERED: p["ink_bg"],
                },
                color={
                    ft.ControlState.DEFAULT: p["text"],
                    ft.ControlState.HOVERED: p["ink_fg"],
                },
                side=ft.BorderSide(1, p["line"]),
                shape=ft.RoundedRectangleBorder(radius=0),
                text_style=ft.TextStyle(
                    font_family=FONT, size=11, font_family_fallback=[FONT_CN]
                ),
            )

        paint_theme_toggle(p)

    header = ft.Container(
        content=ft.Row(
            [
                ft.Column(
                    [
                        ft.Row(
                            [title, title_dot],
                            spacing=0,
                            vertical_alignment=ft.CrossAxisAlignment.END,
                        ),
                        ft.Container(subtitle, margin=ft.Margin.only(top=6)),
                    ],
                    spacing=0,
                ),
                btn_settings,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.START,
        ),
        padding=ft.Padding.symmetric(horizontal=24, vertical=12),
        height=HEADER_H,
    )

    left_zone = ft.Column(
        [
            ft.Row([n01, lbl_src], spacing=8),
            ft.Row([seg_clip, seg_out], spacing=8, margin=ft.Margin.only(top=6)),
            ft.Row([n02, lbl_mail], spacing=8, margin=ft.Margin.only(top=14)),
            ft.Container(email_box, height=EMAIL_BOX_HEIGHT, margin=ft.Margin.only(top=6)),
            ft.Row([n03, lbl_cited], spacing=8, margin=ft.Margin.only(top=14)),
            ft.Container(cited, margin=ft.Margin.only(top=6)),
        ],
        spacing=0,
        width=LEFT_W,
    )

    right_zone = ft.Column(
        [
            ft.Row([n04, lbl_sum], spacing=8),
            ft.Container(summary, margin=ft.Margin.only(top=6)),
            ft.Row([n05, lbl_tags], spacing=8, margin=ft.Margin.only(top=14)),
            ft.Container(labels, margin=ft.Margin.only(top=6)),
            ft.Row([n06, lbl_draft], spacing=8, margin=ft.Margin.only(top=14)),
            ft.Container(draft, height=DRAFT_HEIGHT, margin=ft.Margin.only(top=6)),
        ],
        spacing=0,
        width=RIGHT_W,
    )

    body = ft.Container(
        content=ft.Row(
            [left_zone, ft.Container(width=3.5), divider, ft.Container(width=3.5), right_zone],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
        padding=ft.Padding.symmetric(horizontal=24, vertical=10),
        height=BODY_H,
    )

    footer = ft.Container(
        content=ft.Row(
            [
                ft.Column(
                    [
                        ft.Row([status_word, chip_text], spacing=6),
                        ft.Row([conf, gen], spacing=16),
                    ],
                    spacing=6,
                ),
                ft.Row([btn_read, btn_copy, btn_primary], spacing=8),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=24),
        height=FOOTER_H,
    )

    main_view = ft.Column([header, rule_top, body, rule_bottom, footer], spacing=0)

    settings_header = ft.Container(
        content=ft.Row(
            [
                ft.Column(
                    [
                        ft.Row(
                            [settings_title, settings_dot],
                            spacing=0,
                            vertical_alignment=ft.CrossAxisAlignment.END,
                        ),
                    ],
                    spacing=0,
                ),
                btn_back,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.START,
        ),
        padding=ft.Padding.symmetric(horizontal=24, vertical=12),
        height=SETTINGS_HEADER_H,
    )

    settings_rule = ft.Container(height=1)
    settings_lines = [
        ft.Container(height=1, margin=ft.Margin.only(top=8)) for _ in range(5)
    ]

    def sec_row(num_idx, label_t):
        return ft.Row(
            [SETTING_NUMS[num_idx - 1], label_t],
            spacing=8,
        )

    settings_body = ft.Container(
        content=ft.Column(
            [
                sec_row(1, sec_look),
                settings_lines[0],
                ft.Row(
                    [dark_label, theme_box],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    margin=ft.Margin.only(top=12),
                ),
                sec_row(2, sec_api),
                settings_lines[1],
                ft.Container(lbl_key, margin=ft.Margin.only(top=12)),
                ft.Container(api_key_field, margin=ft.Margin.only(top=6)),
                ft.Container(lbl_base, margin=ft.Margin.only(top=10)),
                ft.Container(base_url_field, margin=ft.Margin.only(top=6)),
                ft.Container(lbl_model, margin=ft.Margin.only(top=10)),
                ft.Container(model_field, margin=ft.Margin.only(top=6)),
                ft.Row(
                    [btn_save_api, api_status],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    margin=ft.Margin.only(top=12),
                ),
                ft.Container(sec_row(3, sec_book), margin=ft.Margin.only(top=26)),
                settings_lines[2],
                ft.Container(handbook_list, margin=ft.Margin.only(top=12)),
                ft.Container(btn_upload, margin=ft.Margin.only(top=10)),
                ft.Container(sec_row(4, sec_style), margin=ft.Margin.only(top=26)),
                settings_lines[3],
                ft.Container(style_status, margin=ft.Margin.only(top=12)),
                ft.Row(
                    [btn_import, btn_style_clear],
                    spacing=8,
                    margin=ft.Margin.only(top=10),
                ),
                ft.Container(sec_row(5, sec_rules), margin=ft.Margin.only(top=26)),
                settings_lines[4],
                ft.Container(rules_list, margin=ft.Margin.only(top=12)),
                ft.Row(
                    [rule_field, btn_rule_add],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    margin=ft.Margin.only(top=10),
                ),
                ft.Container(lbl_block, margin=ft.Margin.only(top=12)),
                ft.Container(blocklist_field, margin=ft.Margin.only(top=6)),
                ft.Container(btn_blocklist_save, margin=ft.Margin.only(top=10)),
                ft.Container(btn_rebuild, margin=ft.Margin.only(top=26, bottom=12)),
            ],
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        padding=ft.Padding.symmetric(horizontal=24, vertical=10),
        expand=True,
    )

    settings_view = ft.Column([settings_header, settings_rule, settings_body], spacing=0)

    def refresh_settings():
        api_key_field.value = os.environ.get("LLM_API_KEY", "")
        base_url_field.value = os.environ.get("LLM_BASE_URL", "")
        model_field.value = os.environ.get("LLM_MODEL", "")
        api_status.value = f"当前：{llm.mode} · {llm.model}"

        p = PALETTE[state["mode"]]
        book_dir = DATA / "handbook"
        files = sorted(x.name for x in book_dir.iterdir() if x.is_file()) if book_dir.exists() else []
        disabled = set(config["handbook_disabled"])
        handbook_list.controls = [
            ft.Row(
                [
                    txt(name, size=12, height=16, expand=True),
                    make_toggle(
                        name not in disabled,
                        lambda v, n=name: on_handbook_toggle(n, v),
                    ),
                    ft.Button(
                        "删除",
                        width=44,
                        height=26,
                        style=mini_style(p),
                        on_click=lambda e, n=name: ask_handbook_del(e, n),
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            for name in files
        ] or [txt("（暂无手册文件）", size=12, height=16)]

        n_builtin = 0
        if STYLE_SAMPLES.exists():
            try:
                n_builtin = len(json.loads(STYLE_SAMPLES.read_text(encoding="utf-8")))
            except Exception:
                n_builtin = 0
        n_imp = 0
        if STYLE_IMPORTED.exists():
            try:
                n_imp = len(json.loads(STYLE_IMPORTED.read_text(encoding="utf-8")))
            except Exception:
                n_imp = 0
        style_status.value = f"内置样本 {n_builtin} 封 ｜ 已导入 {n_imp} 封"

        rules_list.controls = [
            ft.Row(
                [
                    txt(rule, size=12, height=16, expand=True),
                    ft.Button(
                        "删除",
                        width=44,
                        height=26,
                        style=mini_style(p),
                        on_click=lambda e, i=i: on_rule_del(i),
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            for i, rule in enumerate(config["rules"])
        ] or [txt("（暂无规则）", size=12, height=16)]
        blocklist_field.value = "、".join(config["blocklist"])

    def show_settings(e=None):
        refresh_settings()
        stage.content = settings_view
        paint()
        page.update()

    def show_main(e=None):
        stage.content = main_view
        paint()
        page.update()

    stage = ft.Container(width=BASE_W, height=BASE_H, content=main_view)
    wrapper = ft.Container(content=stage, alignment=ft.Alignment.CENTER, expand=True)

    def on_resize(e):
        s = min(e.width / BASE_W, e.height / BASE_H)
        if s > 0 and abs(s - state["scale"]) > 0.004:
            state["scale"] = s
            stage.scale = ft.Scale(scale=s)
            page.update()

    page.on_resize = on_resize

    picker = ft.FilePicker()
    page.services.append(picker)

    paint()
    page.add(wrapper)
    await page.window.center()


if __name__ == "__main__":
    ft.run(main, assets_dir=str(ROOT / "assets"))
