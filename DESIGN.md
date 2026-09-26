---
name: Onchain Rule Triggers (working name)
description: Ladder-logic rule tracing drawn on a green-phosphor CRT set into a rugged monitor bezel.
colors:
  phosphor: "#5dff8f"
  phosphor-hot: "#9dffbd"
  phosphor-2: "#3fd873"
  phosphor-3: "#2aa452"
  phosphor-4: "#1c6e37"
  phosphor-5: "#103f20"
  phosphor-6: "#0a2814"
  screen: "#03120a"
  screen-glow: "#062a15"
  screen-edge: "#010804"
  field: "#062012"
  inspector-top: "#051c0e"
  housing: "#25281f"
  bezel-top: "#3b3f31"
  bezel-bottom: "#22251c"
typography:
  display:
    fontFamily: "VT323, 'Share Tech Mono', ui-monospace, monospace"
    fontSize: "30px"
    fontWeight: 400
    lineHeight: 1
    letterSpacing: "0.04em"
  numeral:
    fontFamily: "VT323, 'Share Tech Mono', ui-monospace, monospace"
    fontSize: "34px"
    fontWeight: 400
    lineHeight: 1.2
  headline:
    fontFamily: "VT323, 'Share Tech Mono', ui-monospace, monospace"
    fontSize: "26px"
    fontWeight: 400
    lineHeight: 1.05
    letterSpacing: "0.03em"
  title:
    fontFamily: "VT323, 'Share Tech Mono', ui-monospace, monospace"
    fontSize: "24px"
    fontWeight: 400
    lineHeight: 1
    letterSpacing: "0.05em"
  tab:
    fontFamily: "VT323, 'Share Tech Mono', ui-monospace, monospace"
    fontSize: "22px"
    fontWeight: 400
    lineHeight: 1
    letterSpacing: "0.06em"
  body:
    fontFamily: "'Share Tech Mono', ui-monospace, Menlo, Consolas, monospace"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: "0.01em"
  body-sm:
    fontFamily: "'Share Tech Mono', ui-monospace, Menlo, Consolas, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "'Share Tech Mono', ui-monospace, Menlo, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.2
    letterSpacing: "0.06em"
  gate-subject:
    fontFamily: "'Share Tech Mono', ui-monospace, Menlo, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
    letterSpacing: "0.04em"
  gate-test:
    fontFamily: "'Share Tech Mono', ui-monospace, Menlo, Consolas, monospace"
    fontSize: "14px"
    fontWeight: 400
  gate-number:
    fontFamily: "'Share Tech Mono', ui-monospace, Menlo, Consolas, monospace"
    fontSize: "10px"
    fontWeight: 400
rounded:
  none: "0px"
  hit: "2px"
  screen: "14px"
  bezel: "22px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "14px"
  xl: "18px"
components:
  button:
    backgroundColor: "transparent"
    textColor: "{colors.phosphor}"
    typography: "{typography.body}"
    rounded: "{rounded.none}"
    padding: "7px 12px"
  button-hover:
    backgroundColor: "{colors.phosphor-6}"
  button-sm:
    typography: "{typography.body-sm}"
    padding: "4px 9px"
  button-primary:
    backgroundColor: "{colors.phosphor}"
    textColor: "{colors.screen}"
    rounded: "{rounded.none}"
    padding: "7px 12px"
  button-primary-hover:
    backgroundColor: "{colors.phosphor-hot}"
  tab:
    backgroundColor: "transparent"
    textColor: "{colors.phosphor-3}"
    typography: "{typography.tab}"
    rounded: "{rounded.none}"
    padding: "5px 12px 4px"
  tab-active:
    backgroundColor: "{colors.screen}"
    textColor: "{colors.phosphor}"
  tab-new-rule:
    backgroundColor: "{colors.phosphor}"
    textColor: "{colors.screen}"
    padding: "5px 12px 4px"
  chip:
    backgroundColor: "transparent"
    textColor: "{colors.phosphor-2}"
    rounded: "{rounded.none}"
    padding: "3px 8px"
  chip-selected:
    backgroundColor: "{colors.phosphor}"
    textColor: "{colors.screen}"
  tag:
    backgroundColor: "transparent"
    textColor: "{colors.phosphor-2}"
    rounded: "{rounded.none}"
    padding: "3px 6px"
  tag-inverse:
    backgroundColor: "{colors.phosphor}"
    textColor: "{colors.screen}"
    rounded: "{rounded.none}"
    padding: "3px 6px"
  input:
    backgroundColor: "{colors.field}"
    textColor: "{colors.phosphor}"
    rounded: "{rounded.none}"
    padding: "7px 8px"
  input-sentence:
    backgroundColor: "{colors.field}"
    textColor: "{colors.phosphor}"
    rounded: "{rounded.none}"
    padding: "10px 12px"
  journal-row:
    backgroundColor: "transparent"
    textColor: "{colors.phosphor}"
    padding: "6px 14px"
    height: "50px"
  journal-row-hover:
    backgroundColor: "{colors.phosphor-6}"
  journal-row-selected:
    backgroundColor: "{colors.phosphor-5}"
  value-highlight:
    backgroundColor: "{colors.phosphor}"
    textColor: "{colors.screen}"
    padding: "0 5px"
  toast:
    backgroundColor: "{colors.phosphor}"
    textColor: "{colors.screen}"
    rounded: "{rounded.none}"
    padding: "9px 14px"
