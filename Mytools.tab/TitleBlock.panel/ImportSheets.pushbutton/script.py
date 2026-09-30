# -*- coding: utf-8 -*-
"""
Import title block parameters from Excel and write them back to Revit.
Shows a diff/preview before making any changes.

Author : TitleBlockManager
Revit  : 2024 / 2025 / 2026
PyRevit: 4.8+
"""
__title__ = 'Import\nSheets'
__doc__   = 'Read an exported Excel file and update title block parameters in the model.'

import os
import sys

# ── add shared lib to path ────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB  = os.path.join(_HERE, 'lib')
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from xlsxlite import XlsxReader

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewSheet,
    BuiltInCategory,
    StorageType,
    Transaction,
    ElementId,
)

try:
    from Autodesk.Revit.DB import SpecTypeId
    HAS_FORGE = True
except Exception:
    HAS_FORGE = False

from pyrevit import forms, script

doc    = __revit__.ActiveUIDocument.Document
output = script.get_output()


# ── helpers ───────────────────────────────────────────────────────────────────
def _is_yesno(p):
    if not HAS_FORGE:
        return False
    try:
        return p.Definition.GetDataType() == SpecTypeId.Boolean.YesNo
    except Exception:
        return False


def _current_value(p):
    """Read current Revit value as a display string (for diff table)."""
    if p is None:
        return u''
    st = p.StorageType
    if st == StorageType.String:
        v = p.AsString()
        return v if v is not None else u''
    if st == StorageType.Integer:
        return (u'True' if p.AsInteger() == 1 else u'False') if _is_yesno(p) else unicode(p.AsInteger())
    if st == StorageType.Double:
        return unicode(p.AsDouble())
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


def _parse_value(raw, p):
    """Convert raw Excel cell value to the type required by p.Set().
    Returns (converted_value, error_string_or_None).
    converted_value is None when the cell should be skipped (blank or error).
    """
    if raw is None or unicode(raw).strip() == u'':
        return (None, None)   # blank → skip silently

    st = p.StorageType

    if st == StorageType.String:
        return (unicode(raw), None)

    if st == StorageType.Integer:
        if _is_yesno(p):
            s = unicode(raw).strip().lower()
            if s in (u'true', u'yes', u'1'):
                return (1, None)
            if s in (u'false', u'no', u'0'):
                return (0, None)
            return (None, u'Expected True/False/Yes/No/1/0, got "{}"'.format(raw))
        try:
            return (int(raw), None)
        except (ValueError, TypeError):
            return (None, u'Cannot convert "{}" to integer'.format(raw))

    if st == StorageType.Double:
        try:
            return (float(raw), None)
        except (ValueError, TypeError):
            return (None, u'Cannot convert "{}" to number'.format(raw))

    if st == StorageType.ElementId:
        try:
            return (ElementId(int(raw)), None)
        except (ValueError, TypeError):
            return (None, u'Cannot convert "{}" to ElementId'.format(raw))

    return (None, u'Unsupported StorageType')


# ════════════════════════════════════════════════════════════════════════════
# STEP 1 — Pick file
# ════════════════════════════════════════════════════════════════════════════
xl_path = forms.pick_file(file_ext='xlsx')
if not xl_path:
    script.exit()


# ════════════════════════════════════════════════════════════════════════════
# STEP 2 — Load workbook
# ════════════════════════════════════════════════════════════════════════════
try:
    wb = XlsxReader(xl_path)
except Exception as e:
    forms.alert('Could not open file:\n{}'.format(e), exitscript=True)

if u'Sheet Parameters' not in wb.sheet_names:
    forms.alert(
        'This file does not contain a "Sheet Parameters" sheet.\n'
        'Make sure you are opening a file exported by TitleBlockManager.',
        exitscript=True
    )

rows = wb.sheet(u'Sheet Parameters')
if not rows:
    forms.alert('The "Sheet Parameters" sheet is empty.', exitscript=True)


