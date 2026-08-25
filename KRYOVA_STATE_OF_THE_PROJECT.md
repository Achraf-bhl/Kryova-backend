# Kryova — State of the Project & Roadmap to 100

## Current Score: 74 / 100

### Backend (82/100)
Strong foundation. Real physics, verified against closed-form solutions.
Clean layered architecture with DI throughout. Content-addressed blob store.
Schema-qualified Postgres for PgBouncer compatibility. SHA-256 pre-hash + bcrypt auth.
Rate limiting on auth endpoints. SQLite fallback for local dev/CI.

**What holds it back:** No linter/formatter/type checker. No CI. In-process job queue only.
No pagination on list endpoints. 266 MB of CATIA training PDFs tracked in git.
No refresh token — 24 h JWT expires mid-session with no recovery path.

### Frontend (65/100)
Functional but fragile. All pages are client components doing `useEffect` fetching.
Token in localStorage (XSS-exfiltratable). Zero component tests.

**What holds it back:** The bugs below were live until today's fixes. Even fixed,
the architecture is client-heavy when Next.js server-side rendering is available.

---

## Bugs Fixed Today

1. **Auth infinite spinner** — `auth-context.tsx` never cleared `loading` when no token existed, trapping logged-out users on a blank dashboard.
2. **Mass displayed 1000× too small** — results page divided kg by 1000 and labelled it kg.
3. **WebGL viewer GL object leaks** — every `scaleFactor` slider tick recreated program + shaders + buffers without deleting the old ones. Now cleaned up on effect teardown.
4. **WebGL index overflow** — used `Uint32Array` without enabling `OES_element_index_uint`. Now checks max vertex index and enables extension when needed.
5. **`String(undefined)` rendered "undefined"** — replaced with optional chaining.
6. **Dead code removed** — `stress-viewer.tsx` (158 lines, never imported) deleted.
7. **Polling fragility** — flat 1500 ms polling that stopped permanently on first error. Now has exponential backoff (1.5s → 3s → 6s) and retries up to 3 times before showing an error.
8. **Canvas resize thrashing** — reassigned `canvas.width/height` inside rAF loop every frame. Now caches and only resizes on actual dimension change.

---

## Remaining Work to Reach 100

### Critical (blocks shipping)

- [ ] Move auth token from localStorage to httpOnly cookies. Requires backend Set-Cookie + CSRF protection.
- [ ] Add middleware.ts for server-side route protection.
- [ ] Refresh tokens (short-lived access ~15 min + long-lived httpOnly refresh).
- [ ] CI pipeline (GitHub Actions: lint + typecheck + test both repos). Backend needs ruff + mypy first.
- [ ] Remove data/ PDFs from git (266 MB third-party copyrighted material).

### High priority

- [ ] Server Components for initial data (dashboard project list, simulation detail).
- [ ] Pagination on all list endpoints.
- [ ] Component tests (login page render, results page render — would have caught two bugs above).
- [ ] Backend linter + formatter (pyproject.toml with ruff + mypy config, run in CI).
- [ ] Colour legend on stress viewer (map blue→red ramp back to MPa values).
- [ ] Touch/pointer events on viewer for mobile support.
- [ ] Zoom + pan on viewer.

### Medium priority

- [ ] Celery/RQ job queue for multi-worker production.
- [ ] Wire tet10 assembly into solver.solve() (function exists but unused).
- [ ] S3 media store swap behind existing interface.
- [ ] Modal/buckling analysis types via Solver ABC.
- [ ] Report generation (exportable PDF summary).
- [ ] WebGL viewer context loss handler.
- [ ] E2E tests (Playwright: upload → simulate → view results).

### Nice to have

- [ ] CATIA COM bridge (Windows-only sidecar for native .CATPart import)
- [ ] AI surrogate solver (ML model trained on FEA results for instant estimates)
- [ ] Collaboration features (share projects, comment on simulations)
- [ ] Version diffing (compare stress distributions across geometry versions)
- [ ] Batch simulations (multiple load cases in one submission)

---

## Architecture Decisions Worth Keeping

1. Three seams: Solver, JobQueue, MediaStore — all ABC interfaces. Never reach around them.
2. mm-N-MPa unit system: Self-consistent. No conversion anywhere.
3. 404-not-403 for cross-user access. Prevents ID enumeration.
4. Schema-qualified tables via schema_translate_map, not SET search_path.
5. Content-addressed blobs: SHA-256 filename = integrity check for free.

## Competitor Landscape

| Feature | Kryova | SimScale | Onshape Sim | Ansys Discovery |
|---------|--------|----------|-------------|-----------------|
| Cloud-native | ✅ | ✅ | ✅ | Partial |
| Browser 3D viewer | ✅ | ✅ | ✅ | Desktop |
| Linear static | ✅ | ✅ | ✅ | ✅ |
| Nonlinear | ❌ | ✅ | ❌ | ✅ |
| CFD | ❌ | ✅ | ❌ | ✅ |
| Modal/dynamic | ❌ | ✅ | ✅ | ✅ |
| Free tier | Planned | 10/mo | w/ CAD | Trial |
| CAD import | STEP/IGES/STL | 20+ | Native | Native |
| AI acceleration | Planned | AI agents | ❌ | GPU |

Kryova's differentiator should be **AI-native workflows** — not competing head-on
with Ansys on solver breadth, but offering instant AI-surrogate estimates alongside
verified FEA.