---

# Design System: Onchain Rule Triggers (working name)

## Overview

**Creative North Star: "The Phosphor Ladder"**

Every rule is drawn as a ladder-logic circuit on a green-phosphor CRT, and the trace is the product. The screen sits inside a rugged olive-grey monitor bezel; everything on the glass is one hue of phosphor at six intensities, plus the near-black of the tube. There is no second accent colour. State is carried by brightness, by line form (solid glowing stroke versus dim dotted trace, dashed for unknown) and by inversion (phosphor block with screen-black text), which is how a monochrome terminal has always shown emphasis.

Density is operator-grade: a match journal on the left, the energised circuit of the selected match on the right, and an inspector that explains any gate in plain words with the exact value that powered it. Chrome and data share one monospace face (Share Tech Mono); VT323, a bitmap-terminal face, is kept for headings, tab labels, the wordmark and big numerals. When a match is opened, power visibly travels the wire from the left rail at a fixed speed, lights each gate it passes, flashes the value that satisfied the gate, and bursts the coil.

The user's vocabulary is part of the world: a rule is a **circuit**, a condition is a **gate** (numbered G1, G2… in drawing order), a match output is the **coil**, enabled is **armed**. Navigation is two terminal tabs, **JOURNAL** and **CIRCUITS**, plus the inverse **NEW CIRCUIT** tab. There is no Blocks page and there are no hardware buttons on the bezel. This one dark world has no light variant: the phosphor tokens override both `prefers-color-scheme` branches and both `data-theme` values.

**Key Characteristics:**
- One hue, six phosphor intensities on a near-black tube; no second accent.
- Emphasis by inversion: phosphor block, screen-black text, glow removed.
- Lit is solid and glowing; unlit is a dim dotted trace; unknown is dashed with a blinking `?`.
- Square everything on the glass (0 radius); only the bezel and tube are rounded.
- Scanlines, vignette and a faint text glow are applied once, to the tube, never per component.
- Power-on animation driven by wire position at 820 px/s, fully static under reduced motion.

## Colors

A single green-phosphor ramp lit on a black tube, framed by a matte olive housing.

### Primary
- **Full Phosphor** (`phosphor`): the lit state. Body text, values, lit wires, held contact bars, the energised coil, the active tab, and every inverse block (primary button, NEW CIRCUIT tab, selected chip, CARRIED POWER / ENERGISED / DECODED tags, value highlights, toast, text selection).
- **Hot Phosphor** (`phosphor-hot`): hover state of inverse controls only (primary button, NEW CIRCUIT tab).

### Secondary
- **Phosphor 2** (`phosphor-2`): secondary text. Chip and outline-tag text, header metadata, the inspector's small heading.
- **Phosphor 3** (`phosphor-3`): tertiary text and structural strokes. Labels, gate subject lines, idle tabs, status-line captions, unheld gate values, rails and contact bars at rest, the coil at rest, the tab-strip underline, button borders, the inspector's top border.
- **Phosphor 4** (`phosphor-4`): dim lines. Unlit dotted wires, pane borders, chip and tag outlines, input borders, the status-line top border, the dark-branch chain border. A line colour, not a text colour (see The Ink Floor Rule).
- **Phosphor 5** (`phosphor-5`): faint fills. Selected journal row, the armed-switch track, progress-bar track, soft row dividers.
- **Phosphor 6** (`phosphor-6`): hover fill for rows, buttons and chain rows.

