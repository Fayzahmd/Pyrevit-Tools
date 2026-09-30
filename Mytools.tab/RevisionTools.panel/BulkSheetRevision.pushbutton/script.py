# -*- coding: utf-8 -*-
"""
Bulk Sheet Revision

Shows every sheet in one grid with the revision its revision clouds are on,
lets you pick a new revision (per row, or for all checked rows at once), and
then moves every revision cloud on those sheets to the new revision.

How "current revision" works here:
    A sheet's current revision = the highest-sequence Revision among the
    revision clouds drawn directly on that sheet. Clouds drawn inside views
    placed on the sheet belong to those views, so they are ignored.

What Apply does:
    Every cloud on the sheet gets   cloud.RevisionId = new_revision.Id
    The cloud shapes are kept. Nothing is deleted. Sheets with no clouds are
    skipped (no cloud is invented) and listed in the results.

Author : Fayaz
Revit  : 2024 / 2025 / 2026
PyRevit: 4.8+ (IronPython 2.7)
"""
__title__ = 'Bulk Sheet\nRevision'
__doc__ = 'Change the revision of many sheets at once by moving their ' \
          'revision clouds to a new revision.'

# -- .NET / WinForms imports ---------------------------------------------------
# WinForms and Revit both have classes called "Form", "Color", "Point", so the
# WinForms/Drawing namespaces are imported under short aliases (WF, SD) to keep
# them apart from the Revit classes.
import clr
clr.AddReference('System')
clr.AddReference('System.Drawing')
clr.AddReference('System.Windows.Forms')

import System
import System.Drawing as SD
import System.Windows.Forms as WF
from System.Collections.Generic import List

# -- Revit API imports ------------------------------------------------------
from Autodesk.Revit.DB import (
    BuiltInParameter,
    CheckoutStatus,
    ElementId,
    FailureProcessingResult,
    FailureSeverity,
    FilteredElementCollector,
    IFailuresPreprocessor,
    ModelUpdatesStatus,
    Revision,
    RevisionCloud,
    SubTransaction,
    Transaction,
    TransactionStatus,
    ViewSheet,
    WorksharingUtils,
)

from pyrevit import forms, script


# =============================================================================
# Constants
# =============================================================================
TOOL_NAME = 'Bulk Sheet Revision'
DASH = unichr(0x2014)                # the long dash used in labels
NO_CHANGE = u''                           # blank "New Revision" = leave sheet alone
BULK_CLEAR = u'(clear ' + DASH + u' no change)'
NONE_TEXT = DASH + u' None'

# Grid column positions (used everywhere instead of magic numbers)
COL_CHECK = 0
COL_NUMBER = 1
COL_NAME = 2
COL_CLOUDS = 3
COL_CURRENT = 4
COL_TITLEBLOCK = 5
COL_NEW = 6

# Row tint colours
COLOR_PENDING = SD.Color.FromArgb(255, 248, 200)    # will be updated (yellow)
COLOR_WILL_SKIP = SD.Color.FromArgb(255, 222, 222)  # will be skipped (pink)
COLOR_READONLY = SD.Color.FromArgb(246, 246, 246)   # read-only columns (grey)

# Reasons a sheet can be skipped (used to group the results summary)
SKIP_NO_CLOUDS = 'No revision clouds on this sheet'
SKIP_ISSUED = 'Issued revision blocked'
SKIP_ALREADY = 'Already on the target revision'
SKIP_WORKSHARE = 'Clouds borrowed by another user / not up to date'


# =============================================================================
# Small helpers
# =============================================================================
def id_int(element_id):
    """Return an ElementId as a plain number (safe to use as a dict key).
    Revit 2024+ has .Value; .IntegerValue is the older (now removed) name."""
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


def safe_text(value):
    """Turn anything (None, numbers, .NET strings, exceptions) into text
    without ever raising an error."""
    if value is None:
        return u''
    try:
        return u'{}'.format(value)
    except Exception:
        try:
            return unicode(value)
        except Exception:
            return u'?'


def cell_text(value):
    """Read a grid cell value as text. Blank combo cells can come back as
    None or DBNull, so treat all of those as 'no change'."""
    if value is None or isinstance(value, System.DBNull):
        return NO_CHANGE
    return safe_text(value)


def md_safe(text):
    """Stop '|' in sheet names from breaking the markdown results tables."""
    return safe_text(text).replace(u'|', u'/').replace(u'\n', u' ')


def revision_label(rev):
    """Dropdown text for a revision, e.g. '3 - CONCEPT SUBMISSION  (12/08/2025)'.
    The sequence number makes every label unique, even when two revisions
    have the same description."""
    desc = safe_text(rev.Description).strip() or u'(no description)'
    label = u'{} {} {}'.format(rev.SequenceNumber, DASH, desc)
    date = safe_text(rev.RevisionDate).strip()
    if date:
        label += u'  ({})'.format(date)
    if rev.Issued:
        label += u'  [ISSUED]'
    return label


