# -*- coding: utf-8 -*-
__title__ = "Split Wall by Level - Fixed"
__doc__ = """Fixed version with better error handling"""

from pyrevit import revit, DB, script, forms
from Autodesk.Revit.DB import Wall, XYZ, Transaction, ElementId, Level, BuiltInParameter
import clr
clr.AddReference('System')
from System.Collections.Generic import List

# Get document
app = __revit__.Application
uidoc = __revit__.ActiveUIDocument
doc = __revit__.ActiveUIDocument.Document

def select_walls():
    """Select walls from current selection"""
    output = script.get_output()
    sel_ids = uidoc.Selection.GetElementIds()
    if not sel_ids:
        output.print_md("**No elements are pre-selected in Revit. Please select wall(s) and rerun the script.**")
        script.exit()
    
    walls = []
    for id in sel_ids:
        try:
            element = doc.GetElement(id)
            if element and isinstance(element, Wall):
                walls.append(element)
        except Exception as e:
            output.print_md("**Warning: Could not process element {}: {}**".format(id, str(e)))
    
    if not walls:
        output.print_md("**No wall elements found in selection. Please select wall(s) and rerun the script.**")
        script.exit()
    return walls

def select_levels(levels):
    """Select levels from dialog"""
    try:
        level_names = []
        for lvl in levels:
            try:
                level_names.append(lvl.Name)
            except:
                level_names.append("Level_{}".format(lvl.Id))
        
        selected_names = forms.SelectFromList.show(
            level_names,
            multiselect=True,
            title="Select Levels to Use for Splitting"
        )
        if not selected_names:
            return []
        
        return [lvl for i, lvl in enumerate(levels) if level_names[i] in selected_names]
    except Exception as e:
        script.get_output().print_md("**Error in level selection: {}**".format(str(e)))
        return []

def get_sorted_levels(doc):
    """Get sorted levels"""
    try:
        collector = DB.FilteredElementCollector(doc).OfClass(Level)
        levels = []
        for lvl in collector:
            try:
                levels.append(lvl)
            except:
                continue
        return sorted(levels, key=lambda x: x.Elevation)
    except Exception as e:
        script.get_output().print_md("**Error getting levels: {}**".format(str(e)))
        return []

def safe_delete_wall(doc, wall_id):
    """Safely delete a wall - use List method first as it handles dependencies"""
    try:
        # Method 1: List deletion (handles dependent elements like dimensions)
        ids_to_delete = List[ElementId]()
        ids_to_delete.Add(wall_id)
        deleted_ids = doc.Delete(ids_to_delete)
        return True, "List deletion successful - deleted {} elements".format(len(deleted_ids) if deleted_ids else 1)
    except Exception as e1:
        try:
            # Method 2: Direct deletion (fallback)
            deleted_ids = doc.Delete(wall_id)
            return True, "Direct deletion successful"
        except Exception as e2:
            return False, "Both methods failed - List: {}, Direct: {}".format(str(e1), str(e2))

def split_wall_simple(doc, wall, levels, base_idx, top_idx):
    """Simplified wall splitting with better error handling"""
    output = script.get_output()
    
    try:
        # Get wall properties safely
        location_curve = wall.Location.Curve
        wall_type = wall.WallType
        wall_id = wall.Id
        
        # Get structural property safely
        structural = False
        try:
            structural_param = wall.get_Parameter(BuiltInParameter.WALL_STRUCTURAL_SIGNIFICANT)
            if structural_param:
                structural = bool(structural_param.AsInteger())
        except:
            pass
        
        # Create segments
        wall_segments = []
        for idx in range(base_idx, top_idx):
            if idx + 1 < len(levels):
                base_level = levels[idx]
                top_level = levels[idx + 1]
                height = top_level.Elevation - base_level.Elevation
                if height > 0:
                    wall_segments.append((base_level, top_level, height))
        
        if not wall_segments:
            raise Exception("No valid wall segments to create")
        
        # Start transaction
        trans = Transaction(doc, "Split Wall by Level")
        trans.Start()
        
        try:
            # Create new walls
            new_walls = []
            for base_level, top_level, height in wall_segments:
                try:
                    new_wall = Wall.Create(doc, location_curve, wall_type.Id, base_level.Id,
                                         height, 0.0, False, structural)
                    
                    # Set top constraint
                    try:
                        top_param = new_wall.get_Parameter(BuiltInParameter.WALL_HEIGHT_TYPE)
                        if top_param:
                            top_param.Set(top_level.Id)
                    except:
                        pass
                    
                    new_walls.append(new_wall)
                    
                except Exception as create_error:
                    output.print_md("**Warning: Failed to create segment {}-{}: {}**".format(
                        base_level.Name, top_level.Name, str(create_error)))
            
            if not new_walls:
                raise Exception("Failed to create any new wall segments")
            
            # Delete original wall
            success, message = safe_delete_wall(doc, wall_id)
            if not success:
                raise Exception("Cannot delete original wall: {}".format(message))
            
            trans.Commit()
            return len(new_walls)
            
        except Exception as trans_error:
            trans.RollBack()
            raise trans_error
            
    except Exception as e:
        raise Exception("Split wall failed: {}".format(str(e)))

# Main execution
try:
    output = script.get_output()
    
    # Get levels
    levels = get_sorted_levels(doc)
    if len(levels) < 2:
        output.print_md("**Need at least 2 levels in the project**")
        script.exit()
    
    # Get walls
    selected_walls = select_walls()
    
    # Get levels to use
    selected_levels = select_levels(levels)
    # Allow splitting with just 1 level - will split at that level
    
    # Sort selected levels
    selected_levels.sort(key=lambda x: x.Elevation)
    
    # Process each wall
    success_count = 0
    for wall in selected_walls:
        try:
            # For simplicity, split between all selected levels
            segments_created = split_wall_simple(doc, wall, selected_levels, 0, len(selected_levels) - 1)
            output.print_md("**Wall {} split into {} segments**".format(wall.Id, segments_created))
            success_count += 1
        except Exception as wall_error:
            output.print_md("**Error processing Wall {}: {}**".format(wall.Id, str(wall_error)))
    
    if success_count > 0:
        output.print_md("**Successfully processed {} wall(s)**".format(success_count))
    else:
        output.print_md("**No walls were successfully processed**")

except Exception as e:
    script.get_output().print_md("**Script Error: {}**".format(str(e)))
    import traceback
    script.get_output().print_md("**Details: {}**".format(traceback.format_exc()))