### Neutral
- **Tube Black** (`screen`): the glass. Inverse-block text, sticky journal header, active-tab fill.
- **Tube Glow** (`screen-glow`) and **Tube Edge** (`screen-edge`): the tube's radial gradient, brightest just above centre (50% 45%), falling to `screen` at 70% and `screen-edge` at the corners.
- **Field Green** (`field`): input and select wells.
- **Inspector Top** (`inspector-top`): the inspector's top-to-screen gradient start.
- **Housing** (`housing`): the page behind the monitor (radial `#2f3327` to `housing` to `#1b1d16`).
- **Bezel Top / Bezel Bottom** (`bezel-top`, `bezel-bottom`): the bezel's vertical gradient.

### Named Rules
**The One Hue Rule.** Everything on the glass is a step of the phosphor ramp or tube black. State is intensity, line form and inversion, never a new hue. No amber, no red.

**The Inversion Rule.** The strongest emphasis is a solid phosphor block with tube-black text and `text-shadow: none`. It is reserved for the primary action, the NEW CIRCUIT tab, selected filter chips, power-state tags, the value that powered a gate, and the toast.

**The Ink Floor Rule.** Text on the glass is `phosphor-3` or brighter. `phosphor-4` and dimmer are for lines, borders and fills.

## Typography

**Display Font:** VT323 (with Share Tech Mono, ui-monospace fallback)
**Body Font:** Share Tech Mono (with ui-monospace, Menlo, Consolas)
**Label/Mono Font:** Share Tech Mono; hashes, addresses and amounts use the same face as the chrome.

**Character:** VT323 is a bitmap CRT face, used large and sparse so its pixels read as the terminal's own lettering. Share Tech Mono is a clean technical monospace that holds hashes, amounts and labels at small sizes. Both faces ship only weight 400; hierarchy comes from face, size, case and phosphor intensity, never weight.

### Hierarchy
- **Display** (VT323 400, 30px, 1, 0.04em): the wordmark "ONCHAIN RULE TRIGGERS" with its outlined WORKING NAME badge (Share Tech Mono 13px, `phosphor-3` on a `phosphor-4` outline).
- **Numeral** (VT323 400, 34px): the transfer amount in the trace, the backtest count, the match count on a circuit row.
- **Headline** (VT323 400, 26px, 1.05, 0.03em): the circuit name above a trace and on circuit rows; the inspector title at 26px/1.1, 0.02em.
- **Title** (VT323 400, 24px, 1, 0.05em, uppercase): pane headings (MATCH JOURNAL, TRACE, CIRCUITS, BACKTEST).
- **Tab** (VT323 400, 22px, 0.06em): JOURNAL and CIRCUITS tabs with a Share Tech Mono 13px count; NEW CIRCUIT at 20px. The coil chain's end row, "COIL BNB-OUT · MATCH", also uses VT323 at 22px.
- **Body** (Share Tech Mono 400, 15px, 1.45, 0.01em): default text. Inspector chain rows 14px/1.3; step lists 13.5px; status line, header metadata, kv terms and notes 13px.
- **Label** (Share Tech Mono 400, 12px, 0.06em, uppercase, `phosphor-3`): column heads, section heads, form labels, pane sub-labels.
- **Diagram text**: gate subject 12px uppercase 0.04em `phosphor-3` (12.5px in the trace); gate test 14px `phosphor` (14.5px in the trace); gate value 12px (12.5px in the trace); coil labels 13px; gate number 10px; continuation-marker letters 12px.

### Named Rules
**The Bitmap Headline Rule.** VT323 is for headings, tabs, the wordmark and big numerals only. Values, addresses, hashes, labels and running text are Share Tech Mono.

**The No-Weight Rule.** Never set a weight other than 400. Emphasis is size, case, intensity or inversion.

## Layout

**The monitor.** The page is a housing (16px padding) holding one bezel (14px padding) holding one tube. The tube is a four-row grid: header, tab strip, main (fills and scrolls), status line. At desktop the monitor fills the viewport height and panes scroll inside it.

