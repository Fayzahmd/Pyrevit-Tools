# -*- coding: utf-8 -*-
"""
Isolates every element in the active view whose parameter meets a condition
you build in one window: categories, parameter, condition and value.

Author : Fayaz
Revit  : 2024 / 2025 / 2026
PyRevit: 4.8+
"""
__title__   = 'Isolate by\nParameter'
__doc__     = ('Pick categories, a parameter, a condition and a value. '
               'Isolates every matching element in the active view.')

# -- Revit API imports -----------------------------------------------------
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ElementId,
    StorageType,
    Transaction,
    UnitUtils,
)
from pyrevit import forms, script
from System.Collections.Generic import List
from System.Windows import Thickness
from System.Windows.Controls import CheckBox
from System.Windows.Media import Brushes
import os

output = script.get_output()
doc    = __revit__.ActiveUIDocument.Document
active_view = doc.ActiveView

# ── 1. Collect elements visible in the active view ─────────────────────────
view_elements = [
    e for e in FilteredElementCollector(doc, active_view.Id)
    .WhereElementIsNotElementType()
    .ToElements()
    if e.Category is not None
]
if not view_elements:
    forms.alert('No elements found in the active view.', exitscript=True)

# ── 2. One window: categories, parameter, condition, value ─────────────────
by_category = {}
for e in view_elements:
    by_category.setdefault(e.Category.Name, []).append(e)

type_cache = {}

def get_type(el):
    tid = el.GetTypeId()
    if tid == ElementId.InvalidElementId:
        return None
    if tid not in type_cache:
        type_cache[tid] = doc.GetElement(tid)
    return type_cache[tid]


TEXT_OPS    = ['equals', 'does not equal', 'contains', 'does not contain',
               'begins with', 'ends with']
NUMBER_OPS  = ['is greater than', 'is less than',
               'is greater than or equal to', 'is less than or equal to']
NOVALUE_OPS = ['has a value', 'has no value']


def to_number(text):
    """'3000', '3000 mm' or '3,000' -> 3000.0; None if not a number."""
    try:
        return float(text.strip().split(' ')[0].replace(',', ''))
    except (ValueError, IndexError, AttributeError):
        return None


class IsolateWindow(forms.WPFWindow):
    def __init__(self, xaml_file):
        forms.WPFWindow.__init__(self, xaml_file)
        self.result = None
        self._param_cache = {}     # category name -> set of parameter names
        self._param_names = set()
        self._bulk = False
        self._checkboxes = []

        for name in sorted(by_category):
            cb = CheckBox()
            cb.Content = '{} ({})'.format(name, len(by_category[name]))
            cb.Tag = name
            cb.Margin = Thickness(0, 2, 0, 2)
            cb.Checked += self.categories_changed
            cb.Unchecked += self.categories_changed
            self.category_panel.Children.Add(cb)
            self._checkboxes.append(cb)

        for op in TEXT_OPS + NUMBER_OPS + NOVALUE_OPS:
            self.operator_cb.Items.Add(op)
        self.operator_cb.SelectedIndex = 0
        self.refresh_params()

    # -- helpers -----------------------------------------------------------
    def checked_categories(self):
        return [cb.Tag for cb in self._checkboxes if cb.IsChecked]

    def params_for(self, cat_name):
        # collected once per category, the first time it is ticked
        if cat_name not in self._param_cache:
            names = set()
            for el in by_category[cat_name]:
                for p in el.Parameters:
                    names.add(p.Definition.Name)
                el_type = get_type(el)
                if el_type is not None:
                    for p in el_type.Parameters:
                        names.add(p.Definition.Name)
            self._param_cache[cat_name] = names
        return self._param_cache[cat_name]

    def refresh_params(self):
        cats = self.checked_categories()
        names = set()
        for c in cats:
            names |= self.params_for(c)
        self._param_names = names

        current = self.param_cb.Text
        self.param_cb.ItemsSource = sorted(names)
        self.param_cb.Text = current if current in names else ''

        count = sum(len(by_category[c]) for c in cats)
        self.show_status('{} element(s) in {} categor{} picked.'.format(
            count, len(cats), 'y' if len(cats) == 1 else 'ies'))

    def show_status(self, text, error=False):
        self.status_tb.Text = text
        self.status_tb.Foreground = Brushes.Firebrick if error else Brushes.DimGray

    def set_all(self, state):
        self._bulk = True
        for cb in self._checkboxes:
            cb.IsChecked = state
        self._bulk = False
        self.refresh_params()

    # -- events ------------------------------------------------------------
    def categories_changed(self, sender, args):
        if not self._bulk:
            self.refresh_params()

    def select_all_click(self, sender, args):
        self.set_all(True)

    def select_none_click(self, sender, args):
        self.set_all(False)

    def operator_changed(self, sender, args):
        self.value_tb.IsEnabled = self.operator_cb.SelectedItem not in NOVALUE_OPS

    def cancel_click(self, sender, args):
        self.Close()

    def isolate_click(self, sender, args):
        cats = self.checked_categories()
        param = (self.param_cb.Text or '').strip()
        op = self.operator_cb.SelectedItem
        val = (self.value_tb.Text or '').strip()

        if not cats:
            return self.show_status('Tick at least one category.', True)
        if param not in self._param_names:
            return self.show_status('Pick a parameter from the list.', True)
        if op not in NOVALUE_OPS:
            if not val:
                return self.show_status(
                    'Enter a value (or use "has no value").', True)
            if op in NUMBER_OPS and to_number(val) is None:
                return self.show_status('"{}" is not a number.'.format(val), True)
        else:
            val = ''

        self.result = (cats, param, op, val)
        self.Close()


