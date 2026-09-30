# -*- coding: utf-8 -*-
"""
Duplicate Parent View and Dependencies
Duplicates a selected parent view and all its dependent views with "_Copy" suffix.
"""

__title__ = "Duplicate Views"
__author__ = "Fayaz Ahamed"
__doc__ = """Duplicate a parent view and all its dependent views in Revit.
Select a parent view from Project Browser and choose duplication options."""

# Import required modules
import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *
from pyrevit import revit, forms


def get_selected_view():
    """Get the selected view from Project Browser using ActiveUIDocument.Selection."""
    doc = revit.doc
    uidoc = revit.uidoc
    
    # Get selected elements from Project Browser
    selection = uidoc.Selection.GetElementIds()
    
    if not selection or len(selection) == 0:
        forms.alert("Please select a view from the Project Browser first.", exitscript=True)
    
    if len(selection) > 1:
        forms.alert("Please select only one view from the Project Browser.", exitscript=True)
    
    # Get the selected element if selection exists
    if selection and len(selection) > 0:
        try:
            selected_element = doc.GetElement(selection[0])
        except Exception as e:
            forms.alert("Failed to get selected element: {}".format(str(e)), exitscript=True)
        # Check if it's a view
        if not isinstance(selected_element, View):
            forms.alert("Selected element is not a view. Please select a view from the Project Browser.", exitscript=True)
        return selected_element

    # Fallback: use active view if nothing selected
    try:
        active_view = getattr(revit, 'active_view', None)
        if not active_view:
            # pyRevit sometimes exposes get_active_view
            active_view = getattr(revit, 'get_active_view', lambda: None)()
    except Exception:
        active_view = None

    if active_view and isinstance(active_view, View):
        # confirm with user
        use_active = forms.alert(
            "No view selected. Use the active view ('{}')?".format(active_view.Name),
            yes=True, no=True, title="Use Active View?"
        )
        if use_active:
            return active_view

    forms.alert("Please select a view from the Project Browser first.", exitscript=True)


def is_dependent_view(view):
    """Check if a view is a dependent view."""
    try:
        # A view is dependent if it has a parent view ID
        if not hasattr(view, 'GetPrimaryViewId'):
            return False
        parent_id = view.GetPrimaryViewId()
        return parent_id != ElementId.InvalidElementId
    except Exception:
        return False


def get_dependent_views(parent_view):
    """Get all dependent views of a parent view."""
    try:
        if not hasattr(parent_view, 'GetDependentViewIds'):
            return []
        dependent_ids = parent_view.GetDependentViewIds()
        dependent_views = []
        
        for view_id in dependent_ids:
            dependent_view = revit.doc.GetElement(view_id)
            if dependent_view and isinstance(dependent_view, View):
                dependent_views.append(dependent_view)
        
        return dependent_views
    except Exception as e:
        print('Error getting dependent views: {}'.format(e))
        return []


def duplicate_view_with_copy_suffix(view, duplicate_option):
    """Duplicate a view, append '_Copy' to its name, and remove view template."""
    try:
        # Duplicate the view
        new_view_id = view.Duplicate(duplicate_option)
        new_view = revit.doc.GetElement(new_view_id)

        # Build a base name and ensure uniqueness
        base_name = view.Name + "_Copy"

        # Try to set name directly, if fails increment suffix
        try:
            new_view.Name = base_name
        except Exception:
            # gather existing view names
            try:
                collector = FilteredElementCollector(revit.doc).OfClass(View)
                existing_names = set(v.Name for v in collector)
            except Exception:
                existing_names = set()

            suffix = 1
            unique_name = base_name
            while unique_name in existing_names:
                unique_name = "{0}_{1}".format(base_name, suffix)
                suffix += 1

            try:
                new_view.Name = unique_name
            except Exception as e:
                print("Failed to rename duplicated view: {}".format(e))

        # Remove view template (set to None/ElementId.InvalidElementId)
        try:
            new_view.ViewTemplateId = ElementId.InvalidElementId
        except Exception:
            pass  # Some view types might not support view templates

        return new_view
    except Exception as e:
        print("Error duplicating view '{}': {}".format(getattr(view, 'Name', '<unknown>'), str(e)))
        return None