def short_revision_text(rev):
    """Shorter label used in the read-only 'Current Revision' column."""
    if rev is None:
        return NONE_TEXT
    desc = safe_text(rev.Description).strip() or u'(no description)'
    return u'{} {} {}'.format(rev.SequenceNumber, DASH, desc)


def read_title_block_revision(doc, sheet):
    """What Revit itself shows as the sheet's current revision (the value the
    title block displays). Returns (display_text, sequence_number or None).

    Note: this also counts revisions ticked in the sheet's 'Revisions on
    Sheet' dialog, not just clouds, so it can differ from the cloud-based
    'Current Revision' column."""
    try:
        rev_id = sheet.GetCurrentRevision()
        if rev_id is None or rev_id == ElementId.InvalidElementId:
            return NONE_TEXT, None
        rev = sheet.Document.GetElement(rev_id)
        if rev is None:
            return NONE_TEXT, None
        param = sheet.get_Parameter(BuiltInParameter.SHEET_CURRENT_REVISION)
        number = param.AsString() if param else None
        if number:
            return u'{}  (seq {})'.format(number, rev.SequenceNumber), rev.SequenceNumber
        return u'seq {}'.format(rev.SequenceNumber), rev.SequenceNumber
    except Exception:
        return u'?', None


# =============================================================================
# Data gathered once when the tool starts
# =============================================================================
class SheetInfo(object):
    """Everything the grid needs to know about one sheet, worked out once."""

    def __init__(self, sheet, clouds, rev_by_id):
        self.sheet = sheet
        self.element_id = sheet.Id
        self.number = safe_text(sheet.SheetNumber)
        self.name = safe_text(sheet.Name)
        self.clouds = clouds                    # RevisionCloud elements on this sheet
        self.search_key = (self.number + u' ' + self.name).lower()

        # Distinct revisions used by this sheet's clouds
        cloud_revs = {}
        for cloud in clouds:
            rev = rev_by_id.get(id_int(cloud.RevisionId))
            if rev is not None:
                cloud_revs[id_int(rev.Id)] = rev

        # "Current revision" = the highest sequence number among those clouds
        if cloud_revs:
            self.current_rev = max(cloud_revs.values(), key=lambda r: r.SequenceNumber)
        else:
            self.current_rev = None
        self.other_rev_count = max(len(cloud_revs) - 1, 0)

        self.title_block_text, self.title_block_seq = \
            read_title_block_revision(sheet.Document, sheet)

    def current_text(self):
        text = short_revision_text(self.current_rev)
        if self.other_rev_count:
            # Tell the user some clouds are on older revisions - they will be
            # moved too when this sheet is applied.
            text += u'  (+{} other rev{})'.format(
                self.other_rev_count, u's' if self.other_rev_count > 1 else u'')
        return text


def collect_revisions(doc):
    """All project revisions, ordered by sequence number."""
    revs = FilteredElementCollector(doc).OfClass(Revision).ToElements()
    return sorted(revs, key=lambda r: r.SequenceNumber)


def scan_sheets(doc, rev_by_id):
    """Collect sheets and revision clouds ONCE, build {sheet id: [clouds]},
    then build one SheetInfo per sheet. Shows a progress bar."""
    # 1. All real sheets (placeholder sheets can't hold clouds)
    sheets = [s for s in FilteredElementCollector(doc)
              .OfClass(ViewSheet)
              .WhereElementIsNotElementType()
              .ToElements()
              if not s.IsPlaceholder]
    sheet_ids = set(id_int(s.Id) for s in sheets)

    # 2. All revision clouds in one pass, grouped by the sheet that owns them.
    #    Clouds owned by any other view (plans, legends...) are ignored.
    clouds_by_sheet = {}
    for cloud in FilteredElementCollector(doc) \
            .OfClass(RevisionCloud) \
            .WhereElementIsNotElementType():
        try:
            owner = id_int(cloud.OwnerViewId)
            if owner in sheet_ids:
                clouds_by_sheet.setdefault(owner, []).append(cloud)
        except Exception:
            continue

    # 3. One SheetInfo per sheet - a bad sheet is counted and skipped
    infos = []
    unreadable = 0
    total = len(sheets)
    with forms.ProgressBar(title='Reading sheets... {value} of {max_value}') as pb:
        for i, sheet in enumerate(sheets):
            try:
                infos.append(SheetInfo(sheet,
                                       clouds_by_sheet.get(id_int(sheet.Id), []),
                                       rev_by_id))
            except Exception:
                unreadable += 1
            if i % 10 == 0 or i == total - 1:
                pb.update_progress(i + 1, total)

    infos.sort(key=lambda x: x.number)
    return infos, unreadable


# =============================================================================
# The window (WinForms DataGridView)
# =============================================================================
def safe_handler(func):
    """Wrap a window event handler so an error shows a friendly message
    instead of the WinForms 'unhandled exception' crash dialog."""
    def wrapper(self, sender, args):
        try:
            return func(self, sender, args)
        except Exception as err:
            WF.MessageBox.Show(self,
                u'Something went wrong in the window:\n\n{}'.format(safe_text(err)),
                TOOL_NAME, WF.MessageBoxButtons.OK, WF.MessageBoxIcon.Warning)
    return wrapper


