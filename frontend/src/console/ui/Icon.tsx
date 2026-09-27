const ICONS = {
  warn: (
    <>
      <path d="M8 2 1.5 13.5h13z" />
      <path d="M8 6.5v3.2M8 11.6v.1" />
    </>
  ),
  copy: (
    <>
      <rect x="5" y="5" width="8.5" height="8.5" rx="1" />
      <path d="M3 10.5V3.5a1 1 0 0 1 1-1h6.5" />
    </>
  ),
  back: <path d="M10 3 5 8l5 5" />,
  x: <path d="M4 4l8 8M12 4l-8 8" />,
  play: <path d="M5 3.5v9l7-4.5z" />,
  info: (
    <>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 7.2v4M8 4.9v.1" />
    </>
  ),
  plus: <path d="M8 3v10M3 8h10" />,
  edit: <path d="M10.5 2.5 13.5 5.5 5.5 13.5H2.5V10.5z" />,
  retry: <path d="M13 8a5 5 0 1 1-1.5-3.6M13 2.5v3h-3" />,
};

export type IconName = keyof typeof ICONS;

/** 16px line icons, 1.5 stroke, in currentColor. */
export function Icon({ name }: { name: IconName }) {
  return (
    <svg className="i" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      {ICONS[name]}
    </svg>
  );
}
