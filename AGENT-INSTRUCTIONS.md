# AGENT-INSTRUCTIONS.md

# DUMBO DISTRICT DIGITAL TWIN MODULE

Status: Independent Workstream

This module is intentionally separate from the Manhattan Bridge Digital Twin project.

The Manhattan Bridge team owns:
- Bridge geometry
- Bridge control dimensions
- Bridge component taxonomy
- Bridge photogrammetry
- Bridge engineering detail
- Bridge-specific browser assets

The DUMBO team owns:
- Neighborhood geospatial model
- Buildings
- Streets
- Waterfront
- Terrain
- Property metadata
- Tile streaming
- LOD management
- Walking experience
- District-level photogrammetry

The two projects must interoperate but must not duplicate effort.

---

# GOVERNANCE

Treat Manhattan Bridge as an external dependency.

Do not rebuild:
- bridge towers
- cables
- suspenders
- deck systems
- bridge trusses
- bridge metadata

Instead consume:

/modules/manhattan-bridge/

through a published contract.

The DUMBO module should assume a future file:

bridge-manifest.json

will provide:
- asset ids
- geographic anchor locations
- bounding volumes
- lod definitions
- viewer metadata

---

# PRIMARY OBJECTIVE

Create a walkable browser-renderable DUMBO digital twin.

Target experience:

User can:
- stand on Washington Street (or any Dumbo street, waterfront, park) and look toward Manhattan Bridge or Brooklyn Bridge
- walk dumbo streets and park 
- explore buildings
- inspect metadata
- switch between map and 3D view

Use game-style streaming.

High fidelity nearby.

Lower fidelity at distance.

---

# SOURCE HIERARCHY

Tier A
- NYC Building Footprints
- NYC 3D Buildings
- PLUTO
- MapPLUTO
- NYC LiDAR
- DEM

Tier B
- Historic imagery
- Street imagery
- Mapillary
- Orthophotography

Tier C
- Photogrammetry
- Existing meshes
- AI-assisted reconstruction

No Tier C geometry can override Tier A geometry.

---

# PROJECT STRUCTURE

modules/
  dumbo-district/
    AGENT-INSTRUCTIONS.md
    DUMBO-SCOPE.md
    DUMBO-SOURCE-REGISTER.md
    DUMBO-GEOSPATIAL-CONTROL.md
    DUMBO-LOD-STRATEGY.md
    DUMBO-MAP-VIEWER-INTEGRATION.md

    data/
      boundaries/
      footprints/
      pluto/
      lidar/
      dem/
      imagery/

    assets/
      buildings/
      terrain/
      streetscape/
      waterfront/

    viewer/
      map/
      scene/
      registry/

---

# INTERSECTION CONTRACT

The ONLY shared ownership area is:

/shared-contracts/

Create:

asset-registry.schema.json
source-confidence.schema.json
lod.schema.json
metadata.schema.json
coordinate-system.md

Both projects consume these.

Neither project edits the other's geometry.

---

# VIEWER OWNERSHIP

Avoid collision by assigning responsibilities.

Manhattan Bridge Team
- bridge metadata panels
- bridge component inspection
- bridge exploded views

DUMBO Team
- map integration
- camera system
- tile streaming
- walking mode
- LOD system
- district navigation

Shared Viewer Layer
- asset registry
- object selection API
- metadata API
- coordinate transforms

---

# LOD STRATEGY

LOD0
Hero zone
Washington Street
Water Street
Bridge approaches
Waterfront

LOD1
Walkable district

LOD2
Context buildings

LOD3
Map silhouette

---

# PHASE 1 DELIVERABLES

1. Boundary definition
2. Tile grid
3. Building footprint import
4. PLUTO integration
5. Geospatial control model
6. Asset registry schema
7. Map viewer prototype
8. Scene viewer prototype
9. Bridge placeholder integration point

No photogrammetry yet.

---

# VS CODE AGENT START PROMPT

Read AGENT-INSTRUCTIONS.md completely before making changes.

Project Goal:
Build the DUMBO District Digital Twin as an independent module that interoperates with the Manhattan Bridge Digital Twin but does not duplicate bridge work.

Rules:
1. Manhattan Bridge is an external dependency.
2. Do not model bridge geometry.
3. Build geospatial control layers first.
4. Use NYC footprints, PLUTO, NYC 3D model, and LiDAR as authoritative sources.
5. Every asset must have source_basis and confidence.
6. Create shared schemas only in /shared-contracts.
7. Viewer work is limited to map integration, navigation, tile streaming, and LOD management.

Deliverables:
- DUMBO-SCOPE.md
- DUMBO-SOURCE-REGISTER.md
- DUMBO-GEOSPATIAL-CONTROL.md
- DUMBO-LOD-STRATEGY.md
- asset-registry.schema.json
- metadata.schema.json
- tile-index.schema.json
- DUMBO map boundary definition
- hero fidelity zone definition
- initial tile grid
- browser map/scene architecture

Stop after Phase 1 and produce an implementation report.

---

# CRITICAL PRINCIPLE

The Manhattan Bridge project is the authoritative bridge model.

The DUMBO project is the authoritative neighborhood model.

Integration happens through shared contracts and asset references.

Never through duplicated geometry.
