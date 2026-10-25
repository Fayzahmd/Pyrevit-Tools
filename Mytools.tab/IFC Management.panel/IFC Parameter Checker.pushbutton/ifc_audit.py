# -*- coding: utf-8 -*-
"""
The audit engine.

Deliberately free of any user interface: it takes a document, a list of
elements and a progress callback, and returns data. That keeps it testable
from a headless context and keeps the audit strictly read-only - no
transaction is started anywhere in this module.

Three ideas are kept apart on purpose, because conflating them is what makes
most IFC audits misleading:

  missing   the parameter does not exist on the element or its type at all,
            so there is nowhere to put a value. Usually a binding problem:
            the category was never bound, or the family predates the
            parameter.

  blank     the parameter exists and is writable but holds no value. The
            binding is fine; somebody has to type something.

  invalid   the parameter exists and holds a value, but the value is not one
            the IFC schema accepts for that element - an entity the exporter
            does not know, or a predefined type outside the enumeration for
            the chosen entity. The export will run and produce wrong data,
            which is worse than an empty field.

A fourth state matters just as much: unable to verify. If a parameter cannot
be read, or validation has no authoritative enumeration to check against, the
audit says so instead of reporting a pass.

Author : Fayaz
Revit  : 2025
PyRevit: 4.8+
"""
from Autodesk.Revit.DB import ElementId

import ifc_config as cfg
import ifc_params as params
import ifc_validate as validate


# ---------------------------------------------------------------------------
# Result objects. Plain attributes so WPF can bind straight to them.
# ---------------------------------------------------------------------------
class ParamResult(object):
    """One audited parameter on one element - a row in the results table."""

    def __init__(self):
        self.element_id = ''
        self.unique_id = ''
        self.category = ''
        self.family = ''
        self.type_name = ''
        self.param_name = ''
        self.location = params.LOC_UNKNOWN
        self.value = ''
        self.status = cfg.ST_UNKNOWN
        self.reason = ''
        self.read_only = False
        self.found_via = ''
        # Filled in once the element's overall status is known. Carried on the
        # row so the results table can group and filter without walking back
        # to the element.
        self.overall = ''
        # The live ElementId, for "select in Revit". Never shown in a column.
        self.revit_id = None

    @property
    def read_only_text(self):
        return 'Yes' if self.read_only else ''


class ElementResult(object):
    """The three parameter results for one element, plus its overall status."""

    def __init__(self, element):
        self.element = element
        self.element_id = ''
        self.unique_id = ''
        self.category = ''
        self.family = ''
        self.type_name = ''
        self.rows = []
        self.overall = cfg.OV_UNKNOWN
        self.inspection_failed = False

    @property
    def used_type_level(self):
        return any(r.location == params.LOC_TYPE for r in self.rows)

    @property
    def issue_summary(self):
        bad = ['{}: {}'.format(r.param_name, r.status) for r in self.rows
               if r.status not in (cfg.ST_OK, cfg.ST_TYPE)]
        return '; '.join(bad)


class AuditResult(object):
    """Everything the window and the report need."""

    def __init__(self):
        self.doc_title = ''
        self.revit_version = ''
        self.timestamp = ''
        self.scope = ''
        self.categories = []
        self.include_type_level = True
        self.elements = []          # ElementResult
        self.rows = []              # ParamResult, flattened
        self.notes = []
        self.link_notes = []
        self.definition_notes = []
        self.validation_notes = []
        self.cancelled = False

    # -- summary ----------------------------------------------------------
    def _count(self, overall):
        return len([e for e in self.elements if e.overall == overall])

    @property
    def total(self):
        return len(self.elements)

    @property
    def passed(self):
        return self._count(cfg.OV_OK)

    @property
    def with_missing(self):
        return self._count(cfg.OV_MISSING)

    @property
    def with_invalid(self):
        return self._count(cfg.OV_INVALID)

    @property
    def with_blank(self):
        return self._count(cfg.OV_BLANK)

    @property
    def needs_review(self):
        return self._count(cfg.OV_UNKNOWN)

    @property
    def with_type_level(self):
        return len([e for e in self.elements if e.used_type_level])

    @property
    def not_inspected(self):
        return len([e for e in self.elements if e.inspection_failed])

    def summary_lines(self):
        lines = [
            'Total elements scanned          : {}'.format(self.total),
            'Fully populated and valid       : {}'.format(self.passed),
            'With a missing parameter        : {}'.format(self.with_missing),
            'With a blank parameter          : {}'.format(self.with_blank),
            'With an invalid value           : {}'.format(self.with_invalid),
            'Relying on a type-level value   : {}'.format(self.with_type_level),
            'Requiring manual review         : {}'.format(self.needs_review),
            'Could not be inspected          : {}'.format(self.not_inspected),
        ]
        return lines

    def elements_by_overall(self):
        grouped = {}
        for element_result in self.elements:
            grouped.setdefault(element_result.overall, []).append(
                element_result)
        return grouped


