# -*- coding: utf-8 -*-
"""
Validation of IFC entity names and IFC predefined types.

The authoritative source for both is the IFC exporter shipped with Revit
itself. Rather than hard-coding tables that go stale with every release, this
module reflects over the exporter assemblies at run time:

  Revit.IFC.Common.Enums.IFCEntityType          - every IFC entity the
                                                  installed exporter knows
  Revit.IFC.Export.Toolkit.IFC4.IFC<Name>Type   - the predefined type
  Revit.IFC.Export.Toolkit.IFC4x3.IFC<Name>Type   enumeration for an entity

Both assemblies live next to Revit.exe and are already loaded in the Revit
process once IFC export has been touched; they are loaded on demand here.

If reflection fails, a curated IFC4 table is used as a fallback (switchable in
ifc_config.USE_FALLBACK_ENUMS). If neither source can answer, validation
returns UNKNOWN - the audit then reports "Unable to verify" rather than
guessing, which is what the audit requires.

Author : Fayaz
Revit  : 2025
PyRevit: 4.8+
"""
import clr

from System import AppDomain, Enum

import ifc_config as cfg

# Validation verdicts.
VALID   = 'valid'
INVALID = 'invalid'
UNKNOWN = 'unknown'

_ENTITY_ASSEMBLY = 'Revit.IFC.Common'
_EXPORT_ASSEMBLY = 'Revit.IFC.Export'

_ENTITY_ENUM = 'Revit.IFC.Common.Enums.IFCEntityType'

# Namespaces in Revit.IFC.Export that hold the predefined type enumerations,
# best first. IFC4 is the target schema, IFC4x3 is consulted next, and the
# unversioned toolkit namespace last. Namespaces are matched exactly; .NET
# type names are case-sensitive, so lookups go through the index built below
# rather than through a name assembled from a lower-cased entity.
_PREDEF_NAMESPACE_PRIORITY = {
    'Revit.IFC.Export.Toolkit.IFC4': 0,
    'Revit.IFC.Export.Toolkit.IFC4x3': 1,
    'Revit.IFC.Export.Toolkit': 2,
}


