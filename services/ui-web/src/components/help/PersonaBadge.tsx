import { Badge } from "@/components/ui/primitives";

/** A small pill for a persona/role, highlighted when it's the viewer's own role. */
export function PersonaBadge({ role, active = false }: { role: string; active?: boolean }) {
  return (
    <Badge
      variant={active ? undefined : "secondary"}
      className={active ? "bg-primary text-primary-foreground" : ""}
    >
      {role}
      {active && <span className="ml-1 opacity-90">· you</span>}
    </Badge>
  );
}
