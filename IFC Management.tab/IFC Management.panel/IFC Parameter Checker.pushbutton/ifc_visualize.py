# -*- coding: utf-8 -*-
"""
The optional colour-coded 3D audit view.

This is the only module in the tool that writes to the document, and it runs
only when the user ticks the visualisation box and then confirms. Everything
it does is confined to a view it creates itself:

  - a new isometric 3D view, never an existing one
  - per-element graphic overrides inside that view only
  - optionally a drafting view holding the colour key

It never touches element parameters, geometry, materials, families, worksets,
or the graphics of any view the user works in. Elements in linked models
cannot take per-element overrides from a host view at all, which is reported
rather than worked around.

Everything happens in one transaction. If any part fails the transaction is
rolled back, so a failed visualisation leaves the model exactly as it was -
and the audit results it was built from are untouched either way.

Author : Fayaz
Revit  : 2025
PyRevit: 4.8+
"""
from Autodesk.Revit.DB import (Color, ElementId, ElementTypeGroup,
                               FillPatternElement, FillPatternTarget,
                               FilteredElementCollector, OverrideGraphicSettings,
                               TextNote, Transaction, View, View3D,
                               ViewDetailLevel, ViewDrafting, ViewFamily,
                               ViewFamilyType, XYZ)
from System.Collections.Generic import List

import ifc_audit
import ifc_collect
import ifc_config as cfg

# Plain-language colour names for the legend text.
COLOR_NAMES = {
    cfg.OV_MISSING: 'Red',
    cfg.OV_INVALID: 'Magenta',
    cfg.OV_BLANK:   'Orange',
    cfg.OV_UNKNOWN: 'Yellow',
    cfg.OV_OK:      'Green',
}

_DETAIL_LEVELS = {
    'coarse': ViewDetailLevel.Coarse,
    'medium': ViewDetailLevel.Medium,
    'fine': ViewDetailLevel.Fine,
}


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
def _view_names(doc):
    names = set()
    for view in FilteredElementCollector(doc).OfClass(View).ToElements():
        try:
            if view.IsTemplate:
                continue
            names.add(view.Name)
        except Exception:
            continue
    return names


def view_name_exists(doc, name):
    return name in _view_names(doc)


def unique_view_name(doc, base):
    """base, or 'base (2)', 'base (3)' ... until the name is free."""
    existing = _view_names(doc)
    if base not in existing:
        return base
    index = 2
    while index < 1000:
        candidate = '{} ({})'.format(base, index)
        if candidate not in existing:
            return candidate
        index += 1
    raise ValueError('Could not find a free view name based on "{}"'.format(
        base))


def find_existing_audit_views(doc):
    """Previously generated audit views, for the update-existing option."""
    found = []
    for view in FilteredElementCollector(doc).OfClass(View3D).ToElements():
        try:
            if view.IsTemplate:
                continue
            if view.Name == cfg.VIEW_NAME_BASE or \
                    view.Name.startswith(cfg.VIEW_NAME_BASE + ' ('):
                found.append(view)
        except Exception:
            continue
    return found


def _solid_fill_pattern_id(doc):
    """A solid fill pattern, needed for a solid surface colour override.

    Found by inspecting the patterns rather than by name, because the solid
    pattern is called different things in different Revit languages.
    """
    fallback = ElementId.InvalidElementId
    for pattern_element in FilteredElementCollector(doc) \
            .OfClass(FillPatternElement).ToElements():
        try:
            pattern = pattern_element.GetFillPattern()
            if not pattern.IsSolidFill:
                continue
            if pattern.Target == FillPatternTarget.Drafting:
                return pattern_element.Id
            if fallback == ElementId.InvalidElementId:
                fallback = pattern_element.Id
        except Exception:
            continue
    return fallback


def _view_family_type(doc, family):
    for view_family_type in FilteredElementCollector(doc) \
            .OfClass(ViewFamilyType).ToElements():
        try:
            if view_family_type.ViewFamily == family:
                return view_family_type
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------
def _overrides_for(overall_status, solid_fill_id):
    red, green, blue = cfg.OVERALL_COLORS[overall_status]
    color = Color(red, green, blue)

    settings = OverrideGraphicSettings()
    settings.SetProjectionLineColor(color)
    settings.SetCutLineColor(color)

    if solid_fill_id != ElementId.InvalidElementId:
        settings.SetSurfaceForegroundPatternVisible(True)
        settings.SetSurfaceForegroundPatternId(solid_fill_id)
        settings.SetSurfaceForegroundPatternColor(color)
        settings.SetCutForegroundPatternVisible(True)
        settings.SetCutForegroundPatternId(solid_fill_id)
        settings.SetCutForegroundPatternColor(color)

    try:
        settings.SetSurfaceTransparency(int(cfg.VIEW_TRANSPARENCY))
    except Exception:
        pass
    return settings


