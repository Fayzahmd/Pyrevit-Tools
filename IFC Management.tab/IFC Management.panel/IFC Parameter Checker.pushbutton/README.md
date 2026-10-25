# IFC Parameter Checker

A read-only audit of the three IFC export parameters on the structural
elements of a Revit project.

| Parameter | What it controls |
|---|---|
| `Export to IFC` | whether the element is exported at all |
| `Export to IFC As` | the IFC entity it is exported as, e.g. `IfcWall` |
| `IFC Predefined Type` | the predefined type within that entity, e.g. `SHEAR` |

The audit never starts a transaction. The only part of the tool that writes to
the document is the optional colour-coded 3D view, which is **off by default**,
is confirmed separately, and is confined to a view the tool creates itself.

---

## Installation

The tool ships as a pushbutton inside the `Mytool` extension, which is already
registered with pyRevit:

```
Mytool.extension/
└── IFC Management.tab/
    └── IFC Management.panel/
        └── IFC Parameter Checker.pushbutton/
            ├── script.py          entry point, both windows
            ├── scan.xaml          scan options window
            ├── results.xaml       results window
            ├── ifc_config.py      colours, categories, view names
            ├── ifc_params.py      parameter discovery and safe reading
            ├── ifc_validate.py    IFC entity / predefined type validation
            ├── ifc_collect.py     element collection and structural filters
            ├── ifc_audit.py       the audit engine
            ├── ifc_report.py      CSV writer
            ├── ifc_visualize.py   optional 3D audit view
            ├── bundle.yaml
            └── README.md
```

To load it:

1. In Revit, go to the **pyRevit** tab and click **Reload**.
2. A new **IFC Management** tab appears with an **IFC Parameter Checker**
   button.

If the tab does not appear, confirm the extensions folder is registered:
pyRevit tab → Settings → Custom Extension Directories should include
`%APPDATA%\pyRevit\Extensions`.

To install it as a standalone extension instead, copy the `IFC Management.tab`
folder into a new folder called `IFCTools.extension` in the same extensions
directory. No code changes are needed - the modules only import each other.

---

## The three statuses that matter most

Most IFC audits are misleading because they collapse these into one count.
They need different people to do different things.

**Missing** - the parameter does not exist on the element *or* its type, so
there is nowhere to put a value. This is a binding problem, not a data-entry
problem: the category was never bound to the parameter, or the family predates
it. Filling in a schedule will not fix it; somebody has to fix the binding or
the family. In this tool, missing is only reported once the type has also been
checked and come up empty.

**Blank** - the parameter exists and is writable but holds no value. The
binding is fine. Somebody has to type something, and a schedule or a Dynamo
graph will do it in bulk.

**Invalid** - the parameter exists and holds a value, but the value is not one
the IFC schema accepts for that element: an entity the exporter does not know,
or a predefined type outside the enumeration for the chosen entity. This is
the most dangerous of the three, because the export will run, succeed, and
produce wrong data. A blank field at least fails visibly downstream.

Two more statuses carry real weight:

**Value on type** - the effective value comes from the element type. That is
legitimate and is *not* a missing parameter. A tool that reports it as missing
will send people hunting for a problem that is not there.

**Unable to verify** - the parameter could not be read, or there was no
authoritative enumeration to validate it against. The audit says so rather
than reporting a pass. Nothing in this tool reports a successful audit for a
parameter it could not inspect.

---

## How the parameters are identified

By `BuiltInParameter`, not by display name. Display names are localised and a
user parameter can duplicate them, so the built-in enum is the only reliable
handle. All six were confirmed present in the installed Revit 2025 API
(`RevitAPI.dll`, build 25.4.x) and confirmed resolving at run time:

| Audited parameter | Instance | Type |
|---|---|---|
| Export to IFC | `IFC_EXPORT_ELEMENT` | `IFC_EXPORT_ELEMENT_TYPE` |
| Export to IFC As | `IFC_EXPORT_ELEMENT_AS` | `IFC_EXPORT_ELEMENT_TYPE_AS` |
| IFC Predefined Type | `IFC_EXPORT_PREDEFINEDTYPE` | `IFC_EXPORT_PREDEFINEDTYPE_TYPE` |

**On shared parameter GUIDs:** these are built-in parameters, so they carry no
shared-parameter GUID - there is nothing to match a GUID against, and
GUID-based identification does not apply to them. The IFC shared parameter
files that ship with Revit
(`IFC Shared Parameters-RevitIFCBuiltIn_ALL.txt` and its `-Type_ALL` twin)
define the *property set* parameters such as
`IfcDoorLiningProperties.LiningDepth`, not these three. The legacy
project/shared parameters older exporter versions honoured (`IfcExportAs`,
`IfcExportType`) are still looked up by name as a fallback, and the report
names which route found each value in its "Identified via" column.

---

## How values are validated

Validation is sourced from the IFC exporter that ships with Revit, reflected
at run time rather than hard-coded, so it does not go stale with a new
release:

- **IFC entities** come from `Revit.IFC.Common.Enums.IFCEntityType`
  (1,058 entities in Revit 2025).
- **Predefined types** come from the per-entity enumerations in
  `Revit.IFC.Export.Toolkit.IFC4` and `.IFC4x3`, for example
  `Revit.IFC.Export.Toolkit.IFC4.IFCWallType` (291 enumerations indexed in
  Revit 2025).

`Export to IFC As` accepts several shapes, all handled: `IfcWall`,
`IfcWallType`, `IfcWall.SHEAR`, and the special token `DontExport`.

If reflection fails, a curated IFC4 table in `ifc_validate.py` is used as a
fallback and the report says so. Note that the live enumerations are stricter
than the curated table in places - `RETAININGWALL` is a valid `IfcWall`
predefined type in IFC4x3 but is not in Revit's IFC4 `IFCWallType`, and the
live check correctly flags it. If neither source can answer, the value is
reported as unverifiable, never as valid.

`Export to IFC` is an integer-backed state parameter ("By Type", and so on),
not text. Its states are whatever the installed build offers, so the check is
that the stored state resolves to a label; a state with no label is reported
as unverifiable rather than guessed at.

---

## Using it

**Scan window**

- **Scope** - all supported structural elements in the model, or the current
  selection. Model scanning is view-independent: elements hidden in the active
  view, or behind a view template, are still audited.
- **Categories** - the structural categories, each tickable. Structural Walls
  and Structural Slabs apply the element's own structural flag; the "all"
  variants at the bottom of the list do not and are unticked by default.
- **Include type-level parameter checks** - on by default. Switch it off and a
  parameter that lives only on the type is reported as unverified rather than
  missing, because with type checking off the tool cannot honestly call it
  missing.
- **Create colour-coded 3D view** - off by default. See below.

**Results window**

Summary, colour key, and a filterable table with element id, UniqueId,
category, family, type, parameter, parameter location (instance / type /
unknown), current value, audit status and the reason for that status.

- **Select in Revit** selects the element without changing your view.
- **Show element** asks Revit to show it, which may switch views.
- **Open in output window** prints the same findings with clickable element
  links, which also survives any WPF data-binding problem.
- **Export CSV report** asks where to save. The tool never writes a report to
  a location you did not choose.

---

## Optional 3D visualisation

Off by default. When ticked, after the audit you are asked to confirm, and
only then is anything added to the model.

| Status | Colour | Meaning |
|---|---|---|
| Missing parameter | Red | a required parameter does not exist |
| Invalid IFC configuration | Magenta | entity or predefined type not valid |
| Value required | Orange | parameter exists but is blank |
| Manual review required | Yellow | could not be verified |
| Audit passed | Green | all three present and valid |

An element with more than one issue takes the colour of its most severe one,
in the order above - so an element missing `IFC Predefined Type` *and* holding
a blank `Export to IFC As` is red, not orange. The per-parameter detail stays
in the table and the CSV.

### Show findings only

A sub-option under the visualisation tick box: **Show findings only - hide
elements that passed**. It is enabled only once you ask for the view, and is
off by default.