def create_dependent_view_from_parent(parent_view, original_dependent_view, duplicate_option):
    """Create a new dependent view from the new parent view, copying settings from original dependent."""
    try:
        # Create dependent view from the new parent
        new_dependent_id = parent_view.Duplicate(ViewDuplicateOption.AsDependent)
        new_dependent = revit.doc.GetElement(new_dependent_id)
        
        # Build a base name and ensure uniqueness
        base_name = original_dependent_view.Name + "_Copy"
        
        # Try to set name directly, if fails increment suffix
        try:
            new_dependent.Name = base_name
        except Exception:
            # gather existing view names
            try:
                collector = FilteredElementCollector(revit.doc).OfClass(View)
                existing_names = set(v.Name for v in collector)
            except Exception:
                existing_names = set()

            suffix = 1
            unique_name = base_name
            while unique_name in existing_names:
                unique_name = "{0}_{1}".format(base_name, suffix)
                suffix += 1

            try:
                new_dependent.Name = unique_name
            except Exception as e:
                print("Failed to rename dependent view: {}".format(e))

        # Copy view-specific settings from original dependent view
        try:
            # Copy crop box if applicable
            if hasattr(original_dependent_view, 'CropBoxActive') and hasattr(new_dependent, 'CropBoxActive'):
                if original_dependent_view.CropBoxActive:
                    new_dependent.CropBoxActive = True
                    if hasattr(original_dependent_view, 'CropBox') and hasattr(new_dependent, 'CropBox'):
                        new_dependent.CropBox = original_dependent_view.CropBox
            
            # Copy scale if applicable
            if hasattr(original_dependent_view, 'Scale') and hasattr(new_dependent, 'Scale'):
                new_dependent.Scale = original_dependent_view.Scale
                
            # Copy detail level if applicable
            if hasattr(original_dependent_view, 'DetailLevel') and hasattr(new_dependent, 'DetailLevel'):
                new_dependent.DetailLevel = original_dependent_view.DetailLevel
                
        except Exception as copy_error:
            print("Warning: Could not copy all settings from original dependent view: {}".format(copy_error))

        # Remove view template (set to None/ElementId.InvalidElementId)
        try:
            new_dependent.ViewTemplateId = ElementId.InvalidElementId
        except Exception:
            pass  # Some view types might not support view templates

        return new_dependent
        
    except Exception as e:
        print("Error creating dependent view from parent '{}': {}".format(getattr(parent_view, 'Name', '<unknown>'), str(e)))
        return None


def main():
    """Main function to execute the view duplication process."""
    doc = revit.doc
    
    if not doc:
        forms.alert("No active Revit document found.", exitscript=True)
    
    # Get the selected view from Project Browser
    selected_view = get_selected_view()
    
    # Check if it's a dependent view
    if is_dependent_view(selected_view):
        forms.alert("The selected view is a dependent view. Please select a parent view instead.", exitscript=True)
    
    print("Selected parent view: {}".format(selected_view.Name))
    
    # Get dependent views
    dependent_views = get_dependent_views(selected_view)
    print("Found {} dependent views".format(len(dependent_views)))
    
    # Ask user about duplication option (with or without detailing)
    duplication_choice = forms.alert(
        "Do you want to duplicate with detailing?\n\n"
        "Yes = With Detailing (includes annotations, dimensions, etc.)\n"
        "No = Without Detailing (geometry only)",
        title="Duplication Option",
        yes=True, no=True
    )
    
    if duplication_choice is None:
        print("Operation cancelled by user.")
        return
    
    # Set duplication option based on user choice
    duplicate_option = ViewDuplicateOption.WithDetailing if duplication_choice else ViewDuplicateOption.Duplicate
    option_text = "with detailing" if duplication_choice else "without detailing"
    
    print("Duplication option: {}".format(option_text))
    
    # Start transaction and ensure safe rollback on errors
    transaction = Transaction(doc, "Duplicate Views with Dependencies")
    committed = False
    transaction.Start()

    try:
        # Duplicate parent view first
        print("Duplicating parent view...")
        new_parent_view = duplicate_view_with_copy_suffix(selected_view, duplicate_option)

        if not new_parent_view:
            raise Exception("Failed to duplicate parent view.")

        print("Successfully duplicated parent view: {}".format(new_parent_view.Name))

        # Create dependent views from the new parent view
        successfully_duplicated = 0

        for dep_view in dependent_views:
            try:
                print("Creating dependent view from new parent based on: {}".format(dep_view.Name))
                new_dep_view = create_dependent_view_from_parent(new_parent_view, dep_view, duplicate_option)
                if new_dep_view:
                    successfully_duplicated += 1
                    print("Successfully created dependent view: {}".format(new_dep_view.Name))
                    print("  - Parent: {}".format(new_parent_view.Name))
                    print("  - Dependent relationship maintained: True")
            except Exception as inner_e:
                print("Failed to create dependent view based on {}: {}".format(getattr(dep_view, 'Name', '<unknown>'), inner_e))

        # Commit transaction
        transaction.Commit()
        committed = True

        # Show final results
        result_message = "Duplication completed successfully!\n\n"
        result_message += "Parent view duplicated: {}\n".format(new_parent_view.Name)
        result_message += "Dependent views created: {}\n".format(successfully_duplicated)
        result_message += "\nNote: New dependent views are linked to the new parent view."

        forms.alert(result_message, title="Success")
        print("Operation completed successfully. {} dependent views created as dependents of new parent.".format(successfully_duplicated))

    except Exception as e:
        try:
            if not committed:
                transaction.RollBack()
        except Exception:
            pass
        error_message = "Error during duplication: {}".format(str(e))
        print(error_message)
        forms.alert(error_message, title="Error")


# Execute main function
if __name__ == "__main__":
    main()