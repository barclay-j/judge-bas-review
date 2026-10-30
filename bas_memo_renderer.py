#!/usr/bin/env python3
"""
Judge Accountants — BAS Review Memo / Prefilled BAS renderer.

Usage:  python3 bas_memo_renderer.py memo.json out.pdf [--logo logo.png] [--font-dir DIR]

Turns the agent's JSON (schema: bas_memo_schema.json) into the branded A4 PDF:
  - header, title, subheading, meta table, link legend
  - Executive Summary bullets and Key risk drivers, each with its own fillable checkbox
  - five locked indicator rows rendered as static glyphs (not AcroForm fields)
  - GST Variance Explanation and Notes to Reviewer as 1,000-char fillable text fields
  - sections with anchors; green Xero links ({{xero:URL|text}}) and blue section jumps ({{sec:N}})
  - High rows in red; footer with page numbers
  - verifies every link annotation with pypdf and prints a report; non-zero exit on failure
"""
import json, re, sys, os, argparse, html
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, Flowable, KeepTogether, Image)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

GREEN = colors.HexColor("#00852B"); BLUE = colors.HexColor("#1155CC"); RED = colors.HexColor("#C00000")
GREY_FILL = colors.HexColor("#F2F2F2"); GREY_LINE = colors.HexColor("#BFBFBF"); GREY_TXT = colors.HexColor("#666666")
FOOTER = "Prepared by Claude (AI-assisted)  |  Judge Accountants  |  For Accountant Review Only  |  Not for Client Distribution"

# ---------- fonts ----------
def register_fonts(font_dir):
    """Prefer Segoe UI if supplied; otherwise DejaVu Sans (has the ☐/☑ glyphs)."""
    cands = []
    if font_dir:
        cands.append(("SegoeUI", os.path.join(font_dir, "segoeui.ttf"), os.path.join(font_dir, "segoeuib.ttf"),
                      os.path.join(font_dir, "segoeuii.ttf")))
    cands.append(("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"))
    for name, reg, bold, ital in cands:
        if all(os.path.exists(p) for p in (reg, bold, ital)):
            pdfmetrics.registerFont(TTFont(name, reg)); pdfmetrics.registerFont(TTFont(name + "-Bold", bold))
            pdfmetrics.registerFont(TTFont(name + "-Italic", ital))
            pdfmetrics.registerFontFamily(name, normal=name, bold=name + "-Bold", italic=name + "-Italic",
                                          boldItalic=name + "-Bold")
            return name
    return "Helvetica"

# ---------- markup ----------
TOKEN_XERO = re.compile(r"\{\{xero:([^|}]+)\|([^}]+)\}\}")
TOKEN_SEC = re.compile(r"\{\{sec:(\d+)\}\}")

