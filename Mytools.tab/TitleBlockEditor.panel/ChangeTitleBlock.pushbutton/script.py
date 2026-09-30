# -*- coding: utf-8 -*-
"""
Sheet Title Block Manager
=========================

Opens a table of sheets. Each row shows the sheet number, the sheet name and a
dropdown with the title block type currently placed on that sheet. Change any
dropdown, click Apply, and every change is written to the model as ONE undo
step.

What it does NOT do (on purpose, because these are riskier operations):
  * it never PLACES a title block on a sheet that has none
  * it never touches a sheet whose dropdown you did not change
  * it never touches a sheet that has more than one title block on it

Everything is read-only until you press Apply.

Author : Mytools
Revit  : 2024 / 2025 / 2026
PyRevit: 4.8+
IronPython 2.7 -- no f-strings, no type hints, use "{}".format(x)
"""

__title__ = 'Change\nTitle Block'
__doc__ = 'Swap the title block type on one, many, or all sheets in one undo step.'

import os

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    ElementId,
    FamilyInstance,
    FilteredElementCollector,
    Transaction,
    ViewSheet,
)

from pyrevit import forms, script


# =============================================================================
# 0. Guard rails -- bail out cleanly BEFORE doing any real work
# =============================================================================
uidoc = getattr(__revit__, 'ActiveUIDocument', None)
if uidoc is None or uidoc.Document is None:
    forms.alert('No active document. Open a project and try again.',
                title='Title Block Manager', exitscript=True)

doc = uidoc.Document

if doc.IsFamilyDocument:
    forms.alert('This tool works on project documents, not family documents.',
                title='Title Block Manager', exitscript=True)

output = script.get_output()

# Text shown in the read-only "Note" column. These are deliberately NOT valid
# title block type names, so they can never be mistaken for a chosen value.
NOTE_NONE = u'no title block'
NOTE_MULTI = u'{} title blocks - manual review'
NOTE_OK = u''

DISPLAY_NO_TB = u'(no title block placed)'


