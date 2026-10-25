# IFC Parameter Checker - test report

Environment: Autodesk Revit 2025, build 25.4.x, Windows 11, pyRevit with the
Routes/MCP bridge. Tests were run through `execute_revit_code` inside the live
Revit process, except the static checks noted below.

**Nothing in this report is marked passed unless it was actually executed.**
The section at the end lists what has not been run and why.

---

## 1. Executed and passed

### 1.1 Static checks

| Check | Result |
|---|---|
| `ast.parse` on all 8 Python modules | pass, no syntax errors |
| XML well-formedness of `scan.xaml`, `results.xaml` | pass |

### 1.2 Parameter definition investigation

Verified against the installed Revit 2025 `RevitAPI.dll` metadata before any
code was written, then confirmed resolving at run time inside Revit.

All six built-in parameters resolved:

```
Export to IFC       : instance IFC_EXPORT_ELEMENT=resolved,
                      type     IFC_EXPORT_ELEMENT_TYPE=resolved
Export to IFC As    : instance IFC_EXPORT_ELEMENT_AS=resolved,
                      type     IFC_EXPORT_ELEMENT_TYPE_AS=resolved
IFC Predefined Type : instance IFC_EXPORT_PREDEFINEDTYPE=resolved,
                      type     IFC_EXPORT_PREDEFINEDTYPE_TYPE=resolved
unresolved: []
```

Also established, and the reason the tool does not attempt GUID matching for
these three: they are built-in parameters and carry no shared-parameter GUID.
The two IFC shared parameter files shipped in the Revit program folder
(`IFC Shared Parameters-RevitIFCBuiltIn_ALL.txt` and `-Type_ALL.txt`) were
read and contain only property-set parameters such as
`IfcDoorLiningProperties.LiningDepth`, not these three.

### 1.3 Validation source reflection

```
IFC entity list: Revit.IFC.Common.Enums.IFCEntityType (1058 entities)
Predefined type enumerations: 291 enumerations indexed from Revit.IFC.Export
```

Per-entity enumerations read live for 16 structural entities, for example:

```
IfcWall              11 <- Revit.IFC.Export.Toolkit.IFC4.IFCWallType
   ELEMENTEDWALL, MOVABLE, NOTDEFINED, PARAPET, PARTITIONING, PLUMBINGWALL,
   POLYGONAL, SHEAR, SOLIDWALL, STANDARD, USERDEFINED
IfcColumn             4 <- Revit.IFC.Export.Toolkit.IFC4.IFCColumnType
   COLUMN, NOTDEFINED, PILASTER, USERDEFINED
IfcSlab               6 <- Revit.IFC.Export.Toolkit.IFC4.IFCSlabType
   BASESLAB, FLOOR, LANDING, NOTDEFINED, ROOF, USERDEFINED
IfcFooting            7 <- Revit.IFC.Export.Toolkit.IFC4.IFCFootingType
   CAISSON_FOUNDATION, FOOTING_BEAM, NOTDEFINED, PAD_FOOTING, PILE_CAP,
   STRIP_FOOTING, USERDEFINED
IfcReinforcingBar    10 <- Revit.IFC.Export.Toolkit.IFC4.IFCReinforcingBarType
   ANCHORING, EDGE, LIGATURE, MAIN, NOTDEFINED, PUNCHING, RING, SHEAR, STUD,
   USERDEFINED
```

A defect was found and fixed during this test: the enum type name was being
assembled from a lower-cased entity key, producing `IFCwallType`, which never
matches the case-sensitive .NET type `IFCWallType`. Every entity therefore
silently fell back to the curated table. The lookup was replaced with a
case-insensitive index built from `Assembly.GetTypes()`, and the re-run above
confirms the live enumerations are now in use.

### 1.4 Value validation matrix

13 cases executed. `entity` is the verdict on `Export to IFC As`, `predef` the
verdict on `IFC Predefined Type`.

| Export to IFC As | IFC Predefined Type | entity | predef | covers spec test |
|---|---|---|---|---|
| `IfcWall` | `SHEAR` | valid | valid | 1 - all present and valid |
| `IfcWall` | `PARAPET` | valid | valid | 1 |
| `IfcWall` | `BANANA` | valid | **invalid** | 8 - invalid predefined type |
| `IfcWall` | `RETAININGWALL` | valid | **invalid** | 8 |
| `IfcWallType` | `SHEAR` | valid | valid | 5 - type-level spelling |
| `IfcColumn` | `SHEAR` | valid | **invalid** | 8 - valid token, wrong entity |
| `IfcSlab` | `FLOOR` | valid | valid | 1 |
| `IfcSlab` | `SHEAR` | valid | **invalid** | 8 |
| `IfcFooting` | `PAD_FOOTING` | valid | valid | 1 |
| `IfcBeam` | `USERDEFINED` | valid | valid + note | 1 |
| `IfcNonsense` | `SHEAR` | **invalid** | unknown | 7 - invalid IFC entity |
| `DontExport` | blank | valid | unknown | special exporter token |
| blank | `SHEAR` | unknown | unknown | 12 - cannot be verified |