# ---------------------------------------------------------------------------
# Curated IFC4 fallback, used only when the exporter enums cannot be read.
# Values are the IFC4 ADD2 PredefinedType enumerations for the entities a
# structural audit actually meets. A few IFC4x3 additions are included where
# the Revit exporter accepts them.
# ---------------------------------------------------------------------------
FALLBACK_PREDEFINED_TYPES = {
    'ifcwall': ['MOVABLE', 'PARAPET', 'PARTITIONING', 'PLUMBINGWALL', 'SHEAR',
                'SOLIDWALL', 'STANDARD', 'POLYGONAL', 'ELEMENTEDWALL',
                'RETAININGWALL', 'USERDEFINED', 'NOTDEFINED'],
    'ifccurtainwall': ['USERDEFINED', 'NOTDEFINED'],
    'ifccolumn': ['COLUMN', 'PILASTER', 'USERDEFINED', 'NOTDEFINED'],
    'ifcbeam': ['BEAM', 'JOIST', 'HOLLOWCORE', 'LINTEL', 'SPANDREL', 'T_BEAM',
                'USERDEFINED', 'NOTDEFINED'],
    'ifcmember': ['BRACE', 'CHORD', 'COLLAR', 'MEMBER', 'MULLION', 'PLATE',
                  'POST', 'PURLIN', 'RAFTER', 'STRINGER', 'STRUT', 'STUD',
                  'USERDEFINED', 'NOTDEFINED'],
    'ifcslab': ['FLOOR', 'ROOF', 'LANDING', 'BASESLAB',
                'USERDEFINED', 'NOTDEFINED'],
    'ifcplate': ['CURTAIN_PANEL', 'SHEET', 'USERDEFINED', 'NOTDEFINED'],
    'ifcfooting': ['FOOTING_BEAM', 'PAD_FOOTING', 'PILE_CAP', 'STRIP_FOOTING',
                   'CAISSON_FOUNDATION', 'USERDEFINED', 'NOTDEFINED'],
    'ifcpile': ['BORED', 'DRIVEN', 'JETGROUTING', 'COHESION', 'FRICTION',
                'SUPPORT', 'USERDEFINED', 'NOTDEFINED'],
    'ifcreinforcingbar': ['ANCHORING', 'EDGE', 'LIGATURE', 'MAIN', 'PUNCHING',
                          'RING', 'SHEAR', 'STUD', 'USERDEFINED', 'NOTDEFINED'],
    'ifcreinforcingmesh': ['USERDEFINED', 'NOTDEFINED'],
    'ifctendon': ['BAR', 'COATED', 'STRAND', 'WIRE',
                  'USERDEFINED', 'NOTDEFINED'],
    'ifctendonanchor': ['COUPLER', 'FIXED_END', 'TENSIONING_END',
                        'USERDEFINED', 'NOTDEFINED'],
    'ifcfastener': ['GLUE', 'MORTAR', 'WELD', 'USERDEFINED', 'NOTDEFINED'],
    'ifcmechanicalfastener': ['ANCHORBOLT', 'BOLT', 'DOWEL', 'NAIL',
                              'NAILPLATE', 'RIVET', 'SCREW', 'SHEARCONNECTOR',
                              'STAPLE', 'STUDSHEARCONNECTOR',
                              'USERDEFINED', 'NOTDEFINED'],
    'ifcdiscreteaccessory': ['ANCHORPLATE', 'BRACKET', 'SHOE',
                             'EXPANSION_JOINT_DEVICE',
                             'USERDEFINED', 'NOTDEFINED'],
    'ifcbuildingelementproxy': ['COMPLEX', 'ELEMENT', 'PARTIAL',
                                'PROVISIONFORVOID', 'PROVISIONFORSPACE',
                                'USERDEFINED', 'NOTDEFINED'],
    'ifccovering': ['CEILING', 'FLOORING', 'CLADDING', 'ROOFING', 'MOLDING',
                    'SKIRTINGBOARD', 'INSULATION', 'MEMBRANE', 'SLEEVING',
                    'WRAPPING', 'USERDEFINED', 'NOTDEFINED'],
    'ifcrailing': ['HANDRAIL', 'GUARDRAIL', 'BALUSTRADE',
                   'USERDEFINED', 'NOTDEFINED'],
    'ifcstructuralsurfacemember': ['BENDING_ELEMENT', 'MEMBRANE_ELEMENT',
                                   'SHELL', 'USERDEFINED', 'NOTDEFINED'],
    'ifcstructuralcurvemember': ['CABLE', 'COMPRESSION_MEMBER',
                                 'PIN_JOINED_MEMBER', 'RIGID_JOINED_MEMBER',
                                 'TENSION_MEMBER', 'USERDEFINED', 'NOTDEFINED'],
}


# ---------------------------------------------------------------------------
# Assembly / enum reflection
# ---------------------------------------------------------------------------
_assembly_cache = {}
_entity_names = None          # set of lower-case entity names, or None
_entity_source = 'not loaded'
_predef_cache = {}            # lower-case entity -> (set or None, source)


def _find_assembly(simple_name):
    """Return the loaded assembly with this simple name, loading it if needed."""
    if simple_name in _assembly_cache:
        return _assembly_cache[simple_name]

    found = None
    for asm in AppDomain.CurrentDomain.GetAssemblies():
        try:
            if asm.GetName().Name == simple_name:
                found = asm
                break
        except Exception:
            continue

    if found is None:
        try:
            clr.AddReference(simple_name)
        except Exception:
            found = None
        else:
            for asm in AppDomain.CurrentDomain.GetAssemblies():
                try:
                    if asm.GetName().Name == simple_name:
                        found = asm
                        break
                except Exception:
                    continue

    _assembly_cache[simple_name] = found
    return found


def _enum_names(assembly_name, full_type_name):
    """Return the member names of a .NET enum, or None if unreachable."""
    asm = _find_assembly(assembly_name)
    if asm is None:
        return None
    try:
        enum_type = asm.GetType(full_type_name)
    except Exception:
        return None
    if enum_type is None or not enum_type.IsEnum:
        return None
    try:
        return [str(n) for n in Enum.GetNames(enum_type)]
    except Exception:
        return None


