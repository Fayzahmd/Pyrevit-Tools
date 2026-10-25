# -*- coding: utf-8 -*-
"""
Revit view filters for the audit view, so it stays correct on its own.

A per-element override is a photograph: it records what the audit found and
is wrong the moment somebody fills a parameter in. A view filter is a rule
Revit re-evaluates continuously, so an element leaves the view as soon as its
IFC parameters are set, with no re-run of the audit.

All six IFC parameters were confirmed filterable in Revit 2025 via
ParameterFilterUtilities.GetFilterableParametersInCommon, which is what makes
this possible at all.

WHAT A FILTER CAN AND CANNOT SAY
--------------------------------
A filter is a rule over parameter values, so it can express presence and
absence precisely, and it cannot express validity:

  blank / not set       HasNoValue              yes
  populated             HasValue                yes
  predefined type set
    with no entity      HasValue AND HasNoValue yes
  invalid value         -                       NO

An invalid value - IfcColumn with SHEAR, say - has both parameters populated,
so a presence rule counts it as complete. Expressing it would need a
not-equals rule for every member of every per-entity enumeration, roughly
twenty filters of ten rules each, regenerated whenever Revit's enums change.

So in filter mode the view under-reports: it shows what is unfilled, not what
is wrong. The audit table and the CSV remain the authoritative record, and
ifc_visualize warns with a count when the audit found invalid or unverifiable
elements that the view cannot represent.

THERE ARE TWO KINDS OF BLANK
----------------------------
Also measured in Revit 2025. A text parameter that was never set and one that
was set and then cleared are *not* the same thing to a filter rule:

    never set            HasValue=False  HasNoValue=True   Equals('')=False
    set, then cleared    HasValue=True   HasNoValue=False  Equals('')=True

So HasNoValue alone misses every parameter a user typed into and then emptied,
which is exactly the parameter a QA view most needs to catch. Each presence
test below therefore covers both forms:

    not set   =  HasNoValue  OR  Equals('')
    set       =  HasValue   AND  NotEquals('')

(Revit normalises a whitespace-only value to '', so `Set(' ')` lands in the
same bucket, matching how ifc_audit treats whitespace as blank.)

TYPE PARAMETERS DO NOT WORK IN THESE RULES
------------------------------------------
Measured in Revit 2025, not assumed. A rule on IFC_EXPORT_ELEMENT_TYPE_AS
evaluated against an *instance* returns False for HasValue **and** False for
HasNoValue - the parameter is simply not on the instance, so neither rule
matches:

    IFC_EXPORT_ELEMENT_TYPE_AS   HasValue=False  HasNoValue=False
    IFC_EXPORT_ELEMENT_AS        HasValue=False  HasNoValue=True

The consequence is severe and silent. An earlier version of this module built
"not set" as `instance HasNoValue AND type HasNoValue`, which is therefore
False for every instance, so those filters matched nothing at all and the view
came up empty while looking perfectly well formed.

So the rules here test the instance parameter only. Elements whose value
legitimately lives on the type would then show up as findings, which is the
false positive the audit itself is careful to avoid - so ifc_visualize deals
with them separately, using the audit's own knowledge of which elements those
are rather than a filter rule.

NOTE ON SCOPE
-------------
A ParameterFilterElement is a project-wide element - it appears in the filter
list of every view - even though it is only *applied* to the view this tool
creates. That is unavoidable; Revit has no view-local filter. Existing filters
are never modified: if a name is taken, a uniquely named filter is created
instead.

Author : Fayaz
Revit  : 2025
PyRevit: 4.8+
"""
from Autodesk.Revit.DB import (BuiltInParameter, ElementId,
                               ElementParameterFilter, FilteredElementCollector,
                               LogicalAndFilter, LogicalOrFilter,
                               ParameterFilterElement,
                               ParameterFilterRuleFactory,
                               ParameterFilterUtilities)
from System.Collections.Generic import List

import ifc_collect
import ifc_config as cfg


def _pid(name):
    """ElementId of a BuiltInParameter, or None if this API lacks it."""
    bip = getattr(BuiltInParameter, name, None)
    return ElementId(bip) if bip is not None else None


