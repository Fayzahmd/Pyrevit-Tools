
# -*- coding: utf-8 -*-
__title__ = "Split Column by Level"
__doc__ = """
pyRevit PushButton Script: Split Column by Level

This script allows the user to select one or more vertical columns in a Revit project and split them into stacked segments, with each segment corresponding to the space between consecutive building levels. The script uses pyRevit's forms for user dialogs and the Revit API for geometry and transaction management. It is intended to be run as a pushbutton inside a pyRevit extension.

Main Features:
- Prompts the user to select column(s) to split.
- Prompts the user to select levels to use as split points.
- Splits each selected column into segments between the selected levels.
- Preserves column family type and structural properties where applicable.
- Handles all geometry and parameter management inside a single Revit transaction.

Requirements:
- Must be run inside the pyRevit environment (Revit + pyRevit add-in).
- User must have a Revit project open with columns and levels defined.

Author: Erik Frits (adapted for clarity and compatibility) — column variant
Version: 1.0
Date: 15.06.2024
"""


# --------------------
# Imports: pyRevit and Revit API modules for UI, document, and geometry access
# --------------------
from pyrevit import revit, DB, script, forms  # pyRevit core modules
from Autodesk.Revit.DB import XYZ, Transaction, ElementId, Level, BuiltInParameter  # Revit API
from Autodesk.Revit.DB import FamilyInstance, BuiltInCategory
from Autodesk.Revit.DB import LocationPoint, LocationCurve
from Autodesk.Revit.DB.Structure import StructuralType
from Autodesk.Revit.DB import ElementTransformUtils, Line
import clr  # .NET interop
clr.AddReference('System')
from System.Collections.Generic import List  # For .NET list usage
import math


# --------------------
# Get active document and application objects from pyRevit
# --------------------
app = __revit__.Application  # Revit application object
uidoc = __revit__.ActiveUIDocument  # Active UI document (for selection, etc.)
doc = __revit__.ActiveUIDocument.Document  # Active Revit document (for model access)



# --------------------
# Function: select_columns
# Prompts the user to select one or more column elements in the active Revit document.
# Returns a list of Column objects. Exits if no valid selection is made.
#
# Returns:
#   - columns: List[FamilyInstance] - The selected column elements
# --------------------
def select_columns():
    """
    Uses the current Revit pre-selection and filters to column FamilyInstances
    in categories Structural Columns or Architectural Columns.
    Returns a list of FamilyInstance columns.
    """
    output = script.get_output()
    sel_ids = uidoc.Selection.GetElementIds()
    if not sel_ids:
        output.print_md("**No elements are pre-selected in Revit. Please select column(s) and rerun the script.**")
        script.exit()
    elements = [doc.GetElement(id) for id in sel_ids]
    cols = []
    for el in elements:
        if isinstance(el, FamilyInstance):
            cat = el.Category
            if cat and cat.Id.IntegerValue in (
                int(BuiltInCategory.OST_StructuralColumns),
                int(BuiltInCategory.OST_Columns),
            ):
                cols.append(el)
    if not cols:
        output.print_md("**No column elements found in selection. Please select column(s) and rerun the script.**")
        script.exit()
    return cols



# --------------------
# Function: select_levels
# Prompts the user to select one or more levels from a list dialog.
# Returns a list of Level objects matching the selected names.
#
# Args:
#   - levels: List[Level] - All levels in the project
# Returns:
#   - List[Level] - The selected levels
# --------------------
def select_levels(levels):
    """
    Prompts the user to select one or more levels from a list dialog.
    Returns a list of Level objects matching the selected names.
    """
    level_names = [lvl.Name for lvl in levels]
    selected_names = forms.SelectFromList.show(
        level_names,
        multiselect=True,
        title="Select Levels to Use for Splitting (hold Ctrl or Shift for multi-select)"
    )
    if not selected_names:
        return []
    # Return Level objects matching selected names
    return [lvl for lvl in levels if lvl.Name in selected_names]



# --------------------
# Function: get_sorted_levels
# Retrieves all Level elements in the document and returns them sorted by elevation (lowest to highest).
#
# Args:
#   - doc: The active Revit document
# Returns:
#   - List[Level] - Sorted list of unique Level objects
# --------------------
def get_sorted_levels(doc):
    """
    Retrieves all Level elements in the document and returns them sorted by elevation (lowest to highest).
    """
    collector = DB.FilteredElementCollector(doc).OfClass(Level)
    levels = sorted([lvl for lvl in collector], key=lambda x: x.Elevation)
    return levels