- **Header:** wordmark left; WINDOW and ENGINE readouts right (13px, captions `phosphor-2`, values `phosphor`). Padding 12px 18px 4px.
- **Tab strip:** tabs sit on a 1px `phosphor-3` underline; the active tab takes a `phosphor-3` outline on three sides, a tube-black fill and overlaps the underline by 1px. A spacer pushes the inverse NEW CIRCUIT tab to the right end. Padding 6px 18px 0, 4px gap.
- **Main:** padding 12px 18px. The Journal view is a two-pane split at 5fr / 7fr (journal / trace), 12px gap.
- **Status line:** 1px `phosphor-4` top border; SHEET, SEL and the keyboard hint `J/K NEXT MATCH · [ ] CIRCUIT · R REPLAY`, ending in a blinking block cursor (█, 1.1s steps). 13px, 22px gaps, padding 6px 18px 10px.
- **Panes:** transparent over the glass, 1px `phosphor-4` border, header row padded 10px 14px.
- **Journal grid:** each row is three lines on a `1fr / auto / 16px` grid. Line one has the circuit identity (glyph + tag) on the left, the amount + unit right-aligned, and the abnormal flag. Line two, full width at 12px `phosphor-2`, is from → to. Line three, full width at 11.5px `phosphor-3`, is `16:22:23 UTC · block 18,000,004 · tx 39`. Lines two and three cut off with an ellipsis. An unscaled amount longer than 9 digits shows as `4.332e10 raw` in the row, with every digit kept for the trace. The column header (CIRCUIT / AMOUNT) is sticky and tube-black.
- **Console grid (861px and up):** three panes, channels / journal / trace, at `220px / minmax(330px, 4fr) / 7.5fr`. From 861 to 1400px it's `188px / minmax(280px, 1fr) / 1.8fr`, and channel names drop to the tag alone.
- **Circuits page:** one row per circuit: 56px zero-padded number column (001), a fluid body (name + code tag, the quoted sentence, the drawn circuit at a 640px minimum), and a 190px side column (match count as a VT323 cross-reference link, the Armed/Off switch, Edit). Circuits that are off draw at 45% opacity.
- **Composer:** fluid main pane plus 320px backtest aside, collapsing to one column at 1100px or below.

**Rhythm.** Observed steps are 4, 8, 10, 12, 14 and 18px; 14px is the in-pane gutter and 18px the tube gutter.

### Responsive
- **861px and up:** full-height monitor with the three-pane console (see Console grid). The inspector sits under the diagram and pins to the bottom of the trace pane once a gate or the coil is clicked.
- **860px and below:** the monitor grows with the content (height auto); bezel padding 10px, bezel radius 16px; header padding 10px 12px 2px; wordmark 24px and may wrap; tabs 19px and scroll horizontally, NEW CIRCUIT 16px; main padding 10px. The channels, journal and trace become one column: channels first (the list capped at 188px, hidden while a trace is open), then the journal, opening a match swaps to the trace with a "‹ JOURNAL" back button. The status-line hint is hidden. The inspector is static and full height with a single-column key/value list. The journal list caps at 58vh.
- **520px and below:** the header readouts hide; the journal column header hides and rows keep their three lines; NEW CIRCUIT shortens to NEW; gates use the compact minimum width (104px) and the coil the compact width (72px); the trace's enlarged contact bars fall back to normal.

## Elevation & Depth

Depth belongs to the hardware, not to components. The bezel is a physical object with a lit top edge and a cast shadow; the tube is recessed with an inner shadow and an inner phosphor bloom; on the glass everything is flat, separated by phosphor lines. The one exception is the inspector, which floats over the scrolling trace like a raised sheet.

### Shadow Vocabulary
- **Bezel** (`box-shadow: inset 0 2px 0 rgba(255,255,255,.07), inset 0 -2px 0 rgba(0,0,0,.4), 0 10px 30px rgba(0,0,0,.45)`): the monitor frame only.
- **Tube recess** (`box-shadow: inset 0 0 0 2px #0b0d08, inset 0 0 60px rgba(0,0,0,.85), inset 0 0 12px rgba(93,255,143,.12)`): the glass only.
- **Phosphor text glow** (`text-shadow: 0 0 6px rgba(93,255,143,.35)`): set once on the tube and inherited; inverse blocks set `text-shadow: none`.
- **Lit-line glow** (`filter: drop-shadow(0 0 3px rgba(93,255,143,.8))`): lit wires and held contact bars; rails 0.7 alpha; the energised coil `drop-shadow(0 0 4px rgba(93,255,143,.9))`.
- **Inspector lift** (`box-shadow: 0 -14px 28px rgba(0,0,0,.65)`): the sticky inspector at desktop only.

