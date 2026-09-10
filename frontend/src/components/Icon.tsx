export type IconName = 'overview' | 'positions' | 'market' | 'risk' | 'trade' | 'report' | 'arrow' | 'refresh' | 'shield' | 'wallet' | 'clock' | 'check';
const paths: Record<IconName, string> = {
  overview: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  positions: 'M4 7h16v14H4z M8 7V3h8v4 M4 12h16 M10 12v3h4v-3',
  market: 'M3 19h18 M6 14V6 M12 17V3 M18 13V8 M4 8h4 M10 7h4 M16 10h4',
  risk: 'M12 3 3 7v5c0 5 9 9 9 9s9-4 9-9V7z M12 8v5 M12 16h.01',
  trade: 'M4 7h15l-4-4 M20 17H5l4 4 M19 7l-4 4 M5 17l4-4',
  report: 'M6 3h8l4 4v14H6z M14 3v5h4 M9 12h6 M9 16h6',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  refresh: 'M20 7v5h-5 M4 17v-5h5 M5 8a8 8 0 0 1 13-3l2 3 M4 16l2 3a8 8 0 0 0 13-3',
  shield: 'M12 3 3 7v5c0 5 9 9 9 9s9-4 9-9V7z M8 12l3 3 5-6',
  wallet: 'M20 7H4V4h14v3 M4 7v13h16V7 M16 12h5v4h-5z',
  clock: 'M12 8v5l3 2 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  check: 'M5 12l4 4L19 6',
};
export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