# The instance parameters the rules test. Their type counterparts
# (IFC_EXPORT_ELEMENT_TYPE_AS, IFC_EXPORT_PREDEFINEDTYPE_TYPE) are
# deliberately absent: see TYPE PARAMETERS above.
EXPORT_AS = 'IFC_EXPORT_ELEMENT_AS'
PREDEF = 'IFC_EXPORT_PREDEFINEDTYPE'

# Every parameter the filters touch; a category must support all of them.
REQUIRED_PARAMETERS = [EXPORT_AS, PREDEF]


def required_parameter_ids():
    ids = []
    for name in REQUIRED_PARAMETERS:
        pid = _pid(name)
        if pid is not None:
            ids.append(pid)
    return ids


def unsupported_parameters():
    """Audited parameters with no built-in id in this API."""
    return [n for n in REQUIRED_PARAMETERS if _pid(n) is None]


# ---------------------------------------------------------------------------
# Rule building
# ---------------------------------------------------------------------------
def _has_value(name):
    pid = _pid(name)
    if pid is None:
        return None
    return ElementParameterFilter(
        ParameterFilterRuleFactory.CreateHasValueParameterRule(pid))


def _has_no_value(name):
    pid = _pid(name)
    if pid is None:
        return None
    return ElementParameterFilter(
        ParameterFilterRuleFactory.CreateHasNoValueParameterRule(pid))


def _fold(combiner, filters):
    """Combine filters pairwise, left to right.

    A rule whose parameter is absent from this API comes back as None and is
    dropped, so a partially available parameter set still yields a usable
    filter rather than no filter at all.
    """
    present = [f for f in filters if f is not None]
    if not present:
        return None
    result = present[0]
    for element_filter in present[1:]:
        result = combiner(result, element_filter)
    return result


def _any_of(filters):
    return _fold(LogicalOrFilter, filters)


def _all_of(filters):
    return _fold(LogicalAndFilter, filters)


def _equals(name, value):
    pid = _pid(name)
    if pid is None:
        return None
    return ElementParameterFilter(
        ParameterFilterRuleFactory.CreateEqualsRule(pid, value))


def _not_equals(name, value):
    pid = _pid(name)
    if pid is None:
        return None
    return ElementParameterFilter(
        ParameterFilterRuleFactory.CreateNotEqualsRule(pid, value))


def _is_set(name):
    """The instance parameter holds a real value.

    Present AND not an empty string - a cleared parameter still reports
    HasValue, see THERE ARE TWO KINDS OF BLANK in the module docstring.
    """
    return _all_of([_has_value(name), _not_equals(name, '')])


def _is_not_set(name):
    """The instance parameter is empty, in either of Revit's two forms.

    Instance only. Combining this with a rule on the type counterpart makes
    the whole filter unmatchable - see TYPE PARAMETERS in the module docstring.
    """
    return _any_of([_has_no_value(name), _equals(name, '')])


# ---------------------------------------------------------------------------
# The filters this tool builds
# ---------------------------------------------------------------------------
# name       suffix appended to cfg.FILTER_NAME_PREFIX
# status     which cfg.OV_* colour to give it, or None for no override
# visible    False means matching elements are hidden in the view
# build      returns the ElementFilter, or None if it cannot be built
FILTER_SPECS = [
    {
        'key': 'complete',
        'suffix': 'IFC parameters complete',
        'status': None,
        'visible': False,
        'build': lambda: _all_of([_is_set(EXPORT_AS), _is_set(PREDEF)]),
        'note': 'Both parameters set on the instance. Hidden - this is the '
                'rule that removes an element from the view as soon as it is '
                'fixed.',
    },
    {
        'key': 'predef_without_entity',
        'suffix': 'Predefined Type set but no entity',
        'status': cfg.OV_UNKNOWN,
        'visible': True,
        'build': lambda: _all_of([_is_set(PREDEF), _is_not_set(EXPORT_AS)]),
        'note': 'A predefined type with no "Export to IFC As" to belong to, '
                'so the exporter will not honour it.',
    },
    {
        'key': 'export_as_missing',
        'suffix': 'Export to IFC As not set',
        'status': cfg.OV_BLANK,
        'visible': True,
        'build': lambda: _is_not_set(EXPORT_AS),
        'note': 'No IFC entity on the instance.',
    },
    {
        'key': 'predef_missing',
        'suffix': 'IFC Predefined Type not set',
        'status': cfg.OV_BLANK,
        'visible': True,
        'build': lambda: _is_not_set(PREDEF),
        'note': 'No predefined type on the instance.',
    },
]


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
def filterable_categories(doc, category_ids, notes):
    """Keep the categories that support every parameter the filters need.

    Checked one category at a time: a single unsupported category would
    otherwise silently drop the IFC parameters out of the common set and the
    filters would be built on nothing.
    """
    needed = required_parameter_ids()
    if not needed:
        return []

    needed_keys = set()
    for pid in needed:
        key = ifc_collect.id_value(pid)
        if key is not None:
            needed_keys.add(key)

    kept = []
    dropped = []
    for category_id in category_ids:
        single = List[ElementId]()
        single.Add(category_id)
        try:
            allowed = ParameterFilterUtilities \
                .GetFilterableParametersInCommon(doc, single)
        except Exception:
            dropped.append(category_id)
            continue
        allowed_keys = set()
        for pid in allowed:
            key = ifc_collect.id_value(pid)
            if key is not None:
                allowed_keys.add(key)
        if needed_keys.issubset(allowed_keys):
            kept.append(category_id)
        else:
            dropped.append(category_id)

    if dropped:
        names = []
        for category_id in dropped:
            try:
                names.append(doc.GetElement(category_id).Name)
            except Exception:
                names.append(str(category_id))
        notes.append('{} category/categories cannot filter on the IFC '
                     'parameters and are left out of the filters: {}'.format(
                         len(dropped), ', '.join(sorted(names))))
    return kept


