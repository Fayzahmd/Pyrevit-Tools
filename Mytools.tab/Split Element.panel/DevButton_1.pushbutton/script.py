
# -*- coding: utf-8 -*-
__title__ = "Split Wall by Level"
__doc__ = """
pyRevit PushButton Script: Split Wall by Level

This script allows the user to select one or more walls in a Revit project and split them into segments, with each segment corresponding to the space between consecutive building levels. The script uses pyRevit's forms for user dialogs and the Revit API for geometry and transaction management. It is intended to be run as a pushbutton inside a pyRevit extension.

Main Features:
- Prompts the user to select wall(s) to split.
- Prompts the user to select levels to use as split points.
- Splits each selected wall into segments between the selected levels.
- Preserves wall type and structural properties.
- Handles all geometry and parameter management inside a single Revit transaction.

Requirements:
- Must be run inside the pyRevit environment (Revit + pyRevit add-in).
- User must have a Revit project open with walls and levels defined.

Author: Erik Frits (adapted for clarity and compatibility)
Version: 1.0
Date: 15.06.2024

NOTE: VS Code/Pylance warnings about unresolved imports and unknown types are expected
      when editing pyRevit scripts outside of the Revit environment. These warnings do not
      indicate actual errors - the script will work correctly when run inside Revit with pyRevit.
"""


# --------------------
# Imports: pyRevit and Revit API modules for UI, document, and geometry access
# --------------------
from pyrevit import revit, DB, script, forms  # pyRevit core modules
from Autodesk.Revit.DB import Wall, XYZ, Transaction, ElementId, Level, BuiltInParameter  # Revit API
import clr  # .NET interop

# Import .NET List without explicit System reference - pyRevit should have this available
try:
    from System.Collections.Generic import List
except ImportError:
    # Fallback: If import fails, we'll handle it in the deletion code
    List = None


# --------------------
# Get active document and application objects from pyRevit
# --------------------
app = __revit__.Application  # Revit application object
uidoc = __revit__.ActiveUIDocument  # Active UI document (for selection, etc.)
doc = __revit__.ActiveUIDocument.Document  # Active Revit document (for model access)



# --------------------
# Function: select_walls
# Prompts the user to select one or more wall elements in the active Revit document.
# Returns a list of Wall objects. Exits if no valid selection is made.
#
# Returns:
#   - walls: List[Wall] - The selected wall elements
# --------------------
def select_walls():
    """
    Prompts the user to select one or more wall elements in the active Revit document using a dialog box.
    Returns a list of Wall objects. If no valid selection is made, prints a message and exits the script.
    """
    # Only use pre-selected elements in Revit (no dialog)
    output = script.get_output()
    sel_ids = uidoc.Selection.GetElementIds()
    if not sel_ids:
        output.print_md("**No elements are pre-selected in Revit. Please select wall(s) and rerun the script.**")
        script.exit()
    elements = [doc.GetElement(id) for id in sel_ids]
    # Filter only Wall elements from selection
    walls = [el for el in elements if isinstance(el, Wall)]
    if not walls:
        output.print_md("**No wall elements found in selection. Please select wall(s) and rerun the script.**")
        script.exit()
    return walls



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
def get_wall_boundaries(wall):
    """
    Get the actual base and top elevations of a wall.
    Returns (base_elevation, top_elevation).
    """
    # Get base and top constraints and offsets
    base_constraint = wall.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT).AsElementId()
    base_offset = wall.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET).AsDouble()
    top_constraint = wall.get_Parameter(BuiltInParameter.WALL_HEIGHT_TYPE).AsElementId()
    top_offset = wall.get_Parameter(BuiltInParameter.WALL_TOP_OFFSET).AsDouble()
    
    # Get Level elements for base and top
    base_level = wall.Document.GetElement(base_constraint)
    top_level = wall.Document.GetElement(top_constraint)
    
    # Calculate actual elevations
    base_elev = base_level.Elevation + base_offset
    top_elev = top_level.Elevation + top_offset
    
    return base_elev, top_elev