# --------------------
# Function: get_wall_level_indices
# Determines the indices of the base and top levels for a given wall in the sorted levels list.
#
# Args:
#   - wall: Wall - The wall to analyze
#   - levels: List[Level] - Sorted list of levels
# Returns:
#   - (int, int) - Indices in the levels list for the wall's base and top
# --------------------
def _get_param_double(el, bip_list):
    for bip in bip_list:
        p = el.get_Parameter(bip)
        if p and hasattr(p, 'AsDouble'):
            try:
                val = p.AsDouble()
                return val
            except Exception:
                continue
    return None


def get_column_base_top_info(col, all_levels):
    """
    Determine base and top elevations for a column. Works for:
    - Level-constrained with offsets (preferred)
    - Unconnected/attached by deriving from geometry or height parameters
    Returns (base_elev, top_elev).
    """
    base_lvl_id_param = col.get_Parameter(BuiltInParameter.FAMILY_BASE_LEVEL_PARAM)
    top_lvl_id_param = col.get_Parameter(BuiltInParameter.FAMILY_TOP_LEVEL_PARAM)

    base_elev = None
    top_elev = None

    # Preferred: use level + offset when available
    if base_lvl_id_param:
        base_level = col.Document.GetElement(base_lvl_id_param.AsElementId())
        base_off = _get_param_double(col, [BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM]) or 0.0
        if isinstance(base_level, Level):
            base_elev = base_level.Elevation + base_off
    if top_lvl_id_param:
        top_level = col.Document.GetElement(top_lvl_id_param.AsElementId())
        top_off = _get_param_double(col, [BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM]) or 0.0
        if isinstance(top_level, Level):
            top_elev = top_level.Elevation + top_off

    # Fallbacks when one or both are missing
    loc = col.Location
    try:
        if isinstance(loc, LocationPoint):
            if base_elev is None:
                base_elev = loc.Point.Z
        else:
            crv = getattr(loc, 'Curve', None)
            if crv is not None:
                z0 = crv.GetEndPoint(0).Z
                z1 = crv.GetEndPoint(1).Z
                if base_elev is None:
                    base_elev = min(z0, z1)
                if top_elev is None:
                    top_elev = max(z0, z1)
    except Exception:
        pass

    # Try height-based fallback for point columns
    if top_elev is None and base_elev is not None:
        height = _get_param_double(col, [
            BuiltInParameter.FAMILY_HEIGHT_PARAM,
            BuiltInParameter.INSTANCE_LENGTH_PARAM,
            BuiltInParameter.COLUMN_HEIGHT,
        ])
        if height is not None:
            top_elev = base_elev + height

    if base_elev is None or top_elev is None:
        raise ValueError("Unable to determine column base/top elevations. Ensure location or levels are set.")

    # Normalize: ensure base <= top
    if base_elev > top_elev:
        base_elev, top_elev = top_elev, base_elev

    return base_elev, top_elev


def build_split_elevations_for_element(all_levels, selected_levels, base_elev, top_elev):
    """
    Build an ordered list of elevations to define split spans for the element:
    [base_elev] + (selected_levels within span) + [top_elev]
    Elevations are sorted and de-duplicated.
    """
    lo, hi = (base_elev, top_elev) if base_elev <= top_elev else (top_elev, base_elev)
    in_span = [lvl.Elevation for lvl in selected_levels if lo < lvl.Elevation < hi]
    ordered = [base_elev] + sorted(set(in_span)) + [top_elev]
    # De-duplicate with tolerance
    result = []
    for e in ordered:
        if not result or abs(e - result[-1]) > 1e-9:
            result.append(e)
    return result


def level_and_offset_for_elevation(levels, elev):
    """Pick the level at or below the elevation (closest) and compute offset."""
    if not levels:
        return None, 0.0
    candidate = None
    for lvl in levels:
        if lvl.Elevation <= elev:
            candidate = lvl
        else:
            break
    if candidate is None:
        candidate = levels[0] if levels else None
    return candidate, elev - candidate.Elevation if candidate else 0.0


def get_point_and_rotation(loc):
    """
    Extract base point and rotation from column location.
    Handles LocationPoint and LocationCurve (Line).
    """
    if isinstance(loc, LocationPoint):
        return loc.Point, loc.Rotation
    elif isinstance(loc, LocationCurve):
        curve = loc.Curve
        if isinstance(curve, DB.Line):
            start = curve.GetEndPoint(0)
            end = curve.GetEndPoint(1)
            vector = end - start
            # Rotation is the signed angle in XY plane
            rotation = math.atan2(vector.Y, vector.X)
            return start, rotation
    return None, 0.0



