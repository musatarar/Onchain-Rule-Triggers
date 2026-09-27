---
version: 1
slug: "frontend-src-pages-signinpage-tsx"
primary_target: "frontend/src/pages/SignInPage.tsx"
related_targets: ["frontend/src/pages/RegisterPage.tsx","frontend/src/pages/ConsumePage.tsx"]
---

Scope: the signed-out surfaces: /signin (password, email-link request, link sent, link expired), /register, /auth/consume, the session check, and the console's operator readout + sign out. Mode: Operate.
Audience/job: crypto ops, treasury and risk people getting into the console fast and safely. Username and password is the default route in, and the email magic link is the secondary one (backend on branch claude/practical-bardeen-im1k5a).
Content: server sentences are the copy of record (invalid_credentials, username_taken, weak_password, rate_limited). The UI never claims more than the API says (the email is unverified, and email isn't used to sign in).

## Direction contract
THESIS: Signing in is tuning the monitor in. The tube shows soft static with NO SIGNAL until an operator is recognised, then the picture rolls and locks into the console. It refuses a centred card on a blank page.
OWN-WORLD: The established Phosphor world (DESIGN.md), unchanged: the same bezel, tube, scanlines, VT323/Share Tech Mono, phosphor ramp, square glass controls, inversion for the one primary. New material: a low-res phosphor noise field behind the form, and a rolling hold bar.
STORY: The visitor sees the product's own screen, off-air, and one compact TUNE IN panel. They sign in (or pick NEW OPERATOR or EMAIL LINK from the tab strip). A wrong password leaves the static and prints LOGIN INCORRECT. Success locks the signal and hands over to the journal.
FIRST VIEWPORT: The standard monitor header (coil mark + PHOSPHOR wordmark), a tab strip with SIGN IN · NEW OPERATOR · EMAIL LINK, a full-tube noise field with a dim NO SIGNAL caption, and a centred 380px panel with USERNAME, PASSWORD and an inverse SIGN IN. The status line reads SIGNAL NONE · AWAITING OPERATOR.
FORM: Surface structure 5 of 5 (channel tuning), dealt lead; seed 7b33d29e; degraded roll.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