# ---------------------------------------------------------------------------
# Creating the filters
# ---------------------------------------------------------------------------
def _existing_filter_names(doc):
    names = set()
    for element in FilteredElementCollector(doc) \
            .OfClass(ParameterFilterElement).ToElements():
        try:
            names.add(element.Name)
        except Exception:
            continue
    return names


def unique_filter_name(existing, base):
    """base, or 'base (2)' ... so an existing filter is never modified."""
    if base not in existing:
        return base
    index = 2
    while index < 1000:
        candidate = '{} ({})'.format(base, index)
        if candidate not in existing:
            return candidate
        index += 1
    raise ValueError('No free filter name based on "{}"'.format(base))


def create_filters(doc, category_ids, notes):
    """Build the audit filters. Must run inside an open transaction.

    Returns a list of dicts: {'spec', 'element', 'name'}, in the order they
    should be applied to the view - most specific first, because Revit lets
    the first matching filter win for graphic overrides.
    """
    missing = unsupported_parameters()
    if missing:
        notes.append('FLAGGED - no built-in id in this API for {}, so filters '
                     'cannot be built on them.'.format(', '.join(missing)))

    usable = filterable_categories(doc, category_ids, notes)
    if not usable:
        notes.append('No audited category can filter on the IFC parameters, '
                     'so no filters were created.')
        return []

    existing = _existing_filter_names(doc)
    created = []

    for spec in FILTER_SPECS:
        try:
            element_filter = spec['build']()
        except Exception as exc:
            notes.append('Filter "{}" could not be built: {}'.format(
                spec['suffix'], exc))
            continue
        if element_filter is None:
            notes.append('Filter "{}" could not be built from the available '
                         'parameters and was skipped.'.format(spec['suffix']))
            continue

        base = '{}{}'.format(cfg.FILTER_NAME_PREFIX, spec['suffix'])
        name = unique_filter_name(existing, base)
        if name != base:
            notes.append('A filter called "{}" already exists and was not '
                         'touched; created "{}" instead.'.format(base, name))

        categories = List[ElementId]()
        for category_id in usable:
            categories.Add(category_id)

        try:
            filter_element = ParameterFilterElement.Create(
                doc, name, categories)
            filter_element.SetElementFilter(element_filter)
        except Exception as exc:
            notes.append('Filter "{}" could not be created: {}'.format(
                name, exc))
            continue

        existing.add(name)
        created.append({'spec': spec, 'element': filter_element, 'name': name})

    return created


def describe_filters(created):
    """Lines for the report and the legend."""
    lines = []
    for entry in created:
        spec = entry['spec']
        lines.append('{} - {}'.format(
            entry['name'],
            'hidden' if not spec['visible'] else
            'shown in {}'.format(spec['status'])))
        lines.append('    {}'.format(spec['note']))
    return lines
