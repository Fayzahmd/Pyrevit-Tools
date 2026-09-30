# -*- coding: utf-8 -*-
__title__ = "Split Wall - No Delete"
__doc__ = """Split walls without deleting original - manual cleanup required"""

from pyrevit import revit, DB, script, forms
from Autodesk.Revit.DB import Wall, XYZ, Transaction, ElementId, Level, BuiltInParameter
import clr
clr.AddReference('System')
from System.Collections.Generic import List

# Get document
uidoc = __revit__.ActiveUIDocument
doc = __revit__.ActiveUIDocument.Document

def select_walls():
    """Select walls from current selection"""
    output = script.get_output()
    sel_ids = uidoc.Selection.GetElementIds()
    if not sel_ids:
        output.print_md("**Please select wall(s) first**")
        script.exit()
    
    walls = []
    for id in sel_ids:
        try:
            element = doc.GetElement(id)
            if element and isinstance(element, Wall):
                walls.append(element)
        except:
            continue
    
    if not walls:
        output.print_md("**No walls found in selection**")
        script.exit()
    return walls

def get_levels():
    """Get all levels"""
    collector = DB.FilteredElementCollector(doc).OfClass(Level)
    levels = list(collector)
    return sorted(levels, key=lambda x: x.Elevation)

def create_wall_segments_only(doc, wall, selected_levels):
    """Create wall segments without deleting original"""
    output = script.get_output()
    
    try:
        # Get wall properties
        location_curve = wall.Location.Curve
        wall_type = wall.WallType
        
        # Get structural property
        structural = False
        try:
            structural_param = wall.get_Parameter(BuiltInParameter.WALL_STRUCTURAL_SIGNIFICANT)
            if structural_param:
                structural = bool(structural_param.AsInteger())
        except:
            pass
        
        # Get wall boundaries
        base_constraint = wall.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT).AsElementId()
        top_constraint = wall.get_Parameter(BuiltInParameter.WALL_HEIGHT_TYPE).AsElementId()
        base_offset = wall.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET).AsDouble()
        top_offset = wall.get_Parameter(BuiltInParameter.WALL_TOP_OFFSET).AsDouble()
        
        base_level_elem = wall.Document.GetElement(base_constraint)
        top_level_elem = wall.Document.GetElement(top_constraint)
        
        base_elev = base_level_elem.Elevation + base_offset
        top_elev = top_level_elem.Elevation + top_offset
        
        # Create a comprehensive list of levels to use for splitting
        split_levels = []
        
        # Add wall's base level if not in selected levels
        if base_level_elem not in selected_levels:
            split_levels.append(base_level_elem)
        
        # Add selected levels that fall within the wall height
        for lvl in selected_levels:
            if base_elev <= lvl.Elevation <= top_elev:
                split_levels.append(lvl)
        
        # Add wall's top level if not in selected levels and different from base
        if top_level_elem not in selected_levels and top_level_elem.Id != base_level_elem.Id:
            split_levels.append(top_level_elem)
        
        # Sort levels by elevation
        split_levels.sort(key=lambda x: x.Elevation)
        
        # Remove duplicates
        unique_levels = []
        seen_ids = set()
        for lvl in split_levels:
            if lvl.Id not in seen_ids:
                unique_levels.append(lvl)
                seen_ids.add(lvl.Id)
        
        if len(unique_levels) < 2:
            output.print_md("**Warning: Not enough levels to split wall**")
            return 0
        
        # Create segments between consecutive levels
        trans = Transaction(doc, "Create Wall Segments")
        trans.Start()
        
        new_walls = []
        try:
            for i in range(len(unique_levels) - 1):
                base_level = unique_levels[i]
                top_level = unique_levels[i + 1]
                height = top_level.Elevation - base_level.Elevation
                
                if height > 0:
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
            
            trans.Commit()
            return len(new_walls)
            
        except Exception as e:
            trans.RollBack()
            raise e
            
    except Exception as e:
        output.print_md("**Error: {}**".format(str(e)))
        return 0

# Main execution
try:
    output = script.get_output()
    
    # Get levels
    levels = get_levels()
    if len(levels) < 2:
        output.print_md("**Need at least 2 levels**")
        script.exit()
    
    # Select levels to use
    level_names = [lvl.Name for lvl in levels]
    selected_names = forms.SelectFromList.show(
        level_names,
        multiselect=True,
        title="Select Levels for Splitting"
    )
    
    if not selected_names:
        output.print_md("**No levels selected**")
        script.exit()
    
    selected_levels = [lvl for lvl in levels if lvl.Name in selected_names]
    selected_levels.sort(key=lambda x: x.Elevation)
    
    # Get walls
    walls = select_walls()
    
    # Process walls
    total_segments = 0
    for wall in walls:
        try:
            segments = create_wall_segments_only(doc, wall, selected_levels)
            if segments > 0:
                output.print_md("**Created {} segments for Wall {}**".format(segments, wall.Id))
                total_segments += segments
        except Exception as e:
            output.print_md("**Failed to process Wall {}: {}**".format(wall.Id, str(e)))
    
    if total_segments > 0:
        output.print_md("**SUCCESS: Created {} total wall segments**".format(total_segments))
        output.print_md("**NOTE: Original walls were NOT deleted. You can delete them manually if needed.**")
    else:
        output.print_md("**No segments were created**")

except Exception as e:
    script.get_output().print_md("**Script Error: {}**".format(str(e)))