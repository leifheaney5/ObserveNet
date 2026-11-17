/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "rgb(var(--bg) / <alpha-value>)",
        surface: "rgb(var(--surface) / <alpha-value>)",
        raised: "rgb(var(--raised) / <alpha-value>)",
        line: "rgb(var(--line) / <alpha-value>)",
        ink: "rgb(var(--ink) / <alpha-value>)",
        muted: "rgb(var(--muted) / <alpha-value>)",
        faint: "rgb(var(--faint) / <alpha-value>)",
        accent: "rgb(var(--accent) / <alpha-value>)",
        violet: "rgb(var(--violet) / <alpha-value>)",
        good: "rgb(var(--good) / <alpha-value>)",
        warn: "rgb(var(--warn) / <alpha-value>)",
        serious: "rgb(var(--serious) / <alpha-value>)",
        crit: "rgb(var(--crit) / <alpha-value>)",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      boxShadow: { card: "0 1px 0 0 rgb(255 255 255 / 0.03) inset, 0 8px 24px -12px rgb(0 0 0 / 0.6)" },
      keyframes: {
        "fade-in": { from: { opacity: "0", transform: "translateY(4px)" }, to: { opacity: "1", transform: "none" } },
        pulse_ring: { "0%": { transform: "scale(1)", opacity: "0.7" }, "100%": { transform: "scale(2.4)", opacity: "0" } },
      },
      animation: { "fade-in": "fade-in 200ms ease-out", "pulse-ring": "pulse_ring 1.8s ease-out infinite" },
    },
  },
  plugins: [],
};
