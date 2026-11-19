# ADR-0005: GoogleUX Design System Adaptation & Scope Honesty Boundaries

- **Status:** Accepted (2026-11-18)
- **Context:** The original GoogleUX Figma prototype included street-level C5 flood closures, live Doppler radar tiles, and automated WhatsApp alerts — none of which are backed by free public APIs.
- **Decision:** Faithfully implement the WCAG 2.1 AAA visual design system (`#0B2545` Deep Navy, `#FFFBEB`/`#D97706` Amber P0 card, Inter typography, `48×48px` touch targets, semantic HTML landmarks, Web Speech API audio summary, responsive 1440px/390px layouts, researcher `CSV`/`Parquet` export receipt), while grounding all alerts strictly in verifiable meteorological forecast thresholds and surfacing explicit scope-boundary notices in both API responses (`scope_note`) and UI footer.
- **Consequences:** High-craft accessible UI paired with strict engineering honesty — zero fabricated sensors or misleading emergency claims.