### Tube overlays
Two overlays cover the whole tube, ignore pointer events and follow its radius: scanlines (`repeating-linear-gradient(180deg, transparent 0 2px, rgba(0,0,0,.22) 2px 3px)`, multiply) and a vignette (`radial-gradient(120% 90% at 50% 40%, transparent 60%, rgba(0,0,0,.45) 100%)`) that sits above the content.

### Named Rules
**The Glass Is Flat Rule.** Nothing on the glass casts a shadow except the inspector sheet. Glow means power, never elevation.

## Shapes

On the glass, every corner is square (0): buttons, tabs, chips, tags, inputs, switch track and thumb, toast, value highlights. The only rounded forms are hardware: the bezel (22px, 16px on small screens) and the tube (14px). Inside the diagram, gate and coil hit and selection boxes use a 2px radius. Borders are 1px. The switch is square: a 34×18 track with a 12px thumb that slides 16px.

## Components

### Buttons
Outline terminal keys; inversion marks the one primary.
- **Shape:** square (0).
- **Default:** transparent, 1px `phosphor-3` border, `phosphor` text, uppercase, 0.06em, padding 7px 12px. Small: 13px, padding 4px 9px. Icons are 16px inline SVG line icons (1.5 stroke, currentColor).
- **Hover:** fill `phosphor-6`.
- **Primary:** `phosphor` block, tube-black text, no glow; hover `phosphor-hot`.
- **Disabled:** 45% opacity, not-allowed cursor.
- **Focus:** 2px solid `phosphor` outline, 2px offset (global).

### Circuit identity (tag + glyph)
Every circuit is known by a **tag** (up to 12 characters of A–Z, 0–9 and hyphens, unique per owner: `BNB-OUT`, `STABLE-2K`, `PEPE-1B`) and a **glyph**, one of 12 stroke-drawn symbols on a 16px grid: triangle, diamond, target, square, star, bars, chevron, bolt, hexagon, circle, xmark and ring (1.5 stroke, round joins, no fill), like the channel markers on a scope. The glyph makes a row scannable at a glance and the tag makes it readable. They always appear together as the **ident**: glyph 14px + tag in Share Tech Mono, 6px gap, `phosphor`. The large ident (16px glyph, 15px text, 1px `phosphor-4` outline) heads the trace and each circuit row. On the circuits sheet a 30px glyph with a soft glow replaces row numbers. Glyphs are never the only carrier of meaning; the tag is always next to them.

### Channels + command line
The journal's left pane, titled "CHANNELS" with the hint "/ to search".
- **Command line:** a `>` prompt in VT323 22px, then a borderless input with a dashed `phosphor-4` underline (solid `phosphor` on focus), uppercase, placeholder "TUNE BNB". Typing filters the channel list live by tag, name and sentence (an optional leading "ch", "channel" or "tune" is ignored), and the top result gets a 1px `phosphor` inset ring. Enter tunes the journal to the top result ("all" tunes to ALL), Esc clears it, and `/` focuses it from anywhere.
- **Channel row:** 22px glyph column / tag (14.5px `phosphor`) with the circuit name under it (12px `phosphor-3`, ellipsis) / match count. ALL (ring glyph, "Every armed circuit") comes first, then armed circuits, then disarmed ones dimmed with the count shown as OFF. The selected channel is an inverse block. `[` and `]` step through channels.

### Tabs (navigation)
- JOURNAL and CIRCUITS in VT323 22px, `phosphor-3`, with a small count; hover goes to `phosphor`. The composer keeps CIRCUITS marked current.
- **Active:** `phosphor` text, `phosphor-3` outline on three sides, tube-black fill, overlaps the underline.
- **NEW CIRCUIT:** an inverse tab (phosphor block, tube-black text, 20px) at the far right; hover `phosphor-hot`.

### Chips
- **Style:** transparent, 1px `phosphor-4` outline, `phosphor-2` text, 12.5px, padding 3px 8px, count in 11px at 80% opacity.
- **Selected (aria-pressed):** inverse block, glow removed. Used for example sentences and "also energised by" jumps (which show the ident + name).

