# -*- coding: utf-8 -*-
"""
Title Block Parameter Editor.

Extract title block parameters into editable grids and write changes back to the
model — no Excel round-trip.

  * Sheets grid  — rows = sheets, columns = INSTANCE parameters
  * Types grid   — rows = unique title block types, columns = TYPE parameters
                   (editing a type value affects every sheet using that type)

Number (Double) columns are shown/edited in project display units and converted
back to Revit internal units on write. Batch commit with a diff/preview.

Author : TitleBlockManager
Revit  : 2024 / 2025 / 2026
PyRevit: 4.8+
"""
__title__ = 'Edit\nParameters'
__doc__   = 'Extract and edit title block parameters live inside Revit.'

import os
import clr

clr.AddReference('System.Data')
from System.Data import DataTable
from System import Boolean

try:
    clr.AddReference('PresentationFramework')
    clr.AddReference('PresentationCore')
except Exception:
    pass
from System.Windows.Markup import XamlReader
from System.Windows.Controls import DataGridTemplateColumn

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewSheet,
    BuiltInCategory,
    BuiltInParameter,
    StorageType,
    Transaction,
    ElementId,
    UnitUtils,
)

try:
    from Autodesk.Revit.DB import SpecTypeId
    HAS_FORGE = True
except Exception:
    HAS_FORGE = False

try:
    from Autodesk.Revit.DB import LabelUtils
    HAS_LABELUTILS = True
except Exception:
    HAS_LABELUTILS = False

from pyrevit import forms, script

doc    = __revit__.ActiveUIDocument.Document
output = script.get_output()


# ══════════════════════════════════════════════════════════════════════════════
# Parameter helpers (unit-aware for Double)
# ══════════════════════════════════════════════════════════════════════════════
def _is_yesno(p):
    if not HAS_FORGE:
        return False
    try:
        return p.Definition.GetDataType() == SpecTypeId.Boolean.YesNo
    except Exception:
        return False


def _unit_id(p):
    """Display UnitTypeId (ForgeTypeId) for a Double param, or None."""
    try:
        return p.GetUnitTypeId()
    except Exception:
        return None


def _unit_label(p):
    """Human unit label for a Double param header, e.g. 'mm', or ''."""
    uid = _unit_id(p)
    if uid is None or not HAS_LABELUTILS:
        return u''
    try:
        return LabelUtils.GetLabelForUnit(uid)
    except Exception:
        return u''


def _read_str(p):
    """Current value of a parameter as a display string (Double in display units)."""
    if p is None:
        return u''
    st = p.StorageType
    if st == StorageType.String:
        v = p.AsString()
        return v if v is not None else u''
    if st == StorageType.Integer:
        return (u'True' if p.AsInteger() == 1 else u'False') if _is_yesno(p) else unicode(p.AsInteger())
    if st == StorageType.Double:
        d = p.AsDouble()
        uid = _unit_id(p)
        if uid is not None:
            try:
                d = UnitUtils.ConvertFromInternalUnits(d, uid)
            except Exception:
                pass
        # trim trailing zeros for readability
        return unicode(round(d, 6))
    if st == StorageType.ElementId:
        eid = p.AsElementId()
        try:
            el = doc.GetElement(eid)
            if el is not None:
                return el.Name
        except Exception:
            pass
        return unicode(eid.IntegerValue)
    return u''


