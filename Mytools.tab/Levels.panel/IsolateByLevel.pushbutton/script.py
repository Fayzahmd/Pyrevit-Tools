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
    ElementParameterFilter,
    ElementMulticategoryFilter,
    LogicalAndFilter,
    ParameterFilterRuleFactory,
    BuiltInParameter,
    BuiltInCategory,
    Transaction,
    Level,
    Wall,
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

# SelectFromList unwraps TemplateListItem and returns the Level itself
target_level = getattr(picked, 'item', picked)
target_id = target_level.Id

# ── 2. Find every element on that level — done NATIVELY by Revit, not by
# looping through elements in Python, so it stays fast on big models.
#
# Walls and columns are matched by their Base Constraint / Base Level
# parameter instead of Element.LevelId: columns frequently report an invalid
# LevelId, so ElementLevelFilter alone misses them.
def base_level_filter(bip):
    rule = ParameterFilterRuleFactory.CreateEqualsRule(ElementId(bip), target_id)
    return ElementParameterFilter(rule)

column_cats = List[BuiltInCategory]([
    BuiltInCategory.OST_Columns,
    BuiltInCategory.OST_StructuralColumns,
])
column_cat_filter = ElementMulticategoryFilter(column_cats)

# categories decided by their own level parameter, not Element.LevelId
special_cats = List[BuiltInCategory]([
    BuiltInCategory.OST_Columns,
    BuiltInCategory.OST_StructuralColumns,
    BuiltInCategory.OST_StructuralFraming,
])

# walls: Base Constraint
walls = (FilteredElementCollector(doc)
         .OfClass(Wall)
         .WherePasses(base_level_filter(BuiltInParameter.WALL_BASE_CONSTRAINT))
         .ToElementIds())

# architectural + structural columns: Base Level
columns = (FilteredElementCollector(doc)
           .WherePasses(LogicalAndFilter(
               column_cat_filter,
               base_level_filter(BuiltInParameter.FAMILY_BASE_LEVEL_PARAM)))
           .WhereElementIsNotElementType()
           .ToElementIds())

# beams / structural framing: Reference Level
beams = (FilteredElementCollector(doc)
         .OfCategory(BuiltInCategory.OST_StructuralFraming)
         .WherePasses(base_level_filter(BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM))
         .WhereElementIsNotElementType()
         .ToElementIds())

# everything else: element's own level. Walls, columns and beams are excluded
# here so only their own level-parameter rules above decide them.
others = (FilteredElementCollector(doc)
          .WherePasses(ElementLevelFilter(target_id))
          .WherePasses(ElementMulticategoryFilter(special_cats, True))
          .WhereElementIsNotElementType()
          .ToElements())

matched_ids = list(walls) + list(columns) + list(beams)
matched_ids += [e.Id for e in others if not isinstance(e, Wall)]

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
try:
    t.Start()
    active_view.IsolateElementsTemporary(id_list)
    t.Commit()
except Exception as e:
    if t.HasStarted():
        t.RollBack()
    forms.alert('Could not isolate elements in this view: {}'.format(e), exitscript=True)

output.print_md(
    '**Done.** Isolated **{}** element(s) on level **"{}"** '
    '(including the level itself) - {} wall(s), {} column(s), {} beam(s) '
    'by base / reference level.'.format(len(matched_ids) - 1, target_level.Name,
                                        len(walls), len(columns), len(beams))
)
