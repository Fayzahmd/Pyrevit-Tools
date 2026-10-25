# -*- coding: utf-8 -*-
"""
CSV report writer.

Written by hand rather than through the csv module: IronPython 2.7's csv
writer does not take unicode, and Revit models routinely carry non-ASCII
family and type names. The file is written as UTF-8 with a BOM so Excel opens
it in the right encoding without an import step.

Author : Fayaz
Revit  : 2025
PyRevit: 4.8+
"""
import codecs

import ifc_config as cfg

COLUMNS = [
    'Element Id',
    'UniqueId',
    'Category',
    'Family',
    'Type',
    'Parameter',
    'Parameter location',
    'Current value',
    'Audit status',
    'Read-only',
    'Identified via',
    'Reason for the status',
    'Element overall status',
]


def _cell(value):
    """One CSV cell, quoted and escaped."""
    if value is None:
        text = ''
    else:
        try:
            text = unicode(value)                       # noqa: F821
        except NameError:                               # pragma: no cover
            text = str(value)
        except Exception:
            text = ''
    text = text.replace('\r', ' ').replace('\n', ' ')
    return u'"{}"'.format(text.replace(u'"', u'""'))


def _row(values):
    return u','.join([_cell(v) for v in values])


def _section(handle, title, lines):
    handle.write(_row([title]) + u'\n')
    for line in lines:
        handle.write(_row([u'', line]) + u'\n')
    handle.write(u'\n')


def write_csv(path, audit_result):
    """Write the report. Returns a list of notes, empty when all went well."""
    notes = []
    handle = codecs.open(path, 'w', 'utf-8-sig')
    try:
        handle.write(_row(['IFC Parameter Checker - audit report']) + u'\n')
        handle.write(_row(['Document', audit_result.doc_title]) + u'\n')
        handle.write(_row(['Revit version', audit_result.revit_version]) + u'\n')
        handle.write(_row(['Scan timestamp', audit_result.timestamp]) + u'\n')
        handle.write(_row(['Scope', audit_result.scope]) + u'\n')
        handle.write(_row(['Categories scanned',
                           '; '.join(audit_result.categories)]) + u'\n')
        handle.write(_row(['Type-level parameters checked',
                           'Yes' if audit_result.include_type_level
                           else 'No']) + u'\n')
        handle.write(_row(['Audit mode',
                           'Read-only - no element, type or view was '
                           'modified by the audit']) + u'\n')
        handle.write(u'\n')

        _section(handle, 'Summary', audit_result.summary_lines())
        _section(handle, 'Audited parameters',
                 ['{} ({})'.format(name, _param_note(name))
                  for name in cfg.AUDITED_PARAMETERS])
        _section(handle, 'Parameter definitions resolved',
                 audit_result.definition_notes)
        _section(handle, 'Validation sources', audit_result.validation_notes)

        if audit_result.link_notes:
            _section(handle, 'Linked models', audit_result.link_notes)
        if audit_result.notes:
            _section(handle, 'Collection and scan notes', audit_result.notes)

        _section(handle, 'Status meanings', [
            '{} - {}'.format(cfg.ST_OK,
                             'the parameter exists and holds a value'),
            '{} - {}'.format(cfg.ST_BLANK,
                             'the parameter exists and is writable but holds '
                             'no value; somebody has to fill it in'),
            '{} - {}'.format(cfg.ST_MISSING,
                             'the parameter exists on neither the instance '
                             'nor the type, so there is nowhere to put a '
                             'value; this is a binding problem'),
            '{} - {}'.format(cfg.ST_READONLY,
                             'the parameter exists but cannot be written '
                             'here'),
            '{} - {}'.format(cfg.ST_INVALID,
                             'a value is present but is not accepted by the '
                             'IFC schema for this element; the export will '
                             'run and produce wrong data'),
            '{} - {}'.format(cfg.ST_TYPE,
                             'the effective value comes from the element '
                             'type, which is legitimate and is not a missing '
                             'parameter'),
            '{} - {}'.format(cfg.ST_UNKNOWN,
                             'the parameter could not be read, or no '
                             'authoritative enumeration was available to '
                             'validate it against; treated as unverified, '
                             'never as a pass'),
        ])

        handle.write(_row(COLUMNS) + u'\n')
        for element_result in audit_result.elements:
            for row in element_result.rows:
                handle.write(_row([
                    row.element_id,
                    row.unique_id,
                    row.category,
                    row.family,
                    row.type_name,
                    row.param_name,
                    row.location,
                    row.value,
                    row.status,
                    row.read_only_text,
                    row.found_via,
                    row.reason,
                    element_result.overall,
                ]) + u'\n')

    except Exception as exc:
        notes.append('Report could not be completed: {}'.format(exc))
    finally:
        try:
            handle.close()
        except Exception as exc:
            notes.append('Report file could not be closed cleanly: {}'.format(
                exc))

    return notes


def _param_note(name):
    if name == cfg.PARAM_EXPORT_TO_IFC:
        return 'built-in state parameter, controls whether the element is ' \
               'exported at all'
    if name == cfg.PARAM_EXPORT_TO_IFC_AS:
        return 'the IFC entity the element is exported as'
    if name == cfg.PARAM_IFC_PREDEF_TYPE:
        return 'the predefined type within that IFC entity'
    return ''


def default_filename(audit_result):
    """A sensible suggested file name for the save dialog."""
    title = (audit_result.doc_title or 'model').replace(' ', '_')
    stamp = (audit_result.timestamp or '').replace(':', '').replace('-', '')
    stamp = stamp.replace(' ', '_')
    return 'IFC_Parameter_Audit_{}_{}.csv'.format(title, stamp)