# =============================================================================
# 1. Read the model -- ONE collector pass per thing we need
# =============================================================================
def family_and_type_name(symbol):
    """(family name, type name) for a title block FamilySymbol, never None."""
    try:
        fam = symbol.Family.Name
    except Exception:
        fam = u''
    try:
        typ = symbol.get_Parameter(
            BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
    except Exception:
        typ = None
    if not typ:
        try:
            typ = symbol.Name
        except Exception:
            typ = None
    return (fam or u''), (typ or u'?')


# --- all sheets, sorted ONCE (never re-sorted on a UI redraw) ---------------
all_sheets = sorted(
    [s for s in FilteredElementCollector(doc).OfClass(ViewSheet).ToElements()
     if not s.IsPlaceholder],
    key=lambda s: (s.SheetNumber or u'')
)
if not all_sheets:
    forms.alert('No sheets found in this project.',
                title='Title Block Manager', exitscript=True)

# --- every placed title block instance, grouped by the sheet that owns it ---
# One collector for the whole document beats one collector per sheet.
tb_instances_by_sheet = {}      # sheet id value -> [FamilyInstance, ...]
for tb in (FilteredElementCollector(doc)
           .OfCategory(BuiltInCategory.OST_TitleBlocks)
           .WhereElementIsNotElementType()
           .ToElements()):
    try:
        owner = tb.OwnerViewId
    except Exception:
        continue
    if owner is None or owner == ElementId.InvalidElementId:
        continue
    tb_instances_by_sheet.setdefault(owner.IntegerValue, []).append(tb)

# --- every available title block TYPE, once --------------------------------
tb_symbols = list(FilteredElementCollector(doc)
                  .OfCategory(BuiltInCategory.OST_TitleBlocks)
                  .WhereElementIsElementType()
                  .ToElements())

# A label is just the type name when that name is unique in the project, which
# keeps the dropdown short. When two families both own a type of the same name
# the family is prefixed so the two stay tellable apart.
type_name_counts = {}
for sym in tb_symbols:
    _, typ = family_and_type_name(sym)
    type_name_counts[typ] = type_name_counts.get(typ, 0) + 1

symbol_by_name = {}             # label -> FamilySymbol
label_by_symbol_id = {}         # symbol id value -> label
for sym in tb_symbols:
    fam, typ = family_and_type_name(sym)
    if type_name_counts.get(typ) == 1 or not fam:
        label = typ
    else:
        label = u'{} : {}'.format(fam, typ)
    # last-resort de-duplication, so one label never hides another type
    base, suffix = label, 2
    while label in symbol_by_name:
        label = u'{} ({})'.format(base, suffix)
        suffix += 1
    symbol_by_name[label] = sym
    label_by_symbol_id[sym.Id.IntegerValue] = label


def label_for_symbol(symbol):
    """The dropdown label for a placed title block's type."""
    if symbol is None:
        return DISPLAY_NO_TB
    label = label_by_symbol_id.get(symbol.Id.IntegerValue)
    if label:
        return label
    # a type that somehow was not in the collector -- still show something real
    fam, typ = family_and_type_name(symbol)
    return u'{} : {}'.format(fam, typ) if fam else typ


if not symbol_by_name:
    forms.alert('No title block family types are loaded in this project, so '
                'there is nothing to switch to.\n\nLoad a title block family '
                'first.', title='Title Block Manager', exitscript=True)

# The ONE list every dropdown in every row shares. Built once, never rebuilt.
TYPE_NAMES = sorted(symbol_by_name.keys())


# =============================================================================
# 2. What is selected in the Project Browser right now?
# =============================================================================
def preselected_sheet_ids():
    """Ids of the sheets the user had selected when the button was clicked.

    Project Browser selection shows up in UIDocument.Selection from Revit 2023
    onwards. Older builds simply return nothing, which falls back to
    'All sheets'.
    """
    ids = set()
    try:
        for eid in uidoc.Selection.GetElementIds():
            el = doc.GetElement(eid)
            if isinstance(el, ViewSheet):
                ids.add(eid.IntegerValue)
    except Exception:
        pass
    return ids


PRESELECTED = preselected_sheet_ids()


# =============================================================================
# 3. One row object per sheet
# =============================================================================
class SheetRow(object):
    """A single line in the grid.

    Only `TitleBlock` is written to by the UI. `Original` keeps the value the
    row started with, so Apply can diff and touch only what actually changed.
    """

    def __init__(self, sheet, instances):
        self.sheet = sheet
        self.sheet_id = sheet.Id
        self.instances = instances or []

        self.Number = sheet.SheetNumber or u''
        self.Name = sheet.Name or u''

        # every row points at the SAME list object -- no copies, no re-query
        self.TypeNames = TYPE_NAMES

        count = len(self.instances)
        if count == 0:
            self.Original = DISPLAY_NO_TB
            self.Note = NOTE_NONE
            self.CanEdit = False
        elif count > 1:
            self.Original = label_for_symbol(
                doc.GetElement(self.instances[0].GetTypeId()))
            self.Note = NOTE_MULTI.format(count)
            self.CanEdit = False
        else:
            self.Original = label_for_symbol(
                doc.GetElement(self.instances[0].GetTypeId()))
            self.Note = NOTE_OK
            self.CanEdit = True

        self.TitleBlock = self.Original

    @property
    def is_changed(self):
        return (self.CanEdit
                and self.TitleBlock != self.Original
                and self.TitleBlock in symbol_by_name)

    def reset(self):
        self.TitleBlock = self.Original


ALL_ROWS = [SheetRow(s, tb_instances_by_sheet.get(s.Id.IntegerValue))
            for s in all_sheets]
SELECTED_ROWS = [r for r in ALL_ROWS if r.sheet_id.IntegerValue in PRESELECTED]


# =============================================================================
# 4. The window
# =============================================================================
class TitleBlockWindow(forms.WPFWindow):
    """Thin WPF wrapper. It edits row objects in memory and nothing else --
    all Revit writing happens after the window closes."""

    def __init__(self, xaml_path):
        forms.WPFWindow.__init__(self, xaml_path)

        self.confirmed = False
        self.pending = []           # filled in by apply()
        self._ready = False         # guards handlers firing during XAML load

        # 'Selected only' is only offered if something actually was selected
        if SELECTED_ROWS:
            self.scope_selected_rb.Content = 'Selected sheets only ({})'.format(
                len(SELECTED_ROWS))
            self.scope_selected_rb.IsChecked = True
        else:
            self.scope_selected_rb.Content = \
                'Selected sheets only (none selected)'
            self.scope_selected_rb.IsEnabled = False
            self.scope_all_rb.IsChecked = True

        self.scope_all_rb.Content = 'All sheets in project ({})'.format(
            len(ALL_ROWS))

        self._ready = True
        self.refresh()

    # --------------------------------------------------------------- helpers
    def scope_rows(self):
        """Rows allowed by the Selected/All radio buttons."""
        if self.scope_selected_rb.IsChecked and SELECTED_ROWS:
            return SELECTED_ROWS
        return ALL_ROWS

    def visible_rows(self):
        """Scope rows, narrowed by the search box."""
        rows = self.scope_rows()
        query = (self.search_tb.Text or '').strip().lower()
        if not query:
            return rows
        return [r for r in rows
                if query in r.Number.lower()
                or query in r.Name.lower()
                or query in (r.TitleBlock or u'').lower()]

    def refresh(self):
        """Re-point the grid at the current row set.

        Row objects are plain Python objects with no change notification, so
        the ItemsSource is reset rather than mutated. It is cheap, and it keeps
        every pending edit because the edits live on the row objects.
        """
        if not self._ready:
            return
        try:
            rows = self.visible_rows()
            self.grid.ItemsSource = None
            self.grid.ItemsSource = rows
            self.update_status(len(rows))
        except Exception as err:
            self.status_tb.Text = 'Could not refresh the list: {}'.format(err)

    def update_status(self, shown=None):
        try:
            if shown is None:
                shown = len(self.visible_rows())
            changed = len([r for r in self.scope_rows() if r.is_changed])
            flagged = len([r for r in self.scope_rows() if not r.CanEdit])
            bits = ['{} sheet(s) shown'.format(shown),
                    '{} change(s) pending'.format(changed)]
            if flagged:
                bits.append('{} flagged/skipped'.format(flagged))
            self.status_tb.Text = '   |   '.join(bits)
        except Exception:
            pass

    # -------------------------------------------------------- event handlers
    def scope_changed(self, sender, args):
        self.refresh()

    def search_changed(self, sender, args):
        self.refresh()

    def clear_search(self, sender, args):
        self.search_tb.Text = ''
        self.refresh()

    def titleblock_changed(self, sender, args):
        """A dropdown was used.

        The XAML already binds the dropdown back to the row, but the value is
        also written here straight from the ComboBox. Belt and braces: the row
        objects are plain Python objects, and this keeps the chosen value
        correct even if the binding's write-back ever fails silently.

        Only a real, known type name is accepted, so a stray event can never
        drop rubbish into a row.
        """
        try:
            row = sender.DataContext
            value = sender.SelectedItem
            if value is None:
                value = sender.Text
            if row is not None and value:
                value = u'{}'.format(value)
                if value in symbol_by_name:
                    row.TitleBlock = value
        except Exception:
            pass
        self.update_status()

    def reset_changes(self, sender, args):
        for row in ALL_ROWS:
            row.reset()
        self.refresh()

    def apply(self, sender, args):
        """Collect the diff and close. No Revit writing happens in here."""
        try:
            changed = [r for r in ALL_ROWS if r.is_changed]
            if not changed:
                forms.alert('Nothing to apply - no dropdown was changed.',
                            title='Title Block Manager')
                return
            self.pending = changed
            self.confirmed = True
            self.Close()
        except Exception as err:
            forms.alert('Could not read the pending changes:\n\n{}'.format(err),
                        title='Title Block Manager')

    def cancel_win(self, sender, args):
        self.confirmed = False
        self.pending = []
        self.Close()


_XAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ui.xaml')

try:
    win = TitleBlockWindow(_XAML)
    win.ShowDialog()
except Exception as err:
    forms.alert('The window could not be opened:\n\n{}'.format(err),
                title='Title Block Manager', exitscript=True)

if not win.confirmed or not win.pending:
    script.exit()

pending = win.pending


# =============================================================================
# 5. Confirm, then write everything in ONE transaction
# =============================================================================
if not forms.alert('Change the title block on {} sheet(s)?\n\n'
                   'This is a single undo step.'.format(len(pending)),
                   title='Title Block Manager', yes=True, no=True):
    script.exit()

updated = []        # (sheet number, old name, new name)
skipped = []        # (sheet number, reason)
failed = []         # (sheet number, reason)

use_progress = len(pending) > 50

t = Transaction(doc, 'Update Sheet Title Blocks')
try:
    t.Start()

    progress = None
    if use_progress:
        progress = forms.ProgressBar(
            title='Updating {value} of {max_value}...', cancellable=True)
        progress.__enter__()

    try:
        for index, row in enumerate(pending):
            if progress is not None:
                if progress.cancelled:
                    raise UserWarning('cancelled')
                progress.update_progress(index + 1, len(pending))

            # Each sheet gets its own try/except INSIDE the transaction, so one
            # locked or unhappy sheet cannot abort the rest of the batch.
            try:
                # Re-read the title blocks on this sheet: the model may have
                # moved on since the grid was loaded.
                live = [e for e in (FilteredElementCollector(doc, row.sheet_id)
                                    .OfCategory(BuiltInCategory.OST_TitleBlocks)
                                    .WhereElementIsNotElementType()
                                    .ToElements())
                        if isinstance(e, FamilyInstance)]

                if not live:
                    skipped.append((row.Number,
                                    'No title block on sheet - skipped'))
                    continue
                if len(live) > 1:
                    skipped.append((row.Number,
                                    '{} title blocks on sheet - flagged for '
                                    'manual review'.format(len(live))))
                    continue

                symbol = symbol_by_name.get(row.TitleBlock)
                if symbol is None:
                    failed.append((row.Number,
                                   'Title block type "{}" is no longer in the '
                                   'project'.format(row.TitleBlock)))
                    continue

                # ChangeTypeId silently does nothing on an inactive symbol, so
                # activate and regenerate first.
                if not symbol.IsActive:
                    symbol.Activate()
                    doc.Regenerate()

                instance = live[0]
                if instance.GetTypeId() == symbol.Id:
                    skipped.append((row.Number,
                                    'Already using that title block type'))
                    continue

                instance.ChangeTypeId(symbol.Id)
                updated.append((row.Number, row.Original, row.TitleBlock))

            except Exception as err:
                # covers "element not editable" / worksharing / read-only
                failed.append((row.Number, u'{}'.format(err)))

    finally:
        if progress is not None:
            try:
                progress.__exit__(None, None, None)
            except Exception:
                pass

    t.Commit()

except UserWarning:
    if t.HasStarted() and not t.HasEnded():
        t.RollBack()
    forms.alert('Cancelled. No changes were written.',
                title='Title Block Manager', exitscript=True)

except Exception as err:
    if t.HasStarted() and not t.HasEnded():
        t.RollBack()
    forms.alert('The update failed and was rolled back - no changes were '
                'written.\n\n{}'.format(err),
                title='Title Block Manager', exitscript=True)


# =============================================================================
# 6. Report
# =============================================================================
summary = 'Updated: {}   Skipped: {}   Failed: {}'.format(
    len(updated), len(skipped), len(failed))

output.print_md('## Title Block Update')
output.print_md('**{}**'.format(summary))

if updated:
    output.print_md('### Updated')
    output.print_table([list(r) for r in updated],
                       columns=['Sheet', 'Was', 'Now'])
if skipped:
    output.print_md('### Skipped')
    output.print_table([list(r) for r in skipped],
                       columns=['Sheet', 'Reason'])
if failed:
    output.print_md('### Failed')
    output.print_table([list(r) for r in failed],
                       columns=['Sheet', 'Reason'])

detail = ''
problems = skipped + failed
if problems:
    lines = ['{}  -  {}'.format(n, why) for n, why in problems[:12]]
    if len(problems) > 12:
        lines.append('...and {} more (see the output window).'.format(
            len(problems) - 12))
    detail = '\n\n' + '\n'.join(lines)

forms.alert(summary + detail, title='Title Block Manager')