# --------------------
# Function: split_column_by_levels
# Deletes the original column and creates new column segments for each level span between base and top.
#
# Args:
#   - doc: The active Revit document
#   - col: FamilyInstance - The column to split
#   - levels: List[Level] - The sorted levels
#   - split_elevs: List[float] - The elevations to split at
# --------------------
def split_column_by_levels(doc, col, levels, split_elevs):
    """
    Deletes the original column and creates new stacked column instances for each
    consecutive pair in split_levels. New instances inherit the symbol and location.
    """
    if not split_elevs or len(split_elevs) < 2:
        return

    # Determine location and symbol
    loc = col.Location
    symbol = col.Symbol
    if symbol and hasattr(symbol, 'IsActive') and not symbol.IsActive:
        with Transaction(doc, "Activate Column Type") as t:
            t.Start()
            symbol.Activate()
            doc.Regenerate()
            t.Commit()

    point, rotation = get_point_and_rotation(loc)
    if point is None:
        raise ValueError("Unable to determine column base point.")

    # Check if original is line-based for incline preservation
    is_line_based = isinstance(loc, LocationCurve) and isinstance(loc.Curve, DB.Line)
    if is_line_based:
        original_line = loc.Curve
        start = original_line.GetEndPoint(0)
        end = original_line.GetEndPoint(1)
        point = start  # Use start as base point

    # Build list of (base_level, top_level) pairs for each segment
    segments = []
    for i in range(len(split_elevs) - 1):
        e0 = split_elevs[i]
        e1 = split_elevs[i + 1]
        if abs(e1 - e0) > 1e-9:
            segments.append((e0, e1))

    if not segments:
        return

    with Transaction(doc, "Split Column by Level") as trans:
        trans.Start()
        new_ids = []
        for e0, e1 in segments:
            b_lvl, b_off = level_and_offset_for_elevation(levels, e0)
            t_lvl, t_off = level_and_offset_for_elevation(levels, e1)
            if b_lvl is None or t_lvl is None:
                raise ValueError("Unable to find levels for column segment elevations.")

            if is_line_based:
                # Calculate new start and end points for this segment
                total_height = original_line.Length
                seg_height = e1 - e0
                # Fraction along the original line for each segment
                start_frac = (e0 - split_elevs[0]) / (split_elevs[-1] - split_elevs[0])
                end_frac = (e1 - split_elevs[0]) / (split_elevs[-1] - split_elevs[0])
                seg_start = start + (end - start) * start_frac
                seg_end = start + (end - start) * end_frac
                seg_line = Line.CreateBound(seg_start, seg_end)
                new_col = doc.Create.NewFamilyInstance(seg_line, symbol, b_lvl, StructuralType.Column)
            else:
                new_col = doc.Create.NewFamilyInstance(point, symbol, b_lvl, StructuralType.Column)

            # Constrain base/top levels
            new_col.get_Parameter(BuiltInParameter.FAMILY_BASE_LEVEL_PARAM).Set(b_lvl.Id)
            new_col.get_Parameter(BuiltInParameter.FAMILY_TOP_LEVEL_PARAM).Set(t_lvl.Id)
            # Apply offsets matching elevations
            base_off_p = new_col.get_Parameter(BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM)
            top_off_p = new_col.get_Parameter(BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM)
            if base_off_p: base_off_p.Set(b_off)
            if top_off_p: top_off_p.Set(t_off)

            new_ids.append(new_col.Id)

        doc.Delete(col.Id)
        trans.Commit()



# --------------------
# Main Script Execution
# Entry point for the pushbutton. Handles user dialogs and column splitting logic.
# --------------------
try:
    # Retrieve all levels in the project, sorted by elevation
    all_levels = get_sorted_levels(doc)

    # Prompt user to select columns (pre-selection)
    selected_columns = select_columns()

    # Prompt user to select levels (dialog box)
    selected_levels = select_levels(all_levels)
    if not selected_levels:
        script.get_output().print_md("**No levels selected.**")
        script.exit()

    # For each selected column, split by the selected levels
    for col in selected_columns:
        base_elev, top_elev = get_column_base_top_info(col, all_levels)
        split_elevs = build_split_elevations_for_element(all_levels, selected_levels, base_elev, top_elev)
        split_column_by_levels(doc, col, all_levels, split_elevs)

    # Notify user of success
    script.get_output().print_md("**Columns split by selected levels successfully!**")
except Exception as e:
    # Print error message to pyRevit output and exit script
    script.get_output().print_md("**Error:** {}".format(e))
    script.exit()