class RevisionGridForm(WF.Form):
    """Modal window: one grid row per sheet, toolbar on top, Apply/Cancel at
    the bottom. It only COLLECTS the user's choices - the model is changed
    after the window closes (see apply_changes), which keeps Revit stable."""

    def __init__(self, doc_title, sheet_infos, revisions):
        self.sheet_infos = sheet_infos
        self.revisions = revisions
        self.rev_labels = [revision_label(r) for r in revisions]
        self.label_to_rev = dict(zip(self.rev_labels, revisions))
        self.changes = []           # filled with (SheetInfo, Revision) on Apply
        self._bulk_update = False   # True while we change many cells in code

        self._build_window(doc_title)
        self._build_toolbar()
        self._build_bottom_bar()
        self._build_grid()
        self._fill_grid()

        # Grid fills whatever space the top/bottom bars leave
        self.grid.BringToFront()
        self._update_status()

    # -- layout -------------------------------------------------------------
    def _build_window(self, doc_title):
        self.Text = u'{} {} {}'.format(TOOL_NAME, DASH, doc_title)
        self.Size = SD.Size(1250, 760)
        self.MinimumSize = SD.Size(900, 450)
        self.StartPosition = WF.FormStartPosition.CenterScreen
        self.Font = SD.Font('Segoe UI', 9.0)
        self.ShowIcon = False
        self.ShowInTaskbar = False

        # Timer so the search runs 250 ms after typing stops, not every key
        self.filter_timer = WF.Timer()
        self.filter_timer.Interval = 250
        self.filter_timer.Tick += self._on_filter_timer

    def _label(self, text, bold=False):
        lbl = WF.Label()
        lbl.Text = text
        lbl.AutoSize = True
        lbl.Margin = WF.Padding(6, 8, 3, 3)    # nudged down to line up with boxes
        if bold:
            lbl.Font = SD.Font('Segoe UI', 9.0, SD.FontStyle.Bold)
        return lbl

    def _button(self, text, handler, width=None):
        btn = WF.Button()
        btn.Text = text
        btn.AutoSize = True
        if width:
            btn.AutoSize = False
            btn.Width = width
            btn.Height = 27
        btn.Margin = WF.Padding(3, 3, 3, 3)
        btn.Click += handler
        return btn

    def _build_toolbar(self):
        bar = WF.FlowLayoutPanel()
        bar.Dock = WF.DockStyle.Top
        bar.AutoSize = True
        bar.WrapContents = True
        bar.Padding = WF.Padding(6, 6, 6, 2)

        # Search box
        bar.Controls.Add(self._label('Search:'))
        self.search_box = WF.TextBox()
        self.search_box.Width = 220
        self.search_box.TextChanged += self._on_search_changed
        bar.Controls.Add(self.search_box)

        # Select All / Select None (act on the rows currently shown)
        bar.Controls.Add(self._button('Select All', self._on_select_all))
        bar.Controls.Add(self._button('Select None', self._on_select_none))

        # Bulk revision picker
        bar.Controls.Add(self._label(u'     Set checked rows to:', bold=True))
        self.bulk_combo = WF.ComboBox()
        self.bulk_combo.DropDownStyle = WF.ComboBoxStyle.DropDownList
        self.bulk_combo.Width = 420
        self.bulk_combo.MaxDropDownItems = 25
        self.bulk_combo.Items.Add(BULK_CLEAR)
        for label in self.rev_labels:
            self.bulk_combo.Items.Add(label)
        # Pre-pick the newest non-issued revision (the usual choice)
        open_revs = [r for r in self.revisions if not r.Issued]
        if open_revs:
            self.bulk_combo.SelectedItem = revision_label(open_revs[-1])
        bar.Controls.Add(self.bulk_combo)
        bar.Controls.Add(self._button('Apply to checked', self._on_bulk_apply))

        self.Controls.Add(bar)

    def _build_bottom_bar(self):
        bottom = WF.Panel()
        bottom.Dock = WF.DockStyle.Bottom
        bottom.Height = 62
        bottom.Padding = WF.Padding(8, 6, 8, 8)

        # Buttons on the right
        buttons = WF.FlowLayoutPanel()
        buttons.Dock = WF.DockStyle.Right
        buttons.FlowDirection = WF.FlowDirection.RightToLeft
        buttons.Width = 250
        self.cancel_btn = self._button('Cancel', self._on_cancel, width=100)
        self.apply_btn = self._button('Apply', self._on_apply, width=120)
        self.apply_btn.Font = SD.Font('Segoe UI', 9.0, SD.FontStyle.Bold)
        buttons.Controls.Add(self.cancel_btn)
        buttons.Controls.Add(self.apply_btn)
        self.CancelButton = self.cancel_btn    # Esc = Cancel (no default Enter)

        # Status text on the left
        self.status_label = WF.Label()
        self.status_label.Dock = WF.DockStyle.Fill
        self.status_label.TextAlign = SD.ContentAlignment.MiddleLeft

        bottom.Controls.Add(self.status_label)
        bottom.Controls.Add(buttons)
        self.Controls.Add(bottom)

    def _build_grid(self):
        g = WF.DataGridView()
        g.Dock = WF.DockStyle.Fill
        g.AllowUserToAddRows = False
        g.AllowUserToDeleteRows = False
        g.AllowUserToResizeRows = False
        g.RowHeadersVisible = False
        g.SelectionMode = WF.DataGridViewSelectionMode.CellSelect
        g.EditMode = WF.DataGridViewEditMode.EditOnEnter   # 1 click opens dropdown
        g.AutoSizeColumnsMode = WF.DataGridViewAutoSizeColumnsMode.Fill
        g.BackgroundColor = SD.SystemColors.Window
        g.BorderStyle = getattr(WF.BorderStyle, 'None')   # 'None' is a Python keyword
        g.RowTemplate.Height = 22
        g.ColumnHeadersHeightSizeMode = \
            WF.DataGridViewColumnHeadersHeightSizeMode.AutoSize

        # Double-buffering makes scrolling 350 rows smooth. It's a protected
        # property, so it's switched on via reflection (harmless if it fails).
        try:
            flags = System.Reflection.BindingFlags.Instance | \
                System.Reflection.BindingFlags.NonPublic
            prop = g.GetType().GetProperty('DoubleBuffered', flags)
            prop.SetValue(g, True, None)
        except Exception:
            pass

        # Column 0: checkbox
        col = WF.DataGridViewCheckBoxColumn()
        col.HeaderText = u''
        col.FillWeight = 4
        col.MinimumWidth = 30
        g.Columns.Add(col)

        # Columns 1-5: read-only text
        def text_col(header, weight, min_width):
            c = WF.DataGridViewTextBoxColumn()
            c.HeaderText = header
            c.ReadOnly = True
            c.FillWeight = weight
            c.MinimumWidth = min_width
            c.SortMode = WF.DataGridViewColumnSortMode.Automatic
            c.DefaultCellStyle.BackColor = COLOR_READONLY
            return c

        g.Columns.Add(text_col('Sheet Number', 10, 80))
        g.Columns.Add(text_col('Sheet Name', 30, 150))
        clouds_col = text_col('Clouds', 6, 50)
        clouds_col.DefaultCellStyle.Alignment = \
            WF.DataGridViewContentAlignment.MiddleCenter
        g.Columns.Add(clouds_col)
        g.Columns.Add(text_col('Current Revision (clouds)', 24, 150))
        g.Columns.Add(text_col('Title Block Rev', 11, 90))

        # Column 6: New Revision dropdown - same list on every row
        combo = WF.DataGridViewComboBoxColumn()
        combo.HeaderText = 'New Revision'
        combo.FillWeight = 30
        combo.MinimumWidth = 200
        combo.DropDownWidth = 460
        combo.MaxDropDownItems = 25
        combo.FlatStyle = WF.FlatStyle.Flat
        combo.DisplayStyleForCurrentCellOnly = True   # faster drawing
        combo.Items.Add(NO_CHANGE)
        for label in self.rev_labels:
            combo.Items.Add(label)
        g.Columns.Add(combo)

        self.grid = g
        self.Controls.Add(g)

    def _fill_grid(self):
        """Add one row per sheet. Event handlers are attached afterwards so
        filling the grid doesn't trigger them 350 times."""
        g = self.grid
        g.SuspendLayout()
        try:
            for info in self.sheet_infos:
                idx = g.Rows.Add(False, info.number, info.name, len(info.clouds),
                                 info.current_text(), info.title_block_text,
                                 NO_CHANGE)
                row = g.Rows[idx]
                row.Tag = info                     # link row -> sheet data
                if not info.clouds:
                    row.Cells[COL_CLOUDS].Style.ForeColor = SD.Color.Gray
                    row.Cells[COL_CURRENT].Style.ForeColor = SD.Color.Gray
        finally:
            g.ResumeLayout()

        g.CurrentCellDirtyStateChanged += self._on_dirty_state
        g.CellValueChanged += self._on_cell_value_changed
        g.DataError += self._on_data_error

    # -- helpers ------------------------------------------------------------
    def _end_edit(self):
        """Finish any in-progress cell edit so code changes aren't overwritten."""
        try:
            self.grid.EndEdit()
            self.grid.CurrentCell = None
        except Exception:
            pass

    def _is_checked(self, row):
        return bool(row.Cells[COL_CHECK].Value)

    def _target_for_row(self, row):
        """The Revision chosen in the row's dropdown, or None."""
        return self.label_to_rev.get(cell_text(row.Cells[COL_NEW].Value))

    def _style_row(self, row):
        """Tint a row: yellow = will be updated, pink = will be skipped."""
        target = self._target_for_row(row)
        if target is None:
            row.DefaultCellStyle.BackColor = SD.Color.Empty
        elif not row.Tag.clouds or target.Issued:
            row.DefaultCellStyle.BackColor = COLOR_WILL_SKIP
        else:
            row.DefaultCellStyle.BackColor = COLOR_PENDING

    def _update_status(self, extra=None):
        shown = checked = pending = 0
        for row in self.grid.Rows:
            if row.Visible:
                shown += 1
            if self._is_checked(row):
                checked += 1
            if cell_text(row.Cells[COL_NEW].Value):
                pending += 1
        with_clouds = len([i for i in self.sheet_infos if i.clouds])
        text = (u'{} sheets  |  {} shown  |  {} have revision clouds  |  '
                u'{} checked  |  {} with a New Revision set').format(
            len(self.sheet_infos), shown, with_clouds, checked, pending)
        if extra:
            text += u'\n' + extra
        self.status_label.Text = text

    # -- event handlers -----------------------------------------------------
    @safe_handler
    def _on_dirty_state(self, sender, args):
        # Checkbox / dropdown changes normally only "stick" when you leave the
        # cell. Commit them immediately so the status and colours update.
        cell = self.grid.CurrentCell
        if self.grid.IsCurrentCellDirty and cell is not None and \
                cell.ColumnIndex in (COL_CHECK, COL_NEW):
            self.grid.CommitEdit(WF.DataGridViewDataErrorContexts.Commit)

    @safe_handler
    def _on_cell_value_changed(self, sender, args):
        if self._bulk_update or args.RowIndex < 0:
            return
        if args.ColumnIndex == COL_NEW:
            self._style_row(self.grid.Rows[args.RowIndex])
        self._update_status()

    def _on_data_error(self, sender, args):
        # A combo value that isn't in the list would normally pop up an
        # error box. Swallow it - the value is simply treated as "no change".
        args.ThrowException = False

    @safe_handler
    def _on_search_changed(self, sender, args):
        self.filter_timer.Stop()
        self.filter_timer.Start()

    @safe_handler
    def _on_filter_timer(self, sender, args):
        self.filter_timer.Stop()
        text = safe_text(self.search_box.Text).strip().lower()
        self._end_edit()   # a row holding the current cell can't be hidden
        self.grid.SuspendLayout()
        try:
            for row in self.grid.Rows:
                row.Visible = (not text) or (text in row.Tag.search_key)
        finally:
            self.grid.ResumeLayout()
        self._update_status()

    def _set_visible_checked(self, state):
        self._end_edit()
        self._bulk_update = True
        try:
            for row in self.grid.Rows:
                if row.Visible:
                    row.Cells[COL_CHECK].Value = state
        finally:
            self._bulk_update = False
        self._update_status()

    @safe_handler
    def _on_select_all(self, sender, args):
        self._set_visible_checked(True)

    @safe_handler
    def _on_select_none(self, sender, args):
        self._set_visible_checked(False)

    @safe_handler
    def _on_bulk_apply(self, sender, args):
        choice = cell_text(self.bulk_combo.SelectedItem)
        if not choice:
            WF.MessageBox.Show(self, 'Pick a revision in the "Set checked rows to" '
                               'dropdown first.', TOOL_NAME)
            return

        if choice == BULK_CLEAR:
            new_value = NO_CHANGE
        else:
            rev = self.label_to_rev.get(choice)
            if rev is None:
                return
            if rev.Issued:
                WF.MessageBox.Show(self,
                    u'Revision {} is marked as Issued.\n\nRevit does not allow '
                    u'revision clouds to be assigned to an issued revision. '
                    u'Un-issue it in Manage > Sheet Issues/Revisions first, '
                    u'or pick another revision.'.format(rev.SequenceNumber),
                    TOOL_NAME, WF.MessageBoxButtons.OK, WF.MessageBoxIcon.Warning)
                return
            new_value = choice

        self._end_edit()
        rows = [r for r in self.grid.Rows if self._is_checked(r)]
        if not rows:
            WF.MessageBox.Show(self, 'No rows are checked. Tick the rows you want '
                               '(or use Select All) first.', TOOL_NAME)
            return

        # Change all rows in one go, then refresh colours/status once
        self._bulk_update = True
        try:
            for row in rows:
                row.Cells[COL_NEW].Value = new_value
                self._style_row(row)
        finally:
            self._bulk_update = False

        hidden = len([r for r in rows if not r.Visible])
        msg = u'Set "{}" on {} checked row(s).'.format(
            choice if new_value else u'no change', len(rows))
        if hidden:
            msg += u'  ({} of them are hidden by the search filter.)'.format(hidden)
        self._update_status(msg)

    @safe_handler
    def _on_apply(self, sender, args):
        self._end_edit()

        # Every row with a New Revision is applied - checked or not. The
        # checkbox only controls which rows "Apply to checked" fills in.
        changes = []
        for row in self.grid.Rows:
            target = self._target_for_row(row)
            if target is not None:
                changes.append((row.Tag, target, row.Visible))

        if not changes:
            WF.MessageBox.Show(self, 'No sheet has a New Revision set yet.\n\n'
                               'Tick rows, choose a revision at the top and '
                               'click "Apply to checked" - or pick a revision '
                               'in a row\'s New Revision cell.', TOOL_NAME)
            return

        no_clouds = len([c for c in changes if not c[0].clouds])
        hidden = len([c for c in changes if not c[2]])
        msg = u'Move the revision clouds on {} sheet(s) to their new revision?'.format(
            len(changes))
        if no_clouds:
            msg += u'\n\n{} of these sheets have no revision clouds and will be ' \
                   u'skipped.'.format(no_clouds)
        if hidden:
            msg += u'\n\n{} of these sheets are currently hidden by the search ' \
                   u'filter.'.format(hidden)
        msg += u'\n\nThis is done as ONE undoable step (Ctrl+Z undoes all of it).'

        answer = WF.MessageBox.Show(self, msg, TOOL_NAME, WF.MessageBoxButtons.YesNo,
                                    WF.MessageBoxIcon.Question)
        if answer != WF.DialogResult.Yes:
            return

        self.changes = [(c[0], c[1]) for c in changes]
        self.DialogResult = WF.DialogResult.OK
        self.Close()

    @safe_handler
    def _on_cancel(self, sender, args):
        self.changes = []
        self.DialogResult = WF.DialogResult.Cancel
        self.Close()