# ════════════════════════════════════════════════════════════════════════════
# STEP 3 — Parse header row
# ════════════════════════════════════════════════════════════════════════════
header_row = [unicode(v) if v is not None else u'' for v in rows[0]]

if len(header_row) < 3:
    forms.alert(
        'Expected at least 3 columns (Sheet Number, Sheet Name, + parameters).',
        exitscript=True
    )
if header_row[0] != u'Sheet Number' or header_row[1] != u'Sheet Name':
    forms.alert(
        'Columns 1-2 must be "Sheet Number" and "Sheet Name".\n'
        'This file may be from a different tool or has been modified.',
        exitscript=True
    )

param_columns = header_row[2:]


# ════════════════════════════════════════════════════════════════════════════
# STEP 4 — Read data rows
# ════════════════════════════════════════════════════════════════════════════
data_rows = []
for row in rows[1:]:
    if all(v is None for v in row):
        continue
    sheet_num = unicode(row[0]).strip() if row[0] is not None else u''
    if not sheet_num:
        continue
    row_data = {}
    for i, pname in enumerate(param_columns):
        cell_idx = 2 + i
        row_data[pname] = row[cell_idx] if cell_idx < len(row) else None
    data_rows.append((sheet_num, row_data))

if not data_rows:
    forms.alert('No data rows found in the file.', exitscript=True)


# ════════════════════════════════════════════════════════════════════════════
# STEP 5 — Build Revit lookups
# ════════════════════════════════════════════════════════════════════════════
sheet_by_number = {}
for s in FilteredElementCollector(doc).OfClass(ViewSheet).ToElements():
    sheet_by_number[s.SheetNumber] = s

tb_by_sheet_id = {}
for tb in (FilteredElementCollector(doc)
           .OfCategory(BuiltInCategory.OST_TitleBlocks)
           .WhereElementIsNotElementType()
           .ToElements()):
    tb_by_sheet_id[tb.OwnerViewId.IntegerValue] = tb


# ════════════════════════════════════════════════════════════════════════════
# STEP 6 — Pre-validate (no writes yet)
# ════════════════════════════════════════════════════════════════════════════
# validated_ops: list of (sheet_num, param_name, tb_element, converted_value)
validated_ops  = []
skipped_rows   = []   # (sheet_num, reason)
param_warnings = []   # (sheet_num, param_name, reason)

with forms.ProgressBar(title='Validating...', cancellable=False) as pb:
    for i, (sheet_num, row_data) in enumerate(data_rows):
        pb.update_progress(i, len(data_rows))

        sheet = sheet_by_number.get(sheet_num)
        if sheet is None:
            skipped_rows.append((sheet_num, u'Sheet not found in current Revit model'))
            continue

        tb = tb_by_sheet_id.get(sheet.Id.IntegerValue)
        if tb is None:
            skipped_rows.append((sheet_num, u'No title block found on sheet'))
            continue

        for pname, raw_val in row_data.items():
            p = tb.LookupParameter(pname)
            if p is None:
                param_warnings.append((sheet_num, pname, u'Parameter not found on this title block'))
                continue
            if p.IsReadOnly:
                param_warnings.append((sheet_num, pname, u'Parameter is read-only'))
                continue
            converted, err = _parse_value(raw_val, p)
            if err:
                param_warnings.append((sheet_num, pname, err))
                continue
            if converted is None:
                continue   # blank cell — skip silently
            validated_ops.append((sheet_num, pname, tb, converted))


# ════════════════════════════════════════════════════════════════════════════
# STEP 7 — Diff / preview
# ════════════════════════════════════════════════════════════════════════════
if not validated_ops:
    output.print_md('## No changes to apply')
    if skipped_rows:
        output.print_md('### Skipped Sheets')
        output.print_table([list(r) for r in skipped_rows],
                           columns=['Sheet Number', 'Reason'])
    if param_warnings:
        output.print_md('### Parameter Warnings')
        output.print_table([list(r) for r in param_warnings],
                           columns=['Sheet Number', 'Parameter', 'Reason'])
    forms.alert('Nothing to import — no valid changes found.', exitscript=True)

