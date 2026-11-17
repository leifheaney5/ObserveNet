export function Logo({ size = 28, withText = true }: { size?: number; withText?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label="ObserveNet logo">
        <defs>
          <linearGradient id="on-g" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#22d3ee" />
            <stop offset="100%" stopColor="#8b5cf6" />
          </linearGradient>
        </defs>
        <rect width="32" height="32" rx="8" fill="#0c1322" stroke="#1e2942" />
        <circle cx="16" cy="16" r="8.5" fill="none" stroke="url(#on-g)" strokeWidth="2" />
        <circle cx="16" cy="16" r="2.6" fill="#8b5cf6" />
        <path d="M16 6.5v3.2M16 22.3v3.2M6.5 16h3.2M22.3 16h3.2" stroke="#22d3ee" strokeWidth="2" strokeLinecap="round" />
      </svg>
      {withText && (
        <span className="leading-none">
          <span className="block text-[15px] font-semibold tracking-tight text-ink">ObserveNet</span>
          <span className="mt-0.5 block text-[10px] uppercase tracking-[0.16em] text-faint">Know your network</span>
        </span>
      )}
    </span>
  );
}