def mk(text):
    """Convert plain text with {{xero:url|label}} / {{sec:N}} tokens and **bold** into reportlab markup."""
    if text is None: return ""
    text = str(text)
    parts, pos = [], 0
    for m in re.finditer(r"\{\{(xero|sec):[^}]*\}\}", text):
        parts.append(html.escape(text[pos:m.start()])); parts.append(m.group(0)); pos = m.end()
    parts.append(html.escape(text[pos:]))
    s = "".join(parts)
    s = TOKEN_XERO.sub(lambda m: f'<a href="{m.group(1)}" color="#00852B"><u>{html.escape(m.group(2))}</u></a>', s)
    s = TOKEN_SEC.sub(lambda m: f'<a href="#sec{m.group(1)}" color="#1155CC"><u>{m.group(1)}</u></a>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return s.replace("\n", "<br/>")

# ---------- fillable flowables ----------
class CheckLine(Flowable):
    """One independently toggleable checkbox + paragraph."""
    def __init__(self, name, para, box=11):
        super().__init__(); self.name, self.para, self.box = name, para, box
    def wrap(self, aw, ah):
        self.w, self.h = self.para.wrap(aw - self.box - 8, ah); self.width = aw
        self.height = max(self.h, self.box + 2); return aw, self.height
    def draw(self):
        c = self.canv
        c.acroForm.checkbox(name=self.name, x=0, y=self.height - self.box - 1, size=self.box, checked=False,
                            buttonStyle="check", shape="square", borderWidth=0.75, borderColor=GREY_LINE,
                            fillColor=colors.white, relative=True, forceBorder=True)
        self.para.drawOn(c, self.box + 8, self.height - self.h)

class TextBox(Flowable):
    """Multi-line fillable text field (1,000 chars) with a label above it."""
    def __init__(self, name, label_para, lines=8, font="Helvetica"):
        super().__init__(); self.name, self.label, self.lines, self.font = name, label_para, lines, font
    def wrap(self, aw, ah):
        self.lw, self.lh = self.label.wrap(aw, ah); self.width = aw
        self.fh = self.lines * 14 + 8; self.height = self.lh + 4 + self.fh; return aw, self.height
    def draw(self):
        c = self.canv; self.label.drawOn(c, 0, self.height - self.lh)
        c.acroForm.textfield(name=self.name, x=0, y=0, width=self.width, height=self.fh, value="",
                             fieldFlags="multiline", maxlen=1000, borderWidth=0.75, borderColor=GREY_LINE,
                             fillColor=colors.HexColor("#FBFBFB"), fontName="Helvetica", fontSize=9, relative=True,
                             forceBorder=True)

# ---------- document ----------
class Renderer:
    def __init__(self, data, logo=None, font_dir=None):
        self.d = data; self.logo = logo if logo and os.path.exists(logo) else None
        self.font = register_fonts(font_dir); self.fid = 0
        f = self.font
        self.st = dict(
            body=ParagraphStyle("body", fontName=f, fontSize=10, leading=13),
            small=ParagraphStyle("small", fontName=f, fontSize=8.5, leading=11),
            cell=ParagraphStyle("cell", fontName=f, fontSize=8.5, leading=10.5),
            cellhead=ParagraphStyle("cellhead", fontName=f + "-Bold" if f != "Helvetica" else "Helvetica-Bold",
                                    fontSize=8.5, leading=10.5, textColor=colors.white),
            h1=ParagraphStyle("h1", fontName=f + "-Bold" if f != "Helvetica" else "Helvetica-Bold", fontSize=13,
                              leading=16, textColor=GREEN, spaceBefore=10, spaceAfter=5),
            title=ParagraphStyle("title", fontName=f + "-Bold" if f != "Helvetica" else "Helvetica-Bold",
                                 fontSize=14, leading=17, textColor=colors.black, spaceAfter=2),
            sub=ParagraphStyle("sub", fontName=f + "-Italic" if f != "Helvetica" else "Helvetica-Oblique",
                               fontSize=10, leading=12, textColor=GREY_TXT, spaceAfter=8),
            label=ParagraphStyle("label", fontName=f + "-Bold" if f != "Helvetica" else "Helvetica-Bold",
                                 fontSize=10, leading=13),
        )

    def P(self, text, style="body"): return Paragraph(mk(text), self.st[style])
    def new_field(self, prefix): self.fid += 1; return f"{prefix}_{self.fid}"

    # header / footer on every page
    def on_page(self, c, doc):
        c.saveState(); w, h = A4; top = h - 1.6 * cm
        if self.logo:
            try: c.drawImage(self.logo, 2.5 * cm, top - 0.9 * cm, width=3 * cm, preserveAspectRatio=True, mask="auto")
            except Exception: self.logo = None
        if not self.logo:
            c.setFont(self.font + "-Bold" if self.font != "Helvetica" else "Helvetica-Bold", 20); c.setFillColor(GREEN)
            c.drawString(2.5 * cm, top - 0.5 * cm, "Judge Accountants")
        c.setStrokeColor(GREEN); c.setLineWidth(0.8); c.line(2.5 * cm, top - 0.75 * cm, w - 2.5 * cm, top - 0.75 * cm)
        c.setFont(self.font, 7); c.setFillColor(GREY_TXT)
        c.drawString(2.5 * cm, 1.4 * cm, FOOTER); c.drawRightString(w - 2.5 * cm, 1.0 * cm, f"Page {doc.page}")
        c.restoreState()

    def table(self, cols, rows, red_rows=None, col_widths=None, bold_rows=None):
        red_rows = set(red_rows or []); bold_rows = set(bold_rows or [])
        data = [[Paragraph(mk(c), self.st["cellhead"]) for c in cols]]
        for i, r in enumerate(rows):
            cells = []
            for v in r:
                txt = mk(v)
                if i in red_rows: txt = f'<font color="#C00000">{txt}</font>'
                if i in bold_rows: txt = f"<b>{txt}</b>"
                cells.append(Paragraph(txt, self.st["cell"]))
            data.append(cells)
        aw = A4[0] - 5 * cm
        if col_widths: cw = [aw * x / sum(col_widths) for x in col_widths]
        else: cw = [aw / len(cols)] * len(cols)
        t = Table(data, colWidths=cw, repeatRows=1)
        style = [("BACKGROUND", (0, 0), (-1, 0), GREEN), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("GRID", (0, 0), (-1, -1), 0.4, GREY_LINE), ("LEFTPADDING", (0, 0), (-1, -1), 5),
                 ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 4),
                 ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
        for i in range(1, len(data)):
            if i % 2 == 0: style.append(("BACKGROUND", (0, i), (-1, i), GREY_FILL))
        t.setStyle(TableStyle(style)); return t

    def locked_row(self, label, options, selected):
        glyphs = "   ".join(("\u2611 " if o == selected else "\u2610 ") + html.escape(o) for o in options)
        return Paragraph(f"<b>{html.escape(label)}:</b>  {glyphs}", self.st["body"])

    def build(self, out):
        d, s = self.d, []
        have = {str(x.get("number")) for x in d.get("sections", []) if x.get("number") is not None}
        refs = set(TOKEN_SEC.findall(json.dumps(d, ensure_ascii=False)))
        if refs - have:
            raise SystemExit(f"JSON references sections that are not present: {sorted(refs - have)}; "
                             f"sections present: {sorted(have)}")
        doc = BaseDocTemplate(out, pagesize=A4, leftMargin=2.5 * cm, rightMargin=2.5 * cm, topMargin=2.9 * cm,
                              bottomMargin=2.5 * cm, title=d.get("title", ""), author="Judge Accountants")
        fr = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
        doc.addPageTemplates([PageTemplate(id="p", frames=[fr], onPage=self.on_page)])
        prefilled = d.get("documentType") == "prefilled_bas"

        s.append(Paragraph(html.escape(d["title"]), self.st["title"]))
        s.append(Paragraph(html.escape(d.get("subheading") or (
            "BAS Supporting Working Paper — For Accountant Review Only — Not for Client Distribution" if prefilled
            else "BAS Review Working Paper — For Accountant Review Only — Not for Client Distribution")), self.st["sub"]))
        s.append(self.table(["Working Paper", ""], [[k, v] for k, v in d.get("meta", [])], col_widths=[1, 4]))
        s.append(Spacer(1, 6))
        s.append(self.P("Underlined green text is a hyperlink to the corresponding record in Xero. Underlined blue "
                        "section numbers (e.g. Section {{sec:4}}) jump to that section within this document. "
                        + (d.get("legendExtra") or ""), "small"))
        s.append(Spacer(1, 8))

        if not prefilled:
            es = d["executiveSummary"]
            s.append(Paragraph("EXECUTIVE SUMMARY", self.st["h1"]))
            s.append(self.P("Concise conclusion (tick each line once reviewed and agreed):", "small"))
            for b in es.get("bullets", []):
                s.append(CheckLine(self.new_field("es"), self.P(b))); s.append(Spacer(1, 3))
            s.append(Spacer(1, 6))
            for lab, opts, key in (("Bank Rec Completed", ["Yes", "No", "Unable to Confirm"], "bankRecCompleted"),
                                   ("BAS Lodgement Ready", ["Yes", "No", "With Reservations"], "lodgementReady"),
                                   ("Material Variances Identified", ["Yes", "No"], "materialVariances")):
                s.append(self.locked_row(lab, opts, es[key])); s.append(Spacer(1, 3))
            s.append(Spacer(1, 4)); s.append(self.P("**Key risk drivers:**"))
            for b in es.get("keyRiskDrivers", []):
                s.append(CheckLine(self.new_field("krd"), self.P(b))); s.append(Spacer(1, 3))
            s.append(Spacer(1, 4))
            s.append(self.locked_row("GST Risk Level", ["Low", "Medium", "High"], es["gstRiskLevel"])); s.append(Spacer(1, 3))
            s.append(self.P(f"**GST Variance:** {es.get('gstVariance', '')}")); s.append(Spacer(1, 4))
            s.append(TextBox("gst_variance_explanation", self.P("**GST Variance Explanation** (accountant to complete):"),
                             lines=7, font=self.font))
            s.append(Spacer(1, 6))
            s.append(self.locked_row("Overall Risk Level", ["Low", "Medium", "High"], es["overallRiskLevel"]))
            s.append(Spacer(1, 4)); s.append(self.P(f"**Lock Periods:** {es.get('lockPeriods', '')}"))
            s.append(Spacer(1, 4)); s.append(self.P(f"**Recommended Action:** {es.get('recommendedAction', '')}"))
            s.append(Spacer(1, 6))
            s.append(TextBox("notes_to_reviewer", self.P("**Notes to Reviewer:**"), lines=10, font=self.font))
            s.append(Spacer(1, 6)); s.append(self.P(f"**Time:** {es.get('time', '')}"))
            if es.get("usage"): s.append(self.P(f"**Usage:** {es['usage']}", "small"))
            s.append(PageBreak()); s.append(Paragraph("BAS ACCOUNTANT REVIEW SUMMARY", self.st["h1"]))
        else:
            if d.get("instructions"):
                s.append(Paragraph("Instructions", self.st["h1"]))
                for b in d["instructions"]: s.append(self.P("• " + b)); s.append(Spacer(1, 2))
                s.append(Spacer(1, 6))

        for sec in d.get("sections", []):
            n = sec.get("number"); anchor = f'<a name="sec{n}"/>' if n is not None else ""
            heading = f"{n}. {sec['title']}" if n is not None else sec["title"]
            block = [Paragraph(anchor + html.escape(heading), self.st["h1"])]
            if sec.get("intro"): block.append(self.P(sec["intro"], "small")); block.append(Spacer(1, 4))
            s.append(KeepTogether(block))
            for t in sec.get("tables", []):
                if t.get("caption"): s.append(self.P(f"**{t['caption']}**", "small")); s.append(Spacer(1, 2))
                s.append(self.table(t["columns"], t.get("rows", []), t.get("redRows"), t.get("colWidths"), t.get("boldRows")))
                s.append(Spacer(1, 6))
            for para in sec.get("observations", []): s.append(self.P(para)); s.append(Spacer(1, 3))
            s.append(Spacer(1, 6))
        doc.build(s)

# ---------- verification ----------
def verify(pdf_path, expect_sections, expected_goto):
    """reportlab resolves <a href="#secN"> into direct /Dest arrays [page /XYZ x y z] (genuine GoTo
    destinations, not named dests). Check every link is either a URI or a page-bound GoTo, that the
    number of GoTo links equals the number of {{sec:N}} tokens, and that fields exist."""
    from pypdf import PdfReader
    r = PdfReader(pdf_path); page_ids = {p.indirect_reference.idnum for p in r.pages}
    uri, goto, bad = 0, 0, []
    for pno, page in enumerate(r.pages, 1):
        for a in page.get("/Annots", []) or []:
            a = a.get_object()
            if a.get("/Subtype") != "/Link": continue
            act = a.get("/A"); dest = a.get("/Dest")
            if act is not None and act.get_object().get("/S") == "/URI": uri += 1; continue
            if act is not None and act.get_object().get("/S") == "/GoTo": dest = act.get_object().get("/D")
            if dest is not None:
                d = dest.get_object() if hasattr(dest, "get_object") else dest
                tgt = d[0] if isinstance(d, list) and d else None
                if tgt is not None and getattr(tgt, "idnum", None) in page_ids: goto += 1; continue
            bad.append((pno, str(a.get("/A")), str(a.get("/Dest"))))
    fields = r.get_fields() or {}
    cbs = [k for k, v in fields.items() if v.get("/FT") == "/Btn"]
    txs = [k for k, v in fields.items() if v.get("/FT") == "/Tx"]
    print(f"links: {uri} Xero (URI), {goto} internal (GoTo) of {expected_goto} expected; "
          f"checkboxes: {len(cbs)}; text fields: {len(txs)}; sections: {expect_sections}")
    if bad: print("BAD links:", bad)
    if goto != expected_goto: print("GoTo count mismatch")
    return not bad and goto == expected_goto and len(txs) >= 1

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("json"); ap.add_argument("out")
    ap.add_argument("--logo"); ap.add_argument("--font-dir"); a = ap.parse_args()
    data = json.load(open(a.json, encoding="utf-8"))
    Renderer(data, a.logo, a.font_dir).build(a.out)
    expect = [s["number"] for s in data.get("sections", []) if s.get("number") is not None]
    expected_goto = len(TOKEN_SEC.findall(json.dumps(data, ensure_ascii=False))) + 1  # +1 for the legend example
    ok = verify(a.out, expect, expected_goto); print("PDF:", a.out, "OK" if ok else "FAILED"); sys.exit(0 if ok else 1)

if __name__ == "__main__": main()
