# -*- coding: utf-8 -*-
"""
Configuration for the IFC Parameter Checker.

Everything a user may reasonably want to change - scanned categories, audit
colours, the generated view name, validation strictness - lives here so the
audit engine itself does not have to be edited.

Author : Fayaz
Revit  : 2025
PyRevit: 4.8+
"""

# ---------------------------------------------------------------------------
# The three parameters under audit, in report order.
# ---------------------------------------------------------------------------
PARAM_EXPORT_TO_IFC    = 'Export to IFC'
PARAM_EXPORT_TO_IFC_AS = 'Export to IFC As'
PARAM_IFC_PREDEF_TYPE  = 'IFC Predefined Type'

AUDITED_PARAMETERS = [
    PARAM_EXPORT_TO_IFC,
    PARAM_EXPORT_TO_IFC_AS,
    PARAM_IFC_PREDEF_TYPE,
]

# ---------------------------------------------------------------------------
# Per-parameter audit statuses.
# ---------------------------------------------------------------------------
ST_OK       = 'Present and populated'
ST_BLANK    = 'Present but blank'
ST_MISSING  = 'Missing'
ST_READONLY = 'Read-only'
ST_INVALID  = 'Invalid value'
ST_TYPE     = 'Value on type'
ST_UNKNOWN  = 'Unable to verify'

# ---------------------------------------------------------------------------
# Overall per-element statuses, highest severity first. The order of this list
# IS the priority used to colour an element that has more than one issue.
# ---------------------------------------------------------------------------
OV_MISSING = 'Missing parameter'
OV_INVALID = 'Invalid IFC configuration'
OV_BLANK   = 'Value required'
OV_UNKNOWN = 'Manual review required'
OV_OK      = 'Audit passed'

OVERALL_PRIORITY = [OV_MISSING, OV_INVALID, OV_BLANK, OV_UNKNOWN, OV_OK]

# ---------------------------------------------------------------------------
# Colours for the optional 3D audit view, as (red, green, blue), 0-255.
# Chosen to stay distinguishable in Shaded and Consistent Colors.
# ---------------------------------------------------------------------------
OVERALL_COLORS = {
    OV_MISSING: (220, 30, 30),      # red
    OV_INVALID: (220, 30, 220),     # magenta
    OV_BLANK:   (255, 140, 0),      # orange
    OV_UNKNOWN: (245, 220, 40),     # yellow
    OV_OK:      (40, 170, 70),      # green
}

# Shown in the results window and written into the legend view.
OVERALL_MEANING = {
    OV_MISSING: 'One or more required IFC parameters do not exist',
    OV_INVALID: 'IFC entity or predefined type is not valid',
    OV_BLANK:   'Parameter exists but carries no value',
    OV_UNKNOWN: 'Parameter could not be inspected or validated',
    OV_OK:      'All three parameters present and valid',
}

# ---------------------------------------------------------------------------
# Structural categories offered in the scan window.
#
#   label    what the user sees
#   bic      BuiltInCategory name, resolved with getattr so a category that
#            does not exist in a given Revit version simply drops out
#   filter   optional extra instance test, see ifc_collect.STRUCTURAL_FILTERS
#   default  ticked when the window opens
# ---------------------------------------------------------------------------
CATEGORIES = [
    {'label': 'Structural Walls',                'bic': 'OST_Walls',
     'filter': 'wall_structural',  'default': True},
    {'label': 'Structural Columns',              'bic': 'OST_StructuralColumns',
     'filter': None,               'default': True},
    {'label': 'Structural Framing',              'bic': 'OST_StructuralFraming',
     'filter': None,               'default': True},
    {'label': 'Structural Foundations',          'bic': 'OST_StructuralFoundation',
     'filter': None,               'default': True},
    {'label': 'Structural Slabs (Floors)',       'bic': 'OST_Floors',
     'filter': 'floor_structural', 'default': True},
    {'label': 'Structural Connections',          'bic': 'OST_StructConnections',
     'filter': None,               'default': True},
    {'label': 'Structural Rebar',                'bic': 'OST_Rebar',
     'filter': None,               'default': True},
    {'label': 'Structural Area Reinforcement',   'bic': 'OST_AreaRein',
     'filter': None,               'default': True},
    {'label': 'Structural Path Reinforcement',   'bic': 'OST_PathRein',
     'filter': None,               'default': True},
    {'label': 'Structural Fabric Reinforcement', 'bic': 'OST_FabricReinforcement',
     'filter': None,               'default': True},
    {'label': 'Structural Fabric Areas',         'bic': 'OST_FabricAreas',
     'filter': None,               'default': True},
    {'label': 'Structural Trusses',              'bic': 'OST_Truss',
     'filter': None,               'default': True},
    {'label': 'Structural Stiffeners',           'bic': 'OST_StructuralStiffener',
     'filter': None,               'default': True},
    {'label': 'Structural Beam Systems',         'bic': 'OST_StructuralFramingSystem',
     'filter': None,               'default': True},
    # Non-structural supersets a user may still want to audit. Unticked by
    # default so the tool stays a structural audit unless asked otherwise.
    {'label': 'Walls (all, incl. non-structural)',  'bic': 'OST_Walls',
     'filter': None,               'default': False},
    {'label': 'Floors (all, incl. non-structural)', 'bic': 'OST_Floors',
     'filter': None,               'default': False},
]

