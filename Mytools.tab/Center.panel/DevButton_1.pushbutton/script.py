# -*- coding: utf-8 -*-
"""
Center Viewport in Title Block
Moves all viewports on the active sheet to be centered within the title block.
"""

__title__ = "Center Viewport"
__author__ = "Your Name"
__doc__ = """Centers all viewports on the active sheet within the title block."""

# Import necessary modules
import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *
from Autodesk.Revit.UI.Selection import *
from pyrevit import revit, forms
from pyrevit.framework import List

# Get the current Revit document and UI document
doc = revit.doc
uidoc = revit.uidoc

def get_user_selected_bounding_box(sheet):
    """
    Allow user to select two points to define a bounding box area.
    
    Args:
        sheet (ViewSheet): The sheet view where selection will occur
        
    Returns:
        BoundingBoxXYZ: The user-defined bounding box, or None if cancelled
    """
    try:
        # Prompt user to select first point
        forms.alert("Click to select the FIRST CORNER of the bounding area.", 
                   title="Select First Corner")
        
        # Get the first point
        first_point = uidoc.Selection.PickPoint("Select first corner of bounding area")
        
        # Prompt user to select second point
        forms.alert("Click to select the OPPOSITE CORNER of the bounding area.", 
                   title="Select Second Corner")
        
        # Get the second point
        second_point = uidoc.Selection.PickPoint("Select opposite corner of bounding area")
        
        # Create bounding box from the two points
        # Ensure min and max are correctly assigned
        min_x = min(first_point.X, second_point.X)
        max_x = max(first_point.X, second_point.X)
        min_y = min(first_point.Y, second_point.Y)
        max_y = max(first_point.Y, second_point.Y)
        min_z = min(first_point.Z, second_point.Z)
        max_z = max(first_point.Z, second_point.Z)
        
        # Create the bounding box
        bbox = BoundingBoxXYZ()
        bbox.Min = XYZ(min_x, min_y, min_z)
        bbox.Max = XYZ(max_x, max_y, max_z)
        
        return bbox
        
    except Exception as e:
        # User cancelled or error occurred
        return None

def get_title_block_bounding_box(sheet):
    """
    Get the bounding box of the title block on the given sheet.
    
    Args:
        sheet (ViewSheet): The sheet view to search for title block
        
    Returns:
        BoundingBoxXYZ: The bounding box of the title block, or None if not found
    """
    # Create a filtered element collector to find title blocks
    title_block_collector = FilteredElementCollector(doc, sheet.Id)\
        .OfCategory(BuiltInCategory.OST_TitleBlocks)\
        .WhereElementIsNotElementType()
    
    title_blocks = list(title_block_collector)
    
    if not title_blocks:
        return None
        
    # Get the first title block (typically there's only one per sheet)
    title_block = title_blocks[0]
    
    # Get the bounding box of the title block
    bbox = title_block.get_BoundingBox(sheet)
    
    return bbox

def get_viewports_on_sheet(sheet):
    """
    Get all viewports placed on the given sheet.
    
    Args:
        sheet (ViewSheet): The sheet view to search for viewports
        
    Returns:
        list: List of Viewport elements on the sheet
    """
    # Create a filtered element collector to find viewports
    viewport_collector = FilteredElementCollector(doc, sheet.Id)\
        .OfClass(Viewport)
    
    return list(viewport_collector)

def calculate_center_point(bbox):
    """
    Calculate the center point of a bounding box.
    
    Args:
        bbox (BoundingBoxXYZ): The bounding box
        
    Returns:
        XYZ: The center point of the bounding box
    """
    min_point = bbox.Min
    max_point = bbox.Max
    
    center_x = (min_point.X + max_point.X) / 2
    center_y = (min_point.Y + max_point.Y) / 2
    center_z = (min_point.Z + max_point.Z) / 2
    
    return XYZ(center_x, center_y, center_z)

def center_viewport_in_title_block():
    """
    Main function to center viewports within the title block.
    """
    # Check if the active view is a sheet view
    active_view = doc.ActiveView
    
    if not isinstance(active_view, ViewSheet):
        forms.alert("Please activate a sheet view before running this script.", 
                   title="Invalid View Type")
        return
    
    sheet = active_view
    
    # Ask user to choose between automatic title block detection or manual selection
    selection_options = ["Auto-detect Title Block", "Manually Select Bounding Area"]
    selected_option = forms.SelectFromList.show(
        selection_options,
        title="Choose Bounding Area Method",
        button_name="Select",
        multiselect=False
    )
    
    if not selected_option:
        # User cancelled
        return
    
    # Get the bounding box based on user choice
    if selected_option == "Auto-detect Title Block":
        target_bbox = get_title_block_bounding_box(sheet)
        
        if not target_bbox:
            forms.alert("No title block found on the current sheet.", 
                       title="Title Block Not Found")
            return
    else:
        # Manual selection
        target_bbox = get_user_selected_bounding_box(sheet)
        
        if not target_bbox:
            forms.alert("Bounding area selection was cancelled or failed.", 
                       title="Selection Cancelled")
            return
    
    # Get all viewports on the sheet
    viewports = get_viewports_on_sheet(sheet)
    
    if not viewports:
        forms.alert("No viewports found on the current sheet.", 
                   title="Viewports Not Found")
        return
    
    # Calculate the center of the target bounding box
    target_center = calculate_center_point(target_bbox)
    
    # Start a transaction to modify the document
    transaction = Transaction(doc, "Center Viewports in Selected Area")
    transaction.Start()
    
    try:
        moved_count = 0
        
        # Process each viewport
        for viewport in viewports:
            # Get the viewport's bounding box
            viewport_bbox = viewport.get_BoundingBox(sheet)
            
            if viewport_bbox:
                # Calculate the current center of the viewport
                viewport_center = calculate_center_point(viewport_bbox)
                
                # Calculate the translation vector needed to center the viewport
                # in the target area
                translation_vector = XYZ(
                    target_center.X - viewport_center.X,
                    target_center.Y - viewport_center.Y,
                    0  # Keep Z coordinate unchanged (viewports are on the sheet plane)
                )
                
                # Move the viewport
                ElementTransformUtils.MoveElement(
                    doc, 
                    viewport.Id, 
                    translation_vector
                )
                
                moved_count += 1
        
        # Commit the transaction
        transaction.Commit()
        
        # Show success message
        if moved_count > 0:
            area_type = "title block" if selected_option == "Auto-detect Title Block" else "selected area"
            message = "Successfully centered {} viewport(s) in the {}.".format(moved_count, area_type)
            forms.alert(message, title="Operation Completed")
        else:
            forms.alert("No viewports were moved. Check viewport bounds.", 
                       title="No Changes Made")
            
    except Exception as e:
        # Roll back the transaction in case of error
        transaction.RollBack()
        forms.alert("An error occurred: {}".format(str(e)), 
                   title="Error")

# Execute the main function
if __name__ == '__main__':
    center_viewport_in_title_block()