def entity_names():
    """Lower-case set of every IFC entity the installed exporter knows.

    None means the exporter enum could not be read, in which case entity
    values must be reported as unverifiable rather than invalid.
    """
    global _entity_names, _entity_source
    if _entity_source != 'not loaded':
        return _entity_names

    names = _enum_names(_ENTITY_ASSEMBLY, _ENTITY_ENUM)
    if names:
        _entity_names = set([n.lower() for n in names])
        _entity_source = '{} ({} entities)'.format(_ENTITY_ENUM, len(names))
    else:
        _entity_names = None
        _entity_source = 'unavailable - {} could not be reflected'.format(
            _ENTITY_ENUM)
    return _entity_names


def entity_source():
    """Human-readable description of where the entity list came from."""
    entity_names()
    return _entity_source


_predef_index = None          # lower-case enum type name -> (rank, Type)
_predef_index_note = 'not built'


def _predefined_type_index():
    """Index every predefined type enumeration the exporter carries.

    Built once from Assembly.GetTypes so the lookup is case-insensitive and
    independent of how a namespace is spelled. Revit 2025 carries roughly
    1,500 enums in this assembly, so this is done exactly once per session.
    """
    global _predef_index, _predef_index_note
    if _predef_index is not None or _predef_index_note != 'not built':
        return _predef_index

    asm = _find_assembly(_EXPORT_ASSEMBLY)
    if asm is None:
        _predef_index_note = '{} is not loaded'.format(_EXPORT_ASSEMBLY)
        return None

    try:
        types = asm.GetTypes()
    except Exception as exc:
        _predef_index_note = 'GetTypes on {} failed: {}'.format(
            _EXPORT_ASSEMBLY, exc)
        return None

    index = {}
    for dotnet_type in types:
        try:
            if not dotnet_type.IsEnum:
                continue
            rank = _PREDEF_NAMESPACE_PRIORITY.get(dotnet_type.Namespace or '')
            if rank is None:
                continue
            key = dotnet_type.Name.lower()
            current = index.get(key)
            if current is None or rank < current[0]:
                index[key] = (rank, dotnet_type)
        except Exception:
            continue

    if not index:
        _predef_index_note = 'no predefined type enums found in {}'.format(
            _EXPORT_ASSEMBLY)
        return None

    _predef_index = index
    _predef_index_note = '{} enumerations indexed from {}'.format(
        len(index), _EXPORT_ASSEMBLY)
    return _predef_index


def _enum_type_key(entity):
    """The enum type name for an entity, lower-cased.

    IfcWall     -> ifcwalltype
    IfcWallType -> ifcwalltype   (already the type-level spelling)
    """
    key = (entity or '').strip().lower()
    if not key:
        return ''
    bare = key[3:] if key.startswith('ifc') else key
    if bare.endswith('type'):
        return 'ifc' + bare
    return 'ifc' + bare + 'type'


def predefined_types(entity):
    """(set_of_upper_case_names_or_None, source_description) for an entity."""
    key = (entity or '').strip().lower()
    if not key:
        return None, 'no entity'
    if key in _predef_cache:
        return _predef_cache[key]

    enum_key = _enum_type_key(entity)
    index = _predefined_type_index()
    result = None

    if index is not None:
        entry = index.get(enum_key)
        if entry is not None:
            try:
                names = [str(n) for n in Enum.GetNames(entry[1])]
            except Exception:
                names = []
            if names:
                result = (set([n.upper() for n in names]),
                          entry[1].FullName)

    if result is None:
        reason = 'exporter enum {} not found ({})'.format(
            enum_key, _predef_index_note)
        if cfg.USE_FALLBACK_ENUMS:
            fallback = FALLBACK_PREDEFINED_TYPES.get(key)
            if fallback:
                result = (set(fallback), 'curated IFC4 fallback table')
        if result is None:
            result = (None, reason)

    _predef_cache[key] = result
    return result


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------
def parse_export_as(value):
    """Split an "Export to IFC As" value into its parts.

    The exporter accepts several shapes, all handled here:

        IfcWall                 entity only
        IfcWallType             the type-level spelling of the same entity
        IfcWall.SHEAR           entity with an inline predefined type
        DontExport              a special token, not an entity

    Returns a dict with keys 'raw', 'entity', 'predefined', 'special'.
    """
    raw = (value or '').strip()
    out = {'raw': raw, 'entity': '', 'predefined': '', 'special': ''}
    if not raw:
        return out

    if raw.lower().replace(' ', '') in cfg.EXPORT_AS_SPECIAL_TOKENS:
        out['special'] = raw
        return out

    head = raw
    if '.' in raw:
        head, tail = raw.split('.', 1)
        out['predefined'] = tail.strip()
    out['entity'] = head.strip()
    return out