def _parse(raw, p):
    """Convert a display string to the value p.Set() expects (Double: display→internal).
    Returns (value, error_or_None). value None means 'skip / blank'."""
    if raw is None or unicode(raw).strip() == u'':
        return (None, None)
    st = p.StorageType
    if st == StorageType.String:
        return (unicode(raw), None)
    if st == StorageType.Integer:
        if _is_yesno(p):
            s = unicode(raw).strip().lower()
            if s in (u'true', u'yes', u'1', u'on'):
                return (1, None)
            if s in (u'false', u'no', u'0', u'off'):
                return (0, None)
            return (None, u'Expected True/False/Yes/No/On/Off/1/0')
        try:
            return (int(raw), None)
        except (ValueError, TypeError):
            return (None, u'Not an integer')
    if st == StorageType.Double:
        try:
            f = float(raw)
        except (ValueError, TypeError):
            return (None, u'Not a number')
        uid = _unit_id(p)
        if uid is not None:
            try:
                f = UnitUtils.ConvertToInternalUnits(f, uid)
            except Exception:
                pass
        return (f, None)
    if st == StorageType.ElementId:
        try:
            return (ElementId(int(raw)), None)
        except (ValueError, TypeError):
            return (None, u'Not an ElementId')
    return (None, u'Unsupported type')


def _tb_type_name(tb_type):
    try:
        typ = tb_type.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString() or tb_type.Name
        return u'{} : {}'.format(tb_type.FamilyName, typ)
    except Exception:
        return u'(unknown)'


def _sheet_rev(sheet):
    try:
        p = sheet.get_Parameter(BuiltInParameter.SHEET_CURRENT_REVISION)
        if p is not None:
            v = p.AsString()
            if v:
                return v
    except Exception:
        pass
    return u'-'


def _xml_escape(s):
    return (s.replace(u'&', u'&amp;').replace(u'<', u'&lt;')
             .replace(u'>', u'&gt;').replace(u'"', u'&quot;'))


def _toggle_column(header, colname):
    """A DataGrid column that shows a Yes/No value as an ON/OFF toggle button."""
    safe = _xml_escape(colname)
    xaml = (
        u'<DataTemplate '
        u'xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" '
        u'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">'
        u'<ToggleButton Width="56" Height="20" Cursor="Hand" HorizontalAlignment="Center" '
        u'IsChecked="{{Binding Path=[{col}], Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}}">'
        u'<ToggleButton.Style><Style TargetType="ToggleButton">'
        u'<Setter Property="Content" Value="OFF"/>'
        u'<Setter Property="Background" Value="#B7B7B7"/>'
        u'<Setter Property="Foreground" Value="White"/>'
        u'<Setter Property="FontWeight" Value="Bold"/>'
        u'<Setter Property="FontSize" Value="10"/>'
        u'<Setter Property="BorderThickness" Value="0"/>'
        u'<Style.Triggers><Trigger Property="IsChecked" Value="True">'
        u'<Setter Property="Content" Value="ON"/>'
        u'<Setter Property="Background" Value="#3C8E3C"/>'
        u'</Trigger></Style.Triggers></Style></ToggleButton.Style>'
        u'</ToggleButton></DataTemplate>'
    ).format(col=safe)
    col = DataGridTemplateColumn()
    col.Header = header
    col.CellTemplate = XamlReader.Parse(xaml)
    col.CanUserSort = True
    col.SortMemberPath = colname
    return col


# ══════════════════════════════════════════════════════════════════════════════
# 1. Collect sheets + title blocks
# ══════════════════════════════════════════════════════════════════════════════
all_sheets = sorted(
    list(FilteredElementCollector(doc).OfClass(ViewSheet).ToElements()),
    key=lambda s: s.SheetNumber
)
if not all_sheets:
    forms.alert('No sheets found in the project.', exitscript=True)

tb_by_sheet = {}
for tb in (FilteredElementCollector(doc)
           .OfCategory(BuiltInCategory.OST_TitleBlocks)
           .WhereElementIsNotElementType()
           .ToElements()):
    tb_by_sheet[tb.OwnerViewId.IntegerValue] = tb