### Tags
- **Outline:** `phosphor-4` outline, `phosphor-2` text, 11.5px uppercase 0.04em, padding 3px 6px. Address labels, BLOCKED, CALLDATA · UNVERIFIED.
- **Inverse:** CARRIED POWER, ENERGISED, DECODED.
- **No data / unknown:** NO DATA, Unrecognised and every other abnormal tag is an outline, never a block: transparent fill, 1px **dashed** `phosphor` border, `phosphor` text. CARRIED POWER / ENERGISED / DECODED stay solid inverse blocks. Abnormal notes use `phosphor` text with a 1px dashed `phosphor-3` underline on the message, not an inverse run. The dashed line is the abnormal signature across the whole world: dashed gate bars, the dashed legend swatch, dashed tags.

### Composer identity fields
Beside the backtest, above the circuit name: a **Tag** input (uppercase, spaces become hyphens, 12 max) with a live error line under it (dashed underline) for a duplicate or malformed tag. Below that, a **Glyph** picker: a 6 × 2 grid of 34px square buttons. The selected one is inverse, and glyphs used by other circuits are dimmed with a dotted border but still selectable. The tag and glyph are suggested from the parsed sentence (for example USDT + USDC → STABLE, a from-watchlist gate → BNB-…-OUT, the amount compacted to 2K / 1M / 1B, trimmed to 12 characters) until the user edits the tag. The draft coil shows the tag and glyph live.

**Composer side panel layout.** The pane has three parts: its header, a scrolling body (backtest count, then backtest rows capped at 216px with their own scroll, then Tag, Glyph and Circuit name), and a **save row** (SAVE AND ARM / SAVE CHANGES + CANCEL) that is outside the scroll and always visible at the bottom of the pane. It has a `phosphor-3` top border, a tube-black fill and a soft upward shadow. Below 1100px, where the composer stacks into one column, the save row is `position: sticky; bottom: 0` so it stays on screen while you scroll. The save action never sits below the tube's edge at any height.

### Inputs / Fields
- **Style:** `field` well, 1px `phosphor-4` border, square, `phosphor` text and caret. The sentence input is 20px with padding 10px 12px; editor selects and inputs are 14.5px with padding 7px 8px.
- **Focus:** the global 2px `phosphor` outline.

### Journal row
- Transparent with a `phosphor-5` divider; hover `phosphor-6`; selected `phosphor-5` fill plus a 1px inset `phosphor-3` ring. The amount is `phosphor`, its unit and the from → to line dimmer. A small warning-triangle SVG in the flag column marks an unknown token or unknown decimals. Arrow keys move the selection; J/K from anywhere. Each row leads with the circuit's ident, never a number.

### Armed switch
- Square track, `phosphor-4` outline at rest, thumb `phosphor-3`. Armed: `phosphor` border, `phosphor-5` track, `phosphor` thumb moved 16px (0.15s ease-out). Labelled "Armed" / "Off".

### Toast
- Inverse block, square, fixed bottom-centre, 3.8s.

### Circuit diagram (signature component)
An SVG ladder diagram drawn from the condition tree. All text is Share Tech Mono; all strokes are unfilled.

**Rails.** A left power rail at x=4 and a right return rail at W−4, full height, stroke 3. The left rail is lit whenever a trace is shown; the right rail is never lit. A 22px lead wire joins the left rail to the first gate; a 16px tail follows the coil. The short wire from the coil to the right rail is always unlit.

**Wires.** Unlit: `phosphor-4`, 1.5 stroke, dotted (`2 4`). Lit: `phosphor`, solid, 3.5 stroke (4.5 in the trace view), with lit-line glow. Series (AND) gates sit end to end on one wire line.

**Gates.** Each gate is a cell 86px tall with the wire at y=50. Width is the largest of: the minimum (128px, 104px at 520px and below), the subject length × 7.1 + 26, the test (operator + value) length × 7.9 + 26, and (value length + 2) × 7.0 + 26. Top to bottom, centred: the subject (for example TRANSFER TOKEN) at y=17; the test (for example "is USDT", "≥ 250") at y=33; the contact on the wire, drawn as two vertical bars at centre ±6px (half-length 11px, 16px in the enlarged trace; stroke 2.5, 3 held, 3.5 held in the trace); the value this transaction had at y=78.
- **Gate number:** "G1", "G2"… in drawing order (depth-first, left to right, top branch first) at 10px, x+4, y=45, sitting just above the incoming wire at the left of the cell. It is text, so it's `phosphor-3` at rest (The Ink Floor Rule) and `phosphor-2` when its gate held, at 10.5px. The inspector, the coil chain and the aria labels use the same number.
- **Held** (condition true): the bars turn `phosphor` with glow, a 10×3.5 `phosphor` block fills the gap between them, and the value turns `phosphor`.
- **Open** (condition false): bars `phosphor-4`, value `phosphor-3`.
- **No data** (condition can't be tested, for example unknown decimals): bars `phosphor` dashed (`3 3`), a `?` centred between the bars, and the value in `phosphor`. The `?` blinks (1.1s steps) unless reduced motion is on.
- **The value that powered it:** when power arrived and passed through, the value sits in an inverse block (phosphor box, 17px tall, text width + 10px, top at y=66) with tube-black text. Held gates that power never reached get no box.

**OR buses.** Parallel branches stack vertically with a 10px gap, inset 20px on each side between two vertical buses. The left bus is lit its whole height if power reached it. The right bus is drawn unlit, then lit only from the top branch down to the lowest live branch, so power visibly drops back to the main line. Each branch's output stub is lit only if that branch passed power.

**Fold continuation markers.** A circuit wider than its box folds at top-level AND boundaries only, like a multi-line ladder drawing. The broken line ends in a lettered arrow-tag marker (18px tall, 30px long including a 9px point) near the right edge; the next line starts with the same letter at the left (A, B…), 14px below. If the last line can't also hold the coil, its last gate moves to a line of its own. Markers light (stroke 2.5, text `phosphor`) when power reaches them. A drawing up to 30% too wide is scaled down to fit; beyond that the box scrolls horizontally.

**The coil.** Two facing arcs (radius 15, centre ±8px) around a 14px disc, within a 116px slot (72px compact). The circuit's glyph sits inside the disc (13px, `phosphor-3` stroke, tube-black once energised). The circuit tag is above (y−22) and MATCH or NO MATCH below (y+32), 13px. Energised: `phosphor` arcs with the stronger glow, the disc filled at 28% phosphor, labels `phosphor`. At rest: `phosphor-3`, empty disc.

**Selection and hover.** Gates and the coil are focusable buttons in the trace (to inspect) and in the composer (to edit). Hover washes the cell in 6% phosphor; keyboard focus draws a 1.5px `phosphor` outline on the cell; the selected gate or coil gets a 1.5px `phosphor` dashed box (`4 3`, 2px radius). Enter and Space activate.

**Legend** (under the trace): an 18×3 lit bar for "Carried power", a 2px dotted `phosphor-4` line for "Blocked", a 2px dashed `phosphor` line for "No data", then the hint "Select a gate or the coil to inspect it".

### Power-on animation
Plays when a match is opened: on first load, on selecting a journal row, with J/K, on an "also energised" jump, and with Replay (button or R). Resizing or other re-renders don't replay it.
- **Travel:** power moves left to right at a constant **820 px/s** and **holds 0.6s on every live gate**. An element's delay is its x-position ÷ 820 (plus, on folded circuits, the width of every earlier line), **plus 0.6s for each live gate that sits before it**. A dry drawing pass finds the live gates' positions first. Parallel branches at the same x light together, and each still adds its own hold.
- **The hold:** while power waits on a live gate, a 1.5px `phosphor` frame fills the gate cell with a 14% phosphor wash and a 6px glow, then fades out over the 0.6s (keyframes `hold`: 0 → full at 25% → 0). The powering value flares in the same window, so the eye lands on exactly what let power through before it moves on. The inspector's power chain lights row by row in step with the gates (0.35s fade-in from 18% opacity, each row on its gate's delay; the COIL row on the coil's delay).
- **Wire segments:** each lit segment draws on (dash offset from 1 to 0 on a normalised path length) over its own length ÷ 820 s (minimum 0.04s), linear, starting at its delay. A dotted unlit copy lies beneath it so the dim trace is visible before power arrives. Vertical bus segments start at their x and take their length.
- **Gate lighting:** when power reaches a gate's centre, its bars go from `phosphor-4` to `phosphor` (0.22s ease-out) and the fill block fades in (0.22s).
- **Value flash:** at the same moment the value box fades in and flares (0.6s ease-out: invisible, full brightness with an 8px glow at 30%, then settles). The value text changes to tube-black in a single step at the end.
- **Continuation markers:** light like gates (0.22s) when reached.
- **Coil burst:** when power reaches the coil's input, the arcs light (0.22s), the labels fade in (0.3s), and the disc bursts (0.9s ease-out: scale 0.4 and invisible, scale 1.5 with a 12px glow at 35%, then scale 1).
- **Reduced motion:** every animation above is off; the circuit is drawn in its final lit state straight away. The `?` and the status-line cursor don't blink. Nothing else changes.

### Inspector
A panel under the trace that explains the coil or one gate.
- **Container:** 1px `phosphor-3` top border, sitting under the diagram so it never covers the animation. Clicking a gate, the coil or a chain row **pins** it: gradient from `inspector-top` to `screen` at 40%, sticky at the bottom of the trace pane (max 42% of its height, scrolls inside), with the inspector lift shadow and a square close (×) button in the heading that unpins it. Opening another match unpins it. At 860px and below it's always static. Announces changes politely.
- **Heading:** 13px `phosphor-2` ("COIL ⚡ BNB-OUT", with the glyph and tag, or "GATE G3") followed by a state tag. Title in VT323 26px.
- **Coil view (default when a match opens):** tag ENERGISED; title "Power reached the coil through N gates"; then the power chain, a list with a 2px solid `phosphor` left border (8px inset). Each row is a button (38px number column / condition / value), 14px/1.3, padding 7px 10px: number `phosphor-3`, condition `phosphor`, value in an inverse block (padding 0 5px). Hover `phosphor-6`. The chain ends with "COIL BNB-OUT · MATCH" in VT323 22px. Under the label "Branches that stayed dark" come the gates that power reached but that blocked it: a 2px dotted `phosphor-4` border, text `phosphor-3`, values in a dotted `phosphor-4` outline with no fill. Clicking any row inspects that gate.
- **Gate view:** tag CARRIED POWER (inverse), NO DATA, or BLOCKED (outline); the title is the full condition; then a key/value list (96px term column, terms 13px `phosphor-3`): Reads (the source field), This tx (numbered derivation steps, 13.5px; step numbers follow The Ink Floor Rule), Test, Power (whether power arrived, passed, stopped, or never came), Wiring (position in series or parallel and how many siblings held). An abnormal note follows when relevant: `phosphor` text with a dashed underline, next to a warning icon.

## Do's and Don'ts

### Do:
- **Do** keep every glass colour on the phosphor ramp (`phosphor` through `phosphor-6`) or tube black, and express state as intensity, line form (solid, dotted `2 4`, dashed `3 3`) or inversion.
- **Do** remove the text glow (`text-shadow: none`) on every inverse block.
- **Do** draw lit wires solid at 3.5px (4.5px in the trace) with the 3px phosphor glow, and unlit wires dotted at 1.5px in `phosphor-4`.
- **Do** number gates G1, G2… in drawing order and use the same number in the diagram, the inspector, the coil chain and the accessible name.
- **Do** box the value only on gates that power actually passed through.
- **Do** drive the power-on animation from x-position at 820 px/s and render the final state directly under `prefers-reduced-motion: reduce`.
- **Do** fold wide circuits only at top-level AND boundaries, with matching lettered continuation markers.
- **Do** apply scanlines, vignette and phosphor glow once, at the tube, over the whole screen.
- **Do** use VT323 only for the wordmark, tabs, pane titles, circuit names, inspector titles and big numerals; use Share Tech Mono at 400 for everything else.

### Don't:
- **Don't** add a second hue for warnings or errors; this world has none.
- **Don't** add a light theme or honour `data-theme="light"`; the phosphor tokens override both schemes.
- **Don't** round anything on the glass; radius belongs to the bezel (22px) and the tube (14px) only.
- **Don't** put shadows on panes, cards or buttons; the inspector sheet is the only raised thing on the glass.
- **Don't** set text in `phosphor-4` or dimmer; those steps are for lines and fills.
- **Don't** use VT323 for values, hashes, addresses or running text.
- **Don't** add hardware buttons or controls to the bezel, or a Blocks tab.
