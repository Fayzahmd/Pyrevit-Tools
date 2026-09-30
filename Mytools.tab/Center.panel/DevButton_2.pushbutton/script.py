__title__ = "Align Element to Title Block"
__author__ = "Fayaz Ahamed"
__doc__ = """Aligns a selected schedule, legend, or viewport to title block corners or specific points with millimeter offsets."""

"""
Align Single Element to Title Block with Manual Reference Point Option

This script allows you to select a single schedule, legend, or viewport on a sheet 
and align it to the title block using two reference methods:
1. Title block corners (Top-Left, Top-Right, Bottom-Left, Bottom-Right)
2. Manual point selection anywhere within the title block
All with precise millimeter offsets.

Requirements:
- Active view must be a sheet view
- Select one element (schedule, legend, or viewport)
- Choose element reference point and title block reference method
- Apply X and Y offsets in millimeters

Author: PyRevit User
Date: September 19, 2025
"""

# PyRevit imports
from pyrevit import forms, script

# Revit API imports
import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *
from Autodesk.Revit.UI.Selection import *

# Get current Revit application and document
doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
app = __revit__.Application

class ElementSelectionFilter(ISelectionFilter):
    """
    Selection filter to allow selection of schedules, legends, and viewports only.
    """
    def AllowElement(self, elem):
        """Allow selection of schedules, legends, and viewports."""
        # Allow ScheduleSheetInstance (schedules and legends)
        if isinstance(elem, ScheduleSheetInstance):
            return True
        # Allow Viewport
        if isinstance(elem, Viewport):
            return True
        return False
    
    def AllowReference(self, reference, position):
        """Allow all references."""
        return False

def check_active_view_is_sheet():
    """
    Check if the active view is a sheet view.
    Returns True if active view is a ViewSheet, False otherwise.
    """
    active_view = doc.ActiveView
    return isinstance(active_view, ViewSheet)

def get_user_selected_element():
    """
    Allow user to select a single element (schedule, legend, or viewport) to be aligned.
    Returns the selected element or None if cancelled/invalid.
    """
    try:
        # Prompt user to select element
        selection = uidoc.Selection
        selection_filter = ElementSelectionFilter()
        
        reference = selection.PickObject(
            ObjectType.Element,
            selection_filter,
            "Select a schedule, legend, or viewport to align."
        )
        
        if reference:
            element = doc.GetElement(reference.ElementId)
            return element
        
        return None
        
    except Exception as e:
        if "cancelled" in str(e).lower() or "escape" in str(e).lower():
            return None
        else:
            forms.alert("Error during element selection: {}".format(str(e)), title="Selection Error")
            return None

def get_title_block_on_sheet(sheet_view):
    """
    Find the title block family instance on the given sheet.
    Returns the title block element or None if not found.
    """
    # Collect all family instances on the sheet
    collector = FilteredElementCollector(doc, sheet_view.Id)
    family_instances = collector.OfClass(FamilyInstance).ToElements()
    
    # Find the title block (category: OST_TitleBlocks)
    for instance in family_instances:
        if instance.Category and instance.Category.Id.IntegerValue == int(BuiltInCategory.OST_TitleBlocks):
            return instance
    
    return None

def get_user_selected_point():
    """
    Allow user to select a specific point anywhere on the sheet.
    Returns the selected XYZ point or None if cancelled.
    """
    try:
        # Prompt user to pick a point
        selection = uidoc.Selection
        
        point_ref = selection.PickPoint("Click on a specific point in the title block to use as reference.")
        
        if point_ref:
            # Return the picked point coordinates
            return point_ref
        
        return None
        
    except Exception as e:
        if "cancelled" in str(e).lower() or "escape" in str(e).lower():
            return None
        else:
            forms.alert("Error during point selection: {}".format(str(e)), title="Point Selection Error")
            return None

def get_element_bounding_box(element):
    """
    Get the bounding box of an element in the sheet coordinate system.
    Returns BoundingBoxXYZ or None if bounding box cannot be retrieved.
    """
    try:
        bbox = element.get_BoundingBox(doc.ActiveView)
        return bbox
    except:
        return None