def _audited_categories(audit_result):
    """{numeric category id: ElementId} for the categories actually audited.

    The real ElementId objects are kept rather than rebuilt from integers,
    which avoids the ElementId(int) / ElementId(Int64) constructor change.
    """
    found = {}
    for element_result in audit_result.elements:
        try:
            category_id = element_result.element.Category.Id
        except Exception:
            continue
        key = ifc_collect.id_value(category_id)
        if key is not None and key not in found:
            found[key] = category_id
    return found


def _configure_view(doc, view, audit_result, notes):
    """Name-independent view setup: template, style, detail, visibility."""
    try:
        if view.ViewTemplateId != ElementId.InvalidElementId:
            view.ViewTemplateId = ElementId.InvalidElementId
            notes.append('A view template was applied to the new view by '
                         'default and has been removed from it, so the audit '
                         'overrides are not blocked.')
    except Exception as exc:
        notes.append('View template could not be cleared: {}. If overrides '
                     'do not appear, the template is the reason.'.format(exc))

    try:
        if not view.AreGraphicsOverridesAllowed():
            notes.append('This view reports that graphic overrides are not '
                         'allowed. The colours below may not appear; check '
                         'for a view template or a design option.')
    except Exception:
        pass

    try:
        from Autodesk.Revit.DB import DisplayStyle
        view.DisplayStyle = DisplayStyle.ShadingWithEdges
    except Exception as exc:
        notes.append('Display style could not be set to shaded: {}'.format(exc))

    try:
        view.DetailLevel = _DETAIL_LEVELS.get(
            str(cfg.VIEW_DETAIL_LEVEL).lower(), ViewDetailLevel.Medium)
    except Exception as exc:
        notes.append('Detail level could not be set: {}'.format(exc))

    audited = _audited_categories(audit_result)

    # Make sure nothing audited is hidden by category in this view.
    for category_id in audited.values():
        try:
            view.SetCategoryHidden(category_id, False)
        except Exception:
            continue

    if cfg.HIDE_UNRELATED_CATEGORIES:
        hidden = 0
        try:
            categories = doc.Settings.Categories
        except Exception:
            categories = []
        for category in categories:
            try:
                if ifc_collect.id_value(category.Id) in audited:
                    continue
                if not category.get_AllowsVisibilityControl(view):
                    continue
                if not view.CanCategoryBeHidden(category.Id):
                    continue
                view.SetCategoryHidden(category.Id, True)
                hidden += 1
            except Exception:
                continue
        if hidden:
            notes.append('{} categories that were not audited are hidden in '
                         'this view only.'.format(hidden))


def _clear_view_overrides(doc, view, audit_result, notes):
    """Reset what this dedicated view owns, before rebuilding it.

    Both the overrides and the per-element hiding are reset, and only in this
    view - no other view's graphics or visibility are affected.

    The elements hidden by a previous run matter here: a filtered collector
    returns only what is *visible* in the view, so anything an earlier run
    hid would be invisible to it and stay hidden forever. The audit's own
    element ids are used for the unhide pass instead.
    """
    blank = OverrideGraphicSettings()

    target_ids = {}
    try:
        for element_id in FilteredElementCollector(doc, view.Id) \
                .WhereElementIsNotElementType().ToElementIds():
            target_ids[ifc_collect.id_value(element_id)] = element_id
    except Exception as exc:
        notes.append('Elements visible in the existing view could not be '
                     'listed: {}'.format(exc))

    hidden_now = List[ElementId]()
    for element_result in audit_result.elements:
        try:
            element = element_result.element
            element_id = element.Id
        except Exception:
            continue
        target_ids[ifc_collect.id_value(element_id)] = element_id
        try:
            if element.IsHidden(view):
                hidden_now.Add(element_id)
        except Exception:
            continue

    cleared = 0
    for element_id in target_ids.values():
        try:
            view.SetElementOverrides(element_id, blank)
            cleared += 1
        except Exception:
            continue

    unhidden = 0
    if hidden_now.Count:
        try:
            view.UnhideElements(hidden_now)
            unhidden = hidden_now.Count
        except Exception:
            for element_id in hidden_now:
                single = List[ElementId]()
                single.Add(element_id)
                try:
                    view.UnhideElements(single)
                    unhidden += 1
                except Exception:
                    continue

    notes.append('Rebuilt an existing audit view: overrides reset on {} '
                 'element(s) and {} previously hidden element(s) unhidden, '
                 'in that view only.'.format(cleared, unhidden))