# =============================================================================
# Applying the changes to the model
# =============================================================================
class WarningSwallower(IFailuresPreprocessor):
    """Runs when the transaction commits. Warnings are dismissed silently (so
    350 sheets can't pop up 350 warning boxes). Real errors are recorded and
    the whole transaction is rolled back cleanly instead of showing a dialog."""

    def __init__(self):
        self.errors = []

    def PreprocessFailures(self, failures_accessor):
        try:
            has_error = False
            for failure in failures_accessor.GetFailureMessages():
                if failure.GetSeverity() == FailureSeverity.Warning:
                    failures_accessor.DeleteWarning(failure)
                else:
                    has_error = True
                    self.errors.append(safe_text(failure.GetDescriptionText()))
            if has_error:
                opts = failures_accessor.GetFailureHandlingOptions()
                opts.SetClearAfterRollback(True)
                failures_accessor.SetFailureHandlingOptions(opts)
                return FailureProcessingResult.ProceedWithRollBack
        except Exception as err:
            self.errors.append(safe_text(err))
        return FailureProcessingResult.Continue


class SheetPlan(object):
    """What we intend to do to one sheet."""

    def __init__(self, info, target):
        self.info = info
        self.target = target
        self.to_move = []            # clouds we will reassign
        self.already = 0             # clouds already on the target
        self.issued_blocked = 0      # clouds stuck on an issued revision
        self.ws_blocked = 0          # clouds another user has borrowed
        self.ws_owners = set()


