"use client";
import { useRef, type PointerEvent as ReactPointerEvent } from "react";
import { X, AlertCircle } from "lucide-react";
import { useCanvasStore, type CanvasNode } from "@/lib/pipelines/canvas";
import { cn } from "@/lib/utils";

/* Node/port geometry — shared between node rendering and edge routing. */
const NODE_W = 190;
const HEADER_H = 44;
const ROW_H = 24;
const PAD_Y = 8;
/** Inner (scrollable) canvas surface size. */
const SURFACE_W = 2400;
const SURFACE_H = 1600;

const rowCount = (n: CanvasNode) => Math.max(n.inputs.length, n.outputs.length, 1);
const nodeHeight = (n: CanvasNode) => HEADER_H + PAD_Y * 2 + rowCount(n) * ROW_H;
const portY = (index: number) => HEADER_H + PAD_Y + index * ROW_H + ROW_H / 2;
const outPoint = (n: CanvasNode, i: number) => ({ x: n.x + NODE_W, y: n.y + portY(i) });
const inPoint = (n: CanvasNode, i: number) => ({ x: n.x, y: n.y + portY(i) });

function edgePath(a: { x: number; y: number }, b: { x: number; y: number }): string {
  const dx = Math.max(40, Math.abs(b.x - a.x) / 2);
  return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`;
}

/**
 * The DAG canvas: absolutely-positioned node cards over an SVG edge layer.
 * Nodes drag via pointer events; edges are created by clicking an output port
 * then an input port. Palette items can also be dropped onto the surface.
 */
export function PipelineCanvas({ onDropEntry }: { onDropEntry: (token: string, at: { x: number; y: number }) => void }) {
  const nodes = useCanvasStore((s) => s.nodes);
  const edges = useCanvasStore((s) => s.edges);
  const pending = useCanvasStore((s) => s.pending);
  const scrollRef = useRef<HTMLDivElement>(null);

  const surfacePoint = (clientX: number, clientY: number) => {
    const el = scrollRef.current;
    if (!el) return { x: clientX, y: clientY };
    const rect = el.getBoundingClientRect();
    return { x: clientX - rect.left + el.scrollLeft, y: clientY - rect.top + el.scrollTop };
  };

  return (
    <div
      ref={scrollRef}
      data-testid="pipeline-canvas"
      className="relative h-full flex-1 overflow-auto bg-[radial-gradient(circle,hsl(var(--border))_1px,transparent_1px)] [background-size:20px_20px]"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const token = e.dataTransfer.getData("text/plain");
        if (token) onDropEntry(token, surfacePoint(e.clientX, e.clientY));
      }}
      onClick={(e) => {
        // Click on empty surface clears selection + any pending connection.
        if (e.target === e.currentTarget || (e.target as HTMLElement).dataset.surface === "true") {
          useCanvasStore.getState().selectNode(null);
          useCanvasStore.getState().cancelPending();
        }
      }}
    >
      <div data-surface="true" className="relative" style={{ width: SURFACE_W, height: SURFACE_H }}>
        <svg className="pointer-events-none absolute inset-0 h-full w-full" data-testid="edge-layer">
          {edges.map((e) => {
            const from = nodes.find((n) => n.id === e.from.nodeId);
            const to = nodes.find((n) => n.id === e.to.nodeId);
            if (!from || !to) return null;
            const fi = from.outputs.findIndex((p) => p.name === e.from.port);
            const ti = to.inputs.findIndex((p) => p.name === e.to.port);
            const a = outPoint(from, fi < 0 ? 0 : fi);
            const b = inPoint(to, ti < 0 ? 0 : ti);
            return (
              <g key={e.id} className="pointer-events-auto">
                <path d={edgePath(a, b)} fill="none" stroke="hsl(var(--primary))" strokeWidth={2} />
                {/* Wider invisible hit-path for click-to-delete. */}
                <path
                  d={edgePath(a, b)}
                  fill="none"
                  stroke="transparent"
                  strokeWidth={12}
                  className="cursor-pointer"
                  onClick={() => useCanvasStore.getState().removeEdge(e.id)}
                >
                  <title>Click to remove edge</title>
                </path>
              </g>
            );
          })}
        </svg>

        {nodes.map((n) => (
          <NodeCard key={n.id} node={n} pendingFrom={pending?.nodeId === n.id ? pending.port : null} surfacePoint={surfacePoint} />
        ))}

        {nodes.length === 0 && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <p className="text-sm text-muted-foreground">
              Add a step from the palette (click or drag) to start building.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function NodeCard({
  node,
  pendingFrom,
  surfacePoint,
}: {
  node: CanvasNode;
  pendingFrom: string | null;
  surfacePoint: (x: number, y: number) => { x: number; y: number };
}) {
  const selected = useCanvasStore((s) => s.selectedId === node.id);
  const hasPending = useCanvasStore((s) => !!s.pending);
  const issues = useCanvasStore((s) => s.issues[node.id]);
  const dragState = useRef<{ dx: number; dy: number } | null>(null);

  const onHeaderPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    const p = surfacePoint(e.clientX, e.clientY);
    dragState.current = { dx: p.x - node.x, dy: p.y - node.y };
    e.currentTarget.setPointerCapture(e.pointerId);
    useCanvasStore.getState().selectNode(node.id);
  };
  const onHeaderPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragState.current) return;
    const p = surfacePoint(e.clientX, e.clientY);
    useCanvasStore.getState().moveNode(node.id, Math.max(0, p.x - dragState.current.dx), Math.max(0, p.y - dragState.current.dy));
  };
  const onHeaderPointerUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    dragState.current = null;
    e.currentTarget.releasePointerCapture(e.pointerId);
  };

  return (
    <div
      data-testid="node-card"
      data-node-id={node.id}
      className={cn(
        "absolute rounded-lg border bg-card text-card-foreground shadow-sm",
        selected && "ring-2 ring-primary",
        issues?.length && "border-destructive",
      )}
      style={{ left: node.x, top: node.y, width: NODE_W, height: nodeHeight(node) }}
      onClick={(e) => {
        e.stopPropagation();
        useCanvasStore.getState().selectNode(node.id);
      }}
    >
      <div
        className="flex cursor-grab touch-none items-center justify-between gap-1 rounded-t-lg border-b bg-muted/50 px-2 py-1.5 active:cursor-grabbing"
        onPointerDown={onHeaderPointerDown}
        onPointerMove={onHeaderPointerMove}
        onPointerUp={onHeaderPointerUp}
      >
        <div className="min-w-0">
          <p className="truncate text-sm font-medium leading-tight">{node.displayName}</p>
          <p className="truncate text-[10px] uppercase tracking-wide text-muted-foreground">{node.category}</p>
        </div>
        <div className="flex items-center gap-1">
          {issues?.length ? (
            <span title={issues.join("\n")} className="text-destructive">
              <AlertCircle className="size-3.5" />
            </span>
          ) : null}
          <button
            type="button"
            aria-label={`Remove ${node.displayName}`}
            className="rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              useCanvasStore.getState().removeNode(node.id);
            }}
          >
            <X className="size-3.5" />
          </button>
        </div>
      </div>

      <div className="relative" style={{ height: nodeHeight(node) - HEADER_H }}>
        {node.inputs.map((p, i) => (
          <Port
            key={`in-${p.name}`}
            side="in"
            label={p.name}
            top={portY(i) - HEADER_H}
            highlight={hasPending}
            onClick={(e) => {
              e.stopPropagation();
              const res = useCanvasStore.getState().endConnect(node.id, p.name);
              if (!res.ok && res.reason) announce(res.reason);
            }}
          />
        ))}
        {node.outputs.map((p, i) => (
          <Port
            key={`out-${p.name}`}
            side="out"
            label={`${p.name}: ${p.type}`}
            top={portY(i) - HEADER_H}
            active={pendingFrom === p.name}
            onClick={(e) => {
              e.stopPropagation();
              useCanvasStore.getState().beginConnect(node.id, p.name, p.type);
            }}
          />
        ))}
      </div>
    </div>
  );
}

function Port({
  side,
  label,
  top,
  active,
  highlight,
  onClick,
}: {
  side: "in" | "out";
  label: string;
  top: number;
  active?: boolean;
  highlight?: boolean;
  onClick: (e: React.MouseEvent) => void;
}) {
  return (
    <div
      className={cn("absolute flex items-center gap-1", side === "in" ? "left-0 flex-row" : "right-0 flex-row-reverse")}
      style={{ top: top - ROW_H / 2, height: ROW_H }}
    >
      <button
        type="button"
        aria-label={`${side === "in" ? "input" : "output"} port ${label}`}
        data-port={side}
        onClick={onClick}
        className={cn(
          "size-3 shrink-0 rounded-full border-2 border-primary bg-background transition-colors hover:bg-primary",
          side === "in" ? "-ml-1.5" : "-mr-1.5",
          active && "bg-primary",
          highlight && side === "in" && "ring-2 ring-primary/40",
        )}
      />
      <span className="max-w-[130px] truncate px-1 text-[10px] text-muted-foreground">{label}</span>
    </div>
  );
}

/** Minimal aria-live announcement for illegal-connection feedback. */
function announce(msg: string) {
  if (typeof document === "undefined") return;
  let el = document.getElementById("pipeline-live");
  if (!el) {
    el = document.createElement("div");
    el.id = "pipeline-live";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    el.className = "sr-only";
    document.body.appendChild(el);
  }
  el.textContent = msg;
}
