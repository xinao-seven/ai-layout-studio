# ADR-0008: Solver spike — candidate poses, door clearance and group feasibility

- Status: Accepted (spike conclusions; production solver lands in M4)
- Date: 2026-11-16
- PRD: §6.3, FR-14, M2-08
- Deciders: project owner
- Code: `apps/server/app/solver/spike.py`, `apps/server/tests/test_solver_spike.py`

## Context

M4 needs a deterministic `auto_layout` solver: ≤5 s p95 for 20 items, identical
output for identical input, hard constraints (room inset, OBB collision, door
clearance) and a stable scoring model. M2 only has two days for a spike, so the
goal was to de-risk the three pieces that most affect the M4 architecture
rather than to ship a solver:

1. Can a deterministic *candidate pose* generator produce useful wall-flush
   placements without a search over a fine grid?
2. How expensive is door clearance when evaluated as a two-sided OBB obstacle?
3. Does a greedy group feasibility probe survive a realistic living-room set,
   or is beam search mandatory from day one?

## Spike implementation

- `candidate_poses(room, size, rules)` samples each edge of the Shapely room
  inset at `0.5 m`, pushes the oriented box inward by its half-extent along the
  wall normal (plus `WALL_FLUSH_GAP`) and keeps poses whose four corners are
  inside the inset. Interior grid poses are added as a fallback.
- Poses are deduplicated on `(x, z, rotation)` and sorted lexicographically, so
  the output is deterministic across runs and processes.
- `door_clear_ok` builds one two-sided OBB per door
  (`max(width, DOOR_APPROACH_WIDTH) × 2 × DOOR_CLEAR_DEPTH`) and runs the same
  SAT test used by manual placement validation.
- `group_feasible` places footprints in caller order, taking the first pose that
  clears doors and all earlier boxes. It returns `None` on the first
  unsatisfiable item.

## Results

| Question | Observation | Decision |
|---|---|---|
| Wall-flush candidates | Sampling at 0.5 m produced >10 valid poses for a 1.81 × 0.76 m sofa in a 6 × 5 m room, including all four rotations. | Keep edge sampling as the primary generator; M4 can lower the step to 0.25–0.5 m near anchors only. |
| Determinism | Lexicographic sorting + rounded dedupe yields identical lists in repeated calls. | No RNG or unordered set iteration in the solver core. |
| Door clearance | OBB-vs-OBB against one rectangle per door is cheap and reuses manual-placement semantics. | Share `opening_clearance_obb` between the solver, eval runner and SceneService. |
| Group feasibility | A greedy pass placed sofa + coffee table in a 6 × 5 m room. A room-sized item correctly returns `None`. | Use greedy feasibility as a fast pre-check; M4 still adds beam search because greedy order is fragile for 20 items. |
| Failure mode | Greedy can paint itself into a corner when a large anchor is placed late. | M4 places anchors by `anchorPriority`/area, then searches with beam width `SOLVER_BEAM_WIDTH`, scoring clearance + centrality + group adjacency. |

The spike does **not** cover scoring, L-shaped/concave rooms, walkways or
before/after visual checks. These remain M4 work.

## Consequences

- M4 can reuse the candidate-pose generator and door-clearance predicate, but
  must add scoring, beam search, a time budget and a deterministic tie-break.
- Candidate generation is intentionally independent from catalog rules; M4's
  scoring layer will consume `CatalogRules` (anchor priority, group affinity).
- Concave polygons rely on Shapely's inset and may split into multiple usable
  regions; M4 should choose the component that contains the room centroid and
  record a warning when the largest component is used.
- Evaluation cases in `fixtures/eval/operation-cases.json` already assert
  `noCollision`, `insideRoom` and `doorClear`, so solver outputs can be checked
  with the same runner.
