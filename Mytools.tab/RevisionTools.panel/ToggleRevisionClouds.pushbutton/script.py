# -*- coding: utf-8 -*-
"""
Lets you pick one or more sheets from a checklist, then turn the
Revision Clouds category ON or OFF for those sheets only.

Author : Fayaz
Revit  : 2024 / 2025 / 2026
PyRevit: 4.8+
"""
__title__ = 'Toggle\nRevision Clouds'
__doc__   = 'Show or hide Revision Clouds on selected sheets.'

# -- Revit API imports ----------------------------------------------------
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewSheet,
    Transaction,
    BuiltInCategory,
    Category,
)
from pyrevit import forms, script

# -- Entry point ------------------------------------------------------------
doc    = __revit__.ActiveUIDocument.Document
uidoc  = __revit__.ActiveUIDocument
output = script.get_output()

output.print_md('**Step 1:** Collecting sheets...')

# 1. Collect every sheet in the project, sorted by sheet number -------------
sheets = FilteredElementCollector(doc)\
    .OfClass(ViewSheet)\
    .WhereElementIsNotElementType()\
    .ToElements()

sheets = sorted(sheets, key=lambda s: s.SheetNumber)

if not sheets:
    forms.alert('No sheets were found in this project.', exitscript=True)

output.print_md('Found {} sheet(s). Opening sheet picker...'.format(len(sheets)))

# 2. Build the sheet list as plain strings (faster rendering than TemplateListItem) --
#    Keep a mapping of string labels back to the actual sheet objects.
sheet_labels = ['{} - {}'.format(s.SheetNumber, s.Name) for s in sheets]
sheet_map = {label: sheet for label, sheet in zip(sheet_labels, sheets)}

output.print_md('Sheet picker dialog opening (this may take a moment with large projects)...')

picked_labels = forms.SelectFromList.show(
    sheet_labels,
    title='Select Sheets',
    button_name='Next >',
    multiselect=True,
)

if not picked_labels:
    forms.alert('No sheets selected. Exiting.')
    script.exit()

# Convert picked labels back to sheet objects
selected_sheets = [sheet_map[label] for label in picked_labels]
output.print_md('Selected {} sheet(s):'.format(len(selected_sheets)))
for sheet in selected_sheets:
    output.print_md('  - {} - {}'.format(sheet.SheetNumber, sheet.Name))

# 3. Ask whether to turn revision clouds ON or OFF ---------------------------
#    Simple yes/no prompt: YES = Turn ON, NO = Turn OFF
output.print_md('**Step 2:** Asking for ON/OFF choice...')

turn_on = forms.alert(
    'Turn Revision Clouds ON or OFF?\n\nYES = Turn ON\nNO = Turn OFF',
    yes=True,
    no=True,
)

if turn_on is None:
    forms.alert('Cancelled.')
    script.exit()

hide = not turn_on
action_label = 'ON' if turn_on else 'OFF'
output.print_md('Will turn Revision Clouds **{}**.'.format(action_label))

# 4. Get the "Revision Clouds" category --------------------------------------
output.print_md('**Step 3:** Looking up Revision Clouds category...')

rev_cloud_cat = Category.GetCategory(doc, BuiltInCategory.OST_RevisionClouds)

if rev_cloud_cat is None:
    forms.alert('Could not find the Revision Clouds category in this project.', exitscript=True)

output.print_md('Found Revision Clouds category. Starting transaction...')

# 5. Toggle category visibility on each selected sheet -----------------------
updated = 0
skipped = []

t = Transaction(doc, 'Toggle Revision Clouds on Sheets')
t.Start()
try:
    for i, sheet in enumerate(selected_sheets):
        try:
            output.print_md('  Updating {} - {}...'.format(sheet.SheetNumber, sheet.Name))
            # This is the same switch as unchecking "Revision Clouds" in
            # Visibility/Graphics for this one sheet - it only affects
            # this view, not the whole project.
            sheet.SetCategoryHidden(rev_cloud_cat.Id, hide)
            updated += 1
            output.print_md('    ✓ Updated')
        except Exception as e:
            # Some sheets (e.g. ones with visibility locked by a template)
            # may refuse the change - skip them and keep going.
            output.print_md('    ✗ Skipped: {}'.format(str(e)))
            skipped.append(sheet.SheetNumber)
    
    output.print_md('Committing transaction...')
    t.Commit()
    output.print_md('Transaction committed.')
except Exception as e:
    output.print_md('ERROR: {}'.format(str(e)))
    t.RollbackToSavepoint()
    forms.alert('Error while updating sheets: {}'.format(e), exitscript=True)

# 6. Report results -----------------------------------------------------------
output.print_md('---')
state_label = 'OFF' if hide else 'ON'
msg = 'Revision Clouds turned {} on {} sheet(s).'.format(state_label, updated)
if skipped:
    msg += '\n\nSkipped (visibility locked on): {}'.format(', '.join(skipped))

output.print_md(msg)
forms.alert(msg)
