# WebUI Design System & Frontend Aesthetic Standards

This rule governs all frontend UI/UX development, styling, and component extensions for the Antigravity/CAGY WebUI.

---

## 1. Core Aesthetic & Philosophy
- **Workbench / IDE Grade**: The interface is an engineering workbench, not a consumer chat app. Prioritize information density, compact layout, sharp legibility, and discrete operational status over decorative empty space.
- **Strict Anti-AI Trope Policy**:
  - ❌ **No purple/indigo neon gradients** or floating glowing orbs.
  - ❌ **No oversized chat bubbles** with massive border-radii (`>12px`).
  - ❌ **No decorative sparkle icons** (`✨`) or playful emoji-driven headers.
  - ❌ **No hardcoded inline color hexes** (e.g. `#fff`, `#111`, `#4f46e5`).
  - ❌ **No external UI libraries** or un-tokenized CSS frameworks that bypass the built-in theme system.

---

## 2. Design Tokens & CSS Variable Contracts
All CSS must strictly reference existing `:root` design variables defined in `style.css`:

### Color Tokens
| Variable | Purpose (Light Theme / Dark Theme) |
| :--- | :--- |
| `var(--bg)` | Main background (Warm parchment `#FEFCF7` / Deep Navy `#0D0D1A`) |
| `var(--sidebar)` | Navigation & drawer background (`#FAF7F0` / `#141425`) |
| `var(--surface)` | Cards, tool blocks, elevated surfaces (`#F3EEE3` / `#1A1A2E`) |
| `var(--surface-subtle)` | Low-contrast sub-panels & table rows (`rgba(0,0,0,.025)` / `rgba(255,255,255,.03)`) |
| `var(--border)` | Primary structural borders (`#E0D8C8` / `#2A2A45`) |
| `var(--border-subtle)` | Faint separators and dividers (`rgba(0,0,0,.08)` / `rgba(255,255,255,.08)`) |
| `var(--accent)` | Primary brand accent (`#B8860B` Warm Gold / `#FFD700` Rich Gold) |
| `var(--accent-hover)` | Active/hover accent state (`#996F08` / `#FFC700`) |
| `var(--accent-bg)` | Subtle accent fill for chips/highlights (`rgba(184,134,11,0.08)`) |
| `var(--text)` | High-contrast body text (`#1A1610` / `#FFF8DC`) |
| `var(--muted)` | Secondary labels, hints, and timestamps (`#5C5344` / `#C0C0C0`) |
| `var(--blue)` | Info highlights, links, and secondary badges (`#0288A8` / `#4DD0E1`) |
| `var(--code-bg)` | Code blocks and terminal surfaces (`#F5F0E5` / `#1A1A2E`) |

### Geometry & Spacing
- **Radii**: Use `var(--radius-sm)` (4px), `var(--radius-md)` (8px), or `var(--radius-card)` (8px). Pills (`var(--radius-pill)`) are reserved strictly for compact status badges, telemetry counters, and toggle switches.
- **Spacing**: Use standard scales: `var(--space-1)` (4px), `var(--space-2)` (8px), `var(--space-3)` (12px), `var(--space-4)` (16px).

---

## 3. Typography Rules
- **UI Chrome & Navigation**: Use `var(--font-ui)` (`-apple-system`, `Inter`, `Segoe UI`, `system-ui`).
- **Telemetry, Code, Metadata, Metrics**: Use `var(--font-mono)` (`ui-monospace`, `"SF Mono"`, `Menlo`, `Consolas`).
- **Responsive Font Scaling**: All text and container sizing must respect the `data-font-size` attribute (`small`, `default`, `large`, `xlarge`). Avoid fixed pixel heights on text containers that break when font sizes scale.

---

## 4. Component Patterns & Visual Structure
1. **Collapsible Tool & Execution Blocks**:
   - Discrete summary header with monospace command preview, status pill (Running/Success/Failed), and timing indicator.
   - Collapsible chevron that defaults to compact/closed for finished actions and open for active ones.
2. **Panels & Drawers**:
   - Slide-out drawers or modal sheets must use `var(--sidebar)` or `var(--surface)` with `var(--border)` outlines and `var(--topbar-bg)` headers.
   - Header actions must use crisp icon buttons (`.btn-icon`) with `var(--hover-bg)` hover states.
3. **SVG Iconography**:
   - Match the stroke style and viewBox (`0 0 24 24` or `0 0 16 16`, stroke-width `1.5` to `2.0`, `stroke: currentColor`, `fill: none`) used in `icons.js`.
4. **Interactive Diffs & Data Tables**:
   - Diffs must use syntax-colored red/green line indicators with `var(--font-mono)`.
   - Tables must use subtle alternating rows (`var(--surface-subtle)`) and clear header borders (`var(--border)`).