# ---------------------------------------------------------------------------
# Element identity
# ---------------------------------------------------------------------------
def _safe(call, default=''):
    try:
        value = call()
    except Exception:
        return default
    return default if value is None else value


def _describe_element(element, type_element):
    """(element_id, unique_id, category, family, type_name)"""
    element_id = _safe(lambda: str(element.Id))
    unique_id = _safe(lambda: element.UniqueId)
    category = _safe(lambda: element.Category.Name)

    family = ''
    if type_element is not None:
        family = _safe(lambda: type_element.FamilyName)
        if not family:
            family = _safe(lambda: type_element.Family.Name)
    if not family:
        family = _safe(lambda: element.Symbol.Family.Name)

    type_name = ''
    if type_element is not None:
        type_name = _safe(lambda: type_element.Name)
    if not type_name:
        type_name = _safe(lambda: element.Name)

    return element_id, unique_id, category, family, type_name


# ---------------------------------------------------------------------------
# Per-parameter audit
# ---------------------------------------------------------------------------
def _classify_presence(element, type_element, param_name, include_type_level):
    """Find the parameter and decide where its effective value lives.

    Returns a dict:
        status        a cfg.ST_* value
        location      params.LOC_*
        value         the effective display value
        reason        why that status
        read_only     whether the effective parameter is read-only
        found_via     how the parameter was identified
        info          the read_value dict of the effective parameter, or None
    """
    out = {'status': cfg.ST_UNKNOWN, 'location': params.LOC_UNKNOWN,
           'value': '', 'reason': '', 'read_only': False, 'found_via': '',
           'info': None}

    inst_param, inst_via, inst_err = params.find_parameter(
        element, param_name, params.LOC_INSTANCE)

    type_param, type_via, type_err = (None, '', '')
    if include_type_level and type_element is not None:
        type_param, type_via, type_err = params.find_parameter(
            type_element, param_name, params.LOC_TYPE)

    if inst_err or type_err:
        out['status'] = cfg.ST_UNKNOWN
        out['reason'] = 'Parameter could not be inspected: {}'.format(
            inst_err or type_err)
        return out

    inst_info = params.read_value(inst_param) if inst_param is not None else None
    type_info = params.read_value(type_param) if type_param is not None else None

    if inst_info is not None and inst_info['error']:
        out['status'] = cfg.ST_UNKNOWN
        out['location'] = params.LOC_INSTANCE
        out['reason'] = inst_info['error']
        out['info'] = inst_info
        out['found_via'] = inst_via
        return out

    # 1. A populated instance value wins.
    if inst_info is not None and not inst_info['blank']:
        out.update({'status': cfg.ST_OK,
                    'location': params.LOC_INSTANCE,
                    'value': inst_info['display'],
                    'read_only': bool(inst_info['read_only']),
                    'found_via': inst_via,
                    'info': inst_info,
                    'reason': 'Value present on the instance'})
        return out

    # 2. Otherwise a populated type value is a legitimate source, and must not
    #    be reported as missing.
    if type_info is not None and not type_info['error'] \
            and not type_info['blank']:
        if inst_param is None:
            reason = ('Parameter is bound to the type, not the instance; '
                      'value taken from the type')
        else:
            reason = ('Instance value is empty; the type carries the value '
                      'the exporter will use')
        out.update({'status': cfg.ST_TYPE,
                    'location': params.LOC_TYPE,
                    'value': type_info['display'],
                    'read_only': bool(type_info['read_only']),
                    'found_via': type_via,
                    'info': type_info,
                    'reason': reason})
        return out

    # 2b. The type is the only candidate left but could not be read.
    if inst_param is None and type_info is not None and type_info['error']:
        out.update({'status': cfg.ST_UNKNOWN,
                    'location': params.LOC_TYPE,
                    'found_via': type_via,
                    'info': type_info,
                    'reason': type_info['error']})
        return out

    # 3. The parameter exists somewhere but holds nothing.
    if inst_param is not None or type_param is not None:
        effective_info = inst_info if inst_param is not None else type_info
        location = params.LOC_INSTANCE if inst_param is not None \
            else params.LOC_TYPE
        via = inst_via if inst_param is not None else type_via
        read_only = bool(effective_info['read_only']) if effective_info else False

        status = cfg.ST_BLANK
        reason = 'Parameter exists but holds no value'
        if read_only:
            status = cfg.ST_READONLY
            reason = ('Parameter exists, holds no value and is read-only, so '
                      'it cannot be filled in here')
        out.update({'status': status, 'location': location, 'value': '',
                    'read_only': read_only, 'found_via': via,
                    'reason': reason, 'info': effective_info})
        return out

    # 4. Nowhere to be found.
    if not include_type_level:
        out.update({'status': cfg.ST_UNKNOWN,
                    'location': params.LOC_UNKNOWN,
                    'reason': ('Not on the instance, and type-level checking '
                               'was switched off, so it cannot be called '
                               'missing')})
        return out

    if type_element is None:
        out.update({'status': cfg.ST_UNKNOWN,
                    'location': params.LOC_UNKNOWN,
                    'reason': ('Not on the instance, and the element has no '
                               'type to check')})
        return out

    out.update({'status': cfg.ST_MISSING,
                'location': params.LOC_UNKNOWN,
                'reason': 'Parameter exists on neither the instance nor the '
                          'type'})
    return out