def get_corner_point(bbox, corner_type):
    """
    Get the specific corner point from a bounding box.
    
    Args:
        bbox: BoundingBoxXYZ object
        corner_type: String indicating corner ('Top-Left', 'Top-Right', 'Bottom-Left', 'Bottom-Right')
    
    Returns:
        XYZ point representing the corner
    """
    min_point = bbox.Min
    max_point = bbox.Max
    
    if corner_type == "Top-Left":
        return XYZ(min_point.X, max_point.Y, min_point.Z)
    elif corner_type == "Top-Right":
        return XYZ(max_point.X, max_point.Y, min_point.Z)
    elif corner_type == "Bottom-Left":
        return XYZ(min_point.X, min_point.Y, min_point.Z)
    elif corner_type == "Bottom-Right":
        return XYZ(max_point.X, min_point.Y, min_point.Z)
    else:
        # Default to Top-Left
        return XYZ(min_point.X, max_point.Y, min_point.Z)

def get_user_offset_values():
    """
    Get offset values from user input in millimeters.
    Returns tuple (offset_x, offset_y) in feet (converted from mm), or None if cancelled.
    """
    try:
        # Ask if user wants to apply offset
        use_offset = forms.alert("Do you want to apply an offset to the alignment position?", 
                                title="Offset Option", yes=True, no=True)
        
        if not use_offset:
            return (0.0, 0.0)
        
        # Get X offset in mm
        offset_x_str = forms.ask_for_string(
            default="0",
            prompt="Enter X offset in millimeters (positive = right, negative = left):",
            title="X Offset (mm)"
        )
        
        if offset_x_str is None:
            return None
        
        # Get Y offset in mm
        offset_y_str = forms.ask_for_string(
            default="0", 
            prompt="Enter Y offset in millimeters (positive = up, negative = down):",
            title="Y Offset (mm)"
        )
        
        if offset_y_str is None:
            return None
        
        try:
            offset_x_mm = float(offset_x_str)
            offset_y_mm = float(offset_y_str)
            
            # Convert millimeters to feet (Revit's internal units)
            # 1 foot = 304.8 millimeters
            offset_x_feet = offset_x_mm / 304.8
            offset_y_feet = offset_y_mm / 304.8
            
            return (offset_x_feet, offset_y_feet)
        except ValueError:
            forms.alert("Invalid offset values entered. Please enter numeric values.", title="Invalid Input")
            return None
            
    except Exception as e:
        forms.alert("Error getting offset values: {}".format(str(e)), title="Input Error")
        return None

def align_element_to_title_block(element, element_ref_point, title_block_target_point, offset_x, offset_y):
    """
    Move element so its reference point aligns with the title block target point plus offset.
    
    Args:
        element: Element to move
        element_ref_point: String indicating which corner of element to use as reference
        title_block_target_point: XYZ point on title block to align to
        offset_x: Horizontal offset in feet
        offset_y: Vertical offset in feet
    """
    # Get element's bounding box
    element_bbox = get_element_bounding_box(element)
    
    if element_bbox is None:
        forms.alert("Could not retrieve bounding box for the selected element.", title="Bounding Box Error")
        return False
    
    # Get the current reference point of the element
    current_ref_point = get_corner_point(element_bbox, element_ref_point)
    
    # Apply offset to target point
    offset_target_point = XYZ(
        title_block_target_point.X + offset_x,
        title_block_target_point.Y + offset_y,
        title_block_target_point.Z
    )
    
    # Calculate the translation vector
    translation_vector = offset_target_point - current_ref_point
    
    # Start a transaction and move the element
    with Transaction(doc, "Align Element to Title Block Corner") as trans:
        trans.Start()
        
        try:
            # Move the element
            ElementTransformUtils.MoveElement(doc, element.Id, translation_vector)
            trans.Commit()
            
            # Show success message with offset information in millimeters
            offset_info = ""
            if offset_x != 0 or offset_y != 0:
                # Convert feet back to millimeters for display
                offset_x_mm = offset_x * 304.8
                offset_y_mm = offset_y * 304.8
                offset_info = " with offset (X: {:.0f}mm, Y: {:.0f}mm)".format(offset_x_mm, offset_y_mm)
            
            forms.alert("Successfully aligned element to the title block{}.".format(offset_info), 
                       title="Alignment Complete")
            return True
                
        except Exception as e:
            trans.RollBack()
            forms.alert("Error during alignment: {}".format(str(e)), title="Alignment Error")
            return False