# ---------------------------------------------------------------------------
# Validation behaviour.
# ---------------------------------------------------------------------------
# Tokens that are legitimate values of "Export to IFC As" even though they are
# not IFC entity names. The Revit IFC exporter honours these.
EXPORT_AS_SPECIAL_TOKENS = ['dontexport', 'default']

# When the exporter enums cannot be reflected at runtime, fall back to the
# curated IFC4 table in ifc_validate.FALLBACK_PREDEFINED_TYPES. Set False to
# report "Unable to verify" instead of relying on the table.
USE_FALLBACK_ENUMS = True

# ---------------------------------------------------------------------------
# Optional 3D visualisation.
# ---------------------------------------------------------------------------
VIEW_NAME_BASE   = 'IFC Audit - Missing Parameters'
LEGEND_VIEW_NAME = 'IFC Audit - Legend'

# Prefix for the view filters created in filter mode. Filters are project-wide
# elements, so a recognisable prefix keeps them together in the filter list.
FILTER_NAME_PREFIX = 'IFC Audit - '

# Drive the generated view with Revit view filters instead of per-element
# overrides. Filters are re-evaluated by Revit continuously, so an element
# leaves the view as soon as its IFC parameters are filled in - the view stays
# correct without re-running the audit.
#
# The trade-off: a filter is a rule over parameter values, so it can express
# "not set" exactly but cannot express "set to an invalid value". In filter
# mode the view shows what is unfilled, not what is wrong, and the audit table
# and CSV remain the authoritative record. See ifc_filters for the detail.
USE_VIEW_FILTERS_DEFAULT = True

# Create a drafting view holding the colour key alongside the 3D view.
CREATE_LEGEND_VIEW = True

# Detail level of the generated view: 'Coarse', 'Medium' or 'Fine'.
# Medium keeps large models responsive.
VIEW_DETAIL_LEVEL = 'Medium'

# Surface transparency, 0-100. 0 is solid.
VIEW_TRANSPARENCY = 0

# Hide categories that were not audited, so the audit colours are not lost in
# the rest of the model. Only ever applied to the generated view.
HIDE_UNRELATED_CATEGORIES = True

# Colour elements that passed the audit green as well. False leaves passing
# elements with their normal graphics so the issues stand out.
COLOR_PASSING_ELEMENTS = True

# Default state of the "show only elements with issues" option in the scan
# window. When that option is on, elements whose overall status is not in
# VISIBLE_WHEN_ONLY_ISSUES are hidden in the generated view - in that view
# only, by element, never by deleting or changing anything.
SHOW_ONLY_ISSUES_DEFAULT = False

# Which statuses stay visible when the view is limited to problem elements.
# OV_INVALID is included on purpose: those elements do hold a value, it is
# just not a valid one, and a wrong value needs attention more urgently than
# an empty one. Drop it from this list if you only want unfilled parameters.
VISIBLE_WHEN_ONLY_ISSUES = [OV_MISSING, OV_INVALID, OV_BLANK, OV_UNKNOWN]