# ══════════════════════════════════════════════════════════════════════════════
# 2. Sheet selection — table view (checkbox + sortable columns + search)
# ══════════════════════════════════════════════════════════════════════════════
class SheetRow(object):
    def __init__(self, sheet):
        self.sheet     = sheet
        self.IsChecked = False
        self.Number    = sheet.SheetNumber
        self.Name      = sheet.Name
        self.Rev       = _sheet_rev(sheet)
        tb = tb_by_sheet.get(sheet.Id.IntegerValue)
        self.TitleBlock = (_tb_type_name(doc.GetElement(tb.GetTypeId()))
                           if tb is not None else u'(no title block)')


class SheetSelectorWindow(forms.WPFWindow):
    def __init__(self, xaml_path, rows):
        forms.WPFWindow.__init__(self, xaml_path)
        self._all_rows = rows
        self.selected  = None
        self.grid.ItemsSource = rows
        self._update_count()

    def _visible_rows(self):
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
            return
        q = (self.search_tb.Text or '').strip().lower()
        if not q:
            self._refresh(self._all_rows)
            return
        self._refresh([r for r in self._all_rows
                       if q in (r.Number or u'').lower()
                       or q in (r.Name or u'').lower()
                       or q in (r.TitleBlock or u'').lower()])

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


_SEL_XAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'SheetSelector.xaml')
sel_win = SheetSelectorWindow(_SEL_XAML, [SheetRow(s) for s in all_sheets])
sel_win.ShowDialog()

sel_sheets = sel_win.selected
if not sel_sheets:
    script.exit()

sel_sheets = [s for s in sel_sheets if s.Id.IntegerValue in tb_by_sheet]
if not sel_sheets:
    forms.alert('None of the selected sheets have a title block.', exitscript=True)


# ══════════════════════════════════════════════════════════════════════════════
# 3. Unique title block types among selected sheets
# ══════════════════════════════════════════════════════════════════════════════
type_by_id = {}          # type_id int -> type element
type_ids_ordered = []    # keep stable order
for s in sel_sheets:
    tb = tb_by_sheet[s.Id.IntegerValue]
    tid = tb.GetTypeId()
    if tid.IntegerValue not in type_by_id:
        type_by_id[tid.IntegerValue] = doc.GetElement(tid)
        type_ids_ordered.append(tid.IntegerValue)


# ══════════════════════════════════════════════════════════════════════════════
# 4. Detect parameters (union). Instance from instances, Type from the types.
# ══════════════════════════════════════════════════════════════════════════════
inst_names, type_names = set(), set()
for s in sel_sheets:
    tb = tb_by_sheet[s.Id.IntegerValue]
    for p in tb.GetOrderedParameters():
        n = p.Definition.Name
        if not n.startswith('<'):
            inst_names.add(n)
for tid in type_ids_ordered:
    te = type_by_id[tid]
    if te is None:
        continue
    for p in te.GetOrderedParameters():
        n = p.Definition.Name
        if not n.startswith('<'):
            type_names.add(n)

INST_TAG = u'[Instance] '
TYPE_TAG = u'[Type] '
opt_to_meta = {}   # option -> (pname, is_type)
options = []
for n in sorted(inst_names):
    disp = INST_TAG + n
    options.append(disp)
    opt_to_meta[disp] = (n, False)
for n in sorted(type_names):
    disp = TYPE_TAG + n
    options.append(disp)
    opt_to_meta[disp] = (n, True)

chosen_opts = forms.SelectFromList.show(
    options,
    title='Select Parameters to Edit',
    multiselect=True,
    button_name='Open Editor',
)
if not chosen_opts:
    script.exit()

inst_cols = [opt_to_meta[o][0] for o in chosen_opts if not opt_to_meta[o][1]]
type_cols = [opt_to_meta[o][0] for o in chosen_opts if opt_to_meta[o][1]]


# ══════════════════════════════════════════════════════════════════════════════
# 5. Column metadata + header labels (with units)
# ══════════════════════════════════════════════════════════════════════════════
header_map = {}   # DataTable column name -> display header (adds unit)

def _sample_inst_param(pname):
    for s in sel_sheets:
        tb = tb_by_sheet[s.Id.IntegerValue]
        p = tb.LookupParameter(pname)
        if p is not None:
            return p
    return None

