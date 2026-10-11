"""Brief documents as text: a case folder may hold Markdown, Word, PDF, Excel, JSON/YAML and images.

to_text(path)        one file as Markdown text (never raises: an unreadable file becomes a one-line note)
case_files(folder)   every file under folder, recursively: BRIEF.md first, README.md second, the rest by relative path
load_folder(folder)  every document under a `# <relative name>` heading, in case_files order
"""
from pathlib import Path

TEXT = {".md", ".markdown", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml"}
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".tif", ".tiff"}
CONVERT = {".docx", ".pdf", ".xlsx", ".xlsm", ".xls"}


def read_text(path):
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1254", "latin-1"):   # cp1254: Windows Turkish
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _cell(v):
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def md_table(rows):
    """Rows (lists of values) as a Markdown pipe table; the first row is the header. Empty rows are dropped."""
    rows = [[_cell(v) for v in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return "(empty table)"
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


def _docx(path):
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    d = docx.Document(str(path))
    parts = []
    for el in d.element.body.iterchildren():           # body order: paragraphs and tables interleaved
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(el, d)
            t = p.text.strip()
            if not t:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            if style.lower().startswith("heading"):
                lvl = "".join(c for c in style if c.isdigit()) or "1"
                t = "#" * min(int(lvl) + 1, 6) + " " + t
            parts.append(t)
        elif tag == "tbl":
            tbl = Table(el, d)
            parts.append(md_table([[c.text for c in r.cells] for r in tbl.rows]))
    return "\n\n".join(parts)


def _pdf(path):
    from pypdf import PdfReader
    r = PdfReader(str(path))
    pages = []
    for i, page in enumerate(r.pages, 1):
        t = (page.extract_text() or "").strip()
        pages.append("[page %d]\n%s" % (i, t or "(no text on this page)"))
    return "\n\n".join(pages)


def _xlsx(path):
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    parts = []
    for ws in wb.worksheets:
        parts.append("## Sheet: %s\n\n%s" % (ws.title, md_table(list(ws.iter_rows(values_only=True)))))
    wb.close()
    return "\n\n".join(parts)


def to_text(path):
    """The file as Markdown text. Images and unknown types: a note naming the file. Never raises."""
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext in TEXT:
            return read_text(p)
        if ext == ".docx":
            return _docx(p)
        if ext == ".pdf":
            return _pdf(p)
        if ext in (".xlsx", ".xlsm"):
            return _xlsx(p)
        if ext == ".xls":
            try:
                return _xlsx(p)
            except Exception:
                return "%s (old Excel .xls, not read)" % p.name
        if ext in IMAGES:
            return "%s (image, not read)" % p.name
        try:
            t = p.read_bytes().decode("utf-8")
            if "\x00" not in t:
                return t
        except UnicodeDecodeError:
            pass
        return "%s (unknown type %s, not read)" % (p.name, ext or "(none)")
    except Exception as e:  # noqa: BLE001 - one bad document must not stop the build
        return "%s (could not be read: %s: %s)" % (p.name, type(e).__name__, str(e)[:200])


def _rank(rel):
    name = rel.name.lower()
    top = len(rel.parts) == 1
    if name == "brief.md":
        return 0 if top else 2
    if name == "readme.md":
        return 1 if top else 3
    return 4


def case_files(folder, skip=lambda p: False):
    """Every file under folder (recursively; hidden files and folders left out), BRIEF.md, README.md, then the rest
    sorted by relative path (case-insensitively, then exactly)."""
    folder = Path(folder)
    out = []
    for p in folder.rglob("*"):
        rel = p.relative_to(folder)
        if any(part.startswith(".") for part in rel.parts) or not p.is_file() or skip(p):
            continue
        out.append(p)
    return sorted(out, key=lambda p: (_rank(p.relative_to(folder)), str(p.relative_to(folder)).lower(),
                                      str(p.relative_to(folder))))


def load_folder(folder, skip=lambda p: False):
    folder = Path(folder)
    return "\n\n".join("# %s\n\n%s" % (p.relative_to(folder).as_posix(), to_text(p).strip())
                       for p in case_files(folder, skip))
