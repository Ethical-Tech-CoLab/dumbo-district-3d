# Decision-model recommendation: source-to-asset review

Date: 2026-10-11. Public-source proposal.
Reviewed [README](https://github.com/Ethical-Tech-CoLab/dumbo-district-3d/blob/be41ddd3d29ca2e2d2dec20d1c69609d93ed8672/README.md).
Assessment is README-led, not a review of all source joins.

## Useful seam

After exact building/parcel identifiers and coordinate checks, evaluate an
optional metadata-to-asset relevance assessor for ambiguous photo/source
records. Return same subject / related / different / insufficient evidence,
with explicit source and human review.

Do not replace official footprint joins, coordinate transforms, walking-network
algorithms, shared document schemas or bridge placement with model guesses.
Keep modeled/procedural facades distinct from observed evidence. A photo
ranking score does not establish that a building's dimensions were measured.

## Trial

Compare current exact/fuzzy matching and a local classifier with Jev's
[entity-alignment pattern](https://docs.typesafe.ai/cookbooks/entity_alignment.md),
Microsoft-Decision-1 and Sage on approved public metadata.
Split by building/block/capture source; include neighboring similar facades,
stale addresses, repeated images and incomplete geolocation.

Measure candidate recall, wrong-building associations, calibration, reviewer
burden and total cost per accepted link. Test A/B symmetry and presentation
order. Preserve the original human-reviewed corpus as evidence, not an
automatically overwritten label store.

## Gate and impact

Read-only derived suggestions; no change to tile generation, source grades,
district/bridge separation or shared viewers. Runtime adoption requires
unchanged schema/geometry regression outputs and owner-approved false-link
bounds. Exact hosted checkpoints are not locally available; separate licensed
local models are alternatives. No measured saving is claimed.