window = IsolateWindow(os.path.join(os.path.dirname(__file__), 'ui.xaml'))
window.show_dialog()
if not window.result:
    script.exit()

picked_cats, param_name, operator, value = window.result
elements = []
for name in picked_cats:
    elements.extend(by_category[name])

value_lower = value.lower()
value_num   = to_number(value)

# ── 3. Evaluate every element ──────────────────────────────────────────────
TOLERANCE = 1e-6


def find_param(el):
    p = el.LookupParameter(param_name)
    if p is None:
        el_type = get_type(el)
        if el_type is not None:
            p = el_type.LookupParameter(param_name)
    return p


def param_text(p):
    if p.StorageType == StorageType.String:
        return p.AsString() or ''
    return p.AsValueString() or ''


def param_number(p):
    """Numeric value in project display units, or None."""
    if p.StorageType == StorageType.Double:
        try:
            return UnitUtils.ConvertFromInternalUnits(p.AsDouble(), p.GetUnitTypeId())
        except Exception:
            return p.AsDouble()     # unitless double
    if p.StorageType == StorageType.Integer:
        return float(p.AsInteger())
    return to_number(param_text(p))


def is_equal(p):
    if param_text(p).strip().lower() == value_lower:
        return True
    num = param_number(p)
    return (num is not None and value_num is not None
            and abs(num - value_num) < TOLERANCE)


def matches(p):
    text = param_text(p).strip()
    if operator == 'has a value':
        return p.HasValue and text != ''
    if operator == 'has no value':
        return not p.HasValue or text == ''
    if operator == 'equals':
        return is_equal(p)
    if operator == 'does not equal':
        return not is_equal(p)

    text = text.lower()
    if operator == 'contains':
        return value_lower in text
    if operator == 'does not contain':
        return value_lower not in text
    if operator == 'begins with':
        return text.startswith(value_lower)
    if operator == 'ends with':
        return text.endswith(value_lower)

    num = param_number(p)
    if num is None:
        return False
    if operator == 'is greater than':
        return num > value_num + TOLERANCE
    if operator == 'is less than':
        return num < value_num - TOLERANCE
    if operator == 'is greater than or equal to':
        return num >= value_num - TOLERANCE
    if operator == 'is less than or equal to':
        return num <= value_num + TOLERANCE
    return False


matched_ids = []
missing = 0
for e in elements:
    p = find_param(e)
    if p is None:
        missing += 1        # elements without the parameter never match
        continue
    if matches(p):
        matched_ids.append(e.Id)

condition = '"{}" {} "{}"'.format(param_name, operator, value) if value \
    else '"{}" {}'.format(param_name, operator)

if not matched_ids:
    forms.alert('No elements match {}.'.format(condition), exitscript=True)

# ── 4. Isolate in the active view ──────────────────────────────────────────
t = Transaction(doc, 'Isolate Elements by Parameter')
try:
    t.Start()
    active_view.IsolateElementsTemporary(List[ElementId](matched_ids))
    t.Commit()
except Exception as e:
    if t.HasStarted():
        t.RollBack()
    forms.alert('Could not isolate elements in this view: {}'.format(e), exitscript=True)

output.print_md(
    '**Done.** Isolated **{}** of {} element(s) where {}.'.format(
        len(matched_ids), len(elements), condition)
)
if missing:
    output.print_md(
        '{} element(s) in the picked categories do not have this parameter '
        'and were skipped.'.format(missing)
    )
