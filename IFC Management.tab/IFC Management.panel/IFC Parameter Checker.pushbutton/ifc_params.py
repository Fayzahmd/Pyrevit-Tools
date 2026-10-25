# -*- coding: utf-8 -*-
"""
Discovery and safe reading of the three IFC export parameters.

Identification is by BuiltInParameter, not by display name. The names shown in
the Revit properties palette are localised and can be duplicated by a user
parameter, so the built-in enum is the only reliable handle. Confirmed present
in the Revit 2025 API (RevitAPI.dll, build 25.4.x):

  instance                        type
  ------------------------------  ----------------------------------
  IFC_EXPORT_ELEMENT              IFC_EXPORT_ELEMENT_TYPE
  IFC_EXPORT_ELEMENT_AS           IFC_EXPORT_ELEMENT_TYPE_AS
  IFC_EXPORT_PREDEFINEDTYPE       IFC_EXPORT_PREDEFINEDTYPE_TYPE

These are built-in parameters, so they carry no shared-parameter GUID - there
is nothing to match a GUID against. The legacy shared/project parameters that
older exporter versions honoured (IfcExportAs, IfcExportType) are still looked
up by name as a fallback, and the report says when a value came from one.

Every parameter read is wrapped: a parameter that cannot be inspected is
reported as such and never silently treated as absent.

Author : Fayaz
Revit  : 2025
PyRevit: 4.8+
"""
from Autodesk.Revit.DB import BuiltInParameter, ElementId, StorageType

import ifc_config as cfg

# Where a value was found.
LOC_INSTANCE = 'Instance'
LOC_TYPE     = 'Type'
LOC_UNKNOWN  = 'Unknown'


def _bip(name):
    """BuiltInParameter by name, or None if this Revit version lacks it."""
    return getattr(BuiltInParameter, name, None)


# Built-in handles plus name-based fallbacks, per audited parameter.
PARAM_DEFS = {
    cfg.PARAM_EXPORT_TO_IFC: {
        'instance_bip': _bip('IFC_EXPORT_ELEMENT'),
        'instance_bip_name': 'IFC_EXPORT_ELEMENT',
        'type_bip': _bip('IFC_EXPORT_ELEMENT_TYPE'),
        'type_bip_name': 'IFC_EXPORT_ELEMENT_TYPE',
        'legacy_names': [],
    },
    cfg.PARAM_EXPORT_TO_IFC_AS: {
        'instance_bip': _bip('IFC_EXPORT_ELEMENT_AS'),
        'instance_bip_name': 'IFC_EXPORT_ELEMENT_AS',
        'type_bip': _bip('IFC_EXPORT_ELEMENT_TYPE_AS'),
        'type_bip_name': 'IFC_EXPORT_ELEMENT_TYPE_AS',
        'legacy_names': ['IfcExportAs', 'IfcExportType'],
    },
    cfg.PARAM_IFC_PREDEF_TYPE: {
        'instance_bip': _bip('IFC_EXPORT_PREDEFINEDTYPE'),
        'instance_bip_name': 'IFC_EXPORT_PREDEFINEDTYPE',
        'type_bip': _bip('IFC_EXPORT_PREDEFINEDTYPE_TYPE'),
        'type_bip_name': 'IFC_EXPORT_PREDEFINEDTYPE_TYPE',
        'legacy_names': ['IfcPredefinedType'],
    },
}


def definition_report():
    """Lines describing which definitions resolved, for the CSV header."""
    lines = []
    for name in cfg.AUDITED_PARAMETERS:
        d = PARAM_DEFS[name]
        lines.append(
            '{}: instance {}={}, type {}={}'.format(
                name,
                d['instance_bip_name'],
                'resolved' if d['instance_bip'] else 'NOT IN THIS API',
                d['type_bip_name'],
                'resolved' if d['type_bip'] else 'NOT IN THIS API'))
    return lines


def unresolved_definitions():
    """Audited parameters whose built-in handles are missing from this API."""
    bad = []
    for name in cfg.AUDITED_PARAMETERS:
        d = PARAM_DEFS[name]
        if d['instance_bip'] is None and d['type_bip'] is None:
            bad.append(name)
    return bad