def _hide_passing_elements(doc, view, audit_result, notes):
    """Hide everything that is not a finding, in this view only.

    Which statuses survive is cfg.VISIBLE_WHEN_ONLY_ISSUES. Hiding is
    per-element and view-specific: nothing is deleted, moved or changed, and
    no other view is affected.
    """
    keep = set(cfg.VISIBLE_WHEN_ONLY_ISSUES)
    to_hide = List[ElementId]()
    for element_result in audit_result.elements:
        if element_result.overall in keep:
            continue
        try:
            to_hide.Add(element_result.element.Id)
        except Exception:
            continue

    if not to_hide.Count:
        notes.append('Show-only-issues was requested, but every scanned '
                     'element is a finding, so nothing was hidden.')
        return 0

    hidden = 0
    try:
        view.HideElements(to_hide)
        hidden = to_hide.Count
    except Exception:
        # HideElements rejects the whole collection if any one element cannot
        # be hidden, so fall back to one at a time and report the shortfall.
        failed = 0
        for element_id in to_hide:
            single = List[ElementId]()
            single.Add(element_id)
            try:
                view.HideElements(single)
                hidden += 1
            except Exception:
                failed += 1
        if failed:
            notes.append('{} element(s) that passed the audit could not be '
                         'hidden and remain visible - usually pinned, group '
                         'or link contents.'.format(failed))

    notes.append('Showing findings only: {} element(s) that passed the audit '
                 'are hidden in this view only.'.format(hidden))
    return hidden


def _create_legend_view(doc, audit_result, view_name, notes,
                        only_issues=False):
    """A drafting view holding the colour key, as an accompanying legend.

    A drafting view is used rather than a Revit legend view because the API
    cannot create legend views - they can only be duplicated.
    """
    view_family_type = _view_family_type(doc, ViewFamily.Drafting)
    if view_family_type is None:
        notes.append('No drafting view type found, so the colour key view was '
                     'not created. The legend is shown in the results window '
                     'and written into the CSV report.')
        return None

    try:
        legend = ViewDrafting.Create(doc, view_family_type.Id)
    except Exception as exc:
        notes.append('Colour key view could not be created: {}'.format(exc))
        return None

    try:
        legend.Name = unique_view_name(doc, cfg.LEGEND_VIEW_NAME)
    except Exception:
        pass

    text_type_id = ElementId.InvalidElementId
    try:
        text_type_id = doc.GetDefaultElementTypeId(
            ElementTypeGroup.TextNoteType)
    except Exception:
        text_type_id = ElementId.InvalidElementId

    if text_type_id == ElementId.InvalidElementId:
        notes.append('No default text type is set in this project, so the '
                     'colour key view was left empty.')
        return legend

    lines = ['IFC parameter audit - colour key',
             'For 3D view: {}'.format(view_name),
             'Document: {}'.format(audit_result.doc_title),
             'Scanned: {}'.format(audit_result.timestamp),
             '']
    shown = set(cfg.VISIBLE_WHEN_ONLY_ISSUES) if only_issues else None
    for overall in cfg.OVERALL_PRIORITY:
        red, green, blue = cfg.OVERALL_COLORS[overall]
        suffix = ''
        if shown is not None and overall not in shown:
            suffix = '   [HIDDEN in this view]'
        lines.append('{} (RGB {},{},{}) = {} - {}{}'.format(
            COLOR_NAMES.get(overall, ''), red, green, blue, overall,
            cfg.OVERALL_MEANING.get(overall, ''), suffix))
    lines.append('')
    if only_issues:
        lines.append('This view shows findings only. Elements marked HIDDEN '
                     'above are hidden in it, so the view is not a complete '
                     'picture of the model.')
        lines.append('')
    lines.append('Where an element has more than one issue it takes the '
                 'colour of its most severe one, in the order above.')
    lines.append('Per-parameter detail is in the CSV report, not in this view.')

    offset = 0.0
    for line in lines:
        if line:
            try:
                TextNote.Create(doc, legend.Id, XYZ(0, -offset, 0), line,
                                text_type_id)
            except Exception as exc:
                notes.append('A legend line could not be written: {}'.format(
                    exc))
                break
        offset += 0.30

    return legend


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def issue_element_count(audit_result):
    """How many elements would remain visible with show-only-issues on."""
    keep = set(cfg.VISIBLE_WHEN_ONLY_ISSUES)
    return len([e for e in audit_result.elements if e.overall in keep])