def get_split_levels_for_wall(wall, selected_levels):
    """
    Determines which levels to use for splitting a wall.
    Returns a list of levels that fall within the wall's height range, sorted by elevation.
    """
    base_elev, top_elev = get_wall_boundaries(wall)
    
    # Find levels that are within the wall's height range
    split_levels = []
    
    # Always include a level at or below the base
    base_level_added = False
    for lvl in selected_levels:
        if lvl.Elevation <= base_elev:
            if not base_level_added or lvl.Elevation > split_levels[0].Elevation:
                if base_level_added:
                    split_levels[0] = lvl
                else:
                    split_levels.insert(0, lvl)
                    base_level_added = True
    
    # Add levels that fall within the wall height
    for lvl in selected_levels:
        if base_elev < lvl.Elevation < top_elev:
            split_levels.append(lvl)
    
    # Always include a level at or above the top
    top_level_added = False
    for lvl in selected_levels:
        if lvl.Elevation >= top_elev:
            if not top_level_added or lvl.Elevation < split_levels[-1].Elevation:
                if top_level_added:
                    split_levels[-1] = lvl
                else:
                    split_levels.append(lvl)
                    top_level_added = True
            break
    
    # If we don't have boundary levels, use the wall's existing levels
    if not base_level_added:
        base_constraint = wall.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT).AsElementId()
        base_level = wall.Document.GetElement(base_constraint)
        split_levels.insert(0, base_level)
    
    if not top_level_added:
        top_constraint = wall.get_Parameter(BuiltInParameter.WALL_HEIGHT_TYPE).AsElementId()
        top_level = wall.Document.GetElement(top_constraint)
        split_levels.append(top_level)
    
    # Sort by elevation and remove duplicates
    unique_levels = []
    seen_ids = set()
    for lvl in sorted(split_levels, key=lambda x: x.Elevation):
        if lvl.Id not in seen_ids:
            unique_levels.append(lvl)
            seen_ids.add(lvl.Id)
    
    return unique_levels



# --------------------
# Function: split_wall_by_levels
# Deletes the original wall and creates new wall segments for each level span between base and top.
#
# Args:
#   - doc: The active Revit document
#   - wall: Wall - The wall to split
#   - levels: List[Level] - The selected levels
#   - base_idx: int - Index of the base level
#   - top_idx: int - Index of the top level
# --------------------
def split_wall_by_levels(doc, wall, levels, base_idx, top_idx):
    """
    Deletes the original wall and creates new wall segments for each level span between base and top.
    Each new wall segment matches the original wall's type and structural property, and is assigned the correct base and top levels.
    """
    # Get the wall's geometry and properties
    location_curve = wall.Location.Curve  # The wall's geometry (curve)
    wall_type = wall.WallType  # Wall type to match original
    wall_id = wall.Id  # Store ID before potential deletion
    
    # Get the structural property using the correct parameter
    structural_param = wall.get_Parameter(BuiltInParameter.WALL_STRUCTURAL_SIGNIFICANT)
    structural = False
    if structural_param and hasattr(structural_param, 'AsInteger'):
        structural = bool(structural_param.AsInteger())
    
    wall_segments = []
    # Build list of (base_level, top_level) pairs for each segment
    for idx in range(base_idx, top_idx):
        base_level = levels[idx]
        top_level = levels[idx+1]
        wall_segments.append((base_level, top_level))
    
    if len(wall_segments) == 0:
        raise Exception("No wall segments to create between selected levels")
    
    # Start a transaction to modify the model
    trans = Transaction(doc, "Split Wall by Level")
    trans.Start()
    
    try:
        # Check if wall has dependencies that might prevent deletion
        dependents = None
        try:
            dependents = wall.GetDependentElements(None)
        except:
            pass  # Some walls might not support this method
            
        if dependents and dependents.Count > 0:
            # Wall has dependencies - try a different approach
            # First create the segments, then try deletion
            pass
        
        # Create new wall segments for each level span first
        new_walls = []
        for base_level, top_level in wall_segments:
            try:
                # Calculate height between levels
                height = top_level.Elevation - base_level.Elevation
                if height <= 0:
                    continue  # Skip invalid height segments
                
                # Wall.Create(doc, curve, wallTypeId, levelId, height, offset, flip, structural)
                wall_segment = Wall.Create(doc, location_curve, wall_type.Id, base_level.Id,
                                          height, 0.0, False, structural)
                
                # Set the top constraint for the new wall segment
                top_param = wall_segment.get_Parameter(BuiltInParameter.WALL_HEIGHT_TYPE)
                if top_param:
                    top_param.Set(top_level.Id)
                
                new_walls.append(wall_segment)
                
            except Exception as create_error:
                # If we can't create a segment, continue with others
                continue
        
        if len(new_walls) == 0:
            raise Exception("Failed to create any new wall segments")
        
        # Now delete the original wall using the method that works
        # Based on testing: List deletion works, direct deletion fails with dependencies
        if wall.IsValidObject:
            try:
                # Try to use List[ElementId] method if available - this handles dependent elements properly
                if List is not None:
                    ids_to_delete = List[ElementId]()
                    ids_to_delete.Add(wall_id)
                    deleted_ids = doc.Delete(ids_to_delete)
                else:
                    # Fallback: Use the direct delete method if List is not available
                    deleted_ids = doc.Delete(wall_id)
                # Note: This will delete the wall AND its dependent elements (dimensions, etc.)
            except Exception as del_error:
                # If deletion fails, provide specific error
                raise Exception("Cannot delete original wall (ID: {}). Error: {}".format(wall_id, str(del_error)))
        else:
            raise Exception("Original wall element is no longer valid")
        
        trans.Commit()
        
    except Exception as e:
        trans.RollBack()
        raise Exception("Failed to split wall (ID: {}): {}".format(wall_id, str(e)))



