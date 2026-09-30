# -*- coding: utf-8 -*-
"""
Isolates every element in the model whose base level matches a level you
pick from a list, and includes the Level datum itself in the isolation.

Author : Fayaz
Revit  : 2024 / 2025 / 2026
PyRevit: 4.8+
"""
__title__   = 'Isolate by\nLevel'
__doc__     = ('Pick a Level. This isolates every element whose level '
               'is that Level, plus the Level itself, in the active view.')

# -- Revit API imports -----------------------------------------------------
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ElementId,
    ElementLevelFilter,
    Transaction,
    Level,
)
from pyrevit import forms, script
from System.Collections.Generic import List

output = script.get_output()
doc    = __revit__.ActiveUIDocument.Document
uidoc  = __revit__.ActiveUIDocument
active_view = doc.ActiveView

# ── 1. Collect all levels and let the user pick one ───────────────────────
all_levels = FilteredElementCollector(doc)\
    .OfClass(Level)\
    .WhereElementIsNotElementType()\
    .ToElements()

if not all_levels:
    forms.alert('No levels found in this project.', exitscript=True)


class LevelItem(forms.TemplateListItem):
    @property
    def name(self):
        return self.item.Name

# sort by elevation so the list reads top-to-bottom like the project browser
level_items = sorted(
    [LevelItem(lvl) for lvl in all_levels],
    key=lambda li: li.item.Elevation,
    reverse=True,
)

picked = forms.SelectFromList.show(
    level_items,
    title='Select a Level to Isolate',
    button_name='Isolate',
    multiselect=False,
)
if not picked:
    script.exit()

target_level = picked.item
target_id = target_level.Id

# ── 2. Find every element on that level — done NATIVELY by Revit, not by
# looping through elements in Python. ElementLevelFilter queries the
# document's own level index directly, so it stays fast no matter how big
# the model is (this is what was making the tool hang before — a manual
# Python loop checking parameters on every element one at a time).
level_filter = ElementLevelFilter(target_id)

matched_elements = FilteredElementCollector(doc)\
    .WherePasses(level_filter)\
    .WhereElementIsNotElementType()\
    .ToElements()

matched_ids = [e.Id for e in matched_elements]

# always include the level itself
matched_ids.append(target_id)

if len(matched_ids) <= 1:
    forms.alert(
        'No elements found on level "{}".'.format(target_level.Name),
        exitscript=True,
    )

# ── 3. Isolate in the active view ──────────────────────────────────────────
id_list = List[ElementId](matched_ids)

t = Transaction(doc, 'Isolate Elements by Level')
t.Start()
try:
    active_view.IsolateElementsTemporary(id_list)
    t.Commit()
except Exception as e:
    t.RollbackToSavepoint()
    forms.alert('Could not isolate elements in this view: {}'.format(e), exitscript=True)

output.print_md(
    '**Done.** Isolated **{}** element(s) on level **"{}"** '
    '(including the level itself).'.format(len(matched_ids) - 1, target_level.Name)
)