def create_audit_view(doc, audit_result, view_name, reuse_view=None,
                      include_passing=None, only_issues=False):
    """Build the colour-coded audit view.

    reuse_view        an existing audit View3D to rebuild, or None to create
                      a new one
    include_passing   override cfg.COLOR_PASSING_ELEMENTS for this run
    only_issues       hide the elements that passed the audit, so the view
                      shows findings only

    Returns (view_or_None, notes). On failure the transaction is rolled back
    and the reason is in the notes; the audit results are never affected.
    """
    notes = []
    color_passing = cfg.COLOR_PASSING_ELEMENTS if include_passing is None \
        else include_passing
    if only_issues:
        # Passing elements are about to be hidden, so colouring them green
        # would only cost time.
        color_passing = False

    grouped = ifc_audit.element_ids_by_status(audit_result)
    if not grouped:
        return None, ['The audit produced no elements to colour.']

    if audit_result.link_notes:
        notes.append('Linked models are present. Elements inside a Revit link '
                     'cannot take per-element overrides from a host view, so '
                     'they are not coloured and were not audited.')

    view_family_type = _view_family_type(doc, ViewFamily.ThreeDimensional)
    if view_family_type is None and reuse_view is None:
        return None, ['No 3D view type exists in this project, so an audit '
                      'view cannot be created.']

    transaction = Transaction(doc, 'IFC Audit - create colour-coded 3D view')
    view = None
    try:
        transaction.Start()

        if reuse_view is not None:
            view = reuse_view
            _clear_view_overrides(doc, view, audit_result, notes)
        else:
            view = View3D.CreateIsometric(doc, view_family_type.Id)
            try:
                view.Name = view_name
            except Exception as exc:
                notes.append('View created but could not be named "{}": {}. '
                             'It keeps its default name.'.format(
                                 view_name, exc))

        _configure_view(doc, view, audit_result, notes)

        solid_fill_id = _solid_fill_pattern_id(doc)
        if solid_fill_id == ElementId.InvalidElementId:
            notes.append('No solid fill pattern was found in this project, so '
                         'elements are coloured by line colour only, without '
                         'solid surfaces.')

        coloured = 0
        failed = 0
        for overall, element_ids in grouped.items():
            if overall == cfg.OV_OK and not color_passing:
                continue
            settings = _overrides_for(overall, solid_fill_id)
            for element_id in element_ids:
                try:
                    view.SetElementOverrides(element_id, settings)
                    coloured += 1
                except Exception:
                    failed += 1

        notes.append('{} element(s) coloured in this view.'.format(coloured))
        if failed:
            notes.append('{} element(s) would not take an override - usually '
                         'elements that cannot be overridden individually, '
                         'such as parts of a group or a link.'.format(failed))
        if only_issues:
            _hide_passing_elements(doc, view, audit_result, notes)
        elif not color_passing:
            notes.append('Elements that passed the audit were left with their '
                         'normal graphics.')

        legend = None
        if cfg.CREATE_LEGEND_VIEW:
            try:
                display_name = view.Name
            except Exception:
                display_name = view_name
            legend = _create_legend_view(doc, audit_result, display_name,
                                         notes, only_issues=only_issues)
            if legend is not None:
                try:
                    notes.append('Colour key written to drafting view '
                                 '"{}".'.format(legend.Name))
                except Exception:
                    pass

        transaction.Commit()

    except Exception as exc:
        try:
            if transaction.HasStarted():
                transaction.RollBack()
        except Exception:
            pass
        notes.append('Visualisation failed and was rolled back, so nothing '
                     'was added to the model: {}'.format(exc))
        notes.append('The audit results and the CSV report are unaffected.')
        return None, notes

    return view, notes


def legend_rows():
    """(colour name, status, meaning) for the results window legend."""
    return [(COLOR_NAMES.get(overall, ''), overall,
             cfg.OVERALL_MEANING.get(overall, ''))
            for overall in cfg.OVERALL_PRIORITY]
