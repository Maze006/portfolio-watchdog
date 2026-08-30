---
name: Portfolio Watchdog
colors:
  surface: '#141313'
  surface-dim: '#141313'
  surface-bright: '#3a3939'
  surface-container-lowest: '#0e0e0e'
  surface-container-low: '#1c1b1b'
  surface-container: '#201f1f'
  surface-container-high: '#2b2a2a'
  surface-container-highest: '#353434'
  on-surface: '#e5e2e1'
  on-surface-variant: '#c4c7c7'
  inverse-surface: '#e5e2e1'
  inverse-on-surface: '#313030'
  outline: '#8e9192'
  outline-variant: '#444748'
  surface-tint: '#c8c6c5'
  primary: '#c8c6c5'
  on-primary: '#313030'
  primary-container: '#121212'
  on-primary-container: '#7e7d7d'
  inverse-primary: '#5f5e5e'
  secondary: '#c9c6c5'
  on-secondary: '#313030'
  secondary-container: '#484646'
  on-secondary-container: '#b7b4b4'
  tertiary: '#cac6c3'
  on-tertiary: '#32302f'
  tertiary-container: '#131211'
  on-tertiary-container: '#807d7b'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#e5e2e1'
  primary-fixed-dim: '#c8c6c5'
  on-primary-fixed: '#1c1b1b'
  on-primary-fixed-variant: '#474646'
  secondary-fixed: '#e5e2e1'
  secondary-fixed-dim: '#c9c6c5'
  on-secondary-fixed: '#1c1b1b'
  on-secondary-fixed-variant: '#484646'
  tertiary-fixed: '#e6e1df'
  tertiary-fixed-dim: '#cac6c3'
  on-tertiary-fixed: '#1c1b1a'
  on-tertiary-fixed-variant: '#484645'
  background: '#141313'
  on-background: '#e5e2e1'
  surface-variant: '#353434'
typography:
  display-lg:
    fontFamily: hankenGrotesk
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: hankenGrotesk
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  data-lg:
    fontFamily: jetbrainsMono
    fontSize: 20px
    fontWeight: '500'
    lineHeight: 28px
    letterSpacing: -0.04em
  body-md:
    fontFamily: jetbrainsMono
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0em
  label-sm:
    fontFamily: jetbrainsMono
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.05em
spacing:
  unit: 4px
  container-margin: 16px
  gutter: 8px
  density-high: 4px
  density-medium: 12px
---

## Brand & Style

This design system is engineered for the high-stakes environment of AI-driven financial trading. The brand personality is clinical, authoritative, and relentlessly precise. It rejects decorative softness in favor of a **Swiss-inspired precision** aesthetic, drawing heavy influence from legacy institutional terminals and modern high-frequency trading interfaces.

The visual direction utilizes a "Technical Brutalism" approach:
- **Absolute Precision:** Every element is aligned to a strict grid, emphasizing a sense of structural integrity.
- **Data-First Hierarchy:** Visual flair is stripped away to ensure that real-time metrics and AI signals are the primary focus.
- **Developer-Grade Utility:** The interface feels like a tool for experts, utilizing monospaced accents to evoke a sense of "under-the-hood" transparency and technical rigor.

## Colors

The palette is optimized for long-duration monitoring and high-contrast data legibility. 

- **The Void:** The base layer uses `#0a0a0a` to minimize screen glare and maximize the "pop" of data points.
- **Functional Accents:** Color is never used for decoration. It is strictly semantic.
    - **Emerald Green (#00ff88):** Signals growth, "Buy" actions, and positive delta.
    - **Crimson (#ff3344):** Signals loss, "Sell" actions, and critical alerts.
    - **Electric Blue (#00d1ff):** Reserved for AI-specific logic, automated execution trails, and neutral system status.
- **Neutral Framework:** Shades of charcoal and slate provide the structural scaffolding, using low-vibrancy borders to separate data clusters without creating visual noise.

## Typography

The typography strategy employs a dual-font system to balance readability with technical flavor.

- **Headlines (Hanken Grotesk):** Used for navigation, section titles, and page headers. Its sharp, contemporary geometry provides a professional, "Fintech" structural feel.
- **Data & UI (JetBrains Mono):** All financial figures, tickers, timestamps, and AI logs use this monospaced typeface. The fixed-width nature ensures that rapidly changing numbers do not cause layout "jitter" and remain perfectly vertically aligned in tables.
- **Micro-Labels:** Use uppercase JetBrains Mono with increased letter spacing to provide a clear, "terminal-style" legend for complex data points.

## Layout & Spacing

This design system uses a **High-Density Fixed Grid** optimized for information-rich mobile dashboards. 

- **4px Base Unit:** All spacing must be a multiple of 4px.
- **Density:** Space is treated as a premium resource. Gutters are kept tight (8px) to allow more data to be visible above the fold. 
- **The "Bento" Grid:** Layouts should be composed of modular rectangles that lock together. On mobile, this translates to a single-column stack of cards, but each card can contain internal sub-grids for multi-variable data (e.g., Price | 24h Change | AI Confidence).
- **Margins:** A consistent 16px outer margin ensures the interface doesn't feel cramped against the physical edges of the device.

## Elevation & Depth

In a data-heavy terminal aesthetic, traditional shadows are discarded to prevent visual blur.

- **Flat Layering:** Depth is communicated through **Tonal Stepping**. The background is `#0a0a0a`, and interactive or primary "cards" are `#121212`.
- **Structural Outlines:** Instead of shadows, use 1px solid borders (`#262626`) to define element boundaries.
- **Active State Glow:** Only critical AI signals or active selections may use a subtle outer glow (neon-tinted, 4px blur) to simulate the phosphor luminescence of old-school CRT terminals.
- **Backdrop Filters:** When modals are necessary, use a dark tint with 0% blur—maintain sharp edges throughout the stack.

## Shapes

The design system utilizes **Sharp Corners (0px)** for all UI elements. 

- **Rationale:** Rounded corners evoke consumer-grade friendliness; sharp corners evoke institutional precision and architectural rigidity.
- **Application:** This applies to buttons, cards, input fields, and status indicators. The only exception is the natural circularity of radio buttons or specific circular iconography, though square-boxed checkboxes are preferred.

## Components

- **Action Buttons:** Large, rectangular blocks. Primary actions (Buy/Sell) use full-bleed neon backgrounds with black text for maximum contrast.
- **Data Cards:** Containers with a 1px border. They should feature a "Header" row in label-sm typography and a "Value" row in data-lg.
- **Status Indicators:** Small, rectangular tags.
    - `BUY`: Emerald background, black text.
    - `SELL`: Crimson background, black text.
    - `WATCH`: Electric Blue outline, blue text.
- **Input Fields:** Bottom-border only or full 1px box outline. No background fill unless focused. Focus state changes border color to Electric Blue.
- **Sparklines:** Minimalist, no-axis line charts. Use a 1.5px stroke width. The color of the entire line should reflect the current trend (Emerald or Crimson).
- **AI Logs:** A scrolling feed of monospaced text strings, prefixed with timestamps. Use low-opacity text for history and full-white for new entries.