With it on, the elements that passed the audit are hidden in the generated
view, so only the problems remain. On a model where 4,974 of 5,004 elements
pass, that is the difference between a wall of green and thirty elements you
can actually see and walk through.

Hiding is per-element and view-specific (`View.HideElements`): nothing is
deleted, moved or changed, and no other view is affected. Passing elements are
also not coloured, since they would not be visible anyway.

Which statuses survive is `ifc_config.VISIBLE_WHEN_ONLY_ISSUES`, by default
all four non-passing ones. Note that **Invalid IFC configuration** is included
deliberately: those elements *do* hold a value, it is simply not a valid one,
and a wrong value needs attention more urgently than an empty one. If you
genuinely only want unfilled parameters, remove `OV_INVALID` from that list.

Two safeguards: if nothing has a finding, the view would be empty, so you are
told and no view is created. And when an existing audit view is rebuilt, the
elements a previous run hid are unhidden first - a filtered collector only
returns what is *visible* in a view, so anything hidden earlier would
otherwise stay hidden forever.

The legend marks the statuses that are hidden with `[HIDDEN in this view]` and
states that the view is not a complete picture of the model.

What it does:

- creates a **new** isometric 3D view, never modifying an existing one
- removes any default view template from that view so overrides are not
  blocked, and reports it if overrides are still disallowed
- applies solid surface and projection line overrides with
  `View.SetElementOverrides`, finding the solid fill pattern by inspecting
  patterns rather than by name, so it works in any Revit language
- sets shaded-with-edges display and the configured detail level
- unhides the audited categories in that view, and optionally hides the
  categories that were not audited - in that view only
- writes the colour key into a drafting view. A Revit *legend* view cannot be
  created through the API, only duplicated, so a drafting view is used; the
  same key is always shown in the results window and written into the CSV.

What it does not do: change element parameters, geometry, materials, families,
worksets, or the graphics of any view you work in. Elements inside a Revit link
cannot take per-element overrides from a host view at all - that is a Revit
limitation and it is reported rather than worked around.

**Repeated runs.** If `IFC Audit - Missing Parameters` already exists you are
asked whether to create a uniquely named view (`... (2)`), update the existing
audit view, or cancel. Nothing is overwritten silently. Updating resets the
overrides in that dedicated view only, then rebuilds it from the latest audit.

**Failure.** The whole visualisation runs in one transaction. If any part
fails it is rolled back, nothing is added to the model, and the audit results
and CSV export are untouched.

---

## Configuration

Everything tunable is in `ifc_config.py`:

- `CATEGORIES` - which categories are offered, and which are ticked by default
- `OVERALL_COLORS` - the five RGB colours
- `OVERALL_PRIORITY` - the severity order used for colouring
- `VIEW_NAME_BASE`, `LEGEND_VIEW_NAME`, `CREATE_LEGEND_VIEW`
- `VIEW_DETAIL_LEVEL`, `VIEW_TRANSPARENCY`, `HIDE_UNRELATED_CATEGORIES`
- `COLOR_PASSING_ELEMENTS` - leave passing elements with normal graphics
- `SHOW_ONLY_ISSUES_DEFAULT` - default state of the findings-only tick box
- `VISIBLE_WHEN_ONLY_ISSUES` - which statuses stay visible in that mode
- `USE_FALLBACK_ENUMS` - report unverifiable instead of using the curated table

---

## Linked models

Linked models are listed in the report and in the results window, and are
never audited or modified. Their IFC parameters belong to the linked document
and cannot be read reliably through the host. Audit each linked model by
opening it in its own session.

---

## Limitations

- Linked model elements are neither audited nor coloured.
- A Revit legend view cannot be created by the API; the colour key is a
  drafting view plus the in-window legend.
- Elements that cannot take individual overrides (some group members, link
  contents) are counted and reported rather than coloured.
- `Export to IFC` integer states are validated by whether they resolve to a
  label in the installed build, not against a hard-coded list of states.
