# Tiny in-house `useFetch` hook > SWR > TanStack Query

The original plan was SWR — lightweight, simple API, handles cache + refresh
out of the box, easier on-ramp than TanStack Query for a first JS data-fetch
layer. We made that call when the frontend was still using window globals
and hand-authored mocks.

When we tried to actually load SWR, it didn't work. SWR 2.x ships ESM only
— no UMD bundle on npm, no UMD path on unpkg/jsDelivr/cdnjs. The
`<script src="…swr…umd.js">` tag silently 404'd, `window.SWR` was never
set, and every fetch fell through to an error. The old "fall back to
mocks" path masked it; once that fallback was removed, the broken CDN
became visible as "can't reach the forecast service" on every screen.

Options at that point: (a) bundle SWR ourselves and serve it locally —
breaks the no-build promise; (b) downgrade to SWR 1.x — last UMD shipped
but still CJS-formatted, not browser-loadable; (c) use an ESM-only setup
with `<script type="module">` — out of step with the rest of the (deferred,
non-module) scripts and would require ordering tricks; (d) replace SWR
with a tiny in-house `useFetch` hook.

We picked (d). Our actual usage is small — three keyed fetches with optional
poll and refocus revalidation — and the hook is ~50 lines inside
`dataProvider.js`. If we ever need SWR's full feature set (stale-while-
revalidate, optimistic mutate, focus dedup beyond the trivial case), we'll
revisit once we have a build step. For the MVP, an in-house hook is the
right size of solution and removes a brittle CDN dependency.

# Single-Page App > Multi-Page App

It sounds like single page apps (SPA's) are generally preferred for creating fast and fluid user experiences. The biggest drawback I've read up on is Search Engine Optimization (SEO), but since we are only building a MVP, this is not a concern. I think a SPA for this project will provide a better final experience, and will provide a more applicable learning experience for us for future projects.

# IaC with Terraform > Manual UI Deployment

I hate manually rumaging through UI to set, change and deploy service infrastructure. Terraform is widely used in industry, is open source + free, so it will be a good thing to learn. Also, since we will be assisted by AI, AI can write and inspect the infrastructure code instantly instead of us having to describe it.

