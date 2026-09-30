# -*- coding: utf-8 -*-
"""
Minimal XLSX reader/writer for IronPython 2.7 / PyRevit.
No openpyxl dependency — uses .NET System.IO.Compression (always available in Revit).

Usage (write):
    from xlsxlite import XlsxWriter, STYLE_HEADER, STYLE_LOCKED, STYLE_EDITABLE
    wb = XlsxWriter()
    ws = wb.add_sheet('My Sheet')
    ws.write(0, 0, 'Hello', STYLE_HEADER)   # row, col  (0-based)
    ws.set_col_width(0, 20)
    ws.freeze(1, 2)                          # freeze after row 1, col 2  → C2
    wb.save(r'C:\path\to\file.xlsx')

Usage (read):
    from xlsxlite import XlsxReader
    wb = XlsxReader(r'C:\path\to\file.xlsx')
    print(wb.sheet_names)
    rows = wb.sheet('Sheet Parameters')     # list of lists, values_only
"""

import clr

try:
    clr.AddReference('System.IO.Compression')
except Exception:
    pass
try:
    clr.AddReference('System.IO.Compression.FileSystem')
except Exception:
    pass
try:
    clr.AddReference('System.Xml')
except Exception:
    pass

from System.IO.Compression import ZipArchive, ZipArchiveMode
from System.IO import (MemoryStream, FileStream, FileMode,
                        FileAccess, FileShare, StreamReader)
from System.Text import Encoding
import System.Xml as SysXml

# ── Public style index constants ──────────────────────────────────────────────
STYLE_DEFAULT  = 0   # plain
STYLE_HEADER   = 1   # dark-blue fill, white bold text
STYLE_LOCKED   = 2   # grey fill, grey text  (reference columns)
STYLE_EDITABLE = 3   # light-green fill, black text


# ── XML helpers ───────────────────────────────────────────────────────────────
def _esc(val):
    """XML-escape a value, returning a unicode string."""
    if val is None:
        return u''
    if not isinstance(val, unicode):
        try:
            val = unicode(val)
        except Exception:
            val = unicode(str(val), 'utf-8', 'replace')
    return (val.replace(u'&', u'&amp;')
               .replace(u'<', u'&lt;')
               .replace(u'>', u'&gt;')
               .replace(u'"', u'&quot;'))


def _col_letter(n):
    """0-based column index → Excel column letter(s). 0→A, 25→Z, 26→AA."""
    result = u''
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        result = unichr(65 + r) + result
    return result


def _cell_ref(row, col):
    """0-based (row, col) → 'A1' cell reference."""
    return u'{}{}'.format(_col_letter(col), row + 1)


def _col_index(cell_ref):
    """'A1' → 0,  'B3' → 1,  'AA5' → 26."""
    letters = u''
    for ch in cell_ref:
        if ch.isalpha():
            letters += ch.upper()
        else:
            break
    result = 0
    for ch in letters:
        result = result * 26 + (ord(ch) - 64)
    return result - 1


# ── Static XML fragments ──────────────────────────────────────────────────────
_STYLES_XML = u'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="4">
  <font><sz val="10"/><name val="Calibri"/></font>
  <font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
  <font><sz val="10"/><color rgb="FF808080"/><name val="Calibri"/></font>
  <font><sz val="10"/><color rgb="FF000000"/><name val="Calibri"/></font>
</fonts>
<fills count="5">
  <fill><patternFill patternType="none"/></fill>
  <fill><patternFill patternType="gray125"/></fill>
  <fill><patternFill patternType="solid"><fgColor rgb="FF1F3864"/></patternFill></fill>
  <fill><patternFill patternType="solid"><fgColor rgb="FFD6DCE4"/></patternFill></fill>
  <fill><patternFill patternType="solid"><fgColor rgb="FFE2EFDA"/></patternFill></fill>
</fills>
<borders count="2">
  <border><left/><right/><top/><bottom/></border>
  <border>
    <left style="thin"><color rgb="FFBFBFBF"/></left>
    <right style="thin"><color rgb="FFBFBFBF"/></right>
    <top style="thin"><color rgb="FFBFBFBF"/></top>
    <bottom style="thin"><color rgb="FFBFBFBF"/></bottom>
  </border>