def _sample_type_param(pname):
    for tid in type_ids_ordered:
        te = type_by_id[tid]
        if te is None:
            continue
        p = te.LookupParameter(pname)
        if p is not None:
            return p
    return None

inst_yesno = set()   # instance column names that are Yes/No parameters
type_yesno = set()   # type column names that are Yes/No parameters

for pname in inst_cols:
    sp = _sample_inst_param(pname)
    if sp is not None and _is_yesno(sp):
        inst_yesno.add(pname)
        header_map[pname] = pname   # no unit on Yes/No
        continue
    lbl = _unit_label(sp) if sp is not None else u''
    header_map[pname] = u'{} ({})'.format(pname, lbl) if lbl else pname

for pname in type_cols:
    sp = _sample_type_param(pname)
    if sp is not None and _is_yesno(sp):
        type_yesno.add(pname)
        header_map[pname] = pname
        continue
    lbl = _unit_label(sp) if sp is not None else u''
    header_map[pname] = u'{} ({})'.format(pname, lbl) if lbl else pname

yesno_all = inst_yesno | type_yesno


# ══════════════════════════════════════════════════════════════════════════════
# 6. Build the two DataTables + original snapshots
# ══════════════════════════════════════════════════════════════════════════════
# --- Sheets / instance table ---
sheet_ctx = []   # index -> {sheet_id, tb_id}
inst_dt = DataTable('sheets')
inst_dt.Columns.Add('_rowkey')
inst_dt.Columns.Add('Sheet Number')
inst_dt.Columns.Add('Sheet Name')
for c in inst_cols:
    if c in inst_yesno:
        inst_dt.Columns.Add(c, Boolean)
    else:
        inst_dt.Columns.Add(c)

orig_inst = {}   # (rowkey, col) -> str ("True"/"False" for Yes/No)
for i, s in enumerate(sel_sheets):
    tb = tb_by_sheet[s.Id.IntegerValue]
    sheet_ctx.append({'sheet_id': s.Id, 'tb_id': tb.Id})
    dr = inst_dt.NewRow()
    key = unicode(i)
    dr['_rowkey']      = key
    dr['Sheet Number'] = s.SheetNumber
    dr['Sheet Name']   = s.Name
    for c in inst_cols:
        if c in inst_yesno:
            b = (_read_str(tb.LookupParameter(c)) == u'True')
            dr[c] = b
            orig_inst[(key, c)] = unicode(b)
        else:
            val = _read_str(tb.LookupParameter(c))
            dr[c] = val
            orig_inst[(key, c)] = val
    inst_dt.Rows.Add(dr)

# --- Types / type table ---
type_ctx = []   # index -> {type_id}
type_dt = DataTable('types')
type_dt.Columns.Add('_typekey')
type_dt.Columns.Add('Title Block')
for c in type_cols:
    if c in type_yesno:
        type_dt.Columns.Add(c, Boolean)
    else:
        type_dt.Columns.Add(c)

orig_type = {}   # (typekey, col) -> str
for i, tid in enumerate(type_ids_ordered):
    te = type_by_id[tid]
    type_ctx.append({'type_id': ElementId(tid)})
    dr = type_dt.NewRow()
    key = unicode(i)
    dr['_typekey']    = key
    dr['Title Block'] = _tb_type_name(te) if te is not None else u'(unknown)'
    for c in type_cols:
        if c in type_yesno:
            b = (_read_str(te.LookupParameter(c)) == u'True') if te is not None else False
            dr[c] = b
            orig_type[(key, c)] = unicode(b)
        else:
            val = _read_str(te.LookupParameter(c)) if te is not None else u''
            dr[c] = val
            orig_type[(key, c)] = val
    type_dt.Rows.Add(dr)