# --------------------
# Main Script Execution
# Entry point for the pushbutton. Handles user dialogs and wall splitting logic.
# --------------------
try:
    # Retrieve all levels in the project, sorted by elevation
    levels = get_sorted_levels(doc)
    if not levels:
        script.get_output().print_md("**No levels found in the project.**")
        script.exit()

    # Prompt user to select walls (dialog box)
    selected_walls = select_walls()
    if not selected_walls:
        script.get_output().print_md("**No walls selected.**")
        script.exit()

    # Prompt user to select levels (dialog box)
    selected_levels = select_levels(levels)
    if not selected_levels:
        script.get_output().print_md("**No levels selected.**")
        script.exit()
    
    # Allow splitting with just 1 level selected
    # With 1 level: split at that level (create segments above and below)
    # With 2+ levels: split between all selected levels

    # For each selected wall, split by the selected levels
    success_count = 0
    error_count = 0
    
    for wall in selected_walls:
        try:
            # Get the levels to use for splitting this specific wall
            wall_split_levels = get_split_levels_for_wall(wall, selected_levels)
            
            # Validate that we have at least 2 levels to split between
            if len(wall_split_levels) < 2:
                script.get_output().print_md("**Warning:** Wall {} does not have enough levels for splitting. Skipping.".format(wall.Id))
                continue
                
            # Split the wall using indices 0 to len-1 (all levels for this wall)
            split_wall_by_levels(doc, wall, wall_split_levels, 0, len(wall_split_levels) - 1)
            success_count += 1
            
        except Exception as wall_error:
            error_count += 1
            script.get_output().print_md("**Error processing Wall {}:** {}".format(wall.Id, str(wall_error)))

    # Notify user of results
    if success_count > 0:
        script.get_output().print_md("**Successfully split {} wall(s) by selected levels!**".format(success_count))
    if error_count > 0:
        script.get_output().print_md("**Failed to process {} wall(s). See errors above.**".format(error_count))
        
except Exception as e:
    # Print error message to pyRevit output and exit script
    script.get_output().print_md("**Critical Error:** {}".format(str(e)))
    import traceback
    script.get_output().print_md("**Details:** {}".format(traceback.format_exc()))
    script.exit()