</borders>
<cellStyleXfs count="1">
  <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
</cellStyleXfs>
<cellXfs count="4">
  <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
  <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0"
      applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">
    <alignment horizontal="left" vertical="center"/>
  </xf>
  <xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0"
      applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">
    <alignment horizontal="left" vertical="center"/>
  </xf>
  <xf numFmtId="0" fontId="3" fillId="4" borderId="1" xfId="0"
      applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">
    <alignment horizontal="left" vertical="center"/>
  </xf>
</cellXfs>
</styleSheet>'''

_RELS_XML = u'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''

_EMPTY_SS_XML = (u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 u'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
                 u' count="0" uniqueCount="0"/>')


# ── XlsxSheet ─────────────────────────────────────────────────────────────────
class XlsxSheet(object):
    def __init__(self, name):
        self.name = name
        self._cells = {}         # (row, col) → (value, style_idx)
        self._col_widths = {}    # col → width (chars)
        self._freeze_row = 0
        self._freeze_col = 0

    def write(self, row, col, value, style=STYLE_DEFAULT):
        """Write a value to a cell. row and col are 0-based."""
        self._cells[(row, col)] = (value, style)

    def set_col_width(self, col, width):
        """Set column width in approximate character units. col is 0-based."""
        self._col_widths[col] = min(float(width), 50.0)

    def freeze(self, freeze_row, freeze_col):
        """Freeze panes. freeze(1, 2) locks row 0 and cols 0-1, i.e. C2 in Excel."""
        self._freeze_row = freeze_row
        self._freeze_col = freeze_col

    def to_xml(self):
        parts = [u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                 u'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']

        # sheetViews / freeze pane
        parts.append(u'<sheetViews><sheetView workbookViewId="0">')
        if self._freeze_row > 0 or self._freeze_col > 0:
            top_left = _cell_ref(self._freeze_row, self._freeze_col)
            parts.append(
                u'<pane xSplit="{fc}" ySplit="{fr}" topLeftCell="{tl}"'
                u' activePane="bottomRight" state="frozen"/>'.format(
                    fc=self._freeze_col, fr=self._freeze_row, tl=top_left
                )
            )
        parts.append(u'</sheetView></sheetViews>')

        # column widths
        if self._col_widths:
            parts.append(u'<cols>')
            for col_0 in sorted(self._col_widths):
                w = self._col_widths[col_0]
                parts.append(
                    u'<col min="{c}" max="{c}" width="{w:.2f}" customWidth="1"/>'.format(
                        c=col_0 + 1, w=w
                    )
                )
            parts.append(u'</cols>')

        # sheet data — group cells by row
        parts.append(u'<sheetData>')
        rows_map = {}
        for (r, c), (val, sty) in self._cells.items():
            rows_map.setdefault(r, []).append((c, val, sty))

        for r in sorted(rows_map.keys()):
            parts.append(u'<row r="{}" ht="18" customHeight="1">'.format(r + 1))
            for c, val, sty in sorted(rows_map[r], key=lambda x: x[0]):
                ref = _cell_ref(r, c)
                s = u' s="{}"'.format(sty) if sty else u''
                if val is None:
                    parts.append(u'<c r="{}"{} t="inlineStr"><is><t/></is></c>'.format(ref, s))
                elif isinstance(val, bool):
                    parts.append(u'<c r="{}"{} t="b"><v>{}</v></c>'.format(
                        ref, s, 1 if val else 0))
                elif isinstance(val, (int, long, float)):
                    parts.append(u'<c r="{r}"{s}><v>{v}</v></c>'.format(r=ref, s=s, v=val))
                else:
                    parts.append(u'<c r="{}" t="inlineStr"{}><is><t>{}</t></is></c>'.format(
                        ref, s, _esc(val)))
            parts.append(u'</row>')

        parts.append(u'</sheetData>')
        parts.append(u'</worksheet>')
        return u'\n'.join(parts)


# ── XlsxWriter ────────────────────────────────────────────────────────────────
class XlsxWriter(object):
    def __init__(self):
        self._sheets = []

    def add_sheet(self, name):
        sheet = XlsxSheet(name)
        self._sheets.append(sheet)
        return sheet

    def save(self, path):
        fs = FileStream(path, FileMode.Create, FileAccess.Write)
        try:
            za = ZipArchive(fs, ZipArchiveMode.Create)
            try:
                self._write_entry(za, u'[Content_Types].xml',
                                  self._content_types_xml())
                self._write_entry(za, u'_rels/.rels', _RELS_XML)
                self._write_entry(za, u'xl/workbook.xml',
                                  self._workbook_xml())
                self._write_entry(za, u'xl/_rels/workbook.xml.rels',
                                  self._wb_rels_xml())
                self._write_entry(za, u'xl/styles.xml', _STYLES_XML)
                self._write_entry(za, u'xl/sharedStrings.xml', _EMPTY_SS_XML)
                for i, sheet in enumerate(self._sheets, 1):
                    self._write_entry(
                        za,
                        u'xl/worksheets/sheet{}.xml'.format(i),
                        sheet.to_xml()
                    )
            finally:
                za.Dispose()
        finally:
            fs.Dispose()

    def _write_entry(self, za, name, content_unicode):
        entry = za.CreateEntry(name)
        stream = entry.Open()
        try:
            data = Encoding.UTF8.GetBytes(content_unicode)
            stream.Write(data, 0, data.Length)
        finally:
            stream.Dispose()

    def _content_types_xml(self):
        lines = [
            u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            u'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
            u'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
            u'<Default Extension="xml" ContentType="application/xml"/>',
            u'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
            u'<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
            u'<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>',
        ]
        for i in range(1, len(self._sheets) + 1):
            lines.append(
                u'<Override PartName="/xl/worksheets/sheet{i}.xml"'
                u' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'.format(i=i)
            )
        lines.append(u'</Types>')
        return u'\n'.join(lines)

    def _workbook_xml(self):
        lines = [
            u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            u'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            u' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
            u'<sheets>',
        ]
        for i, sheet in enumerate(self._sheets, 1):
            lines.append(
                u'<sheet name="{n}" sheetId="{i}" r:id="rId{i}"/>'.format(
                    n=_esc(sheet.name), i=i
                )
            )
        lines.append(u'</sheets></workbook>')
        return u'\n'.join(lines)

    def _wb_rels_xml(self):
        lines = [
            u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            u'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
        ]
        for i in range(1, len(self._sheets) + 1):
            lines.append(
                u'<Relationship Id="rId{i}" '
                u'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                u'Target="worksheets/sheet{i}.xml"/>'.format(i=i)
            )
        lines.append(
            u'<Relationship Id="rIdSS" '
            u'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
            u'Target="sharedStrings.xml"/>'
        )
        lines.append(u'</Relationships>')
        return u'\n'.join(lines)


# ── XlsxReader ────────────────────────────────────────────────────────────────
class XlsxReader(object):
    """
    Read an .xlsx file and expose its sheets as lists of rows.
    Each row is a list of Python values (str/int/float/bool/None).
    Handles both shared-string and inline-string cell encodings.
    """
    def __init__(self, path):
        self._sheet_names = []
        self._sheet_data  = {}
        self._load(path)

    @property
    def sheet_names(self):
        return list(self._sheet_names)

    def sheet(self, name):
        """Return list-of-rows for the named sheet, or [] if not found."""
        return self._sheet_data.get(name, [])

    # ── internal ──────────────────────────────────────────────────────────────

    def _load(self, path):
        fs = FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read)
        try:
            za = ZipArchive(fs, ZipArchiveMode.Read)
            try:
                shared = self._read_shared_strings(za)
                sheet_list = self._read_workbook_sheets(za)
                for name, target in sheet_list:
                    self._sheet_names.append(name)
                    rows = self._read_worksheet(za, target, shared)
                    self._sheet_data[name] = rows
            finally:
                za.Dispose()
        finally:
            fs.Dispose()

    def _read_entry_text(self, za, entry_name):
        """Return entry content as unicode string, or None if missing."""
        entry_name = entry_name.replace(u'\\', u'/')
        for entry in za.Entries:
            if entry.FullName.replace(u'\\', u'/') == entry_name:
                s = entry.Open()
                try:
                    sr = StreamReader(s, Encoding.UTF8)
                    return sr.ReadToEnd()
                finally:
                    s.Dispose()
        return None

    def _parse_xml(self, text):
        if not text:
            return None
        doc = SysXml.XmlDocument()
        doc.LoadXml(text)
        return doc

    def _ns(self, doc, prefix, uri):
        mgr = SysXml.XmlNamespaceManager(doc.NameTable)
        mgr.AddNamespace(prefix, uri)
        return mgr

    def _read_shared_strings(self, za):
        text = self._read_entry_text(za, u'xl/sharedStrings.xml')
        if not text:
            return []
        doc = self._parse_xml(text)
        ns = self._ns(doc, u'x',
                      u'http://schemas.openxmlformats.org/spreadsheetml/2006/main')
        result = []
        for si in doc.SelectNodes(u'//x:si', ns):
            t_nodes = si.SelectNodes(u'.//x:t', ns)
            val = u''
            for t in t_nodes:
                val += t.InnerText
            result.append(val)
        return result

    def _read_workbook_sheets(self, za):
        """Return list of (sheet_name, path_in_zip)."""
        wb_text = self._read_entry_text(za, u'xl/workbook.xml')
        doc = self._parse_xml(wb_text)
        ns = self._ns(doc, u'x',
                      u'http://schemas.openxmlformats.org/spreadsheetml/2006/main')

        # Read relationship map from xl/_rels/workbook.xml.rels
        rels_text = self._read_entry_text(za, u'xl/_rels/workbook.xml.rels')
        rels_doc  = self._parse_xml(rels_text)
        r_ns = self._ns(rels_doc, u'r',
                        u'http://schemas.openxmlformats.org/package/2006/relationships')
        rid_map = {}
        for rel in rels_doc.SelectNodes(u'//r:Relationship', r_ns):
            rid_map[rel.GetAttribute(u'Id')] = rel.GetAttribute(u'Target')

        result = []
        for node in doc.SelectNodes(u'//x:sheet', ns):
            name = node.GetAttribute(u'name')
            # r:id attribute lives in the relationships namespace
            rid = node.GetAttribute(
                u'id',
                u'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
            )
            target = rid_map.get(rid, u'')
            # Target is relative to xl/; normalise to full zip path
            if target and not target.startswith(u'xl/'):
                target = u'xl/' + target
            result.append((name, target))
        return result

    def _read_worksheet(self, za, path, shared_strings):
        """Return list of rows; each row is a list of values."""
        text = self._read_entry_text(za, path)
        if not text:
            return []
        doc = self._parse_xml(text)
        ns = self._ns(doc, u'x',
                      u'http://schemas.openxmlformats.org/spreadsheetml/2006/main')

        rows = []
        for row_node in doc.SelectNodes(u'//x:sheetData/x:row', ns):
            row_map = {}
            for c_node in row_node.SelectNodes(u'x:c', ns):
                ref       = c_node.GetAttribute(u'r')
                cell_type = c_node.GetAttribute(u't')
                v_node    = c_node.SelectSingleNode(u'x:v', ns)
                is_node   = c_node.SelectSingleNode(u'x:is/x:t', ns)
                col_idx   = _col_index(ref)

                if cell_type == u's':
                    # Shared string
                    idx = int(v_node.InnerText) if v_node is not None else 0
                    val = shared_strings[idx] if idx < len(shared_strings) else u''
                elif cell_type == u'inlineStr':
                    val = is_node.InnerText if is_node is not None else u''
                elif cell_type == u'b':
                    val = (v_node is not None and v_node.InnerText == u'1')
                elif v_node is not None:
                    raw = v_node.InnerText
                    try:
                        val = int(raw) if u'.' not in raw else float(raw)
                    except (ValueError, TypeError):
                        val = raw
                else:
                    val = None

                row_map[col_idx] = val

            if row_map:
                max_col = max(row_map.keys())
                rows.append([row_map.get(i) for i in range(max_col + 1)])

        return rows