class ApplyReport(object):
    """Collects the outcome of every sheet for the results summary."""

    def __init__(self):
        self.updated = []            # (SheetPlan, clouds_moved)
        self.skipped = []            # (SheetInfo, category, detail)
        self.errors = []             # (SheetInfo, message)
        self.clouds_moved = 0
        self.committed = False
        self.cancelled = False
        self.fatal = None            # message if the whole run was rolled back

    def skip(self, info, category, detail):
        self.skipped.append((info, category, detail))


def reserve_clouds(doc, clouds):
    """Workshared models only: borrow the clouds we're about to edit BEFORE
    the transaction, so one cloud owned by a colleague can't make the whole
    commit fail. Returns the set of cloud ids we may edit, or None when the
    model isn't workshared (meaning: all of them)."""
    if not doc.IsWorkshared or not clouds:
        return None
    ids = List[ElementId]([c.Id for c in clouds])
    try:
        owned = WorksharingUtils.CheckoutElements(doc, ids)
        return set(id_int(i) for i in owned)
    except Exception:
        # Central not reachable etc. - fall back to the locally known status
        allowed = set()
        for cloud in clouds:
            try:
                if WorksharingUtils.GetCheckoutStatus(doc, cloud.Id) == \
                        CheckoutStatus.OwnedByOtherUser:
                    continue
                if WorksharingUtils.GetModelUpdatesStatus(doc, cloud.Id) in (
                        ModelUpdatesStatus.UpdatedInCentral,
                        ModelUpdatesStatus.DeletedInCentral):
                    continue
            except Exception:
                pass
            allowed.add(id_int(cloud.Id))
        return allowed


