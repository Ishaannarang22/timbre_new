---
version: alpha
name: Editorial Warm
description: >
  A postpartum-care console that reads like a thoughtful letter — serif
  headlines on cream, terracotta for action, sage and amber for clinical signal.
colors:
  background: "#F7F1E6"
  surface: "#FFFFFF"
  ink: "#1F1B16"
  ink-muted: "#6B6258"
  muted-bg: "#EFE7D7"
  border: "#E4D9C5"
  primary: "#B8593E"
  primary-hover: "#A14C32"
  primary-foreground: "#FFF6EC"
  sage: "#6F8B6E"
  amber: "#C68A2E"
  crimson: "#B5402D"
  on-accent: "#FFF6EC"
typography:
  display:
    fontFamily: Fraunces
    fontSize: 36px
    fontWeight: 500
    lineHeight: 1.1
    letterSpacing: "-0.02em"
    fontVariation: "'SOFT' 50, 'opsz' 96"
  headline-lg:
    fontFamily: Fraunces
    fontSize: 24px
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: "-0.015em"
    fontVariation: "'SOFT' 50, 'opsz' 48"
  headline-md:
    fontFamily: Fraunces
    fontSize: 18px
    fontWeight: 500
    lineHeight: 1.25
    letterSpacing: "-0.01em"
    fontVariation: "'SOFT' 50, 'opsz' 36"
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.55
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.55
  body-sm:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.5
  label-md:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: 500
    lineHeight: 1.3
  label-caps:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "0.08em"
  mono-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.4
rounded:
  none: 0px
  sm: 6px
  md: 10px
  lg: 14px
  full: 9999px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 40px
  gutter: 24px
  page: 32px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.md}"
    padding: "10px 18px"
    typography: "{typography.label-md}"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
  button-ghost-hover:
    backgroundColor: "{colors.muted-bg}"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "20px"
  chip-default:
    backgroundColor: "{colors.muted-bg}"
    textColor: "{colors.ink}"
    rounded: "{rounded.full}"
    padding: "2px 10px"
    typography: "{typography.label-caps}"
  chip-success:
    backgroundColor: "#E6EDE5"
    textColor: "#3F5A3E"
  chip-warning:
    backgroundColor: "#F7E8CC"
    textColor: "#7A5316"
  chip-critical:
    backgroundColor: "#F4D9D2"
    textColor: "#7A2A1A"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "10px 14px"
    typography: "{typography.body-md}"
  sidebar-item:
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.sm}"
    padding: "8px 12px"
    typography: "{typography.body-md}"
  sidebar-item-active:
    backgroundColor: "{colors.muted-bg}"
    textColor: "{colors.ink}"
---

# Editorial Warm — Postpartum Care Console

## Overview

timbre is a voice agent that calls people during their postpartum recovery. The
console is the clinician's view of those conversations: queues, transcripts,
escalations, and patient histories. The audience is doulas, nurses, and care
coordinators — people who read carefully and care about tone.

The design is **editorial warm**: it should feel closer to a well-set printed
chart or a thoughtful patient letter than to a hospital EMR. Serif headlines
slow the eye down at decision points. A cream surface replaces clinical white.
Terracotta is the single voice of action — never decoration. Sage and amber
carry clinical signal without alarming the reader.

The product tone is **calm, attentive, and respectful of attention**. Density
is allowed where information is real (a patient summary, a transcript), but
spacing opens up around headers and primary actions to give the operator
breathing room.

## Colors

The palette is rooted in warm neutrals, with one accent for action and a small
clinical signal palette for status.

- **Background — Cream Limestone (#F7F1E6):** The page surface. A warm
  off-white that reads as paper rather than as a screen.
- **Surface — Paper White (#FFFFFF):** Card and dialog surfaces sit one tonal
  layer above the cream background. We use elevation tonally, not via shadow.
- **Ink (#1F1B16):** A near-black with a touch of warmth for headlines and core
  body text. Reserved for content the operator must read.
- **Ink Muted — Slate (#6B6258):** Secondary text — captions, metadata,
  timestamps, sidebar items at rest.
- **Muted BG — Sand (#EFE7D7):** A subtle wash for hovered rows, active
  sidebar items, default chips, and quiet group containers.
- **Border — Wheat (#E4D9C5):** All dividers and card borders. Low-contrast on
  purpose — structure should be felt, not seen.
- **Primary — Terracotta (#B8593E):** The sole driver of action. Used for the
  one most-important button per screen, the active state of the wordmark, and
  critical inline links.
- **Sage (#6F8B6E):** Positive clinical state — call completed cleanly, vitals
  in range, recovery on track.
- **Amber (#C68A2E):** Watchful state — flagged but not urgent. PHQ rising,
  follow-up due, doula not yet assigned.
- **Crimson (#B5402D):** Escalations and destructive actions. Distinct enough
  from terracotta that the eye can tell apart "act on this" from "this is
  urgent."

## Typography

Two families do all the work, with a third for technical data.

- **Fraunces** (variable serif) for all headlines and the wordmark. Set with a
  soft `SOFT` axis (~50) and optical sizing — slightly editorial, never
  decorative. Weight 500, never bolder, never italic in UI.
- **Inter** for all body, labels, table cells, and form controls. Weight 400
  for body, 500 for labels, 600 only for uppercase micro-labels.
- **JetBrains Mono** for technical data — call IDs, node names in the dialog
  graph, latency numbers, code in error messages.

Headlines step down in size, not weight: `display` (36px) is used once per
screen at most; `headline-lg` (24px) sits at the top of major sections;
`headline-md` (18px) labels card content. Body text holds at 14px to keep
data-dense screens legible without resorting to 12px.

`label-caps` is reserved for very small UI signals — chips, table column
headers, the "demo data only" footnote. Uppercase, generous tracking, never
inside running prose.

## Layout

The console uses a fixed two-column layout: a 256px sidebar on the left and a
fluid content area on the right, capped at 1280px on wide screens. Mobile drops
the sidebar behind a sheet.

Spacing follows an **8px rhythm** with a 4px half-step. The named scale is
`xs` 4, `sm` 8, `md` 16, `lg` 24, `xl` 40. Page padding is 32px (`page`).
Cards use 20px internal padding to keep dense rows feeling spacious without
becoming airy.

Lists are stacked with 12px gaps. Tables breathe at 12px row padding minimum.
Forms group fields in 16px vertical stacks with 24px between groups.

## Elevation & Depth

The console is **flat**. There are no drop shadows on resting surfaces. Depth
is conveyed through three tools, in this order of preference:

1. **Tonal layers** — cream background, paper-white card. The contrast carries
   the layer.
2. **A 1px Wheat border** when a tonal step alone isn't enough — typically on
   inputs and quiet containers placed directly on the card surface.
3. **A very soft shadow** (`0 1px 2px rgba(31, 27, 22, 0.04)`) reserved for
   floating elements only: popovers, dropdowns, toasts.

Hover and active states use a tonal shift (move up or down one layer), not a
shadow change.

## Shapes

Corners are softly rounded, never sharp and never fully round except for
status dots and avatars.

- **`sm` 6px:** chips' inner corners, small inputs, sidebar items.
- **`md` 10px:** the default — buttons, cards, inputs, dialogs.
- **`lg` 14px:** large feature cards (patient summary, hero metrics).
- **`full`:** avatars, status dots, and pill-shaped status chips only.

Mixing radii within a single composition is allowed only when the container is
`lg` and the children inside are `md` or `sm` — never the other way around.

## Components

### Buttons

Three variants. Only one *primary* per screen.

- **Primary:** Terracotta fill, Paper-White text, 10px radius, 18px horizontal
  padding. Hover deepens to `#A14C32`. No shadow, ever.
- **Outline:** 1px Wheat border, transparent fill, Ink text. Hover fills with
  Muted BG.
- **Ghost:** Transparent at rest, Muted-BG on hover. Used for low-weight
  actions inside cards and table rows.

Buttons use Inter 500 at 14px. They do not gain icons unless the icon
disambiguates the label (e.g. "Call now" with a phone glyph).

### Chips & Badges

Pills (radius `full`) with `label-caps` typography. Five tones:

- **Default** — Muted BG on Ink. Neutral metadata (language, birth type).
- **Success** — soft sage on dark sage. Completed calls, healthy state.
- **Warning** — soft amber on dark amber. Watchful state, due-soon flags.
- **Critical** — soft crimson on dark crimson. Escalations, missed calls.
- **Outline** — transparent fill, Wheat border, Ink text. Counts and inert tags.

### Cards

Paper White on Cream. 1px Wheat border. 10px radius. 20px internal padding. No
shadow at rest. Hover-able cards (queue rows, escalation list items) shift to
Muted BG on hover — never re-color, never add a border.

### Sidebar items

Stack of items with 6px radius and 8/12 padding. Resting state: Ink Muted text.
Active state: Muted BG fill, Ink text. Hover state: half-opacity Muted BG. The
active marker is the fill, never a left rail or underline.

### Inputs

Paper White fill, 1px Wheat border, 10px radius, 10/14 padding, Inter 14px.
Focus replaces the border with Terracotta and adds a 3px Terracotta-at-15%
ring. Error replaces the border with Crimson; the helper line below the field
carries the human-readable message in Crimson.

### Empty states

A dashed 1px Wheat box, 40px padding, content centered. Title in Inter 500 at
14px; description in Ink Muted at 13px. No illustration — the spareness *is*
the empty state.

## Do's and Don'ts

- **Do** reserve Terracotta for the single most important action per screen.
  If two things compete for primary, the design isn't done.
- **Don't** combine Terracotta and Crimson in the same row — the eye reads
  them as the same family and loses the distinction between *act* and *alert*.
- **Do** set all headlines in Fraunces. Body and UI chrome stay in Inter.
- **Don't** italicize, bold above 500, or use small-caps in Fraunces. The
  variable softness already carries the editorial tone.
- **Do** use tonal layers (Cream → Paper White → Muted BG) for hierarchy.
- **Don't** add drop shadows to resting surfaces. Shadows belong only to
  floating elements (popovers, toasts).
- **Do** maintain WCAG AA contrast: Ink on Cream is 16:1; Ink Muted on Cream
  is 4.7:1; Terracotta on Paper White is 4.6:1.
- **Don't** color status with hue alone — escalation rows must also carry the
  Crimson chip label "escalated" so the signal survives color-blindness.
- **Do** prefer the 14px body size; data density is a feature, not a bug.
- **Don't** drop below 13px for any text the operator is expected to read
  more than once.
