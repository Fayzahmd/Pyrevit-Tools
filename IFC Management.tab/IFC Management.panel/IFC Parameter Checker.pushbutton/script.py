# -*- coding: utf-8 -*-
"""
IFC Parameter Checker - a read-only audit of the three IFC export parameters
on the structural elements of the active model.

  Export to IFC          whether the element is exported at all
  Export to IFC As       the IFC entity it is exported as
  IFC Predefined Type    the predefined type within that entity

The audit never starts a transaction. The only thing that writes to the
document is the optional colour-coded 3D view, which is off by default, is
confirmed separately, and is confined to a view this tool creates itself.

Author : Fayaz
Revit  : 2025
PyRevit: 4.8+
"""
import os
import sys

# Make the sibling modules importable regardless of how pyRevit sets up the
# path for this bundle.
_HERE = os.path.dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import clr                                                   # noqa: E402

# The WPF assemblies have to be referenced before anything under
# System.Windows can be imported. pyrevit.forms does this as a side effect,
# but relying on that makes the import order load-bearing, so reference them
# explicitly here.
for _assembly in ('PresentationCore', 'PresentationFramework',
                  'WindowsBase', 'System.Xaml'):
    try:
        clr.AddReference(_assembly)
    except Exception:
        pass

from pyrevit import forms, script                            # noqa: E402

from Autodesk.Revit.DB import ElementId                      # noqa: E402
from System.Collections.Generic import List                  # noqa: E402
from System.Windows import HorizontalAlignment, Thickness    # noqa: E402
from System.Windows.Controls import (CheckBox, Orientation,  # noqa: E402
                                     StackPanel, TextBlock)
from System.Windows.Media import Color as MediaColor         # noqa: E402
from System.Windows.Media import SolidColorBrush             # noqa: E402
from System.Windows.Shapes import Rectangle                  # noqa: E402

import ifc_audit                                             # noqa: E402
import ifc_collect                                           # noqa: E402
import ifc_config as cfg                                     # noqa: E402
import ifc_params                                            # noqa: E402
import ifc_report                                            # noqa: E402
import ifc_visualize                                         # noqa: E402

__title__ = 'IFC Parameter\nChecker'
__doc__ = ('Read-only audit of Export to IFC, Export to IFC As and IFC '
           'Predefined Type on structural elements. Reports missing, blank '
           'and invalid values, exports a CSV, and can optionally build a '
           'colour-coded 3D audit view.')

output = script.get_output()
uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document if uidoc else None

ALL = '<all>'


# ---------------------------------------------------------------------------
# Scan options window
# ---------------------------------------------------------------------------
class ScanWindow(forms.WPFWindow):
    """Scope, categories and options. Returns a settings dict or None."""

    def __init__(self, xaml_path, options, selection_count, warnings):
        forms.WPFWindow.__init__(self, xaml_path)
        self.result = None
        self._checkboxes = []

        for option in options:
            box = CheckBox()
            box.Content = option.label
            box.Tag = option
            box.IsChecked = option.default
            box.Margin = Thickness(0, 2, 0, 2)
            self.category_panel.Children.Add(box)
            self._checkboxes.append(box)

        if selection_count:
            self.selection_note.Text = \
                '{} element(s) selected in Revit.'.format(selection_count)
        else:
            self.selection_note.Text = 'Nothing is selected in Revit.'
            self.scope_selection.IsEnabled = False

        self.only_issues.IsChecked = cfg.SHOW_ONLY_ISSUES_DEFAULT
        self.only_issues.IsEnabled = bool(self.create_view.IsChecked)

        self.warning_note.Text = '\n'.join(warnings) if warnings else ''

    # -- handlers ---------------------------------------------------------
    def create_view_toggled(self, sender, args):
        """The findings-only option only means anything with a view to build."""
        self.only_issues.IsEnabled = bool(self.create_view.IsChecked)

    def select_all_click(self, sender, args):
        for box in self._checkboxes:
            box.IsChecked = True

    def select_none_click(self, sender, args):
        for box in self._checkboxes:
            box.IsChecked = False

    def cancel_click(self, sender, args):
        self.result = None
        self.Close()

    def scan_click(self, sender, args):
        chosen = [box.Tag for box in self._checkboxes if box.IsChecked]
        if not chosen:
            forms.alert('Tick at least one category to scan.',
                        title='IFC Parameter Checker')
            return
        self.result = {
            'categories': chosen,
            'selection_only': bool(self.scope_selection.IsChecked),
            'include_type_level': bool(self.include_type_level.IsChecked),
            'create_view': bool(self.create_view.IsChecked),
            'only_issues': bool(self.only_issues.IsChecked),
        }
        self.Close()