def _validate_value(param_name, presence, export_as_value):
    """Upgrade a populated result to invalid / unverifiable where warranted.

    Presence and validity are separate questions: this only runs once a value
    has been found, and never turns a missing or blank result into an invalid
    one.
    """
    if presence['status'] not in (cfg.ST_OK, cfg.ST_TYPE):
        return presence

    value = presence['value']
    info = presence['info'] or {}

    if param_name == cfg.PARAM_EXPORT_TO_IFC:
        # Normally an integer-backed state parameter ("By Type", "Export",
        # "Don't Export"). Its valid states are whatever this Revit build
        # offers, so the check is that the stored state resolves to a label.
        if info.get('storage') == 'Integer':
            label = ''
            try:
                label = info['display']
            except Exception:
                label = ''
            if not label or label == str(info.get('raw')):
                presence['status'] = cfg.ST_UNKNOWN
                presence['reason'] = (
                    'Stored state {} has no label in this Revit build, so it '
                    'cannot be confirmed as a supported value'.format(
                        info.get('raw')))
            else:
                presence['reason'] += ' (state "{}")'.format(label)
        else:
            presence['reason'] += (
                ' - held as {} rather than the built-in state parameter, so '
                'the value itself is not validated'.format(
                    info.get('storage') or 'an unknown storage type'))
        return presence

    if param_name == cfg.PARAM_EXPORT_TO_IFC_AS:
        verdict, reason = validate.validate_entity(value)
        if verdict == validate.INVALID:
            presence['status'] = cfg.ST_INVALID
        elif verdict == validate.UNKNOWN:
            presence['status'] = cfg.ST_UNKNOWN
        presence['reason'] = reason
        return presence

    if param_name == cfg.PARAM_IFC_PREDEF_TYPE:
        verdict, reason = validate.validate_predefined_type(
            export_as_value, value)
        if verdict == validate.INVALID:
            presence['status'] = cfg.ST_INVALID
        elif verdict == validate.UNKNOWN:
            presence['status'] = cfg.ST_UNKNOWN
        presence['reason'] = reason
        return presence

    return presence