Three behaviours worth calling out, all confirmed:

- `IfcColumn` + `SHEAR` is flagged invalid. `SHEAR` is a real predefined type,
  just not for a column. A tool that checked values against one flat list
  would pass this.
- `RETAININGWALL` is flagged invalid for `IfcWall`. It is valid in IFC4x3 but
  is not in Revit's IFC4 `IFCWallType`. The live enumeration is stricter than
  the curated fallback table, which would have passed it.
- `IfcNonsense` + `SHEAR` returns entity **invalid** and predefined type
  **unknown**, not invalid - with no valid entity there is no enumeration to
  judge the predefined type against, so the tool declines to guess.

### 1.5 Runtime import surface

Run after a first click of the button failed with
`ImportError: No module named Windows` at the `from System.Windows import ...`
line. Cause: `System.Windows` was imported before the WPF assemblies had been
referenced. `pyrevit.forms` references them as a side effect, so the original
order only worked by accident of import sequence. Fixed by referencing
`PresentationCore`, `PresentationFramework`, `WindowsBase` and `System.Xaml`
explicitly, and importing `pyrevit.forms` first.

Verified in the live Revit IronPython engine, without creating any window:

```
AddReference PresentationCore / PresentationFramework / WindowsBase /
             System.Xaml                                           all OK
WPF imports (Thickness, Orientation, CheckBox, StackPanel,
             TextBlock, SolidColorBrush, Rectangle)                OK
SolidColorBrush(Color.FromRgb(220,30,30))                          OK
import ifc_config / ifc_params / ifc_validate / ifc_collect /
       ifc_audit / ifc_report / ifc_visualize                      all OK
```

All seven modules importing cleanly also proves every Revit API name they
import exists in this API. Checked individually for the optional 3D view:

```
Color, ElementTypeGroup, FillPatternElement, FillPatternTarget,
OverrideGraphicSettings, TextNote, Transaction, View, View3D,
ViewDetailLevel, ViewDrafting, ViewFamily, ViewFamilyType, XYZ   all present
View3D.CreateIsometric, ViewDrafting.Create, TextNote.Create     all present
OverrideGraphicSettings.SetProjectionLineColor, SetCutLineColor,
  SetSurfaceForegroundPatternVisible / Id / Color,
  SetCutForegroundPatternVisible / Id / Color,
  SetSurfaceTransparency                                         all present
```

This confirms the API surface the visualisation calls. It does not confirm the
view is built correctly - see below.

### 1.6 Button registration after the fix

After a pyRevit reload, the Revit journal records the button being built with
no error:

```
API_SUCCESS { Added pushbutton Id: 6709, name: IFC Parameter Checker,
  class: mytool_ifcmanagement_ifcmanagement_ifcparameterchecker, ... }
```

The journal was searched for `Traceback` and `ImportError` and contains
neither, so the import failure of 1.5 is resolved. The journal does not record
the script running to completion - pyRevit script output and tracebacks go to
the pyRevit output window, not the journal - so this confirms loading only,
not a successful audit.

### 1.7 Full model audit

Model: `5013-KEO-PD-ZZ-BIM-STR-10001-D1`, Revit 2025 build 25.4.50.35.
Run headlessly through the Routes bridge - engine only, no UI.

Collection, with the 14 structural categories ticked by default:

```
Structural Walls              1887      Structural Slabs (Floors)      243
Structural Columns             879      Structural Foundations         581
Structural Framing            1419      all other structural cats        0
raw instances in them        5009
collected after filters      5004   (5 dropped by the structural flag)
categories absent in build      []
```

Three walls had an unreadable structural flag. They were **audited anyway**
with a note, not dropped - under-reporting an audit is worse than an extra
row. That is the designed behaviour and it fired on real data.

17 linked models were detected, listed, and not audited.

Audit:

```
5004 elements, 15012 rows, 3853 ms (0.77 ms/element)
progress callback fired 5005 times, cancelled=False

Total elements scanned          : 5004
Fully populated and valid       : 4974
With a missing parameter        : 0
With a blank parameter          : 30
With an invalid value           : 0
Relying on a type-level value   : 1
Requiring manual review         : 0
Could not be inspected          : 0
```

Per parameter:

```
Export to IFC         5004 populated   (all "By Type")
Export to IFC As      4973 populated, 30 blank, 1 on type
IFC Predefined Type   4973 populated, 28 blank, 1 on type, 2 unable to verify
```

Three real cases this covers, all of which a cruder audit gets wrong:

**Type-level value** (spec test 5). Element `7160765`, Structural Framing
`B-1000x700`: the instance `Export to IFC As` is empty, the **type** carries
`IfcBeamType` and `BEAM`. Reported as *Value on type*, not missing, and the
`IfcBeamType` spelling validated against `IFCBeamType`:

```
Export to IFC        Instance  'By Type'      Present and populated
Export to IFC As     Type      'IfcBeamType'  Value on type
IFC Predefined Type  Type      'BEAM'         Value on type
```

**Unable to verify** (spec test 12). Elements `7111714` and `7111776` hold
`IFC Predefined Type` = `SHEAR` with a **blank** `Export to IFC As`. Reported
as *Unable to verify*, reason "Cannot validate without an IFC entity in
Export to IFC As" - not as populated, which is what a presence-only check
would say, and not as invalid, since there is no entity to judge against.

**Blank** (spec test 4). 30 elements, concentrated in a few wall types:
`KEO_ST-CIP-RC Upstand - 300` (13), `- 200` (12), plus single instances of
`RC Upstand - 1400`, `- 1450`, `Towe Wall-1000`, `Tower Wall-CW-1800` and
`KEO_ST-CIP-RC SLAB-200`.

Performance (spec test 11): 5,004 elements in under 4 seconds. The first
timed sample ran at 3.19 ms/element and the full run at 0.77 ms/element, the
difference being the type cache warming up.

### 1.8 CSV report

Written for the full audit above: 15,064 lines, 4,272,862 bytes, UTF-8 with
BOM (`ef bb bf`) so Excel opens it directly. Metadata block, summary,
parameter definitions, validation sources, linked models, status meanings,
then the table header at line 52. No writer notes. Sample data row:

```
"1117083","44aad470-...-00110b9b","Walls","Basic Wall",
"KEO_ST-CIP-Retaining RC Wall-RW -850","Export to IFC","Instance","By Type",
"Present and populated","","IFC_EXPORT_ELEMENT",
"Value present on the instance (""By Type"")","Audit passed"
```

### 1.9 Show-findings-only option

Counting verified against the real audit: 30 elements stay visible, 4,974 are
hidden. `View.HideElements`, `View.UnhideElements`, `Element.IsHidden`,
`View.SetElementOverrides`, `View.CanCategoryBeHidden`,
`View.SetCategoryHidden` and `View.AreGraphicsOverridesAllowed` were all
confirmed present on a real `View3D` from this model.

### 1.10 The 3D audit view, findings-only, built and verified

Run on the open model with the user's explicit approval, since it writes to
the document. `create_audit_view(..., only_issues=True)` took 8,795 ms and
returned a view. Notes it reported:

```
Linked models are present. Elements inside a Revit link cannot take
  per-element overrides from a host view, so they are not coloured.
323 categories that were not audited are hidden in this view only.
30 element(s) coloured in this view.
Showing findings only: 4974 element(s) that passed the audit are hidden
  in this view only.
Colour key written to drafting view "IFC Audit - Legend (2)".
```

The committed view was then read back:

```
Name            : IFC Audit - Missing Parameters (2)
DisplayStyle    : ShadingWithEdges
DetailLevel     : Medium
ViewTemplateId  : -1            (no template, so overrides are not blocked)
OverridesAllowed: True

findings visible        : 30
findings with a colour  : 30
passing elements hidden : 4974
inconsistencies         : none
```

The stored colour was compared against the configuration, not just assumed:
`Value required` is held as RGB `255,140,0`, which is exactly
`cfg.OVERALL_COLORS[OV_BLANK]`. Only that one status appears because all 30
findings in this model are blank values.

Name collision handling worked without being prompted: `IFC Audit - Missing
Parameters` already existed, so `unique_view_name` returned
`... (2)` and the existing view was left untouched. The same happened for the
legend.

The legend drafting view carries 12 text notes, including the findings-only
warning and the `[HIDDEN in this view]` marker against green:

```
Green (RGB 40,170,70) = Audit passed - All three parameters present and
  valid   [HIDDEN in this view]
This view shows findings only. Elements marked HIDDEN above are hidden in
  it, so the view is not a complete picture of the model.
```

So spec acceptance criteria for the visualisation are met and observed:
dedicated view created, elements coloured by status, legend explains the
colours, existing views untouched, repeated runs do not overwrite.

### 1.11 Evidence the windows work

While checking the name collision above, two views were found that predate
this session's run:

```
IFC Audit - Missing Parameters   id=7257577  style=ShadingWithEdges
  detail=Medium template=-1
  4954 elements carry an audit colour: 4923 green + 31 orange
IFC Audit - Legend               id=7257586  11 TextNotes,
  first line "IFC parameter audit - colour key"
```

Nothing but this tool creates a view called `IFC Audit - Legend` with that
text. So the button *was* clicked successfully in an earlier session and ran
the whole way through: scan window, audit, the visualisation confirmation,
view creation, colouring and legend. The windows, the progress bar, the
dialogs and the DataGrid therefore do work; the run simply was not reported
back at the time.

That earlier run coloured passing elements green and showed the whole model,
which is the pre-findings-only behaviour and matches
`COLOR_PASSING_ELEMENTS = True`. It audited 4,954 elements with 31 findings
against this session's 5,004 with 30; the category selection used in that run
is not recorded, so the difference is not reconstructable from here.

### 1.12 Filter mode

Built and tested on the open model. Two real bugs were found and fixed by
testing; neither would have been visible from reading the code.

**Precondition.** All six IFC parameters are filterable:
`GetFilterableParametersInCommon` over Walls, Structural Columns, Structural
Framing, Structural Foundations and Floors returned 52 parameters including
all six, so view filters on them are possible at all.

**Bug 1 - type parameter rules never match.** Measured against element
`7160765`, whose instance value is empty and whose type carries `IfcBeamType`:

```
IFC_EXPORT_ELEMENT_TYPE_AS   HasValue=False  HasNoValue=False
IFC_EXPORT_ELEMENT_AS        HasValue=False  HasNoValue=True
```

The type parameter is not on the instance, so *neither* rule matches. The
first implementation built "not set" as `instance HasNoValue AND type
HasNoValue`, which is therefore False for every instance - those filters
matched nothing at all, while the view looked perfectly well formed. Fixed by
testing instance parameters only and settling type-level elements from the
audit's own knowledge.

**Bug 2 - two kinds of blank.** Measured on element `1117083`:

```
as found             AsString='IfcWall'  HasValue=True   HasNoValue=False  Equals('')=False
after Set('')        AsString=''         HasValue=True   HasNoValue=False  Equals('')=True
after Set(' ')       AsString=''         HasValue=True   HasNoValue=False  Equals('')=True
never set (7160765)                      HasValue=False  HasNoValue=True
```

A parameter set and then cleared still reports `HasValue`, so `HasNoValue`
alone misses it - precisely the parameter a QA view most needs to catch. Fixed
by testing `HasNoValue OR Equals('')` for absence and `HasValue AND
NotEquals('')` for presence. Revit normalises whitespace to `''`, which
matches how `ifc_audit` treats it.

**Live behaviour, proven.** One compliant element's parameters were changed
inside a transaction that was then rolled back, so the model is unchanged.
Visibility was read with a collector scoped to the view, which obeys view
filters:

```
element 1117083, KEO_ST-CIP-Retaining RC Wall-RW -850
start             ('IfcWall','USERDEFINED')  visible=False  [complete]
clear entity                                 visible=True   [Export to IFC As not set,
                                                              Predefined Type set but no entity]
clear both                                   visible=True   [both "not set" filters]
entity back only                             visible=True   [IFC Predefined Type not set]
both fixed                                   visible=False  [complete]      <- leaves the view
rollback          ('IfcWall','USERDEFINED')  visible=False
```

That is the behaviour requested: the element leaves the view the moment both
parameters are set, with no re-run of the audit. Note the third row - with the
predefined type still set and the entity cleared, it correctly picks up the
yellow "set but no entity" filter too.

**Other checks.** `audited elements wrongly visible: 0`. Element `7160765`
matches both "not set" filters (as expected, since its value is on the type)
and is hidden directly, as reported in the notes. Filters applied to the view
in the intended order with the intended colours:

```
IFC Audit - IFC parameters complete           visible=False  no override
IFC Audit - Predefined Type set but no entity  visible=True  245,220,40
IFC Audit - Export to IFC As not set           visible=True  255,140,0
IFC Audit - IFC Predefined Type not set        visible=True  255,140,0
```

The 30 elements a collector reports as visible in that view are 26 cameras,
one view extent element and 3 wall openings - collector artifacts and a
non-audited category, no model geometry. Correct, since the model now has zero
findings.

**Model state note.** Between the first full audit and these tests, all 30
blank elements in the model were filled in, including the two that held
`SHEAR` with no entity (now `IfcWall` + `SHEAR`). So the audit's findings were
acted on, and filter mode could not be observed against a model that still had
findings - hence the rolled-back transaction above as the substitute.

### Still unobserved in filter mode

- a model that genuinely contains findings at scan time; every observation
  above came from a rolled-back edit
- the invalid-value warning path, since this model has no invalid values
- removing audit filters from a view on the update-existing path
- a category that cannot filter on the IFC parameters being dropped

---

## 2. Not yet executed

Everything that writes to the document, and everything behind the two windows.

Against the spec's twelve test cases:

| # | Spec test case | Status |
|---|---|---|
| 1 | all parameters present and populated | **passed** - 4,974 elements, 1.7 |
| 2 | one parameter missing | **not observed** - no such element in this model |
| 3 | all three parameters missing | **not observed** - none in this model |
| 4 | blank values | **passed** - 30 elements, 1.7 |
| 5 | type-bound parameters | **passed** - element 7160765, 1.7 |
| 6 | read-only parameters | **not observed** - none encountered |
| 7 | invalid IFC entity values | **passed synthetically** (1.4); none in this model |
| 8 | invalid predefined types | **passed synthetically** (1.4); none in this model |
| 9 | elements hidden in views | **passed** - collection is given the document, never a view, so view visibility cannot affect it; 5,004 elements collected irrespective of the active view |
| 10 | linked structural models | **passed** - 17 links listed and excluded, 1.7 |
| 11 | large models | **passed** - 5,004 elements in 3.85 s, 1.7 |
| 12 | parameter that cannot be verified | **passed** - elements 7111714 / 7111776, 1.7 |

Cases 2, 3 and 6 are honest gaps: the classification code paths exist and are
reachable, but this model contains no element that exercises them, so they
have not been observed running. Cases 7 and 8 were exercised against the live
exporter enums with constructed values (1.4) but no element in this model
holds an invalid value.

### Still unobserved in the visualisation

Built and verified in 1.10, but these specific paths have not run:

- **update-existing-view**, including unhiding what a previous run hid.
  `_clear_view_overrides` is only reached when the user picks "Update the
  existing audit view"; this session always created a new one.
- **rollback on failure.** Nothing failed, so the except branch that rolls the
  transaction back has not been exercised.
- **a view template blocking overrides.** The new view came with no template,
  so the warning path was not reached.
- **the red, magenta and yellow colours.** Only orange occurs in this model,
  since all 30 findings are blank values. The other four colours are verified
  only as configuration values, not as applied overrides.
- **elements that refuse an override or a hide.** The per-element fallback in
  `_hide_passing_elements` did not trigger: the batch `HideElements` call
  succeeded for all 4,974 elements at once.

### Ribbon placement and icon

The button was moved from its own `IFC Management.tab` into the
`IFC Management` panel of the existing `Mytools.tab`, and a 96x96 RGBA
`icon.png` was added to match the other buttons in this extension. The folder
layout and the bundle files are correct, and the icon was inspected at both
96px and the ~32px the ribbon actually draws. Neither has been seen in Revit:
that needs a pyRevit reload, which cannot be driven from here.

### The windows

Cannot be driven from `execute_revit_code` at all - WPF windows,
`forms.ProgressBar` and modal dialogs hang the API context when invoked that
way. 1.11 is strong evidence that the windows work end to end, but it is
inference from the views an earlier run left behind, not an observed run.
Not directly observed:

- the scan window and the results window, including the DataGrid bindings
- the findings-only tick box enabling and disabling with the view tick box
- Select in Revit / Show element / Open in output window
- the save dialog (the CSV writer itself is verified in 1.8)
- each confirmation dialog in the visualisation flow, and the
  name-collision prompt the user actually sees (the underlying
  `unique_view_name` logic is verified in 1.10)

The DataGrid binds to plain attributes on IronPython objects. That pattern is
used widely in pyRevit tools and is expected to work, but it has not been
observed here. If the table renders with empty cells, **Open in output window**
shows the same findings and does not depend on binding.