# ---------------------------------------------------------------------------
# Type element cache
# ---------------------------------------------------------------------------
class TypeCache(object):
    """Caches type elements so a model with many instances of few types does
    not re-fetch the same ElementType thousands of times."""

    def __init__(self, doc):
        self._doc = doc
        self._cache = {}

    def get(self, element):
        try:
            type_id = element.GetTypeId()
        except Exception:
            return None
        if type_id is None or type_id == ElementId.InvalidElementId:
            return None
        key = type_id.IntegerValue if hasattr(type_id, 'IntegerValue') \
            else str(type_id)
        if key not in self._cache:
            try:
                self._cache[key] = self._doc.GetElement(type_id)
            except Exception:
                self._cache[key] = None
        return self._cache[key]


# ---------------------------------------------------------------------------
# Finding a parameter on an element
# ---------------------------------------------------------------------------
def find_parameter(element, logical_name, level):
    """Locate one audited parameter on one element.

    level is LOC_INSTANCE or LOC_TYPE and selects which built-in handle to
    use. Returns (parameter_or_None, how_it_was_found, error_message).
    """
    if element is None:
        return None, '', 'No element to inspect'

    definition = PARAM_DEFS.get(logical_name)
    if definition is None:
        return None, '', 'Unknown audited parameter "{}"'.format(logical_name)

    bip = definition['instance_bip'] if level == LOC_INSTANCE \
        else definition['type_bip']
    bip_name = definition['instance_bip_name'] if level == LOC_INSTANCE \
        else definition['type_bip_name']

    if bip is not None:
        try:
            param = element.get_Parameter(bip)
        except Exception as exc:
            return None, '', 'get_Parameter({}) failed: {}'.format(
                bip_name, exc)
        if param is not None:
            return param, bip_name, ''

    # Fall back to a user/shared parameter with the audited display name, then
    # to the legacy exporter parameter names.
    for candidate in [logical_name] + definition['legacy_names']:
        try:
            param = element.LookupParameter(candidate)
        except Exception as exc:
            return None, '', 'LookupParameter("{}") failed: {}'.format(
                candidate, exc)
        if param is not None:
            return param, 'by name "{}"'.format(candidate), ''

    return None, '', ''


# ---------------------------------------------------------------------------
# Reading a value
# ---------------------------------------------------------------------------
def read_value(param):
    """Read a parameter without assuming its storage type.

    Returns a dict:
        storage      storage type name, or '' if unreadable
        read_only    True / False / None when unknown
        raw          underlying value
        display      what the user would see in the properties palette
        blank        True when the parameter holds no value
        can_be_blank True for storage types that can genuinely be empty
        error        non-empty when the value could not be read
    """
    info = {'storage': '', 'read_only': None, 'raw': None, 'display': '',
            'blank': False, 'can_be_blank': True, 'error': ''}
    if param is None:
        info['error'] = 'No parameter'
        return info

    try:
        storage = param.StorageType
        info['storage'] = str(storage)
    except Exception as exc:
        info['error'] = 'StorageType unreadable: {}'.format(exc)
        return info

    try:
        info['read_only'] = param.IsReadOnly
    except Exception:
        info['read_only'] = None

    try:
        if storage == StorageType.String:
            raw = param.AsString()
            info['raw'] = raw
            info['display'] = raw if raw else ''
            info['blank'] = (raw is None or not raw.strip())

        elif storage == StorageType.Integer:
            # An integer parameter always holds a number, so it cannot be
            # blank. "Export to IFC" is such a parameter: its default 0 is a
            # real state ("By Type"), not an empty value. The label the user
            # sees comes from AsValueString.
            info['can_be_blank'] = False
            raw = param.AsInteger()
            info['raw'] = raw
            label = ''
            try:
                label = param.AsValueString()
            except Exception:
                label = ''
            info['display'] = label if label else str(raw)
            info['blank'] = False

        elif storage == StorageType.ElementId:
            raw = param.AsElementId()
            info['raw'] = raw
            invalid = (raw is None or raw == ElementId.InvalidElementId)
            info['blank'] = invalid
            if invalid:
                info['display'] = ''
            else:
                label = ''
                try:
                    label = param.AsValueString()
                except Exception:
                    label = ''
                info['display'] = label if label else str(raw)

        elif storage == StorageType.Double:
            info['can_be_blank'] = False
            raw = param.AsDouble()
            info['raw'] = raw
            label = ''
            try:
                label = param.AsValueString()
            except Exception:
                label = ''
            info['display'] = label if label else str(raw)
            info['blank'] = False

        else:
            info['error'] = 'Unsupported storage type {}'.format(storage)

    except Exception as exc:
        info['error'] = 'Value unreadable: {}'.format(exc)

    return info


def parameter_display_name(param):
    """The parameter's own display name, for the report."""
    try:
        return param.Definition.Name
    except Exception:
        return ''