# Read current Revit values BEFORE opening any transaction
diff_rows = []
for sheet_num, pname, tb, new_val in validated_ops:
    p = tb.LookupParameter(pname)
    diff_rows.append([sheet_num, pname, _current_value(p), unicode(new_val)])

output.print_md('## Preview — {} change(s) to apply'.format(len(diff_rows)))
output.print_table(diff_rows,
                   columns=['Sheet Number', 'Parameter', 'Current Value', 'New Value'])

if skipped_rows:
    output.print_md('### Sheets that will be skipped')
    output.print_table([list(r) for r in skipped_rows],
                       columns=['Sheet Number', 'Reason'])

if param_warnings:
    output.print_md('### Parameter warnings (will be skipped)')
    output.print_table([list(r) for r in param_warnings],
                       columns=['Sheet Number', 'Parameter', 'Reason'])

proceed = forms.alert(
    'Apply {} change(s) to the model?\n\n'
    'Review the preview in the output window before confirming.'.format(len(diff_rows)),
    yes=True, no=True,
)
if not proceed:
    output.print_md('**Import cancelled — no changes written.**')
    script.exit()


# ════════════════════════════════════════════════════════════════════════════
# STEP 8 — Write inside ONE transaction
# ════════════════════════════════════════════════════════════════════════════
written_sheets = {}   # sheet_num → count written
failed_ops     = []   # (sheet_num, pname, error)

t = Transaction(doc, 'Import Title Block Parameters')
t.Start()
try:
    with forms.ProgressBar(title='Writing parameters...', cancellable=True) as pb:
        for i, (sheet_num, pname, tb, new_val) in enumerate(validated_ops):
            if pb.cancelled:
                t.RollbackToSavepoint()
                forms.alert('Import cancelled. No changes were written.', exitscript=True)
            pb.update_progress(i, len(validated_ops))
            try:
                p = tb.LookupParameter(pname)
                if p is None or p.IsReadOnly:
                    failed_ops.append((sheet_num, pname,
                                       u'Parameter became unavailable during write'))
                    continue
                p.Set(new_val)
                written_sheets[sheet_num] = written_sheets.get(sheet_num, 0) + 1
            except Exception as e:
                failed_ops.append((sheet_num, pname, unicode(e)))
    t.Commit()
except Exception as e:
    if t.HasStarted() and not t.HasEnded():
        t.RollbackToSavepoint()
    forms.alert('Transaction failed — no changes written.\n\n{}'.format(e), exitscript=True)


# ════════════════════════════════════════════════════════════════════════════
# STEP 9 — Full report
# ════════════════════════════════════════════════════════════════════════════
total_written = sum(written_sheets.values())
total_sheets  = len(written_sheets)

output.print_md('---')
output.print_md('## Import Report')

if written_sheets:
    output.print_md('### Updated Sheets ({} sheet(s), {} value(s))'.format(
        total_sheets, total_written))
    output.print_table(
        [[sn, unicode(cnt)] for sn, cnt in sorted(written_sheets.items())],
        columns=['Sheet Number', 'Parameters Updated']
    )

if skipped_rows:
    output.print_md('### Skipped Sheets')
    output.print_table([list(r) for r in skipped_rows],
                       columns=['Sheet Number', 'Reason'])

if param_warnings:
    output.print_md('### Parameter Warnings')
    output.print_table([list(r) for r in param_warnings],
                       columns=['Sheet Number', 'Parameter', 'Reason'])

if failed_ops:
    output.print_md('### Write Failures')
    output.print_table([list(r) for r in failed_ops],
                       columns=['Sheet Number', 'Parameter', 'Error'])

output.print_md(
    '**{} value(s) written across {} sheet(s). '
    '{} sheet(s) skipped. {} warning(s).**'.format(
        total_written, total_sheets, len(skipped_rows), len(param_warnings)
    )
)