# ---------------------------------------------------------------------------
# Results window
# ---------------------------------------------------------------------------
class ResultsWindow(forms.WPFWindow):
    """Summary, legend, filterable table, CSV export and element selection."""

    def __init__(self, xaml_path, audit_result):
        forms.WPFWindow.__init__(self, xaml_path)
        self.audit_result = audit_result
        self._all_rows = list(audit_result.rows)
        self._loading = True

        self.summary_text.Text = '\n'.join(audit_result.summary_lines())
        self._build_legend()

        self.status_filter.Items.Add(ALL)
        for status in [cfg.ST_MISSING, cfg.ST_BLANK, cfg.ST_INVALID,
                       cfg.ST_READONLY, cfg.ST_UNKNOWN, cfg.ST_TYPE,
                       cfg.ST_OK]:
            self.status_filter.Items.Add(status)
        self.status_filter.SelectedIndex = 0

        self.param_filter.Items.Add(ALL)
        for name in cfg.AUDITED_PARAMETERS:
            self.param_filter.Items.Add(name)
        self.param_filter.SelectedIndex = 0

        self.category_filter.Items.Add(ALL)
        for name in sorted(set([r.category for r in self._all_rows if
                                r.category])):
            self.category_filter.Items.Add(name)
        self.category_filter.SelectedIndex = 0

        notes = []
        notes.extend(audit_result.notes)
        notes.extend(audit_result.link_notes)
        for note in audit_result.definition_notes:
            if note.startswith('FLAGGED'):
                notes.append(note)
        self.notes_text.Text = '\n'.join(notes)

        self._loading = False
        self._apply_filter()

    # -- building ---------------------------------------------------------
    def _build_legend(self):
        for color_name, status, meaning in ifc_visualize.legend_rows():
            red, green, blue = cfg.OVERALL_COLORS[status]
            row = StackPanel()
            row.Orientation = Orientation.Horizontal
            row.Margin = Thickness(0, 2, 0, 2)

            swatch = Rectangle()
            swatch.Width = 14
            swatch.Height = 14
            swatch.Fill = SolidColorBrush(
                MediaColor.FromRgb(red, green, blue))
            swatch.Margin = Thickness(0, 0, 6, 0)
            swatch.HorizontalAlignment = HorizontalAlignment.Left
            row.Children.Add(swatch)

            label = TextBlock()
            label.Text = '{} - {}'.format(color_name, status)
            row.Children.Add(label)

            self.legend_panel.Children.Add(row)

    # -- filtering --------------------------------------------------------
    def _apply_filter(self):
        status = self.status_filter.SelectedItem
        param = self.param_filter.SelectedItem
        category = self.category_filter.SelectedItem

        rows = self._all_rows
        if status and status != ALL:
            rows = [r for r in rows if r.status == status]
        if param and param != ALL:
            rows = [r for r in rows if r.param_name == param]
        if category and category != ALL:
            rows = [r for r in rows if r.category == category]

        self.results_grid.ItemsSource = rows
        self.row_count.Text = '{} of {} rows'.format(
            len(rows), len(self._all_rows))

    def filter_changed(self, sender, args):
        if not self._loading:
            self._apply_filter()

    # -- handlers ---------------------------------------------------------
    def _selected_row(self):
        row = self.results_grid.SelectedItem
        if row is None:
            forms.alert('Pick a row in the table first.',
                        title='IFC Parameter Checker')
            return None
        return row

    def select_click(self, sender, args):
        row = self._selected_row()
        if row is None or row.revit_id is None:
            return
        try:
            ids = List[ElementId]()
            ids.Add(row.revit_id)
            uidoc.Selection.SetElementIds(ids)
        except Exception as exc:
            forms.alert('Element {} could not be selected: {}'.format(
                row.element_id, exc), title='IFC Parameter Checker')

    def show_click(self, sender, args):
        row = self._selected_row()
        if row is None or row.revit_id is None:
            return
        try:
            uidoc.ShowElements(row.revit_id)
        except Exception as exc:
            forms.alert('Element {} could not be shown. Revit may need a view '
                        'that contains it: {}'.format(row.element_id, exc),
                        title='IFC Parameter Checker')

    def output_click(self, sender, args):
        rows = self.results_grid.ItemsSource or []
        print_report(self.audit_result, list(rows))

    def export_click(self, sender, args):
        export_csv(self.audit_result)

    def close_click(self, sender, args):
        self.Close()

    def grid_double_click(self, sender, args):
        self.select_click(sender, args)


