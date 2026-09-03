# CATIA V5 — complete capability inventory and Kryova gap

Everything CATIA V5 exposes, and what Kryova does not yet support. Two sources:

1. **The 25 manuals in `Kryova-backend/data/bm25/`** — 5,285 pages, all read. 21 had a
   text layer; the 4 French *Formation* manuals (1,272 pages) are image-only scans and were
   OCR'd (stored upside-down; 180° rotate + `tesseract -l fra+eng`).
2. **Web research**, because the manuals cover only ~39 of CATIA's ~100 workbenches and say
   nothing about the automation API a "compatible" product must speak:
   - `catiadoc.free.fr/online/interfaces/CAAInterfaceIdx.htm` — the CAA V5 **IDL Interface
     Index**, the authoritative list of every automation object.
   - `catiadoc.free.fr/online/interfaces/CAAMasterIdx.htm` — the **IDL API Master Index**,
     every method, property and enum.
   - `catiadoc.free.fr` / `maruf.ca/files/caadoc` — CAA V5 automation articles and the
     R19 documentation home page. *(catiadoc.free.fr is, incidentally, the exact source
     of the 25 PDFs already in our repo.)*
   - CATIA V5R16 *Product Enhancement Overview* — the shipped product/workbench list.
   - `cceintl.com`, `faro.com`, `ennova-cfd.com`, `comsol.com` — format/version support matrices.

**Scale of what "support everything in CATIA V5" means, measured:**

| Surface | Count | Kryova today |
|---|---:|---:|
| Workbenches | ~100 | 0 native (drives Part Design/Sketcher only, through the UI) |
| Documented commands (from the 25 manuals) | 907 | 24 |
| CAA automation objects | **1,080** | 0 |
| CAA automation methods | **5,636** | 0 |
| CAA automation properties | **3,286** | 0 |
| CAA automation enums | **288** | 0 |
| Executable Kryova tools | — | **39** (≈20 modelling) |

Kryova does not use the automation API at all. It drives CATIA by **pressing menu items and
filling dialogs** (`catia_run_command`, `catia_fill_dialog`, `catia_press_key`). That is the
single most important finding here: every number below is a gap against an interface we are
not currently connected to.

---

## 0. The hard limits in today's tool schemas

Closed enums a model cannot escape (`app/catia/tool_specs.py`):

- **Sketch planes: `XY | YZ | ZX` only.** No offset plane, plane on a face, plane through
  points, or user axis system.
- **Every sketch primitive is centred on the origin.** No x/y placement argument exists.
- **Hole placement: 5 named positions** (`center`, `front_left`, `front_right`, `back_left`,
  `back_right`) on **6 named faces** (`top|bottom|front|back|left|right`).
- **Fillet/chamfer edges: `all | vertical | horizontal | top | bottom`.** No edge picking,
  no per-edge radius, no propagation control.
- **Materials: 8 fixed keys** — `aluminium-6061-t6`, `aluminium-7075-t6`, `steel-1018`,
  `stainless-304`, `titanium-ti6al4v`, `abs`, `pla`, `nylon-pa12`.
- **Parameter units: `mm | deg | kg` only.**
- **`shell`** takes a thickness and nothing else — no face selection.
- **`mirror`** takes a plane and nothing else — mirrors the whole body.
- **One body, one part.** No new body, no boolean between bodies, no geometrical set, no
  assembly/product.

Refused outright by `ui_policy.py` (and correctly so): Macro, Tools>Options, Customize,
Save As, Save Management, Exit, Licence. **Note this makes full automation parity impossible
by design** — "support every single thing" and "never run a macro or change a setting" are
in direct conflict. That is a product decision to revisit deliberately, not a bug.

---

## 1. Every CATIA V5 workbench, by Start-menu category

`✗` = no support of any kind. `~` = reachable only by blind menu-pressing.
**Bold** = the only two we model explicitly.

### Infrastructure (12)
✗ Product Structure · ✗ Material Library · ✗ CATIA V5↔V4 Integration · ✗ **Catalog Editor** ·
✗ Photo Studio · ✗ Real Time Rendering · ✗ Immersive Systems Assistant ·
✗ Product Data Filtering · ✗ Feature Dictionary Editor · ✗ Data Exchange Interfaces ·
✗ Component Catalog Editor · ✗ SMARTEAM / ENOVIA integration

### Mechanical Design (23)
~ **Part Design** · ~ **Sketcher** · ✗ Assembly Design · ✗ Drafting (Generative + Interactive) ·
✗ Wireframe and Surface Design · ✗ Sheet Metal Design · ✗ Generative Sheetmetal Design ·
✗ Aerospace Sheet Metal Design · ✗ Sheet Metal Production · ✗ Structure Design ·
✗ Weld Design · ✗ Mold Tooling Design · ✗ Core & Cavity Design · ✗ Die Face Design ·
✗ Functional Molded Part · ✗ Functional Tolerancing & Annotation ·
✗ 3D Functional Tolerancing & Annotation · ✗ Product Functional Tolerancing & Annotation ·
✗ 2D Layout for 3D Design · ✗ Healing Assistant · ✗ Composites Design ·
✗ Composites Grid Design · ✗ Composites Forming / Trimming

### Shape Design & Styling (11)
✗ Generative Shape Design · ✗ Generative Shape Optimizer · ✗ FreeStyle Shaper ·
✗ FreeStyle Optimizer · ✗ FreeStyle Profiler · ✗ Digitized Shape Editor ·
✗ Quick Surface Reconstruction · ✗ Imagine & Shape · ✗ Sketch Tracer ·
✗ Automotive Class A / ICEM Shape Design · ✗ Automotive Body In White Fastening ·
✗ Shape Sculptor · ✗ Developed Shapes · ✗ Mechanical Surface Design

### Analysis & Simulation (8)
✗ Generative Structural Analysis (GPS) · ✗ Generative Assembly Structural Analysis (GAS) ·
✗ ELFINI Structural Analysis · ✗ Generative Dynamic Response Analysis ·
✗ Advanced Meshing Tools · ✗ FEM Surface · ✗ FEM Solid ·
✗ Tolerance Analysis of Deformable Assembly

*(Kryova has its own gmsh + linear-static solver instead — see §5.)*

### AEC Plant (3)
✗ Plant Layout · ✗ Plant & Ship Review · ✗ Compartment and Access

### Machining (10)
✗ Prismatic Machining · ✗ Lathe Machining · ✗ Multi-Slide Lathe Machining ·
✗ Surface Machining · ✗ Advanced Machining · ✗ Multi-Axis Surface Machining ·
✗ NC Manufacturing Review · ✗ NC Manufacturing Infrastructure ·
✗ STL Rapid Prototyping · ✗ Prismatic Machining Preparation Assistant

### Machining Simulation (3)
✗ Machining Simulation · ✗ NC Machine Tool Simulation · ✗ NC Machine Tool Builder

### Digital Mockup (10)
✗ DMU Navigator · ✗ DMU Space Analysis · ✗ DMU Kinematics · ✗ DMU Fitting ·
✗ DMU Optimizer · ✗ DMU 2D Viewer · ✗ DMU Fastening Review · ✗ DMU Composite Review ·
✗ DMU Tolerancing Review · ✗ DMU Immersive Review

### Equipment & Systems Engineering (22)
✗ Electrical Library · ✗ Electrical Harness Installation · ✗ Electrical Harness Flattening ·
✗ Electrical Wire Routing · ✗ Electrical Cableway Routing · ✗ Electrical Connectivity Diagrams ·
✗ Equipment Arrangement · ✗ Systems Routing · ✗ Systems Space Reservation ·
✗ Systems Diagrams · ✗ Piping Design · ✗ Piping & Instrumentation Diagrams ·
✗ Tubing Design · ✗ Tubing Diagrams · ✗ HVAC Design · ✗ HVAC Diagrams ·
✗ Waveguide Design · ✗ Waveguide Diagrams · ✗ Raceway & Conduit Design ·
✗ Hanger Design · ✗ Structure Functional Design · ✗ Circuit Board Design

### Digital Process for Manufacturing (2)
✗ DPM Assembly / Shop Floor · ✗ Storing and Annotations

### Ergonomics Design & Analysis (4)
✗ Human Builder · ✗ Human Measurements Editor · ✗ Human Posture Analysis ·
✗ Human Activity Analysis

### Knowledgeware (6)
✗ Knowledge Advisor · ✗ Knowledge Expert · ✗ Product Engineering Optimizer ·
✗ Product Knowledge Template · ✗ Business Process Knowledge Template ·
✗ Product Function Definition

**Workbenches with zero coverage: ~100. Workbenches with partial coverage: 2.**

---

## 2. The CAA V5 Automation API — the real compatibility surface