def cloud_owner(doc, cloud):
    try:
        return safe_text(WorksharingUtils.GetWorksharingTooltipInfo(doc, cloud.Id).Owner)
    except Exception:
        return u''


def build_plans(doc, changes, rev_by_id, report):
    """Read-only pass: decide, for each sheet, which clouds can be moved and
    which sheets must be skipped (and why). Nothing is changed here."""
    plans = []
    for info, target in changes:
        if not info.clouds:
            report.skip(info, SKIP_NO_CLOUDS,
                        'No clouds to reassign (no cloud was created)')
            continue
        if target.Issued:
            report.skip(info, SKIP_ISSUED,
                        'Target revision {} is Issued - Revit blocks assigning '
                        'clouds to it'.format(target.SequenceNumber))
            continue

        plan = SheetPlan(info, target)
        target_key = id_int(target.Id)
        for cloud in info.clouds:
            if id_int(cloud.RevisionId) == target_key:
                plan.already += 1
                continue
            source = rev_by_id.get(id_int(cloud.RevisionId))
            if source is not None and source.Issued:
                plan.issued_blocked += 1        # Revit locks issued clouds
                continue
            plan.to_move.append(cloud)

        if not plan.to_move:
            if plan.issued_blocked:
                report.skip(info, SKIP_ISSUED,
                            '{} cloud(s) are on Issued revisions and are locked'
                            .format(plan.issued_blocked))
            else:
                report.skip(info, SKIP_ALREADY,
                            'All {} cloud(s) are already on revision {}'
                            .format(plan.already, target.SequenceNumber))
            continue
        plans.append(plan)

    # Workshared: drop clouds we couldn't borrow
    allowed = reserve_clouds(doc, [c for p in plans for c in p.to_move])
    if allowed is not None:
        for plan in plans:
            keep = []
            for cloud in plan.to_move:
                if id_int(cloud.Id) in allowed:
                    keep.append(cloud)
                else:
                    plan.ws_blocked += 1
                    owner = cloud_owner(doc, cloud)
                    if owner:
                        plan.ws_owners.add(owner)
            plan.to_move = keep

    ready = []
    for plan in plans:
        if plan.to_move:
            ready.append(plan)
        else:
            who = u', '.join(sorted(plan.ws_owners)) or u'another user'
            report.skip(plan.info, SKIP_WORKSHARE,
                        u'{} cloud(s) borrowed by {} or not up to date with '
                        u'central - Reload Latest and retry'.format(plan.ws_blocked, who))
    return ready


