# -*- coding: utf-8 -*-
"""
Export selected sheets and their title block parameters to Excel.

Author : TitleBlockManager
Revit  : 2024 / 2025 / 2026
PyRevit: 4.8+
"""
__title__ = 'Export\nSheets'
__doc__   = 'Export selected sheets + title block parameters to Excel.'

import os
import sys

# ── add shared lib to path ────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB  = os.path.join(_HERE, 'lib')
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from xlsxlite import XlsxWriter, STYLE_HEADER, STYLE_LOCKED, STYLE_EDITABLE

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewSheet,
    BuiltInCategory,
    BuiltInParameter,
    StorageType,
)

try:
    from Autodesk.Revit.DB import SpecTypeId
    HAS_FORGE = True
except Exception:
    HAS_FORGE = False

from pyrevit import forms, script

doc    = __revit__.ActiveUIDocument.Document
output = script.get_output()


def _read_param(p):
    if p is None:
        return u''
    st = p.StorageType
    if st == StorageType.String:
        v = p.AsString()
        return v if v is not None else u''
    if st == StorageType.Integer:
        is_yesno = False
        if HAS_FORGE:
            try:
                is_yesno = (p.Definition.GetDataType() == SpecTypeId.Boolean.YesNo)
            except Exception:
                pass
        return (u'True' if p.AsInteger() == 1 else u'False') if is_yesno else p.AsInteger()
    if st == StorageType.Double:
        return p.AsDouble()
    if st == StorageType.ElementId:
        eid = p.AsElementId()
        try:
            el = doc.GetElement(eid)
            if el is not None:
                return el.Name
        except Exception:
            pass
        return eid.IntegerValue
    return u''


def _tb_type_name(tb):
    """Return 'FamilyName : TypeName' for a title block instance."""
    try:
        tb_type = doc.GetElement(tb.GetTypeId())
        fam = tb_type.FamilyName
        typ = tb_type.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()
        if not typ:
            typ = tb_type.Name
        return u'{} : {}'.format(fam, typ)
    except Exception:
        return u'(unknown title block)'


def _sheet_revision(sheet):
    """Return the sheet's current revision string, or '-' if none."""
    try:
        p = sheet.get_Parameter(BuiltInParameter.SHEET_CURRENT_REVISION)
        if p is not None:
            v = p.AsString()
            if v:
                return v
    except Exception:
        pass
    return u'-'


# ── 1. Collect sheets ─────────────────────────────────────────────────────────
all_sheets = sorted(
    list(FilteredElementCollector(doc).OfClass(ViewSheet).ToElements()),
    key=lambda s: s.SheetNumber
)
if not all_sheets:
    forms.alert('No sheets found in the project.', exitscript=True)


# ── 2. Map title blocks to sheets (needed for the picker labels) ──────────────
tb_by_sheet = {}
for tb in (FilteredElementCollector(doc)
           .OfCategory(BuiltInCategory.OST_TitleBlocks)
           .WhereElementIsNotElementType()
           .ToElements()):
    tb_by_sheet[tb.OwnerViewId.IntegerValue] = tb


# ── 3. Sheet picker — table view (checkbox + sortable columns) ────────────────
class SheetRow(object):
    """One row in the selector DataGrid. WPF binds to these attributes."""
    def __init__(self, sheet):
        self.sheet      = sheet
        self.IsChecked  = False
        self.Number     = sheet.SheetNumber
        self.Name       = sheet.Name
        self.Rev        = _sheet_revision(sheet)
        tb = tb_by_sheet.get(sheet.Id.IntegerValue)
        self.TitleBlock = _tb_type_name(tb) if tb is not None else u'(no title block)'


class SheetSelectorWindow(forms.WPFWindow):
    def __init__(self, xaml_path, rows):
        forms.WPFWindow.__init__(self, xaml_path)
        self._all_rows = rows
        self.selected  = None
        self.grid.ItemsSource = rows
        self._update_count()

    def _visible_rows(self):
        # grid.ItemsSource may be a filtered subset after a search
        return list(self.grid.ItemsSource) if self.grid.ItemsSource else []

    def _update_count(self):
        checked = sum(1 for r in self._all_rows if r.IsChecked)
        self.count_tb.Text = '{} of {} selected'.format(checked, len(self._all_rows))

    def _refresh(self, rows):
        self.grid.ItemsSource = None
        self.grid.ItemsSource = rows
        self._update_count()

    def search_changed(self, sender, args):
        if not hasattr(self, '_all_rows'):
            return   # ignore TextChanged raised during XAML load
        q = (self.search_tb.Text or '').strip().lower()
        if not q:
            self._refresh(self._all_rows)
            return
        filtered = [r for r in self._all_rows
                    if q in (r.Number or u'').lower()
                    or q in (r.Name or u'').lower()
                    or q in (r.TitleBlock or u'').lower()]
        self._refresh(filtered)

    def select_all(self, sender, args):
        for r in self._visible_rows():
            r.IsChecked = True
        self._refresh(self._visible_rows())

    def clear_all(self, sender, args):
        for r in self._visible_rows():
            r.IsChecked = False
        self._refresh(self._visible_rows())

    def confirm(self, sender, args):
        self.selected = [r.sheet for r in self._all_rows if r.IsChecked]
        self.Close()


_XAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'SheetSelector.xaml')
rows = [SheetRow(s) for s in all_sheets]

win = SheetSelectorWindow(_XAML, rows)
win.ShowDialog()

selected_sheets = win.selected
if not selected_sheets:
    script.exit()

sheets_with_tb = [s for s in selected_sheets if s.Id.IntegerValue in tb_by_sheet]
if not sheets_with_tb:
    forms.alert('None of the selected sheets have a title block.', exitscript=True)


# ── 4. Auto-detect parameters — UNION across all selected title blocks ────────
# Different title block families expose different parameters, so we gather the
# full set from every selected sheet's title block, not just the first one.
seen, param_names = set(), []
with forms.ProgressBar(title='Detecting parameters...', cancellable=False) as pb:
    for i, sheet in enumerate(sheets_with_tb):
        pb.update_progress(i, len(sheets_with_tb))
        tb = tb_by_sheet.get(sheet.Id.IntegerValue)
        if tb is None:
            continue
        for p in tb.GetOrderedParameters():
            n = p.Definition.Name
            if n.startswith('<') or n in seen:
                continue
            seen.add(n)
            param_names.append(n)
param_names.sort()

if not param_names:
    forms.alert('No usable parameters found on the title blocks.', exitscript=True)


# ── 5. Parameter picker ───────────────────────────────────────────────────────
chosen_params = forms.SelectFromList.show(
    param_names,
    title='Select Parameters to Export as Columns',
    multiselect=True,
    button_name='Next: Choose Save Location',
)
if not chosen_params:
    script.exit()


# ── 6. Save path ──────────────────────────────────────────────────────────────
save_path = forms.save_file(file_ext='xlsx', default_name='TitleBlockExport')
if not save_path:
    script.exit()

if os.path.exists(save_path):
    if not forms.alert('File already exists. Overwrite?\n{}'.format(save_path),
                       yes=True, no=True):
        script.exit()


# ── 7. Build Excel ────────────────────────────────────────────────────────────
wb = XlsxWriter()
ws = wb.add_sheet(u'Sheet Parameters')

headers = [u'Sheet Number', u'Sheet Name'] + list(chosen_params)
for col, h in enumerate(headers):
    ws.write(0, col, h, STYLE_HEADER)

with forms.ProgressBar(title='Exporting sheets...', cancellable=True) as pb:
    for row_idx, sheet in enumerate(sheets_with_tb, 1):
        if pb.cancelled:
            forms.alert('Export cancelled.')
            script.exit()
        pb.update_progress(row_idx - 1, len(sheets_with_tb))
        tb = tb_by_sheet.get(sheet.Id.IntegerValue)
        ws.write(row_idx, 0, sheet.SheetNumber, STYLE_LOCKED)
        ws.write(row_idx, 1, sheet.Name,        STYLE_LOCKED)
        for col, pname in enumerate(chosen_params, 2):
            val = _read_param(tb.LookupParameter(pname)) if tb else u''
            ws.write(row_idx, col, val, STYLE_EDITABLE)

ws.freeze(1, 2)

# Auto-fit column widths (scan all written values)
col_max = [len(h) for h in headers]
for sheet in sheets_with_tb:
    tb = tb_by_sheet.get(sheet.Id.IntegerValue)
    row_vals = [sheet.SheetNumber, sheet.Name] + [
        unicode(_read_param(tb.LookupParameter(p))) if tb else u''
        for p in chosen_params
    ]
    for c, v in enumerate(row_vals):
        col_max[c] = max(col_max[c], len(unicode(v)))

for c, w in enumerate(col_max):
    ws.set_col_width(c, w + 4)

# README sheet
ws2 = wb.add_sheet(u'README')
readme_lines = [
    u'TitleBlockManager — Export Instructions',
    u'',
    u'EDITING RULES:',
    u'  Columns "Sheet Number" and "Sheet Name" are reference keys — DO NOT edit them.',
    u'  They are grey. The importer uses Sheet Number to find the Revit sheet.',
    u'',
    u'  All green columns are editable. Change any value and save the file.',
    u'',
    u'  Yes/No parameters: write  True  or  False  (also accepts Yes/No/1/0).',
    u'',
    u'  Leave a cell blank to skip it during import.',
    u'  A blank cell will NOT clear the existing value in Revit.',
]
for r, line in enumerate(readme_lines):
    ws2.write(r, 0, line)

wb.save(save_path)


# ── 8. Report ─────────────────────────────────────────────────────────────────
skipped = len(selected_sheets) - len(sheets_with_tb)
output.print_md('## Export Complete')
output.print_md('**File:** {}'.format(save_path))
output.print_md('**Sheets exported:** {} | **Columns:** {}'.format(
    len(sheets_with_tb), len(chosen_params)))
if skipped:
    output.print_md('**Skipped (no title block):** {}'.format(skipped))

os.startfile(os.path.dirname(save_path))