This is the section the 25 manuals do not contain at all, and it is what
"be compatible with CATIA" actually means. CATIA exposes a COM/OLE object model
(`V5Automation.chm`, driven from VBScript/CATScript/VBA/Python-win32/C#). **Kryova uses
none of it.**

Using it instead of menu-pressing would remove most of §0's limits at a stroke — e.g.
`ShapeFactory.AddNewHoleFromPoint` places a hole at real coordinates, which the current
5-position enum exists precisely because we cannot do.

**Totals: 1,080 objects · 5,636 methods · 3,286 properties · 288 enums.**

### 2.1 The factories that matter most

**`ShapeFactory`** (82 methods): `AddNewAdd`, `AddNewAffinity2`, `AddNewAssemble`, `AddNewAutoDraft`, `AddNewAutoFillet`, `AddNewAxisToAxis2`, `AddNewBlend`, `AddNewChamfer`, `AddNewCircPattern`, `AddNewCircPatternofList`, `AddNewCloseSurface`, `AddNewDraft`, `AddNewEdgeFilletWithConstantRadius`, `AddNewEdgeFilletWithVaryingRadius`, `AddNewFaceFillet`, `AddNewGSDCircPattern`, `AddNewGSDRectPattern`, `AddNewGroove`, `AddNewGrooveFromRef`, `AddNewHole`, `AddNewHoleFromPoint`, `AddNewHoleFromRefPoint`, `AddNewHoleFromSketch`, `AddNewHoleWith2Constraints`, `AddNewHoleWithConstraint`, `AddNewIntersect`, `AddNewLoft`, `AddNewMirror`, `AddNewPad`, `AddNewPadFromRef`, `AddNewPocket`, `AddNewPocketFromRef`, `AddNewRectPattern`, `AddNewRectPatternofList`, `AddNewRemove`, `AddNewRemoveFace`, `AddNewRemovedBlend`, `AddNewRemovedLoft`, `AddNewReplaceFace`, `AddNewRib`, `AddNewRibFromRef`, `AddNewScaling`, `AddNewSewSurface`, `AddNewShaft`, `AddNewShaftFromRef`, `AddNewShell`, `AddNewSlot`, `AddNewSlotFromRef`, `AddNewSolidCombine`, `AddNewSolidEdgeFilletWithConstantRadius`, `AddNewSolidEdgeFilletWithVaryingRadius`, `AddNewSolidFaceFillet`, `AddNewSolidTritangentFillet`, `AddNewSplit`, `AddNewStiffener`, `AddNewStiffenerFromRef`, `AddNewSurfaceEdgeFilletWithConstantRadius`, `AddNewSurfaceEdgeFilletWithVaryingRadius`, `AddNewSurfaceFaceFillet`, `AddNewSurfaceTritangentFillet`, `AddNewSurfacicAutoFillet`, `AddNewSurfacicCircPattern`, `AddNewSurfacicRectPattern`, `AddNewSurfacicUserPattern`, `AddNewThickSurface`, `AddNewThickness`, `AddNewThreadWithOutRef`, `AddNewThreadWithRef`, `AddNewTrim`, `AddNewTritangentFillet`, `AddNewUserPattern`, `AddNewUserPatternofList`, `AddNewVolumeAdd`, `AddNewVolumeCloseSurface`, `AddNewVolumeIntersect`, `AddNewVolumeRemove`, `AddNewVolumeSewSurface`, `AddNewVolumeShell`, `AddNewVolumeThickSurface`, `AddNewVolumeThickness`, `AddNewVolumeTrim`, `AddNewVolumicDraft`

**`HybridShapeFactory`** (132 methods): `AddNew3DCorner`, `AddNew3DCurveOffset`, `AddNewAffinity`, `AddNewAxisLine`, `AddNewAxisToAxis`, `AddNewBlend`, `AddNewBoundary`, `AddNewBoundaryOfSurface`, `AddNewBump`, `AddNewCircle2PointsRad`, `AddNewCircle3Points`, `AddNewCircleBitangentPoint`, `AddNewCircleBitangentRadius`, `AddNewCircleCenterAxis`, `AddNewCircleCenterAxisWithAngles`, `AddNewCircleCenterTangent`, `AddNewCircleCtrPt`, `AddNewCircleCtrPtWithAngles`, `AddNewCircleCtrRad`, `AddNewCircleCtrRadWithAngles`, `AddNewCircleDatum`, `AddNewCircleTritangent`, `AddNewCombine`, `AddNewConic`, `AddNewConicalReflectLineWithType`, `AddNewConnect`, `AddNewCorner`, `AddNewCurveDatum`, `AddNewCurvePar`, `AddNewCurveSmooth`, `AddNewCylinder`, `AddNewDatums`, `AddNewDevelop`, `AddNewDirection`, `AddNewDirectionByCoord`, `AddNewEmptyRotate`, `AddNewEmptyTranslate`, `AddNewExtract`, `AddNewExtractMulti`, `AddNewExtrapolLength`, `AddNewExtrapolUntil`, `AddNewExtremum`, `AddNewExtremumPolar`, `AddNewExtrude`, `AddNewFill`, `AddNewFilletBiTangent`, `AddNewFilletTriTangent`, `AddNewHealing`, `AddNewHelix`, `AddNewHybridScaling`, `AddNewHybridSplit`, `AddNewHybridTrim`, `AddNewIntegratedLaw`, `AddNewIntersection`, `AddNewInverse`, `AddNewJoin`, `AddNewLawDistProj`, `AddNewLineAngle`, `AddNewLineBiTangent`, `AddNewLineBisecting`, `AddNewLineBisectingOnSupport`, `AddNewLineBisectingOnSupportWithPoint`, `AddNewLineBisectingWithPoint`, `AddNewLineDatum`, `AddNewLineNormal`, `AddNewLinePtDir`, `AddNewLinePtDirOnSupport`, `AddNewLinePtPt`, `AddNewLinePtPtExtended`, `AddNewLinePtPtOnSupport`, `AddNewLinePtPtOnSupportExtended`, `AddNewLineTangency`, `AddNewLineTangencyOnSupport`, `AddNewLoft`, `AddNewNear`, `AddNewOffset`, `AddNewPlane1Curve`, `AddNewPlane1Line1Pt`, `AddNewPlane2Lines`, `AddNewPlane3Points`, `AddNewPlaneAngle`, `AddNewPlaneDatum`, `AddNewPlaneEquation`, `AddNewPlaneMean`, `AddNewPlaneNormal`, `AddNewPlaneOffset`, `AddNewPlaneOffsetPt`, `AddNewPlaneTangent`, `AddNewPointBetween`, `AddNewPointCenter`, `AddNewPointCoord`, `AddNewPointCoordWithReference`, `AddNewPointDatum`, `AddNewPointOnCurveAlongDirection`, `AddNewPointOnCurveFromDistance`, `AddNewPointOnCurveFromPercent`, `AddNewPointOnCurveWithReferenceAlongDirection`, `AddNewPointOnCurveWithReferenceFromDistance`, `AddNewPointOnCurveWithReferenceFromPercent`, `AddNewPointOnPlane`, `AddNewPointOnPlaneWithReference`, `AddNewPointOnSurface`, `AddNewPointOnSurfaceWithReference`, `AddNewPointTangent`, `AddNewPolyline`, `AddNewPositionTransfo`, `AddNewProject`, `AddNewReflectLine`, `AddNewReflectLineWithType`, `AddNewRevol`, `AddNewRotate`, `AddNewSection`, `AddNewSphere`, `AddNewSpine`, `AddNewSpiral`, `AddNewSpline`, `AddNewSurfaceDatum`, `AddNewSweepCircle`, `AddNewSweepConic`, `AddNewSweepExplicit`, `AddNewSweepLine`, `AddNewSymmetry`, `AddNewTransfer`, `AddNewTranslate`, `AddNewUnfold`, `AddNewVolumeDatum`, `AddNewWrapCurve`, `AddNewWrapSurface`, `ChangeFeatureName`, `DeleteObjectForDatum`, `GSMVisibility`, `GetGeometricalFeatureType`

**`Factory2D`** (15 methods): `CreateCircle`, `CreateClosedCircle`, `CreateClosedEllipse`, `CreateControlPoint`, `CreateEllipse`, `CreateHyperbola`, `CreateIntersection`, `CreateIntersections`, `CreateLine`, `CreateLineFromVector`, `CreateParabola`, `CreatePoint`, `CreateProjection`, `CreateProjections`, `CreateSpline`

**`Part`** (11 methods): `Activate`, `CreateReferenceFromBRepName`, `CreateReferenceFromName`, `CreateReferenceFromObject`, `FindObjectByName`, `GetCustomerFactory`, `Inactivate`, `IsInactive`, `IsUpToDate`, `Update`, `UpdateObject`

**`Product`** (24 methods): `ActivateDefaultShape`, `ActivateShape`, `AddMasterShapeRepresentation`, `AddShapeRepresentation`, `ApplyWorkMode`, `Connections`, `CreateReferenceFromName`, `DesactivateDefaultShape`, `DesactivateShape`, `ExtractBOM`, `GetActiveShapeName`, `GetAllShapesNames`, `GetDefaultShapeName`, `GetMasterShapeRepresentation`, `GetMasterShapeRepresentationPathName`, `GetNumberOfShapes`, `GetShapePathName`, `GetShapeRepresentation`, `GetTechnologicalObject`, `HasAMasterShapeRepresentation`, `HasShapeRepresentation`, `RemoveMasterShapeRepresentation`, `RemoveShapeRepresentation`, `Update`

**`Products`** (9 methods): `AddComponent`, `AddComponentsFromFiles`, `AddExternalComponent`, `AddNewComponent`, `AddNewProduct`, `Item`, `Remove`, `ReplaceComponent`, `ReplaceProduct`

**`Sketch`** (6 methods): `CloseEdition`, `Evaluate`, `GetAbsoluteAxisData`, `InverseOrientation`, `OpenEdition`, `SetAbsoluteAxisData`

**`Constraints`** (5 methods): `AddBiEltCst`, `AddMonoEltCst`, `AddTriEltCst`, `Item`, `Remove`

**`Parameters`** (11 methods): `CreateBoolean`, `CreateDimension`, `CreateInteger`, `CreateList`, `CreateReal`, `CreateSetOfParameters`, `CreateString`, `GetNameToUseInRelation`, `Item`, `Remove`, `SubList`

**`Relations`** (13 methods): `CreateCheck`, `CreateDesignTable`, `CreateFormula`, `CreateHorizontalDesignTable`, `CreateLaw`, `CreateProgram`, `CreateRuleBase`, `CreateSetOfEquations`, `CreateSetOfRelations`, `GenerateXMLReportForChecks`, `Item`, `Remove`, `SubList`

**`Selection`** (19 methods): `Add`, `Clear`, `Copy`, `Cut`, `Delete`, `FilterCorrespondence`, `FindObject`, `IndicateOrSelectElement2D`, `IndicateOrSelectElement3D`, `Item`, `Item2`, `Paste`, `PasteSpecial`, `Remove`, `Remove2`, `Search`, `SelectElement2`, `SelectElement3`, `SelectElement4`

**`Documents`** (5 methods): `Add`, `Item`, `NewFrom`, `Open`, `Read`

**`Document`** (12 methods): `Activate`, `Close`, `CreateFilter`, `CreateReferenceFromName`, `ExportData`, `GetWorkbench`, `Indicate2D`, `Indicate3D`, `NewWindow`, `RemoveFilter`, `Save`, `SaveAs`

**`AnalysisManager`** (5 methods): `CreateReferenceFromGeometry`, `CreateReferenceFromObject`, `Import`, `ImportDefineFile`, `ImportFile`

**`DrawingSheets`** (4 methods): `Add`, `AddDetail`, `Item`, `Remove`

**`DrawingViews`** (3 methods): `Add`, `Item`, `Remove`

**`DrawingView`** (11 methods): `Activate`, `AlignedWithReferenceView`, `GetViewName`, `InsertViewAngle`, `InsertViewScale`, `IsGenerative`, `Isolate`, `SaveEdition`, `SetViewName`, `Size`, `UnAlignedWithReferenceView`

**`SPAWorkbench`** (1 methods): `GetMeasurable`

**`Measurable`** (12 methods): `GetAngleBetween`, `GetAxis`, `GetAxisSystem`, `GetCOG`, `GetCenter`, `GetDirection`, `GetMinimumDistance`, `GetMinimumDistancePoints`, `GetPlane`, `GetPoint`, `GetPointsOnAxis`, `GetPointsOnCurve`

**`Application`** (7 methods): `CreateSendTo`, `FileSelectionBox`, `GetWorkbenchId`, `Help`, `Quit`, `StartCommand`, `StartWorkbench`

### 2.2 All 1,080 automation objects, grouped

**Abaqus / SIMULIA analysis (ABQ*)** (46): `ABQAnalysisCase`, `ABQAnalysisCases`, `ABQAnalysisModel`, `ABQAnalyticalRigidSurface`, `ABQBoundaryCondition`, `ABQBoundaryConditions`, `ABQClampBC`, `ABQConcentratedForce`, `ABQDisplacementBC`, `ABQExplicitDynamicsStep`, `ABQFastenedConnectionEnhancement`, `ABQFastenedPair`, `ABQFields`, `ABQFilmCondition`, `ABQGasketProperty`, `ABQGeneralStaticStep`, `ABQGlobalElementAssignment`, `ABQGravity`, `ABQHeatTransferStep`, `ABQInitialStep`, `ABQInitialTemperature`, `ABQInteraction`, `ABQInteractions`, `ABQJob`, `ABQJobs`, `ABQLoad`, `ABQLoads`, `ABQMassScaling`, `ABQMassScalings`, `ABQMechConnBehavior`, `ABQPressure`, `ABQPretensionProperty`, `ABQProperties`, `ABQProperty`, `ABQRigidBodyConstraint`, `ABQRigidCoupling`, `ABQSmoothCoupling`, `ABQSmoothStepAmplitude`, `ABQSpringConnectionProperty`, `ABQStep`, `ABQSteps`, `ABQSurfaceToSurfaceContact`, `ABQTabularAmplitude`, `ABQTemperature`, `ABQTemperatureHistory`, `ABQThermalConnBehavior`

**Sketcher 2D geometry & constraints** (23): `Axis2D`, `Camera2D`, `Circle2D`, `Constraint`, `ConstraintSatisfaction`, `Constraints`, `ControlPoint2D`, `Curve2D`, `Ellipse2D`, `Factory2D`, `GeometricElements`, `Geometry2D`, `Hyperbola2D`, `Line2D`, `Marker2D`, `Parabola2D`, `Point2D`, `Sketch`, `SketchBasedShape`, `Sketches`, `Spline2D`, `Viewer2D`, `Viewpoint2D`

**HybridShape (GSD wireframe & surface)** (107): `HybridShape`, `HybridShape3DCurveOffset`, `HybridShapeAffinity`, `HybridShapeAssemble`, `HybridShapeAxisLine`, `HybridShapeAxisToAxis`, `HybridShapeBlend`, `HybridShapeBoundary`, `HybridShapeBump`, `HybridShapeCircle`, `HybridShapeCircle2PointsRad`, `HybridShapeCircle3Points`, `HybridShapeCircleBitangentPoint`, `HybridShapeCircleBitangentRadius`, `HybridShapeCircleCenterAxis`, `HybridShapeCircleCenterTangent`, `HybridShapeCircleCtrPt`, `HybridShapeCircleCtrRad`, `HybridShapeCircleExplicit`, `HybridShapeCircleTritangent`, `HybridShapeCombine`, `HybridShapeConic`, `HybridShapeConnect`, `HybridShapeCorner`, `HybridShapeCurveExplicit`, `HybridShapeCurvePar`, `HybridShapeCurveSmooth`, `HybridShapeCylinder`, `HybridShapeDevelop`, `HybridShapeDirection`, `HybridShapeExtract`, `HybridShapeExtractMulti`, `HybridShapeExtrapol`, `HybridShapeExtremum`, `HybridShapeExtremumPolar`, `HybridShapeExtrude`, `HybridShapeFactory`, `HybridShapeFill`, `HybridShapeFilletBiTangent`, `HybridShapeFilletTriTangent`, `HybridShapeHealing`, `HybridShapeHelix`, `HybridShapeInstance`, `HybridShapeIntegratedLaw`, `HybridShapeIntersection`, `HybridShapeInverse`, `HybridShapeLawDistProj`, `HybridShapeLineAngle`, `HybridShapeLineBiTangent`, `HybridShapeLineBisecting`, `HybridShapeLineExplicit`, `HybridShapeLineNormal`, `HybridShapeLinePtDir`, `HybridShapeLinePtPt`, `HybridShapeLineTangency`, `HybridShapeLoft`, `HybridShapeNear`, `HybridShapeOffset`, `HybridShapePlane1Curve`, `HybridShapePlane1Line1Pt`, `HybridShapePlane2Lines`, `HybridShapePlane3Points`, `HybridShapePlaneAngle`, `HybridShapePlaneEquation`, `HybridShapePlaneExplicit`, `HybridShapePlaneMean`, `HybridShapePlaneNormal`, `HybridShapePlaneOffset`, `HybridShapePlaneOffsetPt`, `HybridShapePlaneTangent`, `HybridShapePointBetween`, `HybridShapePointCenter`, `HybridShapePointCoord`, `HybridShapePointExplicit`, `HybridShapePointOnCurve`, `HybridShapePointOnPlane`, `HybridShapePointOnSurface`, `HybridShapePointTangent`, `HybridShapePolyline`, `HybridShapePositionTransfo`, `HybridShapeProject`, `HybridShapeReflectLine`, `HybridShapeRevol`, `HybridShapeRotate`, `HybridShapeScaling`, `HybridShapeSection`, `HybridShapeSphere`, `HybridShapeSpine`, `HybridShapeSpiral`, `HybridShapeSpline`, `HybridShapeSplit`, `HybridShapeSurfaceExplicit`, `HybridShapeSweep`, `HybridShapeSweepCircle`, `HybridShapeSweepConic`, `HybridShapeSweepExplicit`, `HybridShapeSweepLine`, `HybridShapeSymmetry`, `HybridShapeThickness`, `HybridShapeTransfer`, `HybridShapeTranslate`, `HybridShapeTrim`, `HybridShapeUnfold`, `HybridShapeVolumeExplicit`, `HybridShapeWrapCurve`, `HybridShapeWrapSurface`, `HybridShapes`

**Part Design solid shapes** (50): `Add`, `Affinity`, `Assemble`, `AxisToAxis`, `Bodies`, `Body`, `Chamfer`, `CircPattern`, `CloseSurface`, `Draft`, `DraftDomain`, `DraftDomains`, `DraftingPageSetup`, `DraftingSettingAtt`, `EdgeFillet`, `FaceFillet`, `Factory`, `Groove`, `Hole`, `Intersect`, `Loft`, `Mirror`, `Pad`, `Pocket`, `RectPattern`, `Remove`, `RemoveFace`, `ReplaceFace`, `Rib`, `Rotate`, `Scaling`, `Scaling2`, `SewSurface`, `Shaft`, `Shape`, `ShapeFactory`, `ShapeInstance`, `Shapes`, `Shell`, `Slot`, `SolidCombine`, `Split`, `Stiffener`, `Symmetry`, `ThickSurface`, `Thickness`, `Translate`, `Trim`, `TritangentFillet`, `UserPattern`

**Product structure & assembly** (19): `AssemblyBoolean`, `AssemblyConvertor`, `AssemblyFeature`, `AssemblyFeatures`, `AssemblyHole`, `AssemblyPocket`, `AssemblySplit`, `Move`, `MoveActionActivity`, `MoveHomeAct`, `MoveJointsAct`, `MoveToPostureActivity`, `Product`, `ProductDocument`, `ProductScene`, `ProductScenes`, `Products`, `Publication`, `Publications`

**Drafting** (32): `DrawingArrow`, `DrawingArrows`, `DrawingComponent`, `DrawingComponents`, `DrawingDimExtLine`, `DrawingDimLine`, `DrawingDimValue`, `DrawingDimension`, `DrawingDimensions`, `DrawingDocument`, `DrawingLeader`, `DrawingLeaders`, `DrawingPageSetup`, `DrawingPicture`, `DrawingPictures`, `DrawingRoot`, `DrawingSheet`, `DrawingSheets`, `DrawingTable`, `DrawingTables`, `DrawingText`, `DrawingTextProperties`, `DrawingTextRange`, `DrawingTexts`, `DrawingThread`, `DrawingThreads`, `DrawingView`, `DrawingViewGenerativeBehavior`, `DrawingViewGenerativeLinks`, `DrawingViews`, `DrawingWelding`, `DrawingWeldings`

**Analysis / GPS / GAS** (34): `AnalysisAdaptivityManager`, `AnalysisCase`, `AnalysisCases`, `AnalysisDocument`, `AnalysisEntities`, `AnalysisEntity`, `AnalysisExport`, `AnalysisGeneralSettingAtt`, `AnalysisGlobalSensor`, `AnalysisImage`, `AnalysisImages`, `AnalysisImport`, `AnalysisLinkedDocuments`, `AnalysisLocalEntities`, `AnalysisLocalEntity`, `AnalysisLocalSensor`, `AnalysisManager`, `AnalysisMaterial`, `AnalysisMeshLocalSpecification`, `AnalysisMeshLocalSpecifications`, `AnalysisMeshManager`, `AnalysisMeshPart`, `AnalysisMeshParts`, `AnalysisModel`, `AnalysisModels`, `AnalysisOutputEntities`, `AnalysisPostManager`, `AnalysisPostProSettingAtt`, `AnalysisReportingSettingAtt`, `AnalysisSensor`, `AnalysisSet`, `AnalysisSets`, `AnalysisSettingAtt`, `AnalysisSupports`

**Kinematics / DMU** (14): `DMUDataFlow`, `DMUTolSettingAtt`, `FittingSettingAtt`, `Joint`, `Joints`, `KinematicsWorkbench`, `Mechanism`, `MechanismCommand`, `MechanismCommands`, `Mechanisms`, `Shuttle`, `Shuttles`, `Track`, `Tracks`

**Knowledgeware** (21): `Check`, `DesignTable`, `Formula`, `KnowledgeActivateObject`, `KnowledgeObject`, `KnowledgeSheetSettingAtt`, `Law`, `Optimization`, `OptimizationConstraint`, `OptimizationConstraints`, `Optimizations`, `Parameter`, `ParameterProfiles`, `ParameterProfilesFactory`, `ParameterSet`, `ParameterSets`, `Parameters`, `Relation`, `Relations`, `Rule`, `RuleBase`

**Electrical / Systems** (2): `ElecSchWire`, `ElecSchematicObject`

**Piping / Tubing / HVAC / Plant** (17): `ArrangementArea`, `ArrangementAreas`, `ArrangementBoundaries`, `ArrangementBoundary`, `ArrangementContour`, `ArrangementContours`, `ArrangementItemReservation`, `ArrangementItemReservations`, `ArrangementNode`, `ArrangementNodes`, `ArrangementPathway`, `ArrangementPathways`, `ArrangementProduct`, `ArrangementRectangle`, `ArrangementRectangles`, `ArrangementRun`, `ArrangementRuns`

**Machining / NC** (34): `MachiningProcess`, `ManufacturingAPTGenerator`, `ManufacturingActivity`, `ManufacturingCopyTransformation`, `ManufacturingFeature`, `ManufacturingFeatures`, `ManufacturingGeneratorData`, `ManufacturingHole`, `ManufacturingInsert`, `ManufacturingMachinableArea`, `ManufacturingMachinableFeature`, `ManufacturingMachinableGeometry`, `ManufacturingMachine`, `ManufacturingMachiningAxis`, `ManufacturingOperation`, `ManufacturingOutput`, `ManufacturingOutputGenerator`, `ManufacturingPattern`, `ManufacturingPrecedence`, `ManufacturingPrecedences`, `ManufacturingPrismaticMachiningArea`, `ManufacturingProcess`, `ManufacturingProgram`, `ManufacturingSetup`, `ManufacturingSurfaceGeomArea`, `ManufacturingSurfaceMachiningArea`, `ManufacturingTool`, `ManufacturingToolAssembly`, `ManufacturingToolCorrector`, `ManufacturingToolMotion`, `ManufacturingView`, `PPRActivity`, `PPRDocument`, `PPRProducts`

**Sheet Metal** (2): `Folder`, `Folders`

**Composites** (2): `CompositeTolerance`, `CompositesMaterial`

**FT&A / Tolerancing** (19): `Annotation`, `AnnotationFactory`, `AnnotationSet`, `AnnotationSets`, `Annotations`, `Capture`, `CaptureFactory`, `Captures`, `DatumSimple`, `DatumTarget`, `FTAInfraSettingAtt`, `FTASettingAtt`, `TPSView`, `TPSViewFactory`, `TPSViews`, `TolerancePerUnitBasisRestrictiveValue`, `ToleranceSheetSettingAtt`, `ToleranceUnitBasisValue`, `ToleranceZone`

**Human / Ergonomics** (7): `HumanActivityGroup`, `HumanActivityGroupFactory`, `HumanActsFactory`, `HumanCallTask`, `HumanProgram`, `HumanTask`, `HumanTaskList`

**Materials & rendering** (28): `Camera`, `Camera3D`, `Cameras`, `Environment`, `Light`, `LightSource`, `LightSources`, `Material`, `MaterialCondition`, `MaterialDocument`, `MaterialESSObjectSettingAtt`, `MaterialFamilies`, `MaterialFamily`, `MaterialManager`, `Materials`, `RenderingEnvironment`, `RenderingEnvironmentWall`, `RenderingEnvironments`, `RenderingLight`, `RenderingLights`, `RenderingMaterial`, `RenderingSettingAtt`, `RenderingShooting`, `RenderingShootings`, `Scene`, `SceneProductData`, `SceneWorkbench`, `Scenes`

**Infrastructure: documents, windows, views, selection, settings** (24): `Application`, `CatalogDocument`, `CatalogSHMObjectSettingAtt`, `Document`, `DocumentationSettingAtt`, `Documents`, `File`, `FileAccessStatisticsSettingAtt`, `FileComponent`, `FileSystem`, `Files`, `MacrosSettingAtt`, `Printer`, `Printers`, `PrintersSettingAtt`, `SearchSettingAtt`, `Selection`, `SelectionSets`, `Viewer`, `Viewer3D`, `Viewers`, `Viewpoint3D`, `Window`, `Windows`

**Settings controllers (*SettingAtt)** (66): `AccesslogStatisticsSettingAtt`, `AsmConstraintSettingAtt`, `AsmGeneralSettingAtt`, `BehaviorSettingAtt`, `CGRAdhesionSettingAtt`, `CacheSettingAtt`, `ColorESSObjectSettingAtt`, `ColorSTDObjectSettingAtt`, `CommandStatisticsSettingAtt`, `DLNameSettingAtt`, `DevAnalysisSettingAtt`, `DisconnectionSettingAtt`, `DynLicenseSettingAtt`, `ErrorlogStatisticsSettingAtt`, `Export3DXmlSettingAtt`, `FASReportingSettingAtt`, `FunctionalSystemSettingAtt`, `GeneralSessionSettingAtt`, `GeneralStatisticsSettingAtt`, `GlobalStatisticsSettingAtt`, `HtsCCPSettingAtt`, `HtsGeneralSettingAtt`, `HtsTaskDisplaySettingAtt`, `IgesSettingAtt`, `IgpOlpSettingAtt`, `ImportD5SettingAtt`, `InteropSettingAtt`, `LanguageSheetSettingAtt`, `Layout2DSettingAtt`, `LibTabSettingAtt`, `LicenseSettingAtt`, `ManipSettingAtt`, `MarkerSettingAtt`, `MeasureSettingAtt`, `MemoryWarningSettingAtt`, `MfgHubSettingAtt`, `MigrBatchSettingAtt`, `MultiCADSettingAtt`, `N4DNavigatorSettingAtt`, `PCSStatisticsSettingAtt`, `PartInfrastructureSettingAtt`, `PathESSRessourcesSettingAtt`, `PlugMapViewSettingAtt`, `RRSSettingAtt`, `ReportGenerationSheetSettingAtt`, `RobAnalysisHeartBeatUsageSettingAtt`, `RobAnalysisSettingAtt`, `SectioningSettingAtt`, `ServerStatisticsSettingAtt`, `SessionStatisticsSettingAtt`, `SimTraceSettingAtt`, `SimulationSettingAtt`, `SpecV4SettingAtt`, `StepSettingAtt`, `TreeTabSettingAtt`, `TreeVizManipSettingAtt`, `TypeESSObjectSettingAtt`, `UnitsSheetSettingAtt`, `V4V5SpaceSettingAtt`, `V4WritingSettingAtt`, `VerifTabSettingAtt`, `ViewCharacteristicCurvesSettingAtt`, `VisualizationSettingAtt`, `VrmlSettingAtt`, `WorkGeneralSettingAtt`, `WorkbenchStatisticsSettingAtt`

**Measure / analysis utilities** (4): `Inertia`, `Inertias`, `Measurable`, `SPAWorkbench`

**Other / uncategorised** (529): `AMPPath`, `AMPTag`, `Abstract`, `ActiveTask`, `Activities`, `Activity`, `Analyze`, `Angle`, `AngularRepartition`, `AnnotatedView`, `AnnotatedViews`, `AnyObject`, `ArrBOMReport`, `ArrBendableString`, `ArrNomenclature`, `ArrNomenclatureTree`, `ArrNomenclatures`, `ArrSystemLineProduct`, `ArrWorkbench`, `AssociatedRefFrame`, `AsySimActivity`, `AttachmentCont`, `AutoDraft`, `AutoFillet`, `AutoWalkActivity`, `AxisSystem`, `AxisSystems`, `BasicComponent`, `BasicComponents`, `BasicDevice`, `Behavior`, `BehaviorExtension`, `BehaviorVBScript`, `Behaviors`, `BiDimFeatEdge`, `BoolParam`, `BooleanShape`, `Boundary`, `CATBaseDispatch`, `CATBaseUnknown`, `CATIAArrangementNode`, `CATIAUnit`, `CalibOffsets`, `Clash`, `ClashResult`, `ClashResults`, `Clashes`, `Collection`, `CollisionFreeWalk`, `Command`, `Conflict`, `Conflicts`, `ConstRadEdgeFillet`, `ControledRadius`, `CurveFastener`, `CylindricalFace`, `D5Device`, `DMOOffset`, `DMOOffsets`, `DMOThickness`, `DMOThicknesses`, `DNB3DState`, `DNB3DStateMgmt`, `DNBAttachment`, `DNBAttachmentFactory`, `DNBFastenerItemServices`, `DOFState`, `DefaultAnnotation`, `DeviceJointRelations`, `Dictionary`, `Dimension`, `Dimension3D`, `DimensionLimit`, `DimensionPattern`, `Distance`, `Distances`, `DressUpShape`, `Dressup`, `Dressups`, `E5Property`, `EHMInsertionActPlugMapViewData`, `EHSUpdateSmoothnessFactor`, `EKPServices`, `Edge`, `EnumParam`, `EnvelopCondition`, `ExpertCheck`, `ExpertCheckRuntime`, `ExpertReportObject`, `ExpertReportObjects`, `ExpertRule`, `ExpertRuleBase`, `ExpertRuleBaseComponentRuntime`, `ExpertRuleBaseComponentRuntimes`, `ExpertRuleBaseRuntime`, `ExpertRuleRuntime`, `ExpertRuleSet`, `ExpertRuleSetRuntime`, `Face`, `Family`, `Fastener`, `FastenerGroup`, `FastenerSet`, `FastenerWorkBench`, `Father`, `FeatureGenerator`, `Fillet`, `FixTogether`, `FixTogethers`, `FlagNote`, `FreeParameter`, `FreeParameters`, `FreeSpace`, `FreeSpaces`, `FreeState`, `FunctActionsGroup`, `FunctActionsGroups`, `FunctAssociation`, `FunctAssociations`, `FunctFacetManagers`, `FunctGenScriptMgr`, `FunctMultiRepMgr`, `FunctNodeGraphLayout`, `FunctScript`, `FunctScripts`, `FunctionalAction`, `FunctionalActions`, `FunctionalDescription`, `FunctionalDocument`, `FunctionalElement`, `FunctionalFacet`, `FunctionalFacetMgr`, `FunctionalObject`, `FunctionalObjectProxy`, `FunctionalObjects`, `FunctionalPosition`, `FunctionalVariant`, `FunctionalVariants`, `GenericAccuracyProfile`, `GenericAction`, `GenericActionFactory`, `GenericMotionProfile`, `GenericObjFrameProfile`, `GenericToolProfile`, `GeometricElement`, `GrabAct`, `Group`, `Groupable`, `Groups`, `HomePosition`, `HtsJointSpeedSettingsAtt`, `HybridBodies`, `HybridBody`, `Hyperlink`, `Hyperlinks`, `IDispatch`, `IPDTemplateProperty`, `IUnknown`, `InstanceFactory`, `IntParam`, `Item`, `Items`, `Layout2DFactory`, `Layout2DRoot`, `Layout2DSheet`, `Layout2DSheets`, `Layout2DView`, `Layout2DViews`, `Length`, `Limit`, `Line`, `LinearRepartition`, `List`, `ListParameter`, `Loop`, `MHILoadParameters`, `MHIOpenAccess`, `MHIRelationManagement`, `MHISaveAccess`, `Marker2Ds`, `Marker3D`, `Marker3Ds`, `Merges`, `MfgActivities`, `MfgAssembly`, `MfgAssemblyFactory`, `MfgToolMotions`, `MonoDimFeatEdge`, `MountActivity`, `MountManager`, `NavigatorWorkbench`, `Noa`, `NonSemanticDatum`, `NonSemanticGDT`, `NotWireBoundaryMonoDimFeatVertex`, `Note`, `OLPTranslator`, `Operation`, `OperationProfile`, `OptimizerWorkBench`, `OrderGenerator`, `OrderedGeometricalSet`, `OrderedGeometricalSets`, `OriginElements`, `Outputs`, `PCBArea`, `PCBBoard`, `PCBComponent`, `PCBHoleAndPattern`, `PCBObject`, `PCBWorkbench`, `PageSetup`, `Part`, `PartComp`, `PartComps`, `PartDocument`, `ParticularTolElem`, `Pattern`, `PertNode`, `PickActivity`, `PlaceActivity`, `PlanarFace`, `Plane`, `Point`, `PointFastener`, `Position`, `PositionedMaterial`, `PrintArea`, `Prism`, `ProcessDocument`, `ProjectedToleranceZone`, `PspAppFactory`, `PspApplication`, `PspAttribute`, `PspAttributeReport`, `PspBuildPart`, `PspClass`, `PspCntrFlow`, `PspConnectable`, `PspConnector`, `PspFunctional`, `PspGroup`, `PspGroupable`, `PspID`, `PspLightBend`, `PspLightConnector`, `PspLightPart`, `PspListOfBSTRs`, `PspListOfDoubles`, `PspListOfLongs`, `PspListOfObjects`, `PspLogicalLine`, `PspObject`, `PspPartConnector`, `PspPhsyicalProduct`, `PspPhysical`, `PspPlacePart`, `PspResource`, `PspSpatial`, `PspStretchableData`, `PspTempListFactory`, `PspWorkbench`, `RealParam`, `RectilinearBiDimFeatEdge`, `RectilinearMonoDimFeatEdge`, `RectilinearTriDimFeatEdge`, `Reference`, `ReferenceFrame`, `References`, `ReleaseAct`, `Repartition`, `Replay`, `Resource`, `ResourceCollection`, `ResourceProgramManager`, `Resources`, `Revolution`, `RobControllerFactory`, `RobGenericController`, `RobotMotion`, `RobotTask`, `RobotTaskFactory`, `Roughness`, `RpmReport`, `SWKIKConstraint`, `SWKManikin`, `SWKManikinPart`, `Sampled`, `Sampleds`, `SampledsNode`, `SchAnnotationBreak`, `SchApp2DZoneFrom3DZone`, `SchAppClass`, `SchAppCntrColor`, `SchAppCntrData`, `SchAppCntrDocLink`, `SchAppCntrFlow`, `SchAppCntrName`, `SchAppCntrShow`, `SchAppCompatible`, `SchAppComponent`, `SchAppConnectable`, `SchAppConnection`, `SchAppConnector`, `SchAppDeleteCheck`, `SchAppDeleteCheck2`, `SchAppEnvironment`, `SchAppGapPriority`, `SchAppGroup`, `SchAppModelInit`, `SchAppMultiImage`, `SchAppMultiImageMaster`, `SchAppObjectFactory`, `SchAppObjectFactory2`, `SchAppReplace`, `SchAppRoute`, `SchAppRoute2`, `SchAppScalingRule`, `SchAppZone`, `SchArrowDisplay`, `SchBaseFactory`, `SchBoundaryElem`, `SchCatalogComponent`, `SchCatalogRoute`, `SchCntrConnect`, `SchCntrDocLink`, `SchCntrGraphic`, `SchCntrLocation`, `SchCompConnector`, `SchCompFlow`, `SchCompGraphic`, `SchCompGroupExt`, `SchCompatible`, `SchComponent`, `SchComponent2`, `SchComponentGroup`, `SchConnectable`, `SchDropOffView`, `SchFrameInfo`, `SchGRR`, `SchGRRCntr`, `SchGRRComp`, `SchGRRFactory`, `SchGRRRoute`, `SchGRRRoute2`, `SchGRRRouteAlternate`, `SchGRRRouteEllipse`, `SchGRRZone`, `SchGapDisplay`, `SchInternalFlow`, `SchListOfBSTRs`, `SchListOfDoubles`, `SchListOfLongs`, `SchListOfObjects`, `SchMovable`, `SchMovable2`, `SchNetworkAnalysis`, `SchObsoleteModel`, `SchPostReplace`, `SchReplace`, `SchRoute`, `SchRouteAlternateGraphic`, `SchRouteGraphic`, `SchRouteSymbol`, `SchSession`, `SchTempListFactory`, `SchUpdateInstances`, `SchWorkbench`, `SchZone`, `SchZoneGraphic`, `SchZoneMembership`, `SchematicExtension`, `SchematicRoot`, `Section`, `Sections`, `SelectedElement`, `SendToService`, `SetOfEquation`, `SettingController`, `SettingControllers`, `ShiftedProfileTolerance`, `Shot`, `Shots`, `Silhouette`, `Silhouettes`, `Simplifications`, `SimulationInitState`, `Solid`, `Spatial`, `SpecsAndGeomWindow`, `SpecsViewer`, `StiDBChildren`, `StiDBItem`, `StiEngine`, `StrAnchorPoint`, `StrAnchorPoints`, `StrComputeServices`, `StrCutback`, `StrFoundation`, `StrFoundations`, `StrMember`, `StrMemberExtremity`, `StrMembers`, `StrObject`, `StrObjectFactory`, `StrParam`, `StrPlate`, `StrPlates`, `StrSection`, `StrWorkbench`, `SurfaceBasedShape`, `Sweep`, `SweptVolume`, `SweptVolumes`, `SystemConfiguration`, `SystemService`, `TCPTrace`, `TCPTraceActivity`, `TCPTraceManager`, `TCPTraceManagerGraphics`, `Tag`, `TagFactory`, `TagGroup`, `TagGroupFactory`, `TangentPlane`, `Task`, `Text`, `TextStream`, `The`, `Thread`, `ThreeDCuts`, `Transformation`, `TransformationShape`, `Translator`, `TriDimFeatEdge`, `TriDimFeatVertexOrBiDimFeatVertex`, `Unit`, `Units`, `UnmountActivity`, `UserNomenclature`, `UserRepartition`, `UserSurface`, `UserSurfaces`, `V4MasterModel`, `V5`, `VarRadEdgeFillet`, `Vertex`, `VibrationVolumes`, `VisPropertySet`, `WIBuyOff`, `WIChangeNotification`, `WIDataCollection`, `WIText`, `WITextAccessEI`, `WalkActivity`, `Workbench`, `WorkerActivity`, `Wrapping`, `Wrappings`, `ZeroDimFeatVertexOrWireBoundaryMonoDimFeatVertex`, `access`, `an`, `analysis`, `another`, `application`, `base`, `based`, `bendable`, `case`, `circle`, `collection`, `component`, `conic`, `connector`, `controller`, `conversion`, `curve`, `cutback`, `document`, `drawing`, `entity`, `equations`, `factory`, `feature`, `file`, `flow`, `folder`, `folders`, `force`, `functional`, `geometry`, `history`, `image`, `level`, `limit`, `line`, `manage`, `manager`, `master`, `member`, `model`, `movable`, `parameter`, `parts`, `physical`, `plate`, `position`, `relation`, `root`, `schematic`, `section`, `service`, `set`, `shape`, `solid`, `stream`, `structure`, `surface`, `system`, `technological`, `temperature`, `template`, `textstream`, `the`, `this`, `traces`, `trim`, `up`, `workbench`

---

## 3. File-format compatibility

CATIA V5 native: `.CATPart` `.CATProduct` `.CATDrawing` `.CATShape` `.CATAnalysis`
`.CATProcess` `.CATMaterial` `.CATalog` `.CATfct` `.cgr` (tessellated) `.3dxml`
`.CATSettings` `.catvbs` / `.CATScript` (macros).

Import/export via Data Exchange Interfaces: **STEP** (AP203, AP214, AP242) · **IGES** (5.1–5.3) ·
**STL** · **DXF/DWG** · **VRML** (1.0/2.0) · **VDA-FS** (1.0/2.0) · **CGM** · **3D PDF** ·
**Parasolid** (`.x_t`/`.x_b`) · **ACIS** (`.sat`/`.sab`) · **JT** · CATIA **V4** (`.model`,
`.session`, `.dlv`, `.exp`) · raster (`.tif`, `.jpg`, `.png`, `.bmp`) · `.hcg` · `.wrl` ·
MULTICAx plug-ins (NX, Creo, SolidWorks, Inventor, Solid Edge).

**Kryova today: `export_step` out, and gmsh reads whatever the upload pipeline accepts.
Nothing else.** No import of a customer's existing CATProduct, no drawing exchange, no
V4 legacy data, no 3dxml, no JT — each of which is a hard blocker in aerospace/automotive,
the two industries that actually run V5.

---

## 4. Command-level gap from the 25 manuals

883 of the 907 documented commands have no first-class tool. Bold = we have it.

### Generative Shape Design (Wireframe & Surface) — 87 of 88 missing

- **Analysis / Shape Analysis** (1): Connect Checker
- **Operations** (26): Affinity, Boundary, Bump, Diabolo, Disassemble, Extract, Extrapolate, Fit To Geometry, Healing, Invert Orientation, Join, Multiple Extract, Near, Rotate, Scaling, Shape Fillet, Shape Morphing, Split, Symmetry, Transfer, Translate, Trim, Unfold, Untrim, Wrap Curve, Wrap Surface
- **Shape Analysis** (13): Apply Dress-Up, Cutting Planes Analysis, Distance Analysis, Draft Analysis (surface), Environment Mapping, Geometric Information, Highlight Lines, Inflection Lines, Isophote Mapping, Porcupine Curvature Analysis, Reflection Lines, Surface Curvature Mapping, Surfacic Curvature Analysis
- **Surfaces** (12): Adaptive Sweep, Blend, Cylinder, Extrude, Fill, Multi-sections Surface, Offset, Revolve, Rough Offset, Sphere, Sweep, Variable Offset
- **Volumes** (9): Thick Surface (volume), Volume Extrude, Volume Fill, Volume Join, Volume Multi-sections, Volume Revolve, Volume Split, Volume Sweep, Volume Trim
- **Wireframe** (27): _(bold = we have it)_ 3D Curve Offset, Axis, **Circle**, Combine, Conic, Connect Curve, Contour, Corner, Extremum, Extremum Polar, Helix, Intersection, Isoparametric Curve, Law, Line, Parallel Curve, Plane, Planes Between, Point, Points and Planes Repetition, Polyline, Projection, Reflect Line, Rolling Offset, Spine, Spiral, Spline

### Sketcher — 80 of 85 missing

- **Constraint** (17): Animate Constraint, Auto Constraint, Coincidence, Concentricity, Constraint, Constraint Defined in Dialog Box, Contact Constraint, Edit Multi-Constraint, Equidistant Point Constraint, Fix Constraint, Fix Together, Horizontal, Parallelism, Perpendicularity, Symmetry Constraint, Tangency, Vertical
- **Operation** (20): _(bold = we have it)_ Break, **Chamfer**, Close, Complement, Corner, Intersect 3D Elements, Isolate, **Mirror**, Offset, Project 3D Canonical Edges, Project 3D Elements, Project 3D Silhouette Edges, Quick Trim, Rectangular Pattern (2D), Rotate, Scale, Symmetry, Translate, Trim, User Pattern (2D)
- **Profile** (34): _(bold = we have it)_ Arc, Axis, Bi-Tangent Line, Bisecting Line, Centered Parallelogram, Centered Rectangle, **Circle**, Circle Using Coordinates, Conic, Connect Curve, Cylindrical Elongated Hole, Ellipse, Elongated Hole, Equidistant Points, Hexagon, Hyperbola by Focus, Infinite Line, Intersection Point, Keyhole Profile, Line, Line Normal To Curve, Oriented Rectangle, Parabola by Focus, Parallelogram, Point, Point by Coordinates, Profile, Projection Point, **Rectangle**, Spline, Three Point Arc, Three Point Arc Starting With Limits, Three Point Circle, Tri-Tangent Circle
- **Sketch tools / Visualization** (11): Change Sketch Support, Construction/Standard Element, Cut Part by Sketch Plane, Dimensional Constraints toggle, Geometrical Constraints toggle, Grid, Output Feature, Reflect Line, Sketch Tools numeric entry, SmartPick, Snap to Point
- **Sketcher** (2): _(bold = we have it)_ Positioned Sketch, **Sketch**
- **Tools** (1): Sketch Analysis

### Part Design — 65 of 80 missing

- **Analysis** (1): Draft Analysis
- **Boolean Operations / Insert menu** (10): Add, Assemble, Change Body, Insert Geometrical Set, Insert New Body, Insert Ordered Geometrical Set, Intersect, Remove, Remove Lump, Union Trim
- **Dress-Up Features** (5): _(bold = we have it)_ **Chamfer**, Draft Angle, **Edge Fillet**, **Shell**, Thread/Tap
- **Dress-Up Features / Surface-Based Features** (13): Chordal Fillet, Close Surface, Draft with Parting Element, Face-Face Fillet, Remove Face, Replace Face, Sew Surface, Split, Thick Surface, Thickness, Tritangent Fillet, Variable Angle Draft, Variable Radius Fillet
- **Edit menu** (1): Copy/Paste Special
- **Reference Elements / Tools** (8): Axis System, Create Datum, Extract, Line, Multiple Extract, Plane, Point, Publication
- **Sketch-Based Features** (15): _(bold = we have it)_ Drafted Filleted Pad, Drafted Filleted Pocket, **Groove**, **Hole**, Multi-Pad, Multi-Pocket, Multi-sections Solid, **Pad**, **Pocket**, Removed Multi-sections Solid, Rib, **Shaft**, Slot, Solid Combine, Stiffener
- **Tools / Analysis / Measure** (15): _(bold = we have it)_ Activate, **Apply Material**, Curvature Analysis, Deactivate, Isolate, **Measure Between**, Measure Inertia, **Measure Item**, Parent/Children, Part Comparison, Reorder, Save Management, Scan or Define In Work Object, Thickness Analysis, **Update**
- **Tools / context menu** (1): Define In Work Object
- **Transformation Features** (11): _(bold = we have it)_ Affinity, Axis to Axis, **Circular Pattern**, Explode Pattern, **Mirror**, **Rectangular Pattern**, Rotation, Scaling, Symmetry, Translation, User Pattern

### Drafting — 64 of 64 missing

- **Annotations** (11): Balloon, Bill of Material Table, Datum Target, Hyperlink, Roughness Symbol, Table, Table from CSV, Text, Text Replication, Text with Leader, Welding Symbol
- **Dimensioning** (15): Angle Dimension, Chained Dimensions, Chamfer Dimension, Coordinate Dimension Table, Coordinate Dimensions, Cumulated Dimensions, Datum Feature, Diameter Dimension, Dimension System, Hole Dimension Table, Length/Distance Dimension, Radius Dimension, Re-route Dimension, Stacked Dimensions, Thread Dimension
- **Dimensions** (1): Dimensions
- **Drawing / File menu** (6): Frame and Title Block, New Sheet, Print/Plot, Sheet Background, Sheet Setup, Update Drawing
- **Dress-Up** (8): 2D Component, Area Fill, Arrow, Axis Line, Axis Line and Center Line, Center Line, Instantiate 2D Component, Thread (Drafting)
- **Tools / Options** (1): Generative View Style
- **Views** (22): Add 3D Clipping, Advanced Front View, Aligned Section Cut, Aligned Section View, Auxiliary View, Breakout View, Broken View, Clipping View, Clipping View Profile, Detail View, Detail View Profile, Exploded View, Front View, Isometric View, Offset Section Cut, Offset Section View, Projection View, Quick Detail View, Section View, Unfolded View, View Creation Wizard, View from 3D

### Assembly Design — 53 of 54 missing

- **Analysis / Edit menu** (6): Bill of Material, Broken Link Analysis, Constraints Analysis, Degrees of Freedom, Dependencies, Mass Properties
- **Assembly Features** (7): Assembly Add, Assembly Hole, Assembly Pocket, Assembly Remove, Assembly Remove Lump, Assembly Split, Assembly Symmetry
- **Constraints** (11): Angle Constraint, Change Constraint, Coincidence Constraint, Contact Constraint, Deactivate Constraint, Fix Component, Fix Together, Offset Constraint, Quick Constraint, Reconnect, Reuse Pattern
- **Constraints / context menu** (1): Flexible/Rigid Sub-Assembly
- **Move** (5): Explode, Manipulation, Smart Move, Snap, Stop Manipulate On Clash
- **Product Structure Tools** (12): Component from Selection, Define Multi Instantiation, Existing Component, Fast Multi Instantiation, Generate Numbering, Graph Tree Reordering, Manage Representations, New Component, New Part, New Product, Replace Component, Selective Load
- **Properties / Cache** (9): Activate Node, Design Mode, Instance Name, Nomenclature, Part Number, Reference vs Instance, Revision, Source (Made/Bought), Visualization Mode
- **Space Analysis** (1): Compute Clash
- **Tools** (1): Publication
- **Update** (1): _(bold = we have it)_ **Update**

### Generative Part Structural Analysis (GPS) — 44 of 44 missing

- **Analysis Results** (1): Precision / Error Estimate
- **Analysis Results / Analysis Tools** (11): Adaptivity, Animate, Cut Plane Analysis, Deformation, Displacement, Global Sensor, Image Extrema, Local Sensor, Principal Stress, Report Generation, Von Mises Stress
- **Compute** (1): Static Case Solution
- **Loads** (13): Acceleration, Bearing Load, Distributed Force, Force Density, Imported Force, Imported Moment, Line Force Density, Moment, Pressure, Rotation Force, Surface Force Density, Temperature Field, Volume Force Density
- **Masses** (4): Distributed Mass, Line Mass Density, Non-structural Mass, Surface Mass Density
- **Restraints** (8): Ball Join, Clamp, Enforced Displacement, Isostatic Restraint, Pivot, Sliding Pivot, Surface Slider, User-defined Restraint
- **Virtual Parts** (6): Contact Virtual Part, Periodicity Condition, Rigid Spring Virtual Part, Rigid Virtual Part, Smooth Spring Virtual Part, Smooth Virtual Part

### Sheet Metal Design — 36 of 38 missing

- **Bending** (1): Unfold
- **Sheet Metal Parameters** (1): Sheet Metal Parameters
- **Walls** (2): Wall, Wall On Edge
- **Walls / Bending / Cutting-Stamping** (34): _(bold = we have it)_ Bead, Bend, Bend From Flat, Bridge, **Chamfer**, Circular Stamp, Conical Bend, Corner, Corner Relief, Curve Stamp, Cutout, Dowel, Extrusion, Flange, Flanged Cutout, Flanged Hole, Fold, Hem, **Hole**, Hopper, Junction, Louver, Mitre Corner, Multi-Viewer, Point or Curve Mapping, Recognize, Rectangular Stamp, Rolled Wall, Stamping Catalogue, Stiffening Rib, Surface Stamp, Tear Drop, User Flange, User Stamp

### Composites Design — 35 of 35 missing

- **Composites Parameters** (1): Composites Parameters
- **Detailed Design** (20): Contour, Core, Core Sampling, Cut Piece, Drop-off, EEOP, Edge of Part, ITP, Limit Contour, MEOP, Material Excess, Plies From Zones, Plies Group, Plies Manually, Ply, Ply Explode, Ply Split, Ramp Support, Skin Swap, Stack-Up File From Plies
- **Preliminary Design** (14): Constant Thickness, Import a Laminate, Laminate, Preliminary Design, Rosette, Sequence, Solid From Zones, Stack-Up File From Zones, Stacking, Transition Zone, Variable Thickness, Virtual Stacking, Zone, Zones Group

### NC Manufacturing Infrastructure — 30 of 30 missing

- **Manufacturing Program** (1): Part Operation
- **Manufacturing entities** (20): Approach Macro, Clearance Macro, Design Part, Feeds and Speeds, Fixture, Insert, Linking Macro, Machine, Machining Axis System, Machining Feature, Machining Pattern, Manufacturing Program, Retract Macro, Safety Plane, Stock, Tool Assembly, Tool Catalogue, Tool Change, Tool Compensation, Transition Macro
- **Output** (1): Post Processor
- **Verification** (8): Collision Check, Machining Time, NC Manufacturing Review, Photo Simulation, Remaining Material Analysis, Shop Floor Documentation, Tool Path Replay, Video Simulation

### Knowledge Advisor — 30 of 30 missing

- **Knowledge** (17): Action, Check, Deactivate Rule, Design Table, Equivalent Dimensions, Formula, Knowledge Inspector, Lock Parameter, Loop, Multiple Values, Parameter, Parameter Set, Published Parameter, Range, Reaction, Rule, Set of Relations
- **Knowledge language** (13): Extended language libraries, Feature access path, MessageBox(), Parameters.GetAttributeString, Trace(), area(), distance(), for loop, if / else, inertia(), length(), smartVolume(), volume()

### FreeStyle — 28 of 28 missing

- **FreeStyle** (28): 3-4 Point Patch, 3D Curve, Bend, Blend Surface (FreeStyle), Break Curve or Surface, Concatenate, Control Points, Curve Connect, Curve Smooth, Curve on Surface, Extend, Extrude Surface (FreeStyle), Fragmentation, Geometry Extraction, Global Deformation, Match Curve, Match Surface, Multi-Side Surface, Net Surface, Planar Patch, Project Curve, Shape Modification, Sketch Curve, Style Corner, Styling Fillet, Styling Sweep, Symmetry (FreeStyle), Twist

### DMU Kinematics — 28 of 28 missing

- **DMU Kinematics** (27): CV Joint, Cable Joint, Clash Detection During Simulation, Command, Cylindrical Joint, Degrees of Freedom (Kinematics), Fix Part, Gear Joint, Mechanism, Mechanism Dressup, Planar Joint, Point Curve Joint, Point Surface Joint, Prismatic Joint, Rack Joint, Replay, Rigid Joint, Roll Curve Joint, Screw Joint, Simulation with Commands, Simulation with Laws, Slide Curve Joint, Speed and Acceleration, Spherical Joint, Swept Volume, Trace, Universal Joint
- **Kinematics Joints** (1): Revolute Joint

### Prismatic Machining — 26 of 26 missing

- **Machining Operations** (26): Back Boring, Boring, Boring Spindle Stop, Boring and Chamfering, Break Chip, Circular Milling, Counterboring, Counterdrilling, Countersinking, Curve Following, Drilling, Drilling Deep Hole, Drilling Dwell Delay, Facing, Groove Milling, Pocketing, Point to Point, Prismatic Rework, Profile Contouring, Reaming, Reverse Threading, Sequential Milling, Spot Drilling, T-Slotting, Tapping, Thread Milling

### Aerospace Sheet Metal — 21 of 21 missing

- **Aerospace Sheet Metal** (17): Aerospace Sheet Metal Parameters, Bead, Bend Relief, Cleat, Clip, Curved Flange, Cutback, Doubler, Extremity Trim, Lightening Hole, Manufacturing View, Reference Plane, Shear Tie, Stiffener, Stringer, Support Surface, Swept Wall
- **Aerospace Sheet Metal Features** (1): Joggle
- **Flattening** (1): Flattening
- **Walls** (2): Flange, Web

### Electrical Harness Installation — 18 of 18 missing

- **Electrical** (17): Bundle Connector, Cableway, Cavity, Connectivity Diagram, Connector, Contact, Electrical Device, Extract Data, Flatten, Formboard Drawing, Geometrical Bundle, Multi-Branchable Document, Protective Covering, Route a Wire, Signal, Support (Electrical), Wire Definition
- **Electrical Harness** (1): Bundle Segment

### Piping Design — 17 of 17 missing

- **Piping** (1): Route a Pipe
- **Routing / Placement** (16): Bend, Connector (Fluid), Design Rules, Flow Direction, Hanger, Insulation, Isometric Generation, Line ID, Part in Placement Mode, Place a Part, Reducer, Route a Duct, Route a Tube, Route a Waveguide, Run, Specification

### Digitized Shape Editor / Quick Surface Reconstruction — 16 of 16 missing

- **Digitized Shape Editor / Quick Surface Reconstruction** (16): Activate, Align Clouds, Automatic Surface, Basic Surface Recognition, Curvature Mapping (QSR), Curve from Cloud, Curve from Scan, Deviation Analysis, Fill Holes, Filter Cloud, Import Cloud, Mesh Creation, Mesh Smoothing, Planar Sections, Power Fit, Remove

### Functional Tolerancing & Annotation (FT&A) — 15 of 15 missing

- **Annotations** (1): Geometrical Tolerance
- **Annotations / Views / Analysis** (14): Analysis Display Mode, Annotation Plane, Annotation Repositioning, Annotation Set, Capture, Datum (FTA), Dimension (FTA), Flag Note, Non-Semantic Annotation, Roughness (FTA), Semantic Annotation, Text with Leader (FTA), Tolerancing Advisor, Weld (FTA)

### Human Builder / Ergonomics — 15 of 15 missing

- **Ergonomics** (15): Anthropometry, Biomechanics Single Action, Carry, Insert a Manikin, Inverse Kinematics, Lift-Lower Analysis, Percentile, Population, Posture Editor, Preferred Angles, Push-Pull Analysis, RULA Analysis, Reach Envelope, Vision Window, Walk

### Generative Assembly Structural Analysis (GAS) — connections — 13 of 13 missing

- **Connection Properties** (12): Analysis Connection, Bolt Tightening Connection, Contact Connection, Node to Node Connection, Pressure Fitting Connection, Rigid Connection, Seam Welding Connection, Smooth Connection, Spot Welding Connection, Surface Welding Connection, Virtual Bolt Tightening Connection, Virtual Spring Bolt Tightening Connection
- **Connections** (1): Fastened Connection

### Surface Machining — 13 of 13 missing

- **Machining Operations** (13): Contour-driven, Isoparametric Machining, Multi-Axis Curve Machining, Multi-Axis Drilling, Multi-Axis Flank Contouring, Multi-Axis Sweeping, Multi-Axis Tube Machining, Pencil, Projection Machining, Roughing, Spiral Milling, Sweeping, ZLevel

### Structure Design — 12 of 12 missing

- **Structure** (12): Beam, Column, Cutback, End Cut, Footing, Handrail, Ladder, Place Section, Plate, Section Catalogue, Stair, Structure Member

### ELFINI / advanced analysis cases — 12 of 12 missing

- **Analysis Case** (7): Buckling Case, Combined Case, Damping, Frequency Case, Harmonic Dynamic Response, Modulation, Transient Dynamic Response
- **External Solvers** (5): Export to ANSYS, Export to Abaqus, Export to Nastran, Export to Patran, External Storage

### Advanced Meshing Tools — 12 of 12 missing

- **Meshing Methods / Mesh Specification** (12): Advancing Front Surface Mesher, Beam Mesher, Element Type, Free Edges, Group by Neighborhood, Local Mesh Sag, Local Mesh Size, Mesh Part Transition, Mesh Quality Analysis, Nodes and Elements, OCTREE Tetrahedron Mesher, OCTREE Triangle Mesher

### DMU Navigator — 12 of 12 missing

- **DMU Navigator** (12): Annotated View, Enhanced Scene, Examine Mode, Fly Mode, Group, Hyperlink (DMU), Magnifier, Markup, Publish (DMU), Turntable, Viewpoint, Walk Mode

### DMU Space Analysis — 11 of 11 missing

- **Space Analysis** (2): Interference, Sectioning
- **Space Analysis / Measure** (9): 3D Compare, Distance and Band Analysis, Fastener Group, Measure Between (DMU), Measure Item (DMU), Section Fill, Silhouette, Snap to Section, Thickness Analysis (DMU)

### Imagine & Shape — 10 of 10 missing

- **Imagine & Shape** (10): Attractor, Convert to NURBS, Crease, Cut Face, Extrude Face, Smooth (Imagine & Shape), Subdivide, Subdivision Primitive, Unweld, Weld

### Composites Manufacturing — 10 of 10 missing

- **Manufacturing** (9): Dart, Export Data, Flat Pattern Export, Flattening, Laser Projection Export, Manufacturing Document, Manufacturing Process, Ply Data Export, Splice
- **Producibility** (1): Producibility

### Weld Design — 9 of 9 missing

- **Weld Features** (9): Butt Weld, Edge Weld, Fillet Weld, Groove Weld, Plug Weld, Seam Weld, Spot Weld, Surfacing Weld, Welding Symbol

### Product Engineering Optimizer — 8 of 8 missing

- **Optimization** (8): Constraints (Optimization), Design of Experiments, Free Parameters, Gradient Algorithm, Objective, Optimization, Results Table, Simulated Annealing

### Composites Engineering — 8 of 8 missing

- **Analysis / Exchange** (8): Composites Grid Design, Graphical Analysis, Interference (Composites), Numerical Analysis, Ply Exchange, Ply Table, Solid Generation, Top Surface Generation

### Lathe Machining — 8 of 8 missing

- **Machining Operations** (8): Axial Machining on a Lathe, Finish Turning, Groove Turning, Ramp Rough Turning, Recess Turning, Rough Turning, Sequential Turning, Thread Turning

### DMU Fitting — 7 of 7 missing

- **DMU Fitting** (7): Automatic Path Finder, Clash Aware Path, Maintainability Study, Sequence, Shuttle, Smooth, Track

### Product Knowledge Template — 7 of 7 missing

- **Templates** (7): Contextual User Feature, Document Template, Instantiate From Catalog, Instantiate From Document, Power Copy, Save In Catalog, User Feature

### DMU Optimizer — 6 of 6 missing

- **DMU Optimizer** (6): Offset (Optimizer), Silhouette (Optimizer), Simplification, Space Reservation, Thickness (Optimizer), Wrapping

### Knowledge Expert — 6 of 6 missing

- **Expert Rules** (6): Expert Check, Expert Rule, Report (Knowledge Expert), Rule Base, Rule Set, Solve

### Systems Space Reservation — 5 of 5 missing

- **Space Reservation** (5): Access Zone, Compartment, Routing Corridor, Segregation Rule, Space Reservation Volume

### Developed Shapes — 3 of 3 missing

- **Developed Shapes** (3): Develop, Transfer (Developed Shapes), Unfold (Developed Shapes)

### Sketch Tracer — 3 of 3 missing

- **Sketch Tracer** (3): Create Immersive Sketch, Create Sketch (Sketch Tracer), Use Painted Sketch


**TOTAL: 883 of 907 documented CATIA commands have no first-class tool.**

---

## 5. FEA / simulation gap

Kryova does not use CATIA's analysis workbenches; it meshes with gmsh and solves with its own
linear-static solver. The whole of GPS, GAS, ELFINI, Advanced Meshing Tools and FEM Surface is
therefore missing, *and* the in-house solver is far narrower than the two Koh FEA volumes and
the GPS manual assume.

Current `LoadCase` (`app/solve/types.py`): one material, ≥1 fixture (DOF-locking on a face/box),
≥1 load (**a total force vector in newtons** on a face/box). That is the entire vocabulary.

**Loads missing:** Pressure · Moment · Bearing Load · Distributed Force · Force Density ·
Line Force Density · Surface Force Density · Volume Force Density · Acceleration (gravity) ·
Rotation Force (centrifugal) · Temperature Field · Imported Force · Imported Moment ·
Enforced Displacement · Belt/tension load

**Masses missing:** Distributed Mass · Line Mass Density · Surface Mass Density ·
Non-structural Mass

**Restraints missing:** Clamp (as a named concept) · Ball Join · Pivot · Sliding Pivot ·
Surface Slider · Isostatic Restraint · User-defined Restraint · Reflective/Cyclic Symmetry ·
Periodicity Condition. **No rotational DOFs at all**, which is why beams and shells cannot be
restrained correctly.

**Connections — the entire category:** Fastened · Contact · Bolt Tightening ·
Virtual Rigid Bolt Tightening · Virtual Spring Bolt Tightening · Pressure Fitting · Rigid ·
Smooth · Slider · Spot Welding · Seam Welding · Surface Welding · Node to Node ·
Welding Point · Distant · Face-Face · Analysis Connection (standalone workbench)

**Virtual parts:** Rigid · Smooth · Contact · Rigid Spring · Smooth Spring

**Analysis case types — only linear static exists:** Frequency/Modal · Buckling · Combined ·
Harmonic Dynamic Response · Transient Dynamic Response · Damping · Modulation ·
Thermal / Temperature Effect · Contact (non-linear) · Multi-case comparison

**Mesh:** tets only, `element_size_mm`, `element_order` 1|2, a `MAX_ELEMENTS` ceiling.
Missing: 2D shell mesh · 1D beam mesh · Beam Section · 2D/1D property · Local Mesh Size ·
Local Mesh Sag · Element Type · OCTREE Triangle/Tetrahedron mesher options ·
Advancing Front Surface Mesher · Mesh Quality Analysis / Worst Element Browser ·
Free Edges check · Duplicate Nodes check · Mesh Adaptation / Adaptivity · Re-meshing a domain ·
Imposing/distributing nodes · Removing holes/cracks · Mesh offset · Joining 2D meshes ·
Mesh export · Group by Neighborhood · Nodes and Elements inspection

**Results:** we show max von Mises, max displacement, FoS, mass, volume, node/element count,
solve time, and one von Mises surface. Missing: Principal Stress · stress components ·
strain · reaction forces · per-component displacement · Precision/Global Error Estimate ·
Local & Global Sensors · Image Extrema · Cut Plane Analysis · animation · deformation
amplification · deformed vs undeformed · element-edge display · results on a Group ·
overlaid/separated images · colour-map range editing · Report Generation · buckling factor ·
modal frequencies · stress-strain curve

**Solver interop:** Export to Nastran · Abaqus · ANSYS · Patran · External Storage.
*(The automation index also exposes a full **Abaqus/SIMULIA object family** — 46 `ABQ*`
objects covering steps, loads, BCs, interactions, jobs — i.e. CATIA can drive Abaqus
directly. We use none of it.)*

---

## 6. Infrastructure / environment gap

From *Formation Infrastructure* (the 262-page scanned manual, OCR'd for this review):

Session start & workbench chooser · Open/save a file · Help display · graphic screen zones ·
**toolbar management** · **the compass** (drag-to-move, reset) · graphic manipulation ·
**spec-tree level management** · tree overview (Shift+F2) · **Tools>Options tree** *(refused
by policy)* · **search / advanced search** · **defined views & custom view standards** ·
Send To > Directory (pack-and-go) · locating linked documents · global view ·
**magnifier (loupe)** · **selection modes** (trap, outside trap, intersecting trap, polygon
trap, paint stroke) · **measures** · visualisation-mode configuration · **light sources** ·
**depth effect** · predefined views · **image capture + options** · **saving images** ·
**printing** (true colour / greyscale) · **image album** · **video recording** ·
graphic properties · customise / personal workbench · macros *(refused by policy)* ·
**catalogue browser and creation** · **ISO standard-parts catalogue** · parameter tables

Plus the entire **Rendering / Photo Studio** workbench: Scene · Camera from Viewpoint ·
Environment · Wallpaper · light-source tuning · sticker apply/modify · material tuning for
render · Quick Render · Turntable · picture sequences · rendering output · image quality
parameters · wall management.

---

## 7. Frontend "button access" gap

The product UI is: chat, plus `projects / runs / files / history / settings`, plus a CATIA
device manager (pair/unpair) and a bridge status chip. **There is no CATIA control surface in
the browser at all** — every modelling action must be typed as a sentence.

Missing as UI, though the backend tool already exists in each case:

- **Feature tree** (`catia_list_features` exists) — cannot see, select, rename, reorder,
  deactivate or delete a feature by clicking.
- **Parameter panel** (`list_parameters` / `set_parameter` exist) — no table to edit a dimension.
- **Geometry picking** — `catia_select` takes feature *names* as strings. Nothing lets a user
  click a face or edge. This is the root cause of the 5-keyword fillet enum.
- **Checkpoint / restore history** — both tools exist; no list to roll back from, and
  `restore` needs an approval token nothing issues visually.
- **Captured-view gallery** (`capture_view` returns images with nowhere to browse them).
- **STEP export button** (`export_step` is agent-only).
- **Material picker** — the 8 materials are reachable only by asking.
- **Workbench indicator/switcher** (`switch_workbench` exists).
- **Dialog inspector** — `describe_dialog` / `fill_dialog` / `dialog_action` drive modal CATIA
  dialogs blind; the user cannot see what the agent sees.

Simulation UI:

- **Exactly one fixture and one load** — the schema allows lists (`min_length=1`), the editor
  has a single fieldset for each.
- Force is **Fx/Fy/Fz in newtons only** — no pressure, gravity toggle, or moment.
- Selector is **axis+side or a numeric box** — no picking a face from the 3D view.
- **No load-case library**, no duplicate-and-tweak, no side-by-side run comparison.
- Results are 8 stat cards + one stress view: **no legend range control, no cut plane, no
  animation, no displacement/component switch, no probe/sensor, no report export.**
- **Polling has no ceiling** — a job stuck in RUNNING polls at 1500 ms forever.
- **No mesh preview before solving**, so `element_size_mm` is chosen blind against a
  `MAX_ELEMENTS` limit that only fails after the fact.

---

## 8. What "support literally everything" actually requires

Stated plainly, because the number matters: full parity is **~100 workbenches, 907+ documented
commands, and a 1,080-object / 5,636-method automation API**. Dassault took two decades and
hundreds of engineers. This is not a backlog — it is a scope decision.

There is, however, a shortcut that changes the shape of the problem:

**Switch the bridge from menu-pressing to the COM automation API.** Today we press buttons and
fill dialogs. If the bridge instead binds `CATIA.ActiveDocument.Part.ShapeFactory` and
`HybridShapeFactory`, then:

- Sketch geometry gets real coordinates → §0's "everything is centred on the origin" limit
  disappears.
- `AddNewHoleFromPoint` / `AddNewHoleFromSketch` → the 5-position hole enum disappears.
- `HybridShapeFactory` (132 methods) delivers **all** of GSD wireframe & surface in one
  integration, instead of 88 separate menu scripts.
- Geometry is addressable by reference, so face/edge picking becomes possible — which is what
  unblocks fillets, shells, drafts, assembly constraints and FEA face selection together.
- No dialog-layout guessing, real error codes, and it is **language-independent** (the current
  approach breaks on a French or German CATIA install, which is why `catia_kb/languages.py`
  exists).

Recommended order, by unblocking power rather than by list length:

1. **COM automation bridge** (`ShapeFactory` + `HybridShapeFactory` + `Sketch`/`Factory2D` +
   `Parameters`/`Relations`). One integration, and most of §0 and much of §4 collapse.
2. **Geometry selection + reference geometry** (Plane, Point, Line, Axis System). The real
   ceiling on part shape.
3. **Sketcher constraints and the general `Profile` tool** — 80 of 85 Sketcher commands,
   including `Line`, `Arc`, `Spline` and every constraint.
4. **Assembly / `ProductDocument`** — nothing multi-part works without it, and it gates
   Drafting, DMU, GAS and BOM.
5. **Pressure, gravity, moment + multiple fixtures** — smallest FEA additions, widest reach.
6. **Feature tree + parameter table in the UI** — both backends already exist; only the
   rendering is missing.
7. **Import formats** (CATProduct, STEP in, V4, 3dxml) — without these, no existing customer
   data can enter the product.

Two things that need a decision rather than an implementation:

- **The macro/settings refusal in `ui_policy.py` caps automation parity by design.** Full
  compatibility and "never run a macro" cannot both hold. If parity is the goal, this needs a
  sandboxed, reviewed macro path rather than a flat refusal.
- **Licensing.** Most of §1 requires licences the customer's seat may not hold
  (`catia_kb/licensing.py` already models 188 licence facts). Supporting a workbench the seat
  cannot open is wasted work — worth checking entitlement before building.

---

## Appendix: source manuals

| Document | Pages | How it was read |
|---|---:|---|
| FR Formation Cours Dessin | 378 | OCR (was unindexed) |
| FR Formation cours conception pièces exercices | 346 | OCR (was unindexed) |
| FR Formation Assemblage | 286 | OCR (was unindexed) |
| FR Formation Infrastructure-Esquisse-Catalogue | 262 | OCR (was unindexed) |
| FR part_design | 430 | text layer |
| FR Generative Shape Design | 390 | text layer |
| FR FreeStyle Shaper & Optimizer & Profiler | 354 | text layer |
| FR Generative Structural Analysis (GPS/EST) | 319 | text layer |
| FR Generative Drafting | 267 | text layer |
| FR DMU Kinematics Simulator | 264 | text layer |
| FR Assembly Design | 250 | text layer |
| FR Wireframe and Surface | 245 | text layer |
| FR Sheet Metal Design | 210 | text layer |
| Koh — CATIA v5 FEA R21 Part 1 | 199 | text layer |
| Koh — CATIA v5 FEA R21 Part 2 | 199 | text layer |
| CATIA V5-6R2015 Basics Part II (Part Modeling) | 197 | text layer |
| EN photo-studio (Rendering) | 146 | text layer |
| CATIA V5-6R2015 Basics Part I (Sketcher) | 131 | text layer |
| EN fem-surface (FEM Surface) | 99 | text layer |
| EN Generative Assembly Structural Analysis | 81 | text layer |
| EN Designer Guide Ch.11 Assembly Modeling | 64 | text layer |
| EN Designer Guide Ch.1 Sketcher | 46 | text layer |
| EN Designer Guide Ch.5 Dress-Up & Holes | 42 | text layer |
| EN Designer Guide Ch.9 Wireframe & Surface | 40 | text layer |
| EN c03_cat_v5r18 (Sketcher II) | 40 | text layer |

**Note:** these are third-party Dassault Systèmes manuals committed to a repository carrying
its own licence — `data/bm25/README.md` flags this, and a later `.gitignore` cannot undo it.
Separately, the BM25 index silently omitted the 4 scanned manuals, so ~24% of the corpus was
invisible to retrieval until this review; rebuilding the index over the OCR'd text fixes that.