def main():
    """
    Main function to execute the alignment process.
    """
    # Step 1: Check if active view is a sheet
    if not check_active_view_is_sheet():
        forms.alert("This script only works when the active view is a sheet view. "
                   "Please open a sheet view and try again.", title="Invalid View Type")
        return
    
    # Step 2: Get the active sheet
    active_sheet = doc.ActiveView
    
    # Step 3: Find title block on the sheet
    title_block = get_title_block_on_sheet(active_sheet)
    
    if title_block is None:
        forms.alert("No title block found on the active sheet. "
                   "Please ensure the sheet has a title block.", title="No Title Block Found")
        return
    
    # Step 4: Get title block bounding box
    title_block_bbox = get_element_bounding_box(title_block)
    
    if title_block_bbox is None:
        forms.alert("Could not retrieve title block bounding box.", title="Bounding Box Error")
        return
    
    # Step 5: Prompt user to select element to align
    forms.alert("Step 1: Select the element you want to align (schedule, legend, or viewport).", 
                title="Select Element to Align")
    
    selected_element = get_user_selected_element()
    
    if not selected_element:
        forms.alert("No valid element selected. Please select a schedule, legend, or viewport.", 
                   title="Invalid Selection")
        return
    
    # Step 6: Let user choose reference point of selected element
    element_corner_options = ["Top-Left", "Top-Right", "Bottom-Left", "Bottom-Right"]
    
    element_ref_point = forms.SelectFromList.show(
        element_corner_options,
        title="Select Element Reference Point",
        message="Choose which corner of the selected element to use as the reference point:",
        multiselect=False
    )
    
    if not element_ref_point:
        return
    
    # Step 7: Let user choose title block reference method
    title_block_ref_options = ["Use title block corner", "Pick specific point in title block"]
    
    title_block_ref_method = forms.SelectFromList.show(
        title_block_ref_options,
        title="Choose Title Block Reference Method",
        message="How would you like to define the reference point on the title block?",
        multiselect=False
    )
    
    if not title_block_ref_method:
        return
    
    # Get title block reference point based on chosen method
    if title_block_ref_method == "Use title block corner":
        # Let user choose target corner of title block
        title_block_corner_options = ["Top-Left", "Top-Right", "Bottom-Left", "Bottom-Right"]
        
        title_block_target_corner = forms.SelectFromList.show(
            title_block_corner_options,
            title="Select Title Block Target Corner",
            message="Choose which corner of the title block to align the element to:",
            multiselect=False
        )
        
        if not title_block_target_corner:
            return
        
        # Get the target corner point from title block
        title_block_target_point = get_corner_point(title_block_bbox, title_block_target_corner)
        
    else:
        # Manual point selection in title block
        forms.alert("Now click on a specific point within the title block to use as the reference point.", 
                   title="Pick Point in Title Block")
        
        title_block_target_point = get_user_selected_point()
        
        if not title_block_target_point:
            forms.alert("No reference point selected. Operation cancelled.", title="No Point Selected")
            return
        
        forms.alert("Reference point selected successfully in the title block.", 
                   title="Point Selected")
    
    # Step 8: Get offset values from user
    offset_values = get_user_offset_values()
    
    if offset_values is None:
        return
    
    offset_x, offset_y = offset_values
    
    # Step 9: Align the element to the title block reference point with offset
    align_element_to_title_block(selected_element, element_ref_point, title_block_target_point, offset_x, offset_y)

# Execute the main function
if __name__ == "__main__":
    main()