def move_sheet_clouds(doc, plan, report):
    """Reassign one sheet's clouds inside its own SubTransaction, so a failure
    on this sheet undoes just this sheet and the loop carries on."""
    sub = SubTransaction(doc)
    try:
        sub.Start()
        for cloud in plan.to_move:
            cloud.RevisionId = plan.target.Id      # keeps the cloud shape
        sub.Commit()
        report.updated.append((plan, len(plan.to_move)))
        report.clouds_moved += len(plan.to_move)
    except Exception as err:
        try:
            if sub.HasStarted() and not sub.HasEnded():
                sub.RollBack()
        except Exception:
            pass
        message = safe_text(err)
        if 'issued' in message.lower():
            report.skip(plan.info, SKIP_ISSUED, message)
        else:
            report.errors.append((plan.info, message))


def apply_changes(doc, changes, rev_by_id):
    """Apply every sheet change inside ONE Transaction (one Ctrl+Z)."""
    report = ApplyReport()
    plans = build_plans(doc, changes, rev_by_id, report)
    if not plans:
        report.committed = True        # nothing to do, nothing went wrong
        return report

    swallower = WarningSwallower()
    t = Transaction(doc, 'Bulk Sheet Revision Change')
    options = t.GetFailureHandlingOptions()
    options.SetFailuresPreprocessor(swallower)
    t.SetFailureHandlingOptions(options)

    total = len(plans)
    try:
        t.Start()
        with forms.ProgressBar(title='Reassigning revision clouds... '
                                     'sheet {value} of {max_value}',
                               cancellable=True) as pb:
            for i, plan in enumerate(plans):
                if pb.cancelled:
                    report.cancelled = True
                    break
                try:
                    move_sheet_clouds(doc, plan, report)
                except Exception as err:          # belt and braces
                    report.errors.append((plan.info, safe_text(err)))
                if i % 5 == 0 or i == total - 1:
                    pb.update_progress(i + 1, total)

        if report.cancelled:
            t.RollBack()
            return report

        status = t.Commit()
        if status == TransactionStatus.Committed:
            report.committed = True
        else:
            report.fatal = u'Revit rolled the change back ({}). No sheets were ' \
                           u'changed.'.format(status)
            if swallower.errors:
                report.fatal += u'\n\n' + u'\n'.join(swallower.errors)
    except Exception as err:
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass
        report.fatal = u'The change was rolled back - no sheets were changed.' \
                       u'\n\n{}'.format(safe_text(err))
    return report