# ---------------------------------------------------------------------------
# Output window report
# ---------------------------------------------------------------------------
def print_report(audit_result, rows):
    """Same findings in the pyRevit output window, with clickable element ids."""
    output.print_md('# IFC parameter audit')
    output.print_md('**{}** - Revit {} - scanned {}'.format(
        audit_result.doc_title, audit_result.revit_version,
        audit_result.timestamp))
    output.print_md('## Summary')
    for line in audit_result.summary_lines():
        print(line)

    for note in (audit_result.definition_notes +
                 audit_result.validation_notes +
                 audit_result.link_notes + audit_result.notes):
        print(note)

    output.print_md('## Findings ({} rows)'.format(len(rows)))
    table = []
    for row in rows:
        link = row.element_id
        try:
            link = output.linkify(row.revit_id)
        except Exception:
            pass
        table.append([link, row.category, row.type_name, row.param_name,
                      row.location, row.value, row.status, row.reason])
    if table:
        output.print_table(
            table_data=table,
            columns=['Element', 'Category', 'Type', 'Parameter', 'Location',
                     'Value', 'Status', 'Reason'])
    else:
        print('No rows match the current filter.')


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
def export_csv(audit_result):
    """Ask where to save, then write. The user always picks the location."""
    path = forms.save_file(file_ext='csv',
                           default_name=ifc_report.default_filename(
                               audit_result),
                           title='Save IFC parameter audit report')
    if not path:
        return

    notes = ifc_report.write_csv(path, audit_result)
    if notes:
        forms.alert('The report was written to:\n{}\n\nbut with problems:\n'
                    '{}'.format(path, '\n'.join(notes)),
                    title='IFC Parameter Checker')
    else:
        forms.alert('Report saved to:\n{}'.format(path),
                    title='IFC Parameter Checker')


