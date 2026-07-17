/**
 * Windrose AI logo — a windrose (compass rose) re-drawn as a decision graph:
 * eight bearings radiate from a center point, each ending in a data node
 * (candidate decisions); one bearing (NE) is resolved into a bold arrow with a
 * filled node — the chosen decision. The dashed ring is the compass dial /
 * governance boundary every decision passes through. Decision Intelligence,
 * literally: many weighed directions, one confident, auditable bearing.
 *
 * Colors ride the app theme: structure uses currentColor (inherit from text
 * color), the decision arrow uses the `primary` design token, so the mark
 * works in light + dark without variants.
 */
export function WindroseLogo({
  className,
  title = "Windrose AI",
}: {
  className?: string;
  title?: string;
}) {
  return (
    <svg
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label={title}
      className={className}
    >
      <title>{title}</title>
      {/* compass dial / governance ring */}
      <circle
        cx="24"
        cy="24"
        r="21"
        stroke="currentColor"
        strokeOpacity="0.35"
        strokeWidth="1.6"
        strokeDasharray="2.6 4.2"
      />
      {/* candidate bearings: N, E, S, W (long) + NW, SW, SE (short) */}
      <g stroke="currentColor" strokeOpacity="0.6" strokeWidth="1.6" strokeLinecap="round">
        <line x1="24" y1="24" x2="24" y2="10" />
        <line x1="24" y1="24" x2="38" y2="24" />
        <line x1="24" y1="24" x2="24" y2="38" />
        <line x1="24" y1="24" x2="10" y2="24" />
        <line x1="24" y1="24" x2="15" y2="15" />
        <line x1="24" y1="24" x2="15" y2="33" />
        <line x1="24" y1="24" x2="33" y2="33" />
      </g>
      {/* candidate decision nodes (hollow) */}
      <g stroke="currentColor" strokeOpacity="0.7" strokeWidth="1.6" fill="none">
        <circle cx="24" cy="8.4" r="2" />
        <circle cx="39.6" cy="24" r="2" />
        <circle cx="24" cy="39.6" r="2" />
        <circle cx="8.4" cy="24" r="2" />
        <circle cx="13.7" cy="13.7" r="1.7" />
        <circle cx="13.7" cy="34.3" r="1.7" />
        <circle cx="34.3" cy="34.3" r="1.7" />
      </g>
      {/* THE decision: the resolved NE bearing — bold arrow + filled node */}
      <g className="text-primary" stroke="currentColor" fill="currentColor">
        <line
          x1="24"
          y1="24"
          x2="35.2"
          y2="12.8"
          strokeWidth="2.6"
          strokeLinecap="round"
        />
        {/* arrowhead */}
        <path d="M38.8 9.2 L36.9 16.1 L31.9 11.1 Z" stroke="none" />
        {/* chosen-decision node */}
        <circle cx="38.8" cy="9.2" r="2.6" stroke="none" />
      </g>
      {/* center pivot — where evidence meets judgment */}
      <circle cx="24" cy="24" r="3" fill="currentColor" fillOpacity="0.9" />
      <circle cx="24" cy="24" r="1.2" className="text-primary" fill="currentColor" />
    </svg>
  );
}