def _overall_status(rows, inspection_failed):
    """Collapse three parameter results into one element status.

    Priority is cfg.OVERALL_PRIORITY: an element missing a parameter is red
    even if another parameter is merely blank.
    """
    if inspection_failed:
        return cfg.OV_UNKNOWN

    statuses = [r.status for r in rows]
    if cfg.ST_MISSING in statuses:
        return cfg.OV_MISSING
    if cfg.ST_INVALID in statuses:
        return cfg.OV_INVALID
    if cfg.ST_BLANK in statuses or cfg.ST_READONLY in statuses:
        return cfg.OV_BLANK
    if cfg.ST_UNKNOWN in statuses:
        return cfg.OV_UNKNOWN
    return cfg.OV_OK


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------
def audit_elements(doc, elements, include_type_level=True,
                   progress=None, type_cache=None):
    """Audit every element. Returns a list of ElementResult.

    progress is called as progress(done, total) and may return False to
    cancel; the partial result is then kept.
    """
    results = []
    cache = type_cache or params.TypeCache(doc)
    total = len(elements)

    for index, element in enumerate(elements):
        if progress is not None:
            if progress(index, total) is False:
                break

        element_result = ElementResult(element)
        try:
            type_element = cache.get(element)
        except Exception:
            type_element = None

        (element_result.element_id, element_result.unique_id,
         element_result.category, element_result.family,
         element_result.type_name) = _describe_element(element, type_element)

        export_as_value = ''
        for param_name in cfg.AUDITED_PARAMETERS:
            row = ParamResult()
            row.element_id = element_result.element_id
            row.unique_id = element_result.unique_id
            row.category = element_result.category
            row.family = element_result.family
            row.type_name = element_result.type_name
            row.param_name = param_name
            try:
                row.revit_id = element.Id
            except Exception:
                row.revit_id = None

            try:
                presence = _classify_presence(
                    element, type_element, param_name, include_type_level)
                presence = _validate_value(
                    param_name, presence, export_as_value)
            except Exception as exc:
                element_result.inspection_failed = True
                row.status = cfg.ST_UNKNOWN
                row.reason = 'Audit of this parameter failed: {}'.format(exc)
                element_result.rows.append(row)
                continue

            row.status = presence['status']
            row.location = presence['location']
            row.value = presence['value']
            row.reason = presence['reason']
            row.read_only = presence['read_only']
            row.found_via = presence['found_via']

            if param_name == cfg.PARAM_EXPORT_TO_IFC_AS:
                export_as_value = row.value

            element_result.rows.append(row)

        element_result.overall = _overall_status(
            element_result.rows, element_result.inspection_failed)
        for row in element_result.rows:
            row.overall = element_result.overall
        results.append(element_result)

    if progress is not None:
        progress(total, total)

    return results


def build_result(doc, elements, include_type_level, scope, category_labels,
                 progress=None):
    """Run the audit and wrap it with everything the report needs."""
    from System import DateTime

    result = AuditResult()
    result.doc_title = _safe(lambda: doc.Title, '<unsaved>')
    result.revit_version = _safe(
        lambda: '{} (build {})'.format(
            doc.Application.VersionNumber, doc.Application.VersionBuild))
    result.timestamp = str(DateTime.Now.ToString('yyyy-MM-dd HH:mm:ss'))
    result.scope = scope
    result.categories = list(category_labels)
    result.include_type_level = include_type_level

    unresolved = params.unresolved_definitions()
    if unresolved:
        result.definition_notes.append(
            'FLAGGED FOR REVIEW - no built-in definition resolved in this '
            'Revit API for: {}. Results for those parameters rest on '
            'name-based lookup only.'.format(', '.join(unresolved)))
    result.definition_notes.extend(params.definition_report())
    result.validation_notes.extend(validate.validation_sources())
    result.link_notes.extend(ifc_link_notes(doc))

    result.elements = audit_elements(
        doc, elements, include_type_level=include_type_level,
        progress=progress)
    result.rows = [row for element in result.elements for row in element.rows]
    if len(result.elements) < len(elements):
        result.cancelled = True
        result.notes.append(
            'Scan cancelled after {} of {} elements; the summary and report '
            'cover the scanned elements only.'.format(
                len(result.elements), len(elements)))
    return result


def ifc_link_notes(doc):
    """Imported lazily so this module stays importable without the collector."""
    try:
        import ifc_collect
        return ifc_collect.linked_model_report(doc)
    except Exception as exc:
        return ['Linked model check failed: {}'.format(exc)]


def element_ids_by_status(audit_result):
    """{overall status: [ElementId]} for the optional visualisation."""
    grouped = {}
    for element_result in audit_result.elements:
        try:
            element_id = element_result.element.Id
        except Exception:
            continue
        if element_id is None or element_id == ElementId.InvalidElementId:
            continue
        grouped.setdefault(element_result.overall, []).append(element_id)
    return grouped