# ---------------------------------------------------------------------------
# Optional visualisation
# ---------------------------------------------------------------------------
def run_visualisation(audit_result, only_issues=False):
    """Confirm, resolve the view name, build the view. Never silent."""
    issues = (audit_result.with_missing + audit_result.with_blank +
              audit_result.with_invalid + audit_result.needs_review)
    visible_count = ifc_visualize.issue_element_count(audit_result) \
        if only_issues else audit_result.total

    include_passing = cfg.COLOR_PASSING_ELEMENTS
    if issues == 0:
        if only_issues:
            forms.alert(
                'Every scanned element has all three IFC parameters present '
                'and valid.\n\n'
                'With "show findings only" ticked the view would be empty, so '
                'none was created. The audit results and the CSV export are '
                'unaffected.',
                title='IFC Parameter Checker')
            return
        answer = forms.alert(
            'Every scanned element has all three IFC parameters present and '
            'valid, so there is nothing to highlight.\n\n'
            'A view would only show the model in green.',
            title='IFC Parameter Checker',
            options=['Create an all-compliant view anyway', 'Skip the view'])
        if answer != 'Create an all-compliant view anyway':
            return
        include_passing = True

    if only_issues and visible_count == 0:
        forms.alert(
            'No element has one of the statuses configured to stay visible '
            '(ifc_config.VISIBLE_WHEN_ONLY_ISSUES), so a findings-only view '
            'would be empty and was not created.',
            title='IFC Parameter Checker')
        return

    if only_issues:
        scope_line = ('{} element(s) with findings would be shown and '
                      'coloured. The {} element(s) that passed would be '
                      'hidden in that view.'.format(
                          visible_count, audit_result.total - visible_count))
    else:
        scope_line = ('{} element(s) would be coloured by their audit '
                      'status.'.format(audit_result.total))

    confirm = forms.alert(
        'Create a colour-coded 3D view of the audit?\n\n'
        '{}\n\n'
        'This adds a new 3D view{} to the model. Existing views, element '
        'parameters, geometry and linked models are not changed.'.format(
            scope_line,
            ' and a drafting view holding the colour key'
            if cfg.CREATE_LEGEND_VIEW else ''),
        title='IFC Parameter Checker',
        options=['Create the view', 'Cancel'])
    if confirm != 'Create the view':
        return

    view_name = cfg.VIEW_NAME_BASE
    reuse_view = None

    if ifc_visualize.view_name_exists(doc, view_name):
        existing = ifc_visualize.find_existing_audit_views(doc)
        choices = ['Create a uniquely named view']
        if existing:
            choices.append('Update the existing audit view')
        choices.append('Cancel')

        answer = forms.alert(
            'A view called "{}" already exists.\n\n'
            'It will not be overwritten unless you choose to update it.'
            .format(view_name),
            title='IFC Parameter Checker', options=choices)

        if answer == 'Create a uniquely named view':
            try:
                view_name = ifc_visualize.unique_view_name(
                    doc, cfg.VIEW_NAME_BASE)
            except Exception as exc:
                forms.alert('A free view name could not be found: {}'.format(
                    exc), title='IFC Parameter Checker')
                return
        elif answer == 'Update the existing audit view':
            reuse_view = existing[0]
            try:
                view_name = reuse_view.Name
            except Exception:
                pass
        else:
            return

    view, notes = ifc_visualize.create_audit_view(
        doc, audit_result, view_name, reuse_view=reuse_view,
        include_passing=include_passing, only_issues=only_issues)

    for note in notes:
        print(note)

    if view is None:
        forms.alert('The 3D view could not be created, and nothing was added '
                    'to the model.\n\n{}\n\nThe audit results and the report '
                    'are unaffected.'.format('\n'.join(notes)),
                    title='IFC Parameter Checker')
        return

    try:
        created_name = view.Name
    except Exception:
        created_name = view_name

    answer = forms.alert(
        'View "{}" is ready.\n\n{}'.format(created_name, '\n'.join(notes)),
        title='IFC Parameter Checker',
        options=['Open the view', 'Stay here'])
    if answer == 'Open the view':
        try:
            uidoc.ActiveView = view
        except Exception as exc:
            forms.alert('The view was created but could not be opened from '
                        'here: {}\nOpen "{}" from the project browser.'.format(
                            exc, created_name),
                        title='IFC Parameter Checker')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if doc is None:
        forms.alert('No active Revit document.', exitscript=True)
    if doc.IsFamilyDocument:
        forms.alert('This tool audits a project, not a family document.',
                    exitscript=True)

    options = ifc_collect.available_categories()
    if not options:
        forms.alert('None of the configured categories exist in this Revit '
                    'version. Check ifc_config.CATEGORIES.', exitscript=True)

    warnings = []
    missing = ifc_collect.missing_categories()
    if missing:
        warnings.append('Not available in this Revit version: {}'.format(
            ', '.join(missing)))
    unresolved = ifc_params.unresolved_definitions()
    if unresolved:
        warnings.append('FLAGGED FOR REVIEW - no built-in definition for: {}. '
                        'Those parameters will be matched by name only.'
                        .format(', '.join(unresolved)))

    try:
        selection_count = len(list(uidoc.Selection.GetElementIds()))
    except Exception:
        selection_count = 0

    scan_window = ScanWindow(os.path.join(_HERE, 'scan.xaml'),
                             options, selection_count, warnings)
    scan_window.ShowDialog()
    settings = scan_window.result
    if not settings:
        return

    chosen = settings['categories']
    if settings['selection_only']:
        elements, notes = ifc_collect.collect_selected_elements(
            doc, uidoc, chosen)
        scope = 'Current selection'
    else:
        elements, notes = ifc_collect.collect_model_elements(doc, chosen)
        scope = 'Whole model, independent of view visibility'

    if not elements:
        forms.alert('No elements to audit.\n\n{}'.format(
            '\n'.join(notes) if notes else
            'The chosen categories hold no instances in this model.'),
            title='IFC Parameter Checker')
        return

    audit_result = None
    with forms.ProgressBar(title='Auditing {value} of {max_value} elements',
                           cancellable=True) as progress_bar:

        def progress(done, total):
            progress_bar.update_progress(done, max_value=total)
            return not progress_bar.cancelled

        audit_result = ifc_audit.build_result(
            doc, elements, settings['include_type_level'], scope,
            [option.label for option in chosen], progress=progress)

    audit_result.notes.extend(notes)

    forms.alert('{}\n\n{}'.format(
        'Scan cancelled - partial results kept.' if audit_result.cancelled
        else 'Scan complete.',
        '\n'.join(audit_result.summary_lines())),
        title='IFC Parameter Checker - summary',
        sub_msg='The detailed table and the CSV export come next.')

    if settings['create_view']:
        run_visualisation(audit_result,
                          only_issues=settings.get('only_issues', False))

    results_window = ResultsWindow(os.path.join(_HERE, 'results.xaml'),
                                   audit_result)
    results_window.ShowDialog()


main()