# ══════════════════════════════════════════════════════════════════════════════
# 7. Editor window
# ══════════════════════════════════════════════════════════════════════════════
class EditorWindow(forms.WPFWindow):
    def __init__(self, xaml_path, inst_table, type_table, inst_editable):
        forms.WPFWindow.__init__(self, xaml_path)
        self._inst_table = inst_table
        self._type_table = type_table
        self.applied = False

        self.grid.ItemsSource = inst_table.DefaultView
        self.type_grid.ItemsSource = type_table.DefaultView

        for c in inst_editable:
            self.col_combo.Items.Add(c)
        if inst_editable:
            self.col_combo.SelectedIndex = 0

        self._set_status('{} sheet(s), {} title block type(s) loaded'.format(
            inst_table.Rows.Count, type_table.Rows.Count))

    def _set_status(self, msg):
        self.status_tb.Text = msg

    def on_auto_col(self, sender, e):
        header = e.Column.Header
        if header in ('_rowkey', '_typekey'):
            e.Cancel = True
            return
        if header in ('Sheet Number', 'Sheet Name', 'Title Block'):
            e.Column.IsReadOnly = True
            return
        # Yes/No parameters → ON/OFF toggle button column
        if header in yesno_all:
            e.Column = _toggle_column(header_map.get(header, header), header)
            return
        # Apply friendly header (with unit) for parameter columns
        if header in header_map:
            e.Column.Header = header_map[header]

    def _commit_pending(self):
        for g in (self.grid, self.type_grid):
            try:
                g.CommitEdit()
                g.CommitEdit()
            except Exception:
                pass

    def _fill_value_for(self, col):
        """Convert the bulk-fill text to the right type for the column."""
        txt = self.fill_value.Text
        if col in inst_yesno:
            return (txt or u'').strip().lower() in ('true', 'yes', '1', 'on')
        return txt

    def fill_selected(self, sender, args):
        self._commit_pending()
        col = self.col_combo.SelectedItem
        if not col:
            return
        val = self._fill_value_for(col)
        rows = list(self.grid.SelectedItems)
        if not rows:
            self._set_status('No rows selected — click row headers to select first.')
            return
        for rv in rows:
            rv.Row[col] = val
        self._set_status('Filled "{}" into {} selected row(s).'.format(col, len(rows)))

    def fill_all(self, sender, args):
        self._commit_pending()
        col = self.col_combo.SelectedItem
        if not col:
            return
        val = self._fill_value_for(col)
        for dr in self._inst_table.Rows:
            dr[col] = val
        self._set_status('Filled "{}" into all {} row(s).'.format(col, self._inst_table.Rows.Count))

    def apply(self, sender, args):
        self._commit_pending()
        self.applied = True
        self.Close()

    def cancel_win(self, sender, args):
        self.applied = False
        self.Close()


_XAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'EditorWindow.xaml')
win = EditorWindow(_XAML, inst_dt, type_dt, inst_cols)
win.ShowDialog()

if not win.applied:
    script.exit()


# ══════════════════════════════════════════════════════════════════════════════
# 8. Compute diffs from both tables
# ══════════════════════════════════════════════════════════════════════════════
# Instance changes
inst_changes = []   # {ctx, pname, old, new, sheet_num}
for dr in inst_dt.Rows:
    key = unicode(dr['_rowkey'])
    ctx = sheet_ctx[int(key)]
    for c in inst_cols:
        new_val = unicode(dr[c]) if dr[c] is not None else u''
        old_val = orig_inst.get((key, c), u'')
        if new_val != old_val:
            inst_changes.append({
                'ctx': ctx, 'pname': c, 'old': old_val, 'new': new_val,
                'sheet_num': unicode(dr['Sheet Number']),
            })