# =============================================================================
# Results summary
# =============================================================================
def show_report(doc, report):
    output = script.get_output()

    if report.cancelled:
        forms.alert('Cancelled. Everything was rolled back - no sheets were changed.',
                    title=TOOL_NAME)
        return
    if report.fatal:
        forms.alert('The bulk revision change could not be completed.',
                    title=TOOL_NAME, sub_msg=report.fatal)
        return

    # Re-read what the title block shows now (Revit only refreshes this after
    # the commit). If it isn't the target, a higher revision is still on the
    # sheet - usually ticked in 'Revisions on Sheet'.
    updated_rows = []
    mismatch = 0
    partial = 0
    for plan, moved in report.updated:
        tb_text, tb_seq = read_title_block_revision(doc, plan.info.sheet)
        notes = []
        if plan.issued_blocked:
            notes.append(u'{} cloud(s) left on issued revisions'.format(plan.issued_blocked))
        if plan.ws_blocked:
            notes.append(u'{} cloud(s) borrowed by another user'.format(plan.ws_blocked))
        if notes:
            partial += 1
        if tb_seq != plan.target.SequenceNumber:
            mismatch += 1
            notes.append(u'title block still shows a higher revision '
                         u'(from Revisions on Sheet or locked clouds)')
        updated_rows.append([
            output.linkify(plan.info.element_id, md_safe(plan.info.number)),
            md_safe(plan.info.name),
            moved,
            md_safe(short_revision_text(plan.target)),
            md_safe(tb_text),
            md_safe(u'; '.join(notes)) or u'OK',
        ])

    # Count skip reasons
    counts = {}
    for _, category, _ in report.skipped:
        counts[category] = counts.get(category, 0) + 1

    # -- pyRevit output window (full detail, clickable sheet links) --------
    output.print_md(u'## {} {} Results'.format(TOOL_NAME, DASH))
    output.print_md(u'**Sheets updated:** {}  |  **Clouds reassigned:** {}  |  '
                    u'**Skipped:** {}  |  **Errors:** {}'.format(
                        len(report.updated), report.clouds_moved,
                        len(report.skipped), len(report.errors)))
    if updated_rows:
        output.print_table(updated_rows, title='Updated sheets',
                           columns=['Sheet', 'Name', 'Clouds moved', 'New revision',
                                    'Title block now', 'Notes'])
    if report.skipped:
        output.print_table(
            [[output.linkify(i.element_id, md_safe(i.number)), md_safe(i.name),
              cat, md_safe(detail)] for i, cat, detail in report.skipped],
            title='Skipped sheets', columns=['Sheet', 'Name', 'Reason', 'Detail'])
    if report.errors:
        output.print_table(
            [[output.linkify(i.element_id, md_safe(i.number)), md_safe(i.name),
              md_safe(msg)] for i, msg in report.errors],
            title='Sheets with errors', columns=['Sheet', 'Name', 'Error'])

    # -- short pop-up summary ---------------------------------------------
    lines = [
        u'Sheets updated: {}{}'.format(
            len(report.updated),
            u'  ({} only partly - see notes)'.format(partial) if partial else u''),
        u'Revision clouds reassigned: {}'.format(report.clouds_moved),
        u'Sheets skipped: {}'.format(len(report.skipped)),
    ]
    for category in (SKIP_NO_CLOUDS, SKIP_ALREADY, SKIP_ISSUED, SKIP_WORKSHARE):
        if counts.get(category):
            lines.append(u'    - {}: {}'.format(category, counts[category]))
    lines.append(u'Sheets with errors: {}'.format(len(report.errors)))
    if mismatch:
        lines.append(u'')
        lines.append(u'Note: on {} updated sheet(s) the title block still shows a '
                     u'higher revision, because that revision is ticked in the '
                     u'sheet\'s "Revisions on Sheet" list.'.format(mismatch))
    forms.alert(u'\n'.join(lines), title=TOOL_NAME,
                sub_msg='Full per-sheet details are in the pyRevit output window.')


# =============================================================================
# Main
# =============================================================================
def main():
    uidoc = __revit__.ActiveUIDocument
    if uidoc is None:
        forms.alert('Open a Revit project first.', title=TOOL_NAME)
        return
    doc = uidoc.Document

    # Up-front checks: must be an editable project with revisions in it
    if doc.IsFamilyDocument:
        forms.alert('This tool works in project files, not families.', title=TOOL_NAME)
        return
    if doc.IsReadOnly:
        forms.alert('This model is read-only, so sheet revisions cannot be changed.',
                    title=TOOL_NAME)
        return

    revisions = collect_revisions(doc)
    if not revisions:
        forms.alert('This project has no revisions defined.\n\n'
                    'Add them in Manage > Sheet Issues/Revisions, then run the '
                    'tool again.', title=TOOL_NAME)
        return
    rev_by_id = dict((id_int(r.Id), r) for r in revisions)

    # Read sheets + clouds once
    sheet_infos, unreadable = scan_sheets(doc, rev_by_id)
    if not sheet_infos:
        forms.alert('No sheets were found in this project.', title=TOOL_NAME)
        return
    if unreadable:
        forms.alert('{} sheet(s) could not be read and are left out of the list.'
                    .format(unreadable), title=TOOL_NAME)

    # Show the grid; it closes with OK only when the user confirms Apply
    form = RevisionGridForm(safe_text(doc.Title), sheet_infos, revisions)
    try:
        result = form.ShowDialog()
        changes = list(form.changes)
    finally:
        form.Dispose()
    if result != WF.DialogResult.OK or not changes:
        return

    report = apply_changes(doc, changes, rev_by_id)
    show_report(doc, report)


# Top-level safety net: any unexpected problem becomes a friendly message
# (technical details are tucked under the "expand" arrow), never a raw
# traceback.
try:
    main()
except Exception as unexpected:
    import traceback
    forms.alert('Something went wrong and the tool stopped. Any change that '
                'was in progress has been rolled back.',
                title=TOOL_NAME,
                sub_msg=safe_text(unexpected),
                expanded=traceback.format_exc())
