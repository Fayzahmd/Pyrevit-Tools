# -*- coding: utf-8 -*-
__title__ = "Debug Wall Deletion"
__doc__ = """Test script to debug wall deletion issues"""

from pyrevit import revit, DB, script
from Autodesk.Revit.DB import Wall, Transaction, ElementId
import clr
clr.AddReference('System')
from System.Collections.Generic import List

# Get document
uidoc = __revit__.ActiveUIDocument
doc = __revit__.ActiveUIDocument.Document

try:
    output = script.get_output()
    
    # Get selected walls
    sel_ids = uidoc.Selection.GetElementIds()
    if not sel_ids:
        output.print_md("**Please select a wall first**")
        script.exit()
    
    wall = None
    wall_id = None
    for id in sel_ids:
        try:
            element = doc.GetElement(id)
            if element and isinstance(element, Wall):
                wall = element
                wall_id = id
                break
        except Exception as get_error:
            output.print_md("**Error getting element {}: {}**".format(id, get_error))
            continue
    
    if not wall:
        output.print_md("**No wall found in selection**")
        script.exit()
    
    output.print_md("**Testing wall deletion for Wall ID: {}**".format(wall_id))
    
    # Test if wall can be deleted
    trans = Transaction(doc, "Test Wall Deletion")
    trans.Start()
    
    try:
        # Check wall properties first - handle potential errors
        try:
            wall_type_name = wall.WallType.Name if wall.WallType else "Unknown"
            output.print_md("Wall Type: {}".format(wall_type_name))
        except:
            output.print_md("Wall Type: Could not retrieve")
        
        try:
            output.print_md("Wall IsValidObject: {}".format(wall.IsValidObject))
        except:
            output.print_md("Wall IsValidObject: Could not check")
        
        # Try to get dependencies
        try:
            from Autodesk.Revit.DB import ElementFilter
            dependents = wall.GetDependentElements(None)
            if dependents and len(dependents) > 0:
                output.print_md("**Wall has {} dependent elements:**".format(len(dependents)))
                for dep_id in dependents:
                    try:
                        dep_elem = doc.GetElement(dep_id)
                        if dep_elem:
                            elem_type_name = dep_elem.GetType().Name if hasattr(dep_elem, 'GetType') else "Unknown"
                            output.print_md("- {} (ID: {})".format(elem_type_name, dep_id))
                    except:
                        output.print_md("- Could not retrieve element (ID: {})".format(dep_id))
            else:
                output.print_md("Wall has no dependent elements")
        except Exception as dep_error:
            output.print_md("**Error checking dependencies:** {}".format(dep_error))
        
        # Try different deletion methods
        output.print_md("**Testing deletion methods...**")
        
        # Method 1: Direct deletion
        try:
            deleted_ids = doc.Delete(wall_id)
            output.print_md("**SUCCESS: Direct deletion worked! Deleted {} elements**".format(len(deleted_ids) if deleted_ids else 0))
            trans.Commit()
            script.exit()
        except Exception as e1:
            output.print_md("**Method 1 Failed:** {}".format(str(e1)))
        
        # Method 2: List deletion
        try:
            ids_to_delete = List[ElementId]()
            ids_to_delete.Add(wall_id)
            deleted_ids = doc.Delete(ids_to_delete)
            output.print_md("**SUCCESS: List deletion worked! Deleted {} elements**".format(len(deleted_ids) if deleted_ids else 0))
            trans.Commit()
            script.exit()
        except Exception as e2:
            output.print_md("**Method 2 Failed:** {}".format(str(e2)))
        
        # If we get here, both methods failed
        trans.RollBack()
        output.print_md("**Both deletion methods failed.**")
        output.print_md("**Possible causes:**")
        output.print_md("- Wall has hosted elements (doors, windows)")
        output.print_md("- Wall is referenced by dimensions")
        output.print_md("- Wall is part of a group or assembly")
        output.print_md("- Wall has geometric constraints")
        output.print_md("- Wall is locked by worksharing")
        
    except Exception as general_error:
        trans.RollBack()
        output.print_md("**General Error:** {}".format(general_error))

except Exception as e:
    output.print_md("**Script Error:** {}".format(e))