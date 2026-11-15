// CodeSmith mark: an anvil (the smith) with code (</>) being forged above it.
export default function Logo({ size = 36 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" role="img" aria-label="CodeSmith logo">
      <defs>
        <linearGradient id="cs-tile" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#2563eb" />
          <stop offset="1" stopColor="#7c3aed" />
        </linearGradient>
      </defs>
      <rect width="48" height="48" rx="11" fill="url(#cs-tile)" />
      {/* code spark: < / > */}
      <path d="M17 10.5 13.5 14l3.5 3.5M31 10.5l3.5 3.5-3.5 3.5M26 9l-4 10" fill="none" stroke="#fde68a"
            strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      {/* anvil: flat face with a horn on the left, narrow waist, wide foot */}
      <path transform="translate(2.5 3)" d="M14 21H38V24C38 25.1 37.1 26 36 26H31L29 30H31C32.1 30 33 30.9 33 32V34H16V32C16 30.9 16.9 30 18 30H20L18 26H14C10 26 7 24.2 5 21.6C8 21.2 11 21 14 21Z" fill="#ffffff" />
    </svg>
  );
}