# Type changes
type_changes = []   # {ctx, pname, old, new, tbname}
for dr in type_dt.Rows:
    key = unicode(dr['_typekey'])
    ctx = type_ctx[int(key)]
    for c in type_cols:
        new_val = unicode(dr[c]) if dr[c] is not None else u''
        old_val = orig_type.get((key, c), u'')
        if new_val != old_val:
            type_changes.append({
                'ctx': ctx, 'pname': c, 'old': old_val, 'new': new_val,
                'tbname': unicode(dr['Title Block']),
            })

if not inst_changes and not type_changes:
    forms.alert('No changes were made.', exitscript=True)


# ══════════════════════════════════════════════════════════════════════════════
# 9. Diff preview + confirm
# ══════════════════════════════════════════════════════════════════════════════
def _disp(val, pname, yesno_set):
    """Show Yes/No values as ON/OFF in the diff table."""
    if pname in yesno_set:
        return u'ON' if unicode(val) == u'True' else u'OFF'
    return val

diff_rows = []
for ch in inst_changes:
    diff_rows.append([ch['sheet_num'], 'Instance', ch['pname'],
                      _disp(ch['old'], ch['pname'], inst_yesno),
                      _disp(ch['new'], ch['pname'], inst_yesno)])
for ch in type_changes:
    diff_rows.append([ch['tbname'], 'Type', ch['pname'],
                      _disp(ch['old'], ch['pname'], type_yesno),
                      _disp(ch['new'], ch['pname'], type_yesno)])

output.print_md('## Preview — {} change(s)'.format(len(diff_rows)))
output.print_md('_Type rows apply to every sheet using that title block type._')
output.print_table(
    diff_rows,
    columns=['Target (Sheet / Type)', 'Scope', 'Parameter', 'Current', 'New']
)

total = len(inst_changes) + len(type_changes)
if not forms.alert('Apply {} change(s) to the model?\n'
                   '({} instance, {} type)'.format(
                       total, len(inst_changes), len(type_changes)),
                   yes=True, no=True):
    output.print_md('**Cancelled — no changes written.**')
    script.exit()


# ══════════════════════════════════════════════════════════════════════════════
# 10. Write in ONE transaction
# ══════════════════════════════════════════════════════════════════════════════
written = 0
failed  = []   # (target, param, error)

t = Transaction(doc, 'Edit Title Block Parameters')
t.Start()
try:
    ops = [('inst', ch) for ch in inst_changes] + [('type', ch) for ch in type_changes]
    with forms.ProgressBar(title='Writing...', cancellable=True) as pb:
        for i, (kind, ch) in enumerate(ops):
            if pb.cancelled:
                t.RollbackToSavepoint()
                forms.alert('Cancelled. No changes written.', exitscript=True)
            pb.update_progress(i, len(ops))

            if kind == 'inst':
                holder = doc.GetElement(ch['ctx']['tb_id'])
                target = ch['sheet_num']
            else:
                holder = doc.GetElement(ch['ctx']['type_id'])
                target = ch['tbname']

            try:
                p = holder.LookupParameter(ch['pname'])
                if p is None or p.IsReadOnly:
                    failed.append((target, ch['pname'], 'read-only or missing'))
                    continue
                val, err = _parse(ch['new'], p)
                if err:
                    failed.append((target, ch['pname'], err))
                    continue
                if val is None:
                    continue
                p.Set(val)
                written += 1
            except Exception as e:
                failed.append((target, ch['pname'], unicode(e)))
    t.Commit()
except Exception as e:
    if t.HasStarted() and not t.HasEnded():
        t.RollbackToSavepoint()
    forms.alert('Transaction failed — no changes written.\n\n{}'.format(e), exitscript=True)


# ══════════════════════════════════════════════════════════════════════════════
# 11. Report
# ══════════════════════════════════════════════════════════════════════════════
output.print_md('---')
output.print_md('## Edit Report')
output.print_md('**{} value(s) written.** {} failure(s).'.format(written, len(failed)))

if failed:
    output.print_md('### Write Failures')
    output.print_table([list(r) for r in failed],
                       columns=['Target', 'Parameter', 'Error'])
