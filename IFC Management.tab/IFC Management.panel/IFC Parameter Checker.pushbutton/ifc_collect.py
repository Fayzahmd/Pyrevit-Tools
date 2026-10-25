# -*- coding: utf-8 -*-
"""
Collection of the structural elements to audit.

Collection is deliberately view-independent: FilteredElementCollector is given
the document, never a view, so elements hidden in the active view, on a closed
worksheet-style view or sitting behind a view template are still audited.

Element types are never collected as audit subjects - only instances are - but
each instance's type is inspected separately when a value is type-bound.

Linked documents are reported but never opened for modification; their
elements belong to another document and are listed as a limitation instead of
being audited through the host.

Author : Fayaz
Revit  : 2025
PyRevit: 4.8+
"""
from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, ElementType,
                               FilteredElementCollector, RevitLinkInstance)

import ifc_config as cfg


def id_value(element_id):
    """The numeric value of an ElementId across Revit versions.

    Revit 2024 introduced ElementId.Value (Int64) and deprecated
    IntegerValue, which still works in 2025 but will not forever.
    """
    if element_id is None:
        return None
    try:
        return int(element_id.Value)
    except Exception:
        pass
    try:
        return int(element_id.IntegerValue)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Extra instance tests referenced by ifc_config.CATEGORIES['filter']
# ---------------------------------------------------------------------------
def _flag_is_set(element, bip_name):
    """True when a yes/no built-in parameter is ticked on this element."""
    bip = getattr(BuiltInParameter, bip_name, None)
    if bip is None:
        return None
    try:
        param = element.get_Parameter(bip)
    except Exception:
        return None
    if param is None:
        return None
    try:
        return param.AsInteger() == 1
    except Exception:
        return None


def _wall_is_structural(element):
    return _flag_is_set(element, 'WALL_STRUCTURAL_SIGNIFICANT')


def _floor_is_structural(element):
    return _flag_is_set(element, 'FLOOR_PARAM_IS_STRUCTURAL')


STRUCTURAL_FILTERS = {
    'wall_structural': _wall_is_structural,
    'floor_structural': _floor_is_structural,
}


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
class CategoryOption(object):
    """One tickable row in the scan window."""

    def __init__(self, entry, bic):
        self.label = entry['label']
        self.bic_name = entry['bic']
        self.bic = bic
        self.filter_name = entry['filter']
        self.default = entry['default']
        self.count = None       # filled in by count_elements

    @property
    def filter_func(self):
        if not self.filter_name:
            return None
        return STRUCTURAL_FILTERS.get(self.filter_name)


def available_categories():
    """The configured categories that exist in this Revit version."""
    options = []
    for entry in cfg.CATEGORIES:
        bic = getattr(BuiltInCategory, entry['bic'], None)
        if bic is None:
            continue
        options.append(CategoryOption(entry, bic))
    return options


def missing_categories():
    """Configured categories this Revit version does not have."""
    return [e['label'] for e in cfg.CATEGORIES
            if getattr(BuiltInCategory, e['bic'], None) is None]


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------
def _element_key(element):
    try:
        key = id_value(element.Id)
    except Exception:
        key = None
    return key if key is not None else id(element)


def _passes(option, element, notes):
    """Apply a category's extra structural test.

    A test that cannot be evaluated keeps the element and records a note -
    dropping it silently would under-report the audit.
    """
    func = option.filter_func
    if func is None:
        return True
    try:
        verdict = func(element)
    except Exception as exc:
        notes.append('{}: structural test failed on element {} ({}), element '
                     'audited anyway'.format(option.label, element.Id, exc))
        return True
    if verdict is None:
        notes.append('{}: structural flag not readable on element {}, '
                     'element audited anyway'.format(option.label, element.Id))
        return True
    return verdict


def collect_model_elements(doc, options):
    """Every instance in the chosen categories, de-duplicated.

    Returns (elements, notes). Overlapping category entries - "Structural
    Walls" and "Walls (all)" both map to OST_Walls - cannot produce the same
    element twice.
    """
    elements = []
    seen = set()
    notes = []

    for option in options:
        try:
            collected = FilteredElementCollector(doc) \
                .OfCategory(option.bic) \
                .WhereElementIsNotElementType() \
                .ToElements()
        except Exception as exc:
            notes.append('{}: collection failed ({}), category skipped'.format(
                option.label, exc))
            continue

        for element in collected:
            key = _element_key(element)
            if key in seen:
                continue
            if not _passes(option, element, notes):
                continue
            seen.add(key)
            elements.append(element)

    return elements, notes


def collect_selected_elements(doc, uidoc, options):
    """The current selection, narrowed to the chosen categories.

    Returns (elements, notes). Anything selected that falls outside the chosen
    categories is counted in the notes rather than dropped in silence.
    """
    elements = []
    notes = []
    wanted = {}
    for option in options:
        wanted.setdefault(int(option.bic), []).append(option)

    try:
        ids = list(uidoc.Selection.GetElementIds())
    except Exception as exc:
        return [], ['Selection could not be read: {}'.format(exc)]

    if not ids:
        return [], ['Nothing is selected in Revit.']

    skipped_category = 0
    skipped_filter = 0
    skipped_types = 0
    seen = set()

    for element_id in ids:
        try:
            element = doc.GetElement(element_id)
        except Exception as exc:
            notes.append('Selected element {} unreadable: {}'.format(
                element_id, exc))
            continue
        if element is None:
            continue

        # An element type can be selected in a schedule or the browser; the
        # audit subject is always an instance.
        if isinstance(element, ElementType):
            skipped_types += 1
            continue

        try:
            if element.Category is None:
                skipped_category += 1
                continue
            category_id = id_value(element.Category.Id)
        except Exception:
            skipped_category += 1
            continue
        if category_id is None:
            skipped_category += 1
            continue

        matching = wanted.get(category_id)
        if not matching:
            skipped_category += 1
            continue

        if not any(_passes(option, element, notes) for option in matching):
            skipped_filter += 1
            continue

        key = _element_key(element)
        if key in seen:
            continue
        seen.add(key)
        elements.append(element)

    if skipped_category:
        notes.append('{} selected element(s) ignored: outside the chosen '
                     'categories.'.format(skipped_category))
    if skipped_filter:
        notes.append('{} selected element(s) ignored: did not pass the '
                     'structural test for their category.'.format(
                         skipped_filter))
    if skipped_types:
        notes.append('{} selected element type(s) ignored: the audit runs on '
                     'instances.'.format(skipped_types))

    return elements, notes


# ---------------------------------------------------------------------------
# Linked models
# ---------------------------------------------------------------------------
def linked_model_report(doc):
    """Describe linked documents, which this tool never audits or modifies."""
    lines = []
    try:
        links = list(FilteredElementCollector(doc)
                     .OfClass(RevitLinkInstance).ToElements())
    except Exception as exc:
        return ['Linked models could not be listed: {}'.format(exc)]

    if not links:
        return []

    names = []
    for link in links:
        try:
            link_doc = link.GetLinkDocument()
            title = link_doc.Title if link_doc is not None else None
        except Exception:
            title = None
        if title is None:
            try:
                title = '{} (not loaded)'.format(link.Name)
            except Exception:
                title = 'unnamed link'
        names.append(title)

    lines.append('{} linked Revit model(s) present and NOT audited: {}'.format(
        len(links), '; '.join(sorted(set(names)))))
    lines.append('IFC parameters of linked elements belong to the linked '
                 'document and cannot be read or reported reliably through '
                 'the host model. Audit each linked model in its own session.')
    return lines