def _entity_candidates(entity):
    """An entity spelling and its instance-level equivalent.

    "IfcWallType" is how the type-level parameter spells "IfcWall"; both are
    legitimate, so try the given spelling and the one without the suffix.
    """
    name = (entity or '').strip()
    out = [name]
    if len(name) > 4 and name.lower().endswith('type'):
        out.append(name[:-4])
    return out


# ---------------------------------------------------------------------------
# Public validation
# ---------------------------------------------------------------------------
def validate_entity(value):
    """(verdict, reason) for an "Export to IFC As" value."""
    parsed = parse_export_as(value)
    if not parsed['raw']:
        return UNKNOWN, 'No value to validate'
    if parsed['special']:
        return VALID, 'Special exporter token "{}"'.format(parsed['special'])

    known = entity_names()
    if known is None:
        return UNKNOWN, ('IFC entity list unavailable ({}), value not '
                         'validated'.format(entity_source()))

    for candidate in _entity_candidates(parsed['entity']):
        if candidate.lower() in known:
            return VALID, 'Matches exporter entity "{}"'.format(candidate)

    return INVALID, ('"{}" is not an IFC entity known to the installed '
                     'exporter'.format(parsed['entity']))


def validate_predefined_type(entity_value, predef_value):
    """(verdict, reason) for an "IFC Predefined Type" value.

    The predefined type is only meaningful against an entity, so an entity
    that is missing, blank or itself invalid makes the predefined type
    unverifiable rather than invalid.
    """
    predef = (predef_value or '').strip()
    if not predef:
        return UNKNOWN, 'No value to validate'

    parsed = parse_export_as(entity_value)
    if parsed['special']:
        return UNKNOWN, ('"Export to IFC As" is "{}", so no predefined type '
                         'enumeration applies'.format(parsed['special']))
    if not parsed['entity']:
        return UNKNOWN, ('Cannot validate without an IFC entity in '
                         '"Export to IFC As"')

    verdict, _ = validate_entity(entity_value)
    if verdict == INVALID:
        return UNKNOWN, ('IFC entity "{}" is not valid, so its predefined '
                         'type cannot be checked'.format(parsed['entity']))
    if verdict == UNKNOWN:
        return UNKNOWN, 'IFC entity could not be validated'

    allowed = None
    source = ''
    for candidate in _entity_candidates(parsed['entity']):
        allowed, source = predefined_types(candidate)
        if allowed:
            break

    if not allowed:
        return UNKNOWN, ('No predefined type enumeration available for "{}" '
                         '({})'.format(parsed['entity'], source))

    if predef.upper() in allowed:
        note = ''
        if predef.upper() == 'USERDEFINED':
            note = (' - USERDEFINED also requires an object type to be set '
                    'for a meaningful export')
        return VALID, 'Valid for {} per {}{}'.format(
            parsed['entity'], source, note)

    return INVALID, ('"{}" is not in the predefined type enumeration for {} '
                     '({})'.format(predef, parsed['entity'], source))


def validation_sources():
    """Short description of what validation is backed by, for the report."""
    lines = ['IFC entity list: {}'.format(entity_source())]
    _predefined_type_index()
    if _predef_index is None:
        lines.append(
            'Predefined type enumerations: unavailable - {}{}'.format(
                _predef_index_note,
                '; curated IFC4 fallback table in use'
                if cfg.USE_FALLBACK_ENUMS
                else '; no fallback (disabled in ifc_config)'))
    else:
        lines.append('Predefined type enumerations: {}'.format(
            _predef_index_note))
    return